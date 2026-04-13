import sensor, image, time, gc, ustruct, sys, os, machine, math

# ==============================================================================
# [1] 통신 프로토콜 정의 (HyProtocol)
# ==============================================================================
class HyProtocol:
    # 💡 [필수 규칙] MicroPython struct 모듈 오류 방지를 위해 포맷 내 공백 전면 제거
    PACKET_FORMAT = "<HIIIHHHBB5f4fI2x"
    PACKET_SIZE = 64
    SYNC_HEADER = 0xAA55

    @staticmethod
    def pack_result(tx_id, cycle_id, img_id, seq_id, tool_id, tool_type, 
                    rst_done, rst_state, x, y, w, h, angle, 
                    stat1, stat2, stat3, stat4, proc_time):
        return ustruct.pack(HyProtocol.PACKET_FORMAT,
                           HyProtocol.SYNC_HEADER,
                           tx_id, cycle_id, img_id, seq_id, tool_id, tool_type,
                           int(rst_done), int(rst_state),
                           float(x), float(y), float(w), float(h), float(angle),
                           float(stat1), float(stat2), float(stat3), float(stat4),
                           int(proc_time))

# ==============================================================================
# [2] 비전 툴 기본 인터페이스 (BaseVisionTool)
# ==============================================================================
class BaseVisionTool:
    def __init__(self, tool_id, seq_id, tool_type, search_roi):
        self.tool_id = tool_id
        self.seq_id = seq_id
        self.tool_type = tool_type # 1:HyLine, 2:HyPatMat, 3:HyLocator, 4:HyIntersection, 5:HyLinePatMat
        self.search_roi = search_roi # (x, y, w, h)
        
        self.last_img_id = -1
        self.cycle_id = 0
        
        # 공통 결과 속성
        self.rst_done = False
        self.rst_state = False
        self.x = 0.0; self.y = 0.0; self.w = 0.0; self.h = 0.0; self.angle = 0.0
        self.stat1 = 0.0; self.stat2 = 0.0; self.stat3 = 0.0; self.stat4 = 0.0
        self.proc_time = 0
        
    def execute(self, img, img_id, cycle_id=0):
        if self.last_img_id == img_id and self.rst_done:
            return True
            
        self.cycle_id = cycle_id
        t_start = time.ticks_ms()
        
        self._run_algorithm(img)
        
        self.proc_time = time.ticks_diff(time.ticks_ms(), t_start)
        self.last_img_id = img_id
        return self.rst_done

    def _run_algorithm(self, img):
        pass

    def get_packet(self, tx_id, cycle_id, img_id):
        return HyProtocol.pack_result(
            tx_id, cycle_id, img_id, self.seq_id, self.tool_id, self.tool_type,
            self.rst_done, self.rst_state, 
            self.x, self.y, self.w, self.h, self.angle,
            self.stat1, self.stat2, self.stat3, self.stat4, self.proc_time
        )

# ==============================================================================
# [3] 구체적인 비전 툴 구현
# ==============================================================================

# ---------------------------------------------------------
# 3.1 HyLine (단일 선 찾기)
# ---------------------------------------------------------
class HyLine(BaseVisionTool):
    def __init__(self, tool_id, seq_id, search_roi, num_splits=5, cut_ratio=0.55, midpoint_ratio=0.7):
        super().__init__(tool_id, seq_id, tool_type=1, search_roi=search_roi)
        self.num_splits = num_splits
        self.cut_ratio = cut_ratio
        self.midpoint_ratio = midpoint_ratio
        self.w = float(search_roi[2])
        self.h = float(search_roi[3])

    def _run_algorithm(self, img):
        self.rst_done = False
        self.rst_state = False
        
        sx, sy, sw, sh = self.search_roi
        split_w = sw // self.num_splits
        mid_idx = self.num_splits // 2
        profiles = []; thresholds = []
        
        for s in range(self.num_splits):
            x_start = sx + (s * split_w)
            col_profile = []
            for y in range(sy, sy + sh):
                m = img.get_statistics(roi=(x_start, y, split_w, 1)).mean()
                col_profile.append(m)
            v_min, v_max = min(col_profile), max(col_profile)
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
        
        for seed_y in seeds_y:
            line_points = [(x_mid, seed_y)]
            curr_x, curr_y = x_mid, seed_y
            is_valid = True

            for s in range(mid_idx + 1, self.num_splits):
                target_x = sx + (s * split_w) + (split_w // 2)
                peaks = get_peaks(profiles[s], thresholds[s], sy)
                if not peaks: is_valid = False; break
                closest_py = min(peaks, key=lambda py: abs(py - curr_y))
                mx, my = int((curr_x + target_x) / 2), int((curr_y + closest_py) / 2)
                mid_val = img.get_statistics(roi=(max(0, mx-1), max(0, my-1), 3, 3)).mean()
                
                if mid_val >= thresholds[s] * self.midpoint_ratio:
                    line_points.append((target_x, closest_py)); curr_x, curr_y = target_x, closest_py
                else: is_valid = False; break
            if not is_valid: continue

            curr_x, curr_y = x_mid, seed_y
            for s in range(mid_idx - 1, -1, -1):
                target_x = sx + (s * split_w) + (split_w // 2)
                peaks = get_peaks(profiles[s], thresholds[s], sy)
                if not peaks: is_valid = False; break
                closest_py = min(peaks, key=lambda py: abs(py - curr_y))
                mx, my = int((curr_x + target_x) / 2), int((curr_y + closest_py) / 2)
                mid_val = img.get_statistics(roi=(max(0, mx-1), max(0, my-1), 3, 3)).mean()
                
                if mid_val >= thresholds[s] * self.midpoint_ratio:
                    line_points.insert(0, (target_x, closest_py)); curr_x, curr_y = target_x, closest_py
                else: is_valid = False; break

            if is_valid and len(line_points) >= 2:
                best_line = line_points; break

        if len(best_line) >= 2:
            n = len(best_line)
            sum_x = sum(p[0] for p in best_line)
            sum_y = sum(p[1] for p in best_line)
            sum_xy = sum(p[0] * p[1] for p in best_line)
            sum_xx = sum(p[0] * p[0] for p in best_line)
            denom = (n * sum_xx - sum_x * sum_x)
            
            m = 0 if denom == 0 else (n * sum_xy - sum_x * sum_y) / denom
            b = sum_y / n if denom == 0 else (sum_y - m * sum_x) / n
            
            self.angle = math.degrees(math.atan(m))
            self.x = sx + sw / 2.0
            self.y = m * self.x + b
            self.rst_done = True
            self.rst_state = True
            self.stat1 = float(max(thresholds)) 

# ---------------------------------------------------------
# 3.2 HyLocator (마스터 앵커 툴)
# ---------------------------------------------------------
class HyLocator(BaseVisionTool):
    def __init__(self, tool_id, seq_id, target_tool, allow_rect, allow_angle_range, update_freq=1):
        super().__init__(tool_id, seq_id, tool_type=3, search_roi=allow_rect)
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
            self.rst_state = False
            return
            
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

# ---------------------------------------------------------
# 3.3 HyPatMat (일반 패턴 매칭)
# ---------------------------------------------------------
class HyPatMat(BaseVisionTool):
    def __init__(self, tool_id, seq_id, search_roi, templates, th_find=0.5):
        super().__init__(tool_id, seq_id, tool_type=2, search_roi=search_roi)
        self.templates = templates 
        self.th_find = th_find
        
    def _run_algorithm(self, img):
        self.rst_done = False; self.rst_state = False
        if not self.templates: return 
        
        target_template = self.templates[-1] # 원본 해상도 템플릿 사용
        
        r = img.find_template(target_template, self.th_find, roi=self.search_roi, step=1, search=image.SEARCH_EX)
        if r:
            self.w = float(r[2])
            self.h = float(r[3])
            self.x = r[0] + self.w / 2.0
            self.y = r[1] + self.h / 2.0
            self.angle = 0.0 
            self.stat1 = float(r[5]) # NCC Score
            
            self.rst_done = True
            self.rst_state = True

# ---------------------------------------------------------
# 3.4 HyIntersection (두 선의 교차점 추출)
# ---------------------------------------------------------
class HyIntersection(BaseVisionTool):
    def __init__(self, tool_id, seq_id, line_tool_1, line_tool_2):
        super().__init__(tool_id, seq_id, tool_type=4, search_roi=(0,0,0,0))
        self.line1 = line_tool_1
        self.line2 = line_tool_2
        
    def _run_algorithm(self, img):
        self.rst_done = False; self.rst_state = False
        
        if not self.line1.rst_done or not self.line2.rst_done: return
            
        a1 = math.radians(self.line1.angle)
        a2 = math.radians(self.line2.angle)
        m1 = math.tan(a1); m2 = math.tan(a2)
        
        if abs(m1 - m2) < 1e-4: return
        
        b1 = self.line1.y - m1 * self.line1.x
        b2 = self.line2.y - m2 * self.line2.x
        
        cx = (b2 - b1) / (m1 - m2)
        cy = m1 * cx + b1
        
        self.x = float(cx)
        self.y = float(cy)
        self.angle = 0.0
        
        self.rst_done = True
        self.rst_state = True

# ---------------------------------------------------------
# 💡 [신규] 3.5 HyLinePatMat (하이브리드: 선 각도 추출 후 패턴 매칭)
# ---------------------------------------------------------
class HyLinePatMat(BaseVisionTool):
    def __init__(self, tool_id, seq_id, target_line_tool, search_roi, templates, th_find=0.5):
        super().__init__(tool_id, seq_id, tool_type=5, search_roi=search_roi)
        self.target_line = target_line_tool
        self.templates = templates
        self.th_find = th_find
        
    def _run_algorithm(self, img):
        self.rst_done = False
        self.rst_state = False
        if not self.templates: return
        
        # 1. 선행 툴에서 획득한 각도 캐싱 (0ms 소요)
        if not self.target_line.rst_done: return
        theta = self.target_line.angle
        
        sx, sy, sw, sh = self.search_roi
        
        # 2. OOM 방어 및 역방향 크롭 (Inverse Crop Warping)
        patch = sensor.alloc_extra_fb(sw, sh, sensor.GRAYSCALE)
        if not patch: return # 메모리 부족 시 패스
        
        try:
            # 원본 이미지에서 Search ROI만 떼어냄
            patch.draw_image(img, 0, 0, roi=(sx, sy, sw, sh))
            
            # 각도 역보정 (-theta 회전)
            patch.rotation_corr(z_rotation=-theta)
            
            target_template = self.templates[-1]
            
            # 3. 똑바르게 펴진 영역에서 패턴 매칭
            r = patch.find_template(target_template, self.th_find, step=1, search=image.SEARCH_EX)
            
            if r:
                lw = float(r[2]); lh = float(r[3])
                lcx = r[0] + lw / 2.0
                lcy = r[1] + lh / 2.0
                
                # 4. 매칭된 좌표를 다시 원본 이미지의 기울어진 절대 좌표로 원상 복구 (Math Rotation)
                pcx = sw / 2.0
                pcy = sh / 2.0
                
                dx = lcx - pcx
                dy = lcy - pcy
                
                rad = math.radians(theta)
                cos_t = math.cos(rad)
                sin_t = math.sin(rad)
                
                # +theta 정방향 회전 보정
                rx = dx * cos_t - dy * sin_t
                ry = dx * sin_t + dy * cos_t
                
                self.x = sx + pcx + rx
                self.y = sy + pcy + ry
                self.w = lw
                self.h = lh
                self.angle = theta # 객체의 각도는 선의 각도를 추종
                self.stat1 = float(r[5]) # NCC Score
                
                self.rst_done = True
                self.rst_state = True
        finally:
            # [매우 중요] 성공/실패, 에러 발생 여부에 상관없이 메모리 누수 완벽 차단
            sensor.dealloc_extra_fb()

# ==============================================================================
# [4] 엔진 매니저 (TaskRunner & ProtocolHandler)
# ==============================================================================
class VisionTaskRunner:
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

class ProtocolHandler:
    def __init__(self):
        try:
            from pyb import USB_VCP
            self.usb = USB_VCP()
            self.usb.setinterrupt(-1)
        except ImportError:
            self.usb = None 
            
    def send_burst(self, packets, img):
        if not self.usb: return
        self.usb.send(ustruct.pack('<HH', HyProtocol.SYNC_HEADER, len(packets)), timeout=500)
        for p in packets:
            self.usb.send(p, timeout=500)
        cimg = img.compress(quality=50)
        self.usb.send(ustruct.pack('<I', cimg.size()), timeout=500)
        self.usb.send(cimg.bytearray(), timeout=500)

# ==============================================================================
# [5] 메인 루프 (테스트용 하드코딩 환경)
# ==============================================================================
if __name__ == "__main__":
    sensor.reset()
    sensor.set_pixformat(sensor.GRAYSCALE)
    sensor.set_framesize(sensor.VGA)
    sensor.skip_frames(time=1000)
    
    runner = VisionTaskRunner()
    handler = ProtocolHandler()
    
    # -------------------------------------------------------------
    # [시퀀스 트리 테스트용 더미 등록]
    # -------------------------------------------------------------
    line_anchor = HyLine(tool_id=101, seq_id=1, search_roi=(100, 100, 400, 200))
    locator = HyLocator(tool_id=201, seq_id=2, target_tool=line_anchor, 
                        allow_rect=(150, 150, 300, 100), allow_angle_range=(-15.0, 15.0), update_freq=0)
    line_measure = HyLine(tool_id=102, seq_id=3, search_roi=(100, 320, 400, 150))
    intersection = HyIntersection(tool_id=301, seq_id=4, line_tool_1=line_anchor, line_tool_2=line_measure)
    
    # [신규] 하이브리드 패턴 매칭 툴 등록 
    # (실제 환경에선 Flash에서 템플릿 이미지를 읽어와 templates 배열을 주입합니다.)
    # 여기서는 빈 템플릿([])으로 등록하여 크래시 여부만 검증합니다.
    hybrid_patmat = HyLinePatMat(tool_id=401, seq_id=5, target_line_tool=line_anchor, 
                                 search_roi=(200, 200, 150, 150), templates=[], th_find=0.6)
    
    runner.add_tool(line_anchor)
    runner.add_tool(locator)
    runner.add_tool(line_measure)
    runner.add_tool(intersection)
    runner.add_tool(hybrid_patmat)
    
    cycle_count = 0
    tx_count = 0
    img_count = 0
    clock = time.clock()
    
    while True:
        clock.tick()
        img = sensor.snapshot()
        img_count += 1
        
        if handler.usb and handler.usb.any():
            cmd = handler.usb.read(1)
            if cmd == b't': 
                tx_count += 1
                cycle_count += 1
                packets = runner.run_all(img, tx_count, cycle_count, img_count)
                handler.send_burst(packets, img)
                gc.collect()