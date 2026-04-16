import cv2
import numpy as np
import math
import time
from HyProtocol import HyProtocol

# ==============================================================================
# [1] 비전 툴 기본 인터페이스 (BaseVisionTool_PC)
# ==============================================================================
class BaseVisionTool_PC:
    def __init__(self, tool_id, seq_id, tool_type, search_roi):
        self.tool_id = tool_id
        self.seq_id = seq_id
        self.tool_type = tool_type
        self.search_roi = search_roi # (x, y, w, h)
        
        self.last_img_id = -1
        self.cycle_id = 0
        
        self.rst_done = False
        self.rst_state = False
        self.x = 0.0; self.y = 0.0; self.w = 0.0; self.h = 0.0; self.angle = 0.0
        self.stat1 = 0.0; self.stat2 = 0.0; self.stat3 = 0.0; self.stat4 = 0.0
        self.proc_time = 0
        
    def execute(self, img, img_id, cycle_id=0):
        if self.last_img_id == img_id and self.rst_done:
            return True
        self.cycle_id = cycle_id
        t_start = time.time()
        
        self._run_algorithm(img)
        
        self.proc_time = int((time.time() - t_start) * 1000)
        self.last_img_id = img_id
        return self.rst_done

    def _run_algorithm(self, img): pass

    def get_packet(self, tx_id, cycle_id, img_id):
        return HyProtocol.pack_result(
            tx_id, cycle_id, img_id, self.seq_id, self.tool_id, self.tool_type,
            self.rst_done, self.rst_state, 
            self.x, self.y, self.w, self.h, self.angle,
            self.stat1, self.stat2, self.stat3, self.stat4, self.proc_time
        )

# ==============================================================================
# [2] 구체적인 비전 툴 구현 (OpenCV/NumPy 최적화)
# ==============================================================================
class HyLine_PC(BaseVisionTool_PC):
    def __init__(self, tool_id, seq_id, search_roi, num_splits=5, cut_ratio=0.55, midpoint_ratio=0.7):
        super().__init__(tool_id, seq_id, 1, search_roi)
        self.num_splits = num_splits
        self.cut_ratio = cut_ratio
        self.midpoint_ratio = midpoint_ratio

    def _run_algorithm(self, img):
        self.rst_done = False; self.rst_state = False
        sx, sy, sw, sh = self.search_roi
        
        # 이미지 바운더리 보호 및 Grayscale 변환
        h, w = img.shape[:2]
        sx, sy = max(0, sx), max(0, sy)
        sw, sh = min(sw, w - sx), min(sh, h - sy)
        if sw <= 0 or sh <= 0: return
        
        roi_img = img[sy:sy+sh, sx:sx+sw]
        if len(roi_img.shape) == 3:
            roi_img = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY)

        split_w = sw // self.num_splits
        mid_idx = self.num_splits // 2
        profiles = []; thresholds = []
        
        # NumPy를 이용한 고속 세로 프로파일링
        for s in range(self.num_splits):
            x_start = s * split_w
            split_img = roi_img[:, x_start:x_start+split_w]
            col_profile = split_img.mean(axis=1) # 각 행의 평균
            
            v_min, v_max = col_profile.min(), col_profile.max()
            v_thresh = max(v_min + (v_max - v_min) * self.cut_ratio, 20)
            profiles.append(col_profile); thresholds.append(v_thresh)

        def get_peaks(profile, threshold, base_y):
            peaks = []; in_band = False; start = 0
            for i, val in enumerate(profile):
                if val >= threshold:
                    if not in_band: start = i; in_band = True
                else:
                    if in_band: peaks.append(base_y + (start + i - 1) // 2); in_band = False
            if in_band: peaks.append(base_y + (start + len(profile) - 1) // 2)
            return peaks

        x_mid = sx + (mid_idx * split_w) + (split_w // 2)
        seeds_y = get_peaks(profiles[mid_idx], thresholds[mid_idx], sy)
        best_line = []
        
        # 중간 명암 검증용 패딩 함수
        def check_mid(mx, my, thresh):
            mx_local, my_local = mx - sx, my - sy
            if 1 <= mx_local < sw-1 and 1 <= my_local < sh-1:
                return roi_img[my_local-1:my_local+2, mx_local-1:mx_local+2].mean() >= thresh
            return False

        for seed_y in seeds_y:
            line_points = [(x_mid, seed_y)]
            curr_x, curr_y = x_mid, seed_y
            is_valid = True

            # 오른쪽 확장
            for s in range(mid_idx + 1, self.num_splits):
                target_x = sx + (s * split_w) + (split_w // 2)
                peaks = get_peaks(profiles[s], thresholds[s], sy)
                if not peaks: is_valid = False; break
                closest_py = min(peaks, key=lambda py: abs(py - curr_y))
                mx, my = int((curr_x + target_x) / 2), int((curr_y + closest_py) / 2)
                
                if check_mid(mx, my, thresholds[s] * self.midpoint_ratio):
                    line_points.append((target_x, closest_py)); curr_x, curr_y = target_x, closest_py
                else: is_valid = False; break
            if not is_valid: continue

            # 왼쪽 확장
            curr_x, curr_y = x_mid, seed_y
            for s in range(mid_idx - 1, -1, -1):
                target_x = sx + (s * split_w) + (split_w // 2)
                peaks = get_peaks(profiles[s], thresholds[s], sy)
                if not peaks: is_valid = False; break
                closest_py = min(peaks, key=lambda py: abs(py - curr_y))
                mx, my = int((curr_x + target_x) / 2), int((curr_y + closest_py) / 2)
                
                if check_mid(mx, my, thresholds[s] * self.midpoint_ratio):
                    line_points.insert(0, (target_x, closest_py)); curr_x, curr_y = target_x, closest_py
                else: is_valid = False; break

            if is_valid and len(line_points) >= 2:
                best_line = line_points; break

        # 선형 회귀
        if len(best_line) >= 2:
            n = len(best_line)
            pts = np.array(best_line)
            sum_x, sum_y = np.sum(pts[:,0]), np.sum(pts[:,1])
            sum_xy, sum_xx = np.sum(pts[:,0]*pts[:,1]), np.sum(pts[:,0]**2)
            denom = (n * sum_xx - sum_x * sum_x)
            
            m = 0 if denom == 0 else (n * sum_xy - sum_x * sum_y) / denom
            b = sum_y / n if denom == 0 else (sum_y - m * sum_x) / n
            
            self.angle = math.degrees(math.atan(m))
            self.x = sx + sw / 2.0
            self.y = m * self.x + b
            self.rst_done = True; self.rst_state = True
            self.stat1 = float(max(thresholds))

class HyLocator_PC(BaseVisionTool_PC):
    def __init__(self, tool_id, seq_id, target_tool, allow_rect, allow_angle_range, update_freq=1):
        super().__init__(tool_id, seq_id, 3, allow_rect)
        self.target_tool = target_tool
        self.allow_rect = allow_rect
        self.allow_angle_range = allow_angle_range
        self.update_freq = update_freq 
        self.locked_cycle_id = -1
        self.locked_x = 0.0; self.locked_y = 0.0; self.locked_angle = 0.0
        
    def _run_algorithm(self, img):
        if self.update_freq == 0 and self.locked_cycle_id == self.cycle_id:
            self.x, self.y, self.angle = self.locked_x, self.locked_y, self.locked_angle
            self.rst_done = True; self.rst_state = True
            return

        self.target_tool.execute(img, self.last_img_id, self.cycle_id)
        self.rst_done = self.target_tool.rst_done
        if not self.rst_done:
            self.rst_state = False; return
            
        cx, cy, cangle = self.target_tool.x, self.target_tool.y, self.target_tool.angle
        ax, ay, aw, ah = self.allow_rect
        min_a, max_a = self.allow_angle_range
        
        in_rect = (ax <= cx <= ax + aw) and (ay <= cy <= ay + ah)
        in_angle = (min_a <= cangle <= max_a)
        
        self.rst_state = bool(in_rect and in_angle)
        self.x, self.y, self.angle = cx, cy, cangle
        
        if self.rst_state and self.update_freq == 0:
            self.locked_cycle_id = self.cycle_id
            self.locked_x, self.locked_y, self.locked_angle = cx, cy, cangle

class HyLinePatMat_PC(BaseVisionTool_PC):
    def __init__(self, tool_id, seq_id, target_line_tool, search_roi, templates, th_find=0.5):
        super().__init__(tool_id, seq_id, 5, search_roi)
        self.target_line = target_line_tool
        self.templates = templates # OpenCV 형식 이미지(numpy) 리스트
        self.th_find = th_find
        
    def _run_algorithm(self, img):
        self.rst_done = False; self.rst_state = False
        if not self.templates: return
        if not self.target_line.rst_done: return
        
        theta = self.target_line.angle
        sx, sy, sw, sh = map(int, self.search_roi)
        
        # 바운더리 체크
        h, w = img.shape[:2]
        if sx < 0 or sy < 0 or sx+sw > w or sy+sh > h: return
        
        roi_img = img[sy:sy+sh, sx:sx+sw]
        if len(roi_img.shape) == 3:
            roi_img = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY)
            
        # 💡 [핵심] OpenCV를 이용한 초고속 역방향 크롭 (Inverse Crop Warping)
        center = (sw / 2.0, sh / 2.0)
        M = cv2.getRotationMatrix2D(center, -theta, 1.0)
        patch = cv2.warpAffine(roi_img, M, (sw, sh))
        
        target_template = self.templates[-1]
        if len(target_template.shape) == 3:
            target_template = cv2.cvtColor(target_template, cv2.COLOR_BGR2GRAY)
            
        th, tw = target_template.shape[:2]
        if patch.shape[0] < th or patch.shape[1] < tw: return
        
        # OpenCV 템플릿 매칭 (TM_CCOEFF_NORMED)
        res = cv2.matchTemplate(patch, target_template, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
        
        if max_val >= self.th_find:
            lcx = max_loc[0] + tw / 2.0
            lcy = max_loc[1] + th / 2.0
            
            # 수학적 원상 복구 (Math Rotation)
            pcx = sw / 2.0
            pcy = sh / 2.0
            dx, dy = lcx - pcx, lcy - pcy
            
            rad = math.radians(theta)
            cos_t, sin_t = math.cos(rad), math.sin(rad)
            rx = dx * cos_t - dy * sin_t
            ry = dx * sin_t + dy * cos_t
            
            self.x = sx + pcx + rx
            self.y = sy + pcy + ry
            self.w = float(tw); self.h = float(th)
            self.angle = theta
            self.stat1 = float(max_val)
            
            self.rst_done = True; self.rst_state = True

class VisionTaskRunner_PC:
    def __init__(self):
        self.tools = []
    def add_tool(self, tool):
        self.tools.append(tool)
    def run_all(self, img, tx_id, cycle_id, img_id):
        packets = []
        for tool in self.tools:
            tool.execute(img, img_id, cycle_id)
            packets.append(tool.get_packet(tx_id, cycle_id, img_id))
        return packets