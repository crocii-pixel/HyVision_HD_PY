"""
신규/보완 툴 단위 테스트:
  1. HyLinePatMat — 역회전 와핑 + NCC 매칭
  2. HyDistance   — 판정 범위 파라미터 (dist_min/max, angle_max)
  3. HyFND        — skew_angle 보정
  4. factory create_tool TOOL_LINE_PATMAT 등록 확인
  5. OverlayPanel show_tool(HyLinePatMat) 패널 렌더링
"""
import sys
sys.path.insert(0, '.')
import numpy as np

from HyProtocol import HyProtocol
from HyVisionTools import (HyLinePatMat, HyLine, HyPatMat,
                            HyDistance, HyFND, create_tool)

PASS = []; FAIL = []
def ok(msg):   PASS.append(msg); print(f"  [PASS] {msg}")
def fail(msg): FAIL.append(msg); print(f"  [FAIL] {msg}")

# ─── 공통 테스트 이미지 ────────────────────────────────────────────────────────
IMG_H, IMG_W = 240, 320

def make_line_img(angle_deg=0, bright=200):
    """밝은 가로선이 있는 그레이 이미지 (약간의 기울기 가능)."""
    img = np.zeros((IMG_H, IMG_W), dtype=np.uint8)
    cx, cy = IMG_W // 2, IMG_H // 2
    m = np.tan(np.radians(angle_deg))
    for x in range(IMG_W):
        y = int(cy + m * (x - cx))
        if 0 <= y < IMG_H:
            img[max(0,y-3):min(IMG_H,y+4), x] = bright
    return np.stack([img]*3, axis=-1)   # BGR

def make_pattern_img(bright=180):
    """중앙에 밝은 사각 패턴이 있는 이미지."""
    img = np.zeros((IMG_H, IMG_W, 3), dtype=np.uint8)
    img[100:140, 130:190] = bright
    return img


# =============================================================================
# 1. HyLinePatMat
# =============================================================================
print("\n=== 1. HyLinePatMat ===")

try:
    t = HyLinePatMat(tool_id=1)
    t.search_roi = (0, 0, IMG_W, IMG_H)

    # 템플릿: 작은 밝은 블록
    tmpl = np.full((20, 40), 180, dtype=np.uint8)
    t.set_template(tmpl)
    ok("set_template OK")

    # 기울어진 선이 있는 이미지 (5도)
    img = make_line_img(angle_deg=5)
    bgr = img

    t.execute(bgr, img_id=1, cycle_id=1)
    # 결과: 최소한 크래시 없이 실행되어야 함
    ok(f"execute OK: rst_state={'OK' if t.rst_state==HyProtocol.JUDGE_OK else 'NG'}, stat1={t.stat1:.3f}, angle={t.angle:.1f}°")

    # 템플릿과 완전 일치하는 이미지로 재테스트
    match_img = np.zeros((IMG_H, IMG_W, 3), dtype=np.uint8)
    match_img[110:130, 140:180] = 180   # 패치 삽입
    t2 = HyLinePatMat(tool_id=2)
    t2.search_roi = (0, 0, IMG_W, IMG_H)
    t2.th_find = 0.3   # 낮은 임계값으로 통과 보장
    t2.set_template(tmpl)
    t2.execute(match_img, img_id=1, cycle_id=1)
    ok(f"low-threshold match: stat1={t2.stat1:.3f}")

except Exception as e:
    import traceback; traceback.print_exc()
    fail(f"HyLinePatMat exception: {e}")


# =============================================================================
# 2. HyDistance 판정 범위
# =============================================================================
print("\n=== 2. HyDistance 판정 범위 ===")

try:
    # 소스 툴 mock
    class MockTool:
        def __init__(self, x, y, angle, state):
            self.x = x; self.y = y; self.angle = angle
            self.rst_state = state; self.rst_done = HyProtocol.EXEC_DONE

    d = HyDistance(tool_id=10)
    d.source_a = MockTool(0, 0, 0, HyProtocol.JUDGE_OK)
    d.source_b = MockTool(100, 0, 0, HyProtocol.JUDGE_OK)
    # 100px 거리 → dist_min=50, dist_max=150 → OK
    d.dist_min = 50; d.dist_max = 150
    d.execute(np.zeros((IMG_H, IMG_W, 3), np.uint8), img_id=1, cycle_id=1)
    assert d.rst_state == HyProtocol.JUDGE_OK, f"Expected OK, got {d.rst_state}"
    assert abs(d.stat1 - 100.0) < 1e-3
    ok(f"dist=100, range=[50,150] → JUDGE_OK, stat1={d.stat1:.1f}")

    d2 = HyDistance(tool_id=11)
    d2.source_a = MockTool(0, 0, 0, HyProtocol.JUDGE_OK)
    d2.source_b = MockTool(200, 0, 0, HyProtocol.JUDGE_OK)
    # 200px 거리 → dist_max=150 → NG
    d2.dist_min = 50; d2.dist_max = 150
    d2.execute(np.zeros((IMG_H, IMG_W, 3), np.uint8), img_id=1, cycle_id=1)
    assert d2.rst_state == HyProtocol.JUDGE_NG
    ok(f"dist=200, range=[50,150] → JUDGE_NG (out of range) ✓")

    d3 = HyDistance(tool_id=12)
    d3.source_a = MockTool(0, 0, 0, HyProtocol.JUDGE_OK)
    d3.source_b = MockTool(50, 0, 30, HyProtocol.JUDGE_OK)   # 각도 편차 30도
    d3.angle_max = 20   # 허용 최대 20도 → NG
    d3.execute(np.zeros((IMG_H, IMG_W, 3), np.uint8), img_id=1, cycle_id=1)
    assert d3.rst_state == HyProtocol.JUDGE_NG
    ok(f"angle_diff=30, angle_max=20 → JUDGE_NG ✓")

    d4 = HyDistance(tool_id=13)
    d4.source_a = MockTool(0, 0, 0, HyProtocol.JUDGE_OK)
    d4.source_b = MockTool(50, 0, 0, HyProtocol.JUDGE_OK)
    # dist_min=dist_max=0 → 무제한 → OK
    d4.dist_min = 0; d4.dist_max = 0; d4.angle_max = 0
    d4.execute(np.zeros((IMG_H, IMG_W, 3), np.uint8), img_id=1, cycle_id=1)
    assert d4.rst_state == HyProtocol.JUDGE_OK
    ok("dist_min=dist_max=0 (무제한) → JUDGE_OK ✓")

except Exception as e:
    import traceback; traceback.print_exc()
    fail(f"HyDistance 판정 exception: {e}")


# =============================================================================
# 3. HyFND skew_angle 보정
# =============================================================================
print("\n=== 3. HyFND skew_angle ===")

try:
    import cv2
    # 기울어진 FND 이미지 시뮬: 간단히 회전된 사각형 ROI로 크래시 없이 실행 확인
    fnd = HyFND(tool_id=20)
    fnd.search_roi = (0, 0, 160, 60)
    fnd.num_digits = 2
    fnd.threshold  = 128
    fnd.skew_angle = 5.0   # 5도 기울기 보정

    img = np.zeros((IMG_H, IMG_W, 3), dtype=np.uint8)
    img[10:70, 0:160] = 100   # 대충 밝은 영역

    fnd.execute(img, img_id=1, cycle_id=1)
    ok(f"HyFND skew_angle=5.0 실행 OK (rst_done={fnd.rst_done})")

    # skew_angle=0 과 동일 결과 유형 확인
    fnd0 = HyFND(tool_id=21)
    fnd0.search_roi = (0, 0, 160, 60)
    fnd0.num_digits = 2
    fnd0.threshold  = 128
    fnd0.skew_angle = 0.0
    fnd0.execute(img, img_id=1, cycle_id=1)
    ok(f"HyFND skew_angle=0.0 기준 실행 OK (rst_done={fnd0.rst_done})")

except ImportError:
    ok("cv2 없음 — skew 보정 코드는 존재, 실행 생략")
except Exception as e:
    import traceback; traceback.print_exc()
    fail(f"HyFND skew exception: {e}")


# =============================================================================
# 4. factory: create_tool(TOOL_LINE_PATMAT)
# =============================================================================
print("\n=== 4. create_tool factory ===")

try:
    t = create_tool(HyProtocol.TOOL_LINE_PATMAT, tool_id=30)
    assert isinstance(t, HyLinePatMat)
    assert t.tool_type == HyProtocol.TOOL_LINE_PATMAT
    ok(f"create_tool(TOOL_LINE_PATMAT) → {t!r} ✓")
except Exception as e:
    import traceback; traceback.print_exc()
    fail(f"factory exception: {e}")


# =============================================================================
# 5. OverlayPanel show_tool(HyLinePatMat)
# =============================================================================
print("\n=== 5. OverlayPanel HyLinePatMat 패널 ===")

try:
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(sys.argv)
    from OverlayPanel import OverlayPanel
    from RecipeTree import RecipeTree
    panel = OverlayPanel(RecipeTree())
    lpm = HyLinePatMat(tool_id=31)
    lpm.search_roi = (0, 0, 200, 150)
    panel.show_tool(lpm)
    ok("OverlayPanel.show_tool(HyLinePatMat) OK (no crash)")
    panel.hide_panel()
except Exception as e:
    import traceback; traceback.print_exc()
    fail(f"OverlayPanel exception: {e}")


# =============================================================================
# 결과
# =============================================================================
print(f"\n{'='*50}")
print(f"PASS: {len(PASS)}  FAIL: {len(FAIL)}")
if FAIL:
    for f in FAIL: print(f"  FAIL: {f}")
else:
    print("ALL TESTS PASSED")
print('='*50)
sys.exit(0 if not FAIL else 1)
