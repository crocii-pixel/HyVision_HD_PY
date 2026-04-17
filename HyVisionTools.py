"""
HyVisionTools.py - OpenCV 기반 비전 툴 라이브러리 (v2.0)
물리 비전 툴 / 정밀 측정 툴 / 로직 집행관 툴 전체 포함.
"""
import time
import math
import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from HyProtocol import HyProtocol


# =============================================================================
# 추상 기본 클래스
# =============================================================================

class HyTool:
    """모든 비전 툴의 공통 인터페이스 (DCOM 트리 리프 노드)."""

    def __init__(self, tool_id: int, tool_type: int, device_id: int = HyProtocol.DEV_CAMERA):
        self.tool_id   = tool_id
        self.seq_id    = 0
        self.tool_type = tool_type
        self.device_id = device_id
        self.search_roi = (0, 0, 100, 100)   # (x, y, w, h) 이미지 좌표
        self.rot_angle  = 0.0                 # Search ROI 회전각 (도°). fparam 채널로 전달
        self.use_anchor = True
        self.parent_id  = 0                   # 0 = 루트 노드
        self.name       = HyProtocol.TOOL_NAMES.get(tool_type, f"Tool_{tool_id}")
        # usable_devices: 이 툴을 실행할 수 있는 장치 플래그
        # {'camera': True, 'pc': False} 형식. 비어 있으면 device_id 기반 기본 동작.
        self.usable_devices: dict = {}

        # 결과 상태
        self.rst_done  = HyProtocol.EXEC_IDLE
        self.rst_state = HyProtocol.JUDGE_NG

        # 결과 기하
        self.x     = 0.0
        self.y     = 0.0
        self.w     = 0.0
        self.h     = 0.0
        self.angle = 0.0

        # 다목적 통계치
        self.stat1 = 0.0
        self.stat2 = 0.0
        self.stat3 = 0.0
        self.stat4 = 0.0
        self.proc_time = 0

        # 이미지 캐시 (같은 img_id면 재연산 생략)
        self._last_img_id   = -1
        self._last_cycle_id = -1

    # ─── 실행 ───────────────────────────────────────────────────────────────
    def execute(self, img: np.ndarray, img_id: int, cycle_id: int) -> int:
        """연산 수행. rst_done 반환. 동일 img_id 면 캐시 반환."""
        if img_id == self._last_img_id and cycle_id == self._last_cycle_id:
            return self.rst_done

        # _run() 이 재귀적으로 target_tool.execute() 를 호출할 때
        # 올바른 img_id/cycle_id 를 전달받을 수 있도록 _run() 호출 전에 갱신.
        self._last_img_id   = img_id
        self._last_cycle_id = cycle_id

        t0 = time.perf_counter()
        try:
            self._run(img)
            self.rst_done = HyProtocol.EXEC_DONE
        except Exception:
            self.rst_done  = HyProtocol.EXEC_ERROR
            self.rst_state = HyProtocol.JUDGE_NG

        self.proc_time = int((time.perf_counter() - t0) * 1000)
        return self.rst_done

    def _run(self, img: np.ndarray) -> None:
        """서브클래스가 오버라이드하는 실제 알고리즘. 기본은 no-op."""
        pass

    # ─── 직렬화 ─────────────────────────────────────────────────────────────
    def to_packet(self, tx_id: int, cycle_id: int, img_id: int) -> bytes:
        """64B Result Struct 직렬화."""
        return HyProtocol.pack_result(
            tx_id, cycle_id, img_id,
            self.seq_id, self.tool_id, self.tool_type,
            self.rst_done, self.rst_state,
            self.x, self.y, self.w, self.h, self.angle,
            self.stat1, self.stat2, self.stat3, self.stat4,
            self.proc_time
        )

    def from_packet(self, d: dict) -> None:
        """Result dict → 상태 역직렬화 (State Injection)."""
        self.rst_done  = d.get('rst_done',  HyProtocol.EXEC_IDLE)
        self.rst_state = d.get('rst_state', HyProtocol.JUDGE_NG)
        self.x         = d.get('x',     0.0)
        self.y         = d.get('y',     0.0)
        self.w         = d.get('w',     0.0)
        self.h         = d.get('h',     0.0)
        self.angle     = d.get('angle', 0.0)
        self.stat1     = d.get('stat1', 0.0)
        self.stat2     = d.get('stat2', 0.0)
        self.stat3     = d.get('stat3', 0.0)
        self.stat4     = d.get('stat4', 0.0)
        self.proc_time = d.get('proc_time', 0)

    # ─── 내부 헬퍼 ──────────────────────────────────────────────────────────
    def _extract_roi(self, img: np.ndarray) -> np.ndarray:
        x, y, w, h = [int(v) for v in self.search_roi]
        ih, iw = img.shape[:2]
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(iw, x + w), min(ih, y + h)
        return img[y1:y2, x1:x2]

    def _to_gray(self, img: np.ndarray) -> np.ndarray:
        if not HAS_CV2:
            return img[:, :, 0] if len(img.shape) == 3 else img
        if len(img.shape) == 3:
            return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return img

    def reset_state(self):
        """결과 상태 초기화."""
        self.rst_done  = HyProtocol.EXEC_IDLE
        self.rst_state = HyProtocol.JUDGE_NG
        self._last_img_id   = -1
        self._last_cycle_id = -1

    def __repr__(self):
        return f"<{self.__class__.__name__} id={self.tool_id} name='{self.name}'>"


# =============================================================================
# 물리 비전 툴 (Device=1 할당)
# =============================================================================

class HyLine(HyTool):
    """
    5-Split 밴드 프로파일링 + 최소자승 회귀로 직선 추출.
    midpoint_check 로 노이즈 밴드 방어.
    """

    def __init__(self, tool_id: int):
        super().__init__(tool_id, HyProtocol.TOOL_LINE, HyProtocol.DEV_CAMERA)
        self.num_splits = 5
        self.cut_ratio  = 0.55
        self.mid_check  = False
        self.mid_ratio  = 0.70
        self.scan_dir   = 0     # 0: 상→하, 1: 하→상
        self.peak_mode  = 0     # 0: 중앙, 1: 시작단, 2: 끝단

    def _run(self, img: np.ndarray) -> None:
        roi  = self._extract_roi(img)
        gray = self._to_gray(roi)
        h, w = gray.shape

        if h < 4 or w < 4:
            self.rst_state = HyProtocol.JUDGE_NG
            return

        band_w = max(1, w // self.num_splits)
        peak_pts = []

        for i in range(self.num_splits):
            bx1 = i * band_w
            bx2 = (i + 1) * band_w if i < self.num_splits - 1 else w
            band = gray[:, bx1:bx2]

            col_means = np.mean(band.astype(float), axis=1)
            if self.scan_dir == 1:
                col_means = col_means[::-1]

            max_val   = float(np.max(col_means))
            threshold = max_val * self.cut_ratio
            above     = np.where(col_means >= threshold)[0]
            if len(above) == 0:
                continue

            if self.peak_mode == 0:
                peak_y = int((above[0] + above[-1]) / 2)
            elif self.peak_mode == 1:
                peak_y = int(above[0])
            else:
                peak_y = int(above[-1])

            if self.scan_dir == 1:
                peak_y = h - 1 - peak_y

            band_cx = bx1 + (bx2 - bx1) // 2
            peak_pts.append((float(band_cx), float(peak_y)))
            self.stat1 = max_val

        if len(peak_pts) < 2:
            self.rst_state = HyProtocol.JUDGE_NG
            return

        px = np.array([p[0] for p in peak_pts])
        py = np.array([p[1] for p in peak_pts])
        m, b = np.polyfit(px, py, 1)

        cx = w / 2.0
        cy = m * cx + b

        rx, ry = self.search_roi[0], self.search_roi[1]
        self.x     = float(rx + cx)
        self.y     = float(ry + cy)
        self.angle = float(math.degrees(math.atan(m)))
        self.w     = float(self.search_roi[2])
        self.h     = float(self.search_roi[3])

        if self.mid_check:
            mx, my = int(cx), int(cy)
            if 0 <= my < h and 0 <= mx < w:
                mid_val = float(gray[my, mx])
                if mid_val < self.stat1 * self.mid_ratio:
                    self.rst_state = HyProtocol.JUDGE_NG
                    return

        self.rst_state = HyProtocol.JUDGE_OK


class HyPatMat(HyTool):
    """
    OpenCV TM_CCOEFF_NORMED 기반 NCC 템플릿 매칭.
    templates dict: {level: ndarray}  (level 0=가장 작은 피라미드)
    """

    def __init__(self, tool_id: int):
        super().__init__(tool_id, HyProtocol.TOOL_PATMAT, HyProtocol.DEV_CAMERA)
        self.templates: dict = {}   # {0: ndarray, ...}
        self.th_scan  = 0.10
        self.th_find  = 0.50

    def set_template(self, tmpl: np.ndarray):
        """템플릿 설정 (그레이스케일 ndarray)."""
        if HAS_CV2:
            gray = cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY) if len(tmpl.shape) == 3 else tmpl
        else:
            gray = tmpl[:, :, 0] if len(tmpl.shape) == 3 else tmpl
        self.templates = {0: gray}

    def _run(self, img: np.ndarray) -> None:
        if not HAS_CV2 or not self.templates:
            self.rst_state = HyProtocol.JUDGE_NG
            return

        roi  = self._extract_roi(img)
        gray = self._to_gray(roi)
        tmpl = self.templates.get(0)

        if tmpl is None or tmpl.shape[0] > gray.shape[0] or tmpl.shape[1] > gray.shape[1]:
            self.rst_state = HyProtocol.JUDGE_NG
            return

        result = cv2.matchTemplate(gray.astype(np.uint8),
                                   tmpl.astype(np.uint8),
                                   cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        self.stat1 = float(max_val)

        if max_val < self.th_find:
            self.rst_state = HyProtocol.JUDGE_NG
            return

        th, tw = tmpl.shape[:2]
        cx = max_loc[0] + tw / 2.0
        cy = max_loc[1] + th / 2.0

        rx, ry = self.search_roi[0], self.search_roi[1]
        self.x     = float(rx + cx)
        self.y     = float(ry + cy)
        self.w     = float(tw)
        self.h     = float(th)
        self.angle = 0.0
        self.rst_state = HyProtocol.JUDGE_OK


class HyLocator(HyTool):
    """
    시스템 전체 공간 좌표계 기준 단일 마스터 앵커.
    target_tool (HyLine 또는 HyPatMat) 의 결과를 앵커 좌표로 확정.
    """
    UPDATE_CONTINUOUS = 0   # 매 프레임 갱신
    UPDATE_CYCLE_LOCK = 1   # 사이클 시작 시 1회 고정

    def __init__(self, tool_id: int):
        super().__init__(tool_id, HyProtocol.TOOL_LOCATOR, HyProtocol.DEV_CAMERA)
        self.use_anchor = False   # 앵커 자신은 앵커를 따르지 않음
        self.target_tool: HyTool | None = None
        self.update_policy = self.UPDATE_CONTINUOUS
        self.allow_rect    = None             # (x,y,w,h) | None = 전체 허용
        self.allow_angle_range = (-180.0, 180.0)
        self._locked_cycle_id = -1

    def _run(self, img: np.ndarray) -> None:
        if self.target_tool is None:
            self.rst_state = HyProtocol.JUDGE_NG
            return

        # Cycle Lock: 이미 이 사이클에서 고정됐으면 재연산 스킵
        if self.update_policy == self.UPDATE_CYCLE_LOCK:
            if self._locked_cycle_id == self._last_cycle_id and self._last_cycle_id >= 0:
                return   # 이미 고정, 이전 결과 유지

        # target_tool 도 같은 img 로 실행 (캐시 적용)
        self.target_tool.execute(img, self._last_img_id, self._last_cycle_id)

        if self.target_tool.rst_state != HyProtocol.JUDGE_OK:
            self.rst_state = HyProtocol.JUDGE_NG
            return

        cx = self.target_tool.x
        cy = self.target_tool.y
        ca = self.target_tool.angle

        # 허용 영역 검사
        if self.allow_rect is not None:
            ax, ay, aw, ah = self.allow_rect
            if not (ax <= cx <= ax + aw and ay <= cy <= ay + ah):
                self.rst_state = HyProtocol.JUDGE_NG
                return

        a_min, a_max = self.allow_angle_range
        if not (a_min <= ca <= a_max):
            self.rst_state = HyProtocol.JUDGE_NG
            return

        self.x     = cx
        self.y     = cy
        self.angle = ca
        self.stat1 = self.target_tool.stat1
        self.rst_state = HyProtocol.JUDGE_OK

        if self.update_policy == self.UPDATE_CYCLE_LOCK:
            self._locked_cycle_id = self._last_cycle_id


class HyIntersection(HyTool):
    """두 HyLine 의 교차점을 연립방정식으로 도출."""

    def __init__(self, tool_id: int):
        super().__init__(tool_id, HyProtocol.TOOL_INTERSECTION, HyProtocol.DEV_CAMERA)
        self.source_a: HyTool | None = None
        self.source_b: HyTool | None = None

    def _run(self, img: np.ndarray) -> None:
        if self.source_a is None or self.source_b is None:
            self.rst_state = HyProtocol.JUDGE_NG
            return
        if (self.source_a.rst_state != HyProtocol.JUDGE_OK or
                self.source_b.rst_state != HyProtocol.JUDGE_OK):
            self.rst_state = HyProtocol.JUDGE_NG
            return

        # 두 선: y = tan(a)*x + b  →  교차점 연립
        a1 = math.radians(self.source_a.angle)
        a2 = math.radians(self.source_b.angle)
        m1, m2 = math.tan(a1), math.tan(a2)
        b1 = self.source_a.y - m1 * self.source_a.x
        b2 = self.source_b.y - m2 * self.source_b.x

        denom = m1 - m2
        if abs(denom) < 1e-9:
            self.rst_state = HyProtocol.JUDGE_NG
            return

        ix = (b2 - b1) / denom
        iy = m1 * ix + b1
        self.x     = float(ix)
        self.y     = float(iy)
        self.angle = float(math.degrees(abs(a1 - a2)))
        self.rst_state = HyProtocol.JUDGE_OK


class HyLinePatMat(HyTool):
    """
    역방향 크롭 와핑(Inverse Crop Warp) 기반 회전-보정 템플릿 매칭.
    1) ROI 내 HyLine 방식으로 기준선 각도 검출
    2) 해당 각도로 역회전(de-rotate) 보정
    3) 보정된 이미지에서 NCC 템플릿 매칭
    4) 매칭 좌표를 원본 좌표계로 역변환
    """

    def __init__(self, tool_id: int):
        super().__init__(tool_id, HyProtocol.TOOL_LINE_PATMAT, HyProtocol.DEV_CAMERA)
        self.templates: dict = {}    # {0: ndarray}  그레이스케일
        self.th_find   = 0.50        # NCC 매칭 임계값
        self.num_splits = 5          # 라인 검출 분할 수
        self.cut_ratio  = 0.55       # 라인 피크 컷 비율
        self._line_helper = None     # 내부 HyLine 인스턴스 (지연 생성)
        self.target_line = None      # 각도 공급원 HyLine 인스턴스 (None이면 자체 검출)

    def set_template(self, tmpl: np.ndarray):
        if HAS_CV2:
            gray = cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY) if len(tmpl.shape) == 3 else tmpl
        else:
            gray = tmpl[:, :, 0] if len(tmpl.shape) == 3 else tmpl
        self.templates = {0: gray}

    def _run(self, img: np.ndarray) -> None:
        if not HAS_CV2 or not self.templates:
            self.rst_state = HyProtocol.JUDGE_NG
            return

        roi  = self._extract_roi(img)
        gray = self._to_gray(roi)
        h, w = gray.shape

        # ── Step 1: 라인 각도 검출 ──────────────────────────────────────────
        # target_line이 설정되어 있고 OK이면 해당 툴의 angle을 재사용
        if (self.target_line is not None
                and self.target_line.rst_state == HyProtocol.JUDGE_OK):
            angle_deg = float(self.target_line.angle)
        else:
            angle_deg = self._detect_line_angle(gray)

        # ── Step 2: 역회전 보정 ─────────────────────────────────────────────
        cx, cy = w / 2.0, h / 2.0
        M = cv2.getRotationMatrix2D((cx, cy), -angle_deg, 1.0)
        derotated = cv2.warpAffine(gray, M, (w, h),
                                   flags=cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_REPLICATE)

        # ── Step 3: NCC 템플릿 매칭 ─────────────────────────────────────────
        tmpl = self.templates.get(0)
        if tmpl is None or tmpl.shape[0] > h or tmpl.shape[1] > w:
            self.rst_state = HyProtocol.JUDGE_NG
            return

        result = cv2.matchTemplate(derotated.astype(np.uint8),
                                   tmpl.astype(np.uint8),
                                   cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        self.stat1 = float(max_val)

        if max_val < self.th_find:
            self.rst_state = HyProtocol.JUDGE_NG
            return

        th, tw = tmpl.shape[:2]
        # 보정 이미지 기준 중심
        mx = max_loc[0] + tw / 2.0
        my = max_loc[1] + th / 2.0

        # ── Step 4: 원본 좌표 역변환 ────────────────────────────────────────
        cos_a = math.cos(math.radians(angle_deg))
        sin_a = math.sin(math.radians(angle_deg))
        ox = cos_a * (mx - cx) - sin_a * (my - cy) + cx
        oy = sin_a * (mx - cx) + cos_a * (my - cy) + cy

        rx, ry = self.search_roi[0], self.search_roi[1]
        self.x     = float(rx + ox)
        self.y     = float(ry + oy)
        self.w     = float(tw)
        self.h     = float(th)
        self.angle = float(angle_deg)
        self.rst_state = HyProtocol.JUDGE_OK

    def _detect_line_angle(self, gray: np.ndarray) -> float:
        """5-split band profiling으로 대략적 라인 각도 반환 (도 단위)."""
        h, w = gray.shape
        band_w = max(1, w // self.num_splits)
        pts = []
        for i in range(self.num_splits):
            bx1 = i * band_w
            bx2 = (i + 1) * band_w if i < self.num_splits - 1 else w
            band = gray[:, bx1:bx2]
            col_means = np.mean(band.astype(float), axis=1)
            max_v = float(np.max(col_means))
            above = np.where(col_means >= max_v * self.cut_ratio)[0]
            if len(above) > 0:
                pts.append((float(bx1 + (bx2 - bx1) // 2),
                            float((above[0] + above[-1]) / 2)))
        if len(pts) < 2:
            return 0.0
        px = np.array([p[0] for p in pts])
        py = np.array([p[1] for p in pts])
        m, _ = np.polyfit(px, py, 1)
        return float(math.degrees(math.atan(m)))


# =============================================================================
# 정밀 측정 툴 (PC=2 할당)
# =============================================================================

class HyDistance(HyTool):
    """두 HyTool 결과 좌표 간 픽셀/물리 거리 및 최대 각도 편차."""

    def __init__(self, tool_id: int):
        super().__init__(tool_id, HyProtocol.TOOL_DISTANCE, HyProtocol.DEV_PC)
        self.source_a: HyTool | None = None
        self.source_b: HyTool | None = None
        self.projection_axis = "perpendicular"  # "perpendicular"|"horizontal"|"vertical"
        self.px_to_mm  = 0.0       # 0 이면 픽셀 단위
        self.dist_min  = 0.0       # 판정 최솟값 (0 = 무제한)
        self.dist_max  = 0.0       # 판정 최댓값 (0 = 무제한)
        self.angle_max = 0.0       # 최대 허용 각도 편차 (0 = 무제한)

    def _run(self, img: np.ndarray) -> None:
        if self.source_a is None or self.source_b is None:
            self.rst_state = HyProtocol.JUDGE_NG
            return
        if (self.source_a.rst_state != HyProtocol.JUDGE_OK or
                self.source_b.rst_state != HyProtocol.JUDGE_OK):
            self.rst_state = HyProtocol.JUDGE_NG
            return

        dx = self.source_b.x - self.source_a.x
        dy = self.source_b.y - self.source_a.y

        if self.projection_axis == "horizontal":
            dist = abs(dx)
        elif self.projection_axis == "vertical":
            dist = abs(dy)
        else:
            dist = math.hypot(dx, dy)

        if self.px_to_mm > 0:
            dist *= self.px_to_mm

        angle_diff = abs(self.source_a.angle - self.source_b.angle)
        self.stat1    = float(dist)
        self.stat2    = float(angle_diff)
        self.x        = self.source_a.x
        self.y        = self.source_a.y
        self.angle    = float(math.degrees(math.atan2(dy, dx)))

        # 판정: 거리 범위 + 각도 편차
        dist_ok  = ((self.dist_min <= 0 or dist >= self.dist_min) and
                    (self.dist_max <= 0 or dist <= self.dist_max))
        angle_ok = (self.angle_max <= 0 or angle_diff <= self.angle_max)
        self.rst_state = HyProtocol.JUDGE_OK if (dist_ok and angle_ok) else HyProtocol.JUDGE_NG


class HyContrast(HyTool):
    """ROI 내 밝기 평균 / 표준편차 분석 → 표면 불량 판정."""

    def __init__(self, tool_id: int):
        super().__init__(tool_id, HyProtocol.TOOL_CONTRAST, HyProtocol.DEV_PC)
        self.mean_range = (0.0, 255.0)   # (min, max)
        self.stdev_max  = 255.0

    def _run(self, img: np.ndarray) -> None:
        roi  = self._extract_roi(img)
        gray = self._to_gray(roi).astype(float)

        mean_val = float(np.mean(gray))
        std_val  = float(np.std(gray))

        self.stat1 = mean_val
        self.stat2 = std_val
        self.x     = float(self.search_roi[0])
        self.y     = float(self.search_roi[1])

        lo, hi = self.mean_range
        if (lo <= mean_val <= hi) and (std_val <= self.stdev_max):
            self.rst_state = HyProtocol.JUDGE_OK
        else:
            self.rst_state = HyProtocol.JUDGE_NG


class HyFND(HyTool):
    """
    가상 블록 기반 7-세그먼트 디스플레이 판독기.
    (현재는 기본 구현 — 블록 밝기 평가 + LUT 디코딩 포함)
    """

    # 7-세그먼트 LUT: 세그먼트 비트 → 문자
    # 비트 순서: a(6) b(5) c(4) d(3) e(2) f(1) g(0)
    SEG_LUT = {
        0b1111110: '0', 0b0110000: '1', 0b1101101: '2',
        0b1111001: '3', 0b0110011: '4', 0b1011011: '5',
        0b1011111: '6', 0b1110000: '7', 0b1111111: '8',
        0b1111011: '9', 0b1110111: 'A', 0b0011111: 'b',
        0b1001110: 'C', 0b0111101: 'd', 0b1001111: 'E',
        0b1000111: 'F',
    }

    def __init__(self, tool_id: int):
        super().__init__(tool_id, HyProtocol.TOOL_FND, HyProtocol.DEV_PC)
        self.num_digits     = 4
        self.threshold      = 128
        self.skew_angle     = 0.0
        self.block_thickness = 4
        self.judge_mode     = "equal"   # "equal" | "range"
        self.target_value   = ""
        self.range_min      = 0.0
        self.range_max      = 9999.0

    def _run(self, img: np.ndarray) -> None:
        if not HAS_CV2:
            self.rst_state = HyProtocol.JUDGE_NG
            return

        roi  = self._extract_roi(img)
        gray = self._to_gray(roi)

        # skew 보정: 기울기 각도만큼 역회전
        if abs(self.skew_angle) > 0.1:
            h0, w0 = gray.shape
            M = cv2.getRotationMatrix2D((w0 / 2.0, h0 / 2.0), -self.skew_angle, 1.0)
            gray = cv2.warpAffine(gray, M, (w0, h0),
                                  flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_REPLICATE)

        h, w = gray.shape
        digit_w = max(1, w // self.num_digits)
        decoded = ""

        for d in range(self.num_digits):
            dx1 = d * digit_w
            dx2 = (d + 1) * digit_w if d < self.num_digits - 1 else w
            dw  = dx2 - dx1
            dh  = h
            digit_img = gray[:, dx1:dx2]

            # 7개 세그먼트 블록 위치 (간략화 버전)
            t = self.block_thickness
            seg_regions = {
                'a': digit_img[0:t,       t:dw - t],         # 상단 가로
                'b': digit_img[t:dh//2,   dw - t:dw],        # 우상 세로
                'c': digit_img[dh//2:dh-t,dw - t:dw],        # 우하 세로
                'd': digit_img[dh - t:dh, t:dw - t],         # 하단 가로
                'e': digit_img[dh//2:dh-t,0:t],              # 좌하 세로
                'f': digit_img[t:dh//2,   0:t],              # 좌상 세로
                'g': digit_img[dh//2-t//2:dh//2+t//2, t:dw-t],  # 중단 가로
            }

            bits = 0
            for i, key in enumerate(['a', 'b', 'c', 'd', 'e', 'f', 'g']):
                region = seg_regions[key]
                if region.size > 0:
                    mean_v = float(np.mean(region))
                    if mean_v >= self.threshold:
                        bits |= (1 << (6 - i))

            ch = self.SEG_LUT.get(bits, '?')
            decoded += ch

        self.stat1 = float(sum(c.isdigit() for c in decoded)) / max(1, len(decoded))
        self.stat2 = float(len(decoded.replace('?', '')))

        if self.judge_mode == "equal":
            self.rst_state = HyProtocol.JUDGE_OK if decoded == self.target_value else HyProtocol.JUDGE_NG
        else:
            try:
                val = float(decoded)
                self.rst_state = HyProtocol.JUDGE_OK if self.range_min <= val <= self.range_max else HyProtocol.JUDGE_NG
            except ValueError:
                self.rst_state = HyProtocol.JUDGE_NG


# =============================================================================
# 로직 집행관 툴 (PC=2 할당) — 컨테이너 노드
# =============================================================================

class HyLogicTool(HyTool):
    """로직 집행관 공통 기반. children 리스트를 보유하는 컨테이너 노드."""

    def __init__(self, tool_id: int, tool_type: int):
        super().__init__(tool_id, tool_type, HyProtocol.DEV_PC)
        self.children: list = []   # list[HyTool]
        self.use_anchor = False

    def add_child(self, tool: HyTool):
        tool.parent_id = self.tool_id
        self.children.append(tool)

    def remove_child(self, tool_id: int):
        self.children = [c for c in self.children if c.tool_id != tool_id]

    def get_all_descendants(self) -> list:
        """DFS 로 모든 하위 노드 반환."""
        result = []
        for c in self.children:
            result.append(c)
            if isinstance(c, HyLogicTool):
                result.extend(c.get_all_descendants())
        return result


class HyWhen(HyLogicTool):
    """
    PLC TON(Timer On-Delay) 개념의 조건부 지연 타이머 및 트리거.
    감시 → 조건 충족 → 타이머 대기 → 하위 실행 → 결과 보고.
    """
    PHASE_WATCHING  = "watching"
    PHASE_TIMING    = "timing"
    PHASE_TRIGGERED = "triggered"

    def __init__(self, tool_id: int):
        super().__init__(tool_id, HyProtocol.TOOL_WHEN)
        self.watch_tool_id = 0       # 감시 대상 툴 ID
        self.condition     = 1       # 0=NG 조건, 1=OK 조건
        self.timeout_ms    = 0       # 조건 충족 후 대기 시간 (ms)
        self.output_mode   = 1       # 0=실행여부, 1=자식 결과
        self._timer_start  = 0.0
        self._phase        = self.PHASE_WATCHING

    def execute(self, img: np.ndarray, img_id: int, cycle_id: int,
                tool_index: dict = None) -> int:
        """tool_index: {tool_id: HyTool} — 감시 대상 조회용."""
        t0 = time.perf_counter()

        watch_tool = None
        if tool_index and self.watch_tool_id in tool_index:
            watch_tool = tool_index[self.watch_tool_id]

        target_ok = False
        if watch_tool:
            expected = HyProtocol.JUDGE_OK if self.condition == 1 else HyProtocol.JUDGE_NG
            target_ok = (watch_tool.rst_state == expected and
                         watch_tool.rst_done  == HyProtocol.EXEC_DONE)

        if self._phase == self.PHASE_WATCHING:
            if not target_ok:
                self.rst_done  = HyProtocol.EXEC_PENDING
                self.rst_state = HyProtocol.JUDGE_PENDING
                self.proc_time = int((time.perf_counter() - t0) * 1000)
                return self.rst_done
            # 조건 충족 → 타이머 시작
            self._phase       = self.PHASE_TIMING
            self._timer_start = time.perf_counter()

        if self._phase == self.PHASE_TIMING:
            elapsed_ms = (time.perf_counter() - self._timer_start) * 1000
            if elapsed_ms < self.timeout_ms:
                self.rst_done  = HyProtocol.EXEC_PENDING
                self.rst_state = HyProtocol.JUDGE_PENDING
                self.proc_time = int((time.perf_counter() - t0) * 1000)
                return self.rst_done
            self._phase = self.PHASE_TRIGGERED

        # TRIGGERED — 하위 노드 실행
        child_result = HyProtocol.JUDGE_OK
        for child in self.children:
            if isinstance(child, HyLogicTool):
                child.execute(img, img_id, cycle_id, tool_index)
            else:
                child.execute(img, img_id, cycle_id)

            if child.rst_state == HyProtocol.JUDGE_NG:
                child_result = HyProtocol.JUDGE_NG
                break

        self.rst_done = HyProtocol.EXEC_DONE
        if self.output_mode == 0:
            self.rst_state = HyProtocol.JUDGE_OK   # "실행됐다" 자체가 OK
        else:
            self.rst_state = child_result

        self._phase    = self.PHASE_WATCHING   # 다음 사이클을 위해 리셋
        self.proc_time = int((time.perf_counter() - t0) * 1000)
        return self.rst_done

    def reset_phase(self):
        self._phase = self.PHASE_WATCHING


class HyAnd(HyLogicTool):
    """
    순차 논리 AND. 판정 우선순위: JUDGE_NG > JUDGE_PENDING > JUDGE_OK.
    NG 발견 즉시 반환(단축 평가).
    """

    def __init__(self, tool_id: int):
        super().__init__(tool_id, HyProtocol.TOOL_AND)

    def execute(self, img: np.ndarray, img_id: int, cycle_id: int,
                tool_index: dict = None) -> int:
        t0 = time.perf_counter()

        has_pending = False
        for child in self.children:
            if isinstance(child, HyWhen):
                child.execute(img, img_id, cycle_id, tool_index)
            elif isinstance(child, HyLogicTool):
                child.execute(img, img_id, cycle_id, tool_index)
            else:
                child.execute(img, img_id, cycle_id)

            if child.rst_state == HyProtocol.JUDGE_NG:
                # NG 최우선 → 즉시 단축
                self.rst_done  = HyProtocol.EXEC_DONE
                self.rst_state = HyProtocol.JUDGE_NG
                self.proc_time = int((time.perf_counter() - t0) * 1000)
                return self.rst_done

            if child.rst_state == HyProtocol.JUDGE_PENDING:
                has_pending = True

        self.rst_done = HyProtocol.EXEC_DONE
        self.rst_state = HyProtocol.JUDGE_PENDING if has_pending else HyProtocol.JUDGE_OK
        self.proc_time = int((time.perf_counter() - t0) * 1000)
        return self.rst_done


class HyOr(HyLogicTool):
    """
    순차 논리 OR. OK 발견 즉시 반환(단축 평가).
    """

    def __init__(self, tool_id: int):
        super().__init__(tool_id, HyProtocol.TOOL_OR)

    def execute(self, img: np.ndarray, img_id: int, cycle_id: int,
                tool_index: dict = None) -> int:
        t0 = time.perf_counter()

        has_pending = False
        for child in self.children:
            if isinstance(child, HyWhen):
                child.execute(img, img_id, cycle_id, tool_index)
            elif isinstance(child, HyLogicTool):
                child.execute(img, img_id, cycle_id, tool_index)
            else:
                child.execute(img, img_id, cycle_id)

            if child.rst_state == HyProtocol.JUDGE_OK:
                self.rst_done  = HyProtocol.EXEC_DONE
                self.rst_state = HyProtocol.JUDGE_OK
                self.proc_time = int((time.perf_counter() - t0) * 1000)
                return self.rst_done

            if child.rst_state == HyProtocol.JUDGE_PENDING:
                has_pending = True

        self.rst_done  = HyProtocol.EXEC_DONE
        self.rst_state = HyProtocol.JUDGE_PENDING if has_pending else HyProtocol.JUDGE_NG
        self.proc_time = int((time.perf_counter() - t0) * 1000)
        return self.rst_done


class HyFin(HyLogicTool):
    """
    사이클 종료자. 최종 판정을 도출하고 I/O 핀 및 Status Box 에 브로드캐스트.
    children 이 있으면 AND 논리로 집계.
    """

    def __init__(self, tool_id: int):
        super().__init__(tool_id, HyProtocol.TOOL_FIN)
        self.io_mapping: dict  = {}          # {판정값: 핀번호}
        self.broadcast_target  = "status_box"
        self._judge_result     = HyProtocol.JUDGE_NG   # 마지막 판정 결과

    def execute(self, img: np.ndarray, img_id: int, cycle_id: int,
                tool_index: dict = None) -> int:
        t0 = time.perf_counter()

        if self.children:
            # AND 방식으로 집계
            result = HyProtocol.JUDGE_OK
            for child in self.children:
                if isinstance(child, HyLogicTool):
                    child.execute(img, img_id, cycle_id, tool_index)
                else:
                    child.execute(img, img_id, cycle_id)
                if child.rst_state == HyProtocol.JUDGE_NG:
                    result = HyProtocol.JUDGE_NG
                    break
                if child.rst_state == HyProtocol.JUDGE_PENDING:
                    result = HyProtocol.JUDGE_PENDING
            self.rst_state = result
        else:
            self.rst_state = HyProtocol.JUDGE_OK   # 자식 없으면 항상 OK

        self._judge_result = self.rst_state
        self.rst_done      = HyProtocol.EXEC_DONE
        self.proc_time     = int((time.perf_counter() - t0) * 1000)
        return self.rst_done

    @property
    def is_ok(self) -> bool:
        return self._judge_result == HyProtocol.JUDGE_OK


# =============================================================================
# 팩토리 함수
# =============================================================================

def create_tool(tool_type: int, tool_id: int) -> HyTool:
    """tool_type 으로 적절한 HyTool 인스턴스 생성."""
    mapping = {
        HyProtocol.TOOL_LINE:         HyLine,
        HyProtocol.TOOL_PATMAT:       HyPatMat,
        HyProtocol.TOOL_LOCATOR:      HyLocator,
        HyProtocol.TOOL_INTERSECTION: HyIntersection,
        HyProtocol.TOOL_LINE_PATMAT:  HyLinePatMat,
        HyProtocol.TOOL_DISTANCE:     HyDistance,
        HyProtocol.TOOL_CONTRAST:     HyContrast,
        HyProtocol.TOOL_FND:          HyFND,
        HyProtocol.TOOL_WHEN:         HyWhen,
        HyProtocol.TOOL_AND:          HyAnd,
        HyProtocol.TOOL_OR:           HyOr,
        HyProtocol.TOOL_FIN:          HyFin,
    }
    cls = mapping.get(tool_type)
    if cls is None:
        raise ValueError(f"알 수 없는 tool_type: {tool_type:#04x}")
    return cls(tool_id)


def is_logic_tool(tool: HyTool) -> bool:
    return isinstance(tool, HyLogicTool)


def is_physical_tool(tool: HyTool) -> bool:
    return not isinstance(tool, HyLogicTool)
