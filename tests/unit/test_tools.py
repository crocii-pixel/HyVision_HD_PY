"""
U-05  HyTool execute() 캐싱 — 동일 img_id 재호출 시 _run 1회만
U-06  HyLine 선 각도 검출 정밀도 — 합성 이미지 기준 각도 오차 ≤ 0.5°
U-07  HyAnd NG > PENDING > OK — [OK, PENDING, NG] → JUDGE_NG
U-08  HyAnd PENDING 전파 — [OK, PENDING, OK] → JUDGE_PENDING
U-09  HyOr OK 단축 평가 → JUDGE_OK
U-10  HyWhen 3-Phase 전환: Watching→Timing→Triggered
U-11  HyWhen Timeout → JUDGE_NG
U-12  HyLocator CycleLock — 동일 cycle_id 재획득 없음
U-13  HyFND LUT 디코딩 — 0~9 샘플
U-14  RecipeTree DFS 직렬화 + 재조립
U-15  RecipeTree State Injection — tool_id 기반
"""
import sys
import os
import time
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pytest
import numpy as np

from HyProtocol import HyProtocol
from HyVisionTools import (
    HyTool, HyLine, HyLocator, HyFND,
    HyWhen, HyAnd, HyOr, HyFin,
    HyLogicTool,
)
from RecipeTree import RecipeTree


# ─────────────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────────────

def grey_img(w=640, h=480):
    return np.zeros((h, w), dtype=np.uint8)


class _CountingTool(HyTool):
    """_run() 호출 횟수를 세는 테스트용 더미 툴."""
    def __init__(self, tool_id=99):
        super().__init__(tool_id, HyProtocol.TOOL_LINE)
        self.run_count = 0

    def _run(self, img):
        self.run_count += 1
        self.rst_state = HyProtocol.JUDGE_OK


class _FixedStateTool(HyTool):
    """_run() 호출 시 미리 지정된 rst_state 를 유지하는 더미 툴."""
    def __init__(self, tool_id, fixed_state):
        super().__init__(tool_id, HyProtocol.TOOL_LINE)
        self._fixed_state = fixed_state
        self.run_count    = 0

    def _run(self, img):
        self.run_count += 1
        self.rst_state = self._fixed_state


def _make_child(tool_id, state):
    """JUDGE_OK/NG/PENDING 상태가 고정된 더미 툴 반환.
    rst_done=EXEC_DONE, rst_state=state 를 미리 설정해
    HyWhen 의 target_ok 판정이 올바르게 동작하도록 함.
    """
    t = _FixedStateTool(tool_id, state)
    t.rst_done  = HyProtocol.EXEC_DONE
    t.rst_state = state   # execute() 전에도 올바른 상태 유지
    return t


# ─────────────────────────────────────────────────────────────────────
# U-05  execute() 캐싱
# ─────────────────────────────────────────────────────────────────────

class TestExecuteCache:
    def test_same_img_id_runs_once(self):
        tool = _CountingTool(1)
        img  = grey_img()
        tool.execute(img, img_id=10, cycle_id=1)
        tool.execute(img, img_id=10, cycle_id=1)   # 동일 img_id
        assert tool.run_count == 1

    def test_different_img_id_runs_again(self):
        tool = _CountingTool(1)
        img  = grey_img()
        tool.execute(img, img_id=10, cycle_id=1)
        tool.execute(img, img_id=11, cycle_id=1)   # 다른 img_id
        assert tool.run_count == 2

    def test_reset_clears_cache(self):
        tool = _CountingTool(1)
        img  = grey_img()
        tool.execute(img, img_id=5, cycle_id=1)
        tool.reset_state()
        tool.execute(img, img_id=5, cycle_id=1)    # 리셋 후 동일 img_id
        assert tool.run_count == 2


# ─────────────────────────────────────────────────────────────────────
# U-06  HyLine 속성 + 선 각도 검출 정밀도 (각도 오차 ≤ 0.5°)
# ─────────────────────────────────────────────────────────────────────

def _line_image(angle_deg: float, w: int = 320, h: int = 240,
                stripe_w: int = 10) -> np.ndarray:
    """알려진 각도의 밝은 선(stripe)이 포함된 그레이스케일 합성 이미지 반환.

    angle_deg: 수평에서 측정한 기울기 각도 (도). 양수 = 우측 아래 방향.
    stripe_w:  선의 두께 (px).
    """
    img  = np.zeros((h, w), dtype=np.uint8)
    m    = math.tan(math.radians(angle_deg))   # 픽셀 기울기 (dy/dx)
    cy   = h / 2.0
    half = stripe_w / 2.0
    for x in range(w):
        y_center = m * (x - w / 2.0) + cy
        y0 = max(0, int(math.floor(y_center - half)))
        y1 = min(h, int(math.ceil(y_center + half)) + 1)
        img[y0:y1, x] = 255
    return img


def _detect_angle(angle_deg: float, w: int = 320, h: int = 240) -> float:
    """합성 이미지에서 HyLine 이 검출한 각도(도)를 반환."""
    img  = _line_image(angle_deg, w=w, h=h)
    line = HyLine(tool_id=1)
    line.search_roi = (0, 0, w, h)
    line.execute(img, img_id=1, cycle_id=1)
    assert line.rst_state == HyProtocol.JUDGE_OK, \
        f"JUDGE_OK expected for angle={angle_deg}, got rst_state={line.rst_state}"
    return line.angle


class TestHyLineRotAngle:
    """rot_angle 속성 기본 검사 (직렬화/표시 용도 확인)."""

    def test_rot_angle_default_zero(self):
        line = HyLine(tool_id=1)
        assert hasattr(line, 'rot_angle')
        assert line.rot_angle == 0.0

    def test_rot_angle_settable(self):
        line = HyLine(tool_id=1)
        line.rot_angle = 15.0
        assert line.rot_angle == 15.0


class TestHyLineAngleDetection:
    """U-06 수용 기준: 합성 선 이미지에서 각도 오차 ≤ 0.5°."""

    def test_horizontal_line_zero_angle(self):
        """수평선(0°) 검출 — 오차 ≤ 0.5°."""
        detected = _detect_angle(0.0)
        assert abs(detected) <= 0.5, f"horizontal: expected ~0, got {detected:.3f}"

    def test_positive_15_degree_line(self):
        """15° 기울어진 선 검출 — 오차 ≤ 0.5°."""
        detected = _detect_angle(15.0)
        assert abs(detected - 15.0) <= 0.5, \
            f"+15 deg: expected ~15, got {detected:.3f}"

    def test_negative_10_degree_line(self):
        """-10° 기울어진 선 검출 — 오차 ≤ 0.5°."""
        detected = _detect_angle(-10.0)
        assert abs(detected - (-10.0)) <= 0.5, \
            f"-10 deg: expected ~-10, got {detected:.3f}"

    def test_small_5_degree_line(self):
        """5° 미세 기울기 검출 — 오차 ≤ 0.5°."""
        detected = _detect_angle(5.0)
        assert abs(detected - 5.0) <= 0.5, \
            f"+5 deg: expected ~5, got {detected:.3f}"


# ─────────────────────────────────────────────────────────────────────
# U-07 / U-08  HyAnd 우선순위
# ─────────────────────────────────────────────────────────────────────

class TestHyAnd:
    def _run_and(self, states):
        logic = HyAnd(tool_id=10)
        for i, st in enumerate(states):
            logic.children.append(_make_child(100 + i, st))
        img = grey_img()
        logic.execute(img, img_id=1, cycle_id=1,
                      tool_index={c.tool_id: c for c in logic.children})
        return logic.rst_state

    def test_ng_wins(self):
        result = self._run_and([
            HyProtocol.JUDGE_OK,
            HyProtocol.JUDGE_PENDING,
            HyProtocol.JUDGE_NG,
        ])
        assert result == HyProtocol.JUDGE_NG

    def test_pending_over_ok(self):
        result = self._run_and([
            HyProtocol.JUDGE_OK,
            HyProtocol.JUDGE_PENDING,
            HyProtocol.JUDGE_OK,
        ])
        assert result == HyProtocol.JUDGE_PENDING

    def test_all_ok(self):
        result = self._run_and([
            HyProtocol.JUDGE_OK,
            HyProtocol.JUDGE_OK,
        ])
        assert result == HyProtocol.JUDGE_OK


# ─────────────────────────────────────────────────────────────────────
# U-09  HyOr OK 단축 평가
# ─────────────────────────────────────────────────────────────────────

class TestHyOr:
    def _run_or(self, states):
        logic = HyOr(tool_id=20)
        for i, st in enumerate(states):
            logic.children.append(_make_child(200 + i, st))
        img = grey_img()
        logic.execute(img, img_id=1, cycle_id=1,
                      tool_index={c.tool_id: c for c in logic.children})
        return logic.rst_state

    def test_ok_short_circuits(self):
        assert self._run_or([
            HyProtocol.JUDGE_NG,
            HyProtocol.JUDGE_OK,
            HyProtocol.JUDGE_NG,
        ]) == HyProtocol.JUDGE_OK

    def test_all_ng_returns_ng(self):
        assert self._run_or([
            HyProtocol.JUDGE_NG,
            HyProtocol.JUDGE_NG,
        ]) == HyProtocol.JUDGE_NG

    def test_pending_propagates(self):
        assert self._run_or([
            HyProtocol.JUDGE_NG,
            HyProtocol.JUDGE_PENDING,
        ]) == HyProtocol.JUDGE_PENDING


# ─────────────────────────────────────────────────────────────────────
# U-10 / U-11  HyWhen 3-Phase + Timeout
# ─────────────────────────────────────────────────────────────────────

class TestHyWhen:
    def _make_watch(self, watch_state):
        watch = _make_child(300, watch_state)
        return watch

    def test_watching_to_timing(self):
        """감시 대상이 OK → Timing 페이즈로 전환."""
        w = HyWhen(tool_id=30)
        w.trigger_value = HyProtocol.JUDGE_OK
        w.timeout_ms    = 500
        watch = self._make_watch(HyProtocol.JUDGE_OK)
        w.watch_tool_id = watch.tool_id
        img = grey_img()
        w.execute(img, img_id=1, cycle_id=1, tool_index={watch.tool_id: watch})
        # 감시 조건 충족 → PENDING(타이머 가동 중) 이어야 함
        assert w.rst_state == HyProtocol.JUDGE_PENDING

    def test_triggered_with_ng_child_yields_ng(self):
        """타이머 만료 후 NG 자식이 있으면 → JUDGE_NG (U-11 매핑)."""
        w = HyWhen(tool_id=31)
        w.condition  = 1           # OK 트리거
        w.timeout_ms = 1           # 1 ms — 사실상 즉시 만료
        w.output_mode = 1          # 자식 결과 반영
        watch = self._make_watch(HyProtocol.JUDGE_OK)
        w.watch_tool_id = watch.tool_id

        ng_child = _FixedStateTool(999, HyProtocol.JUDGE_NG)
        w.children.append(ng_child)

        img = grey_img()
        # 1회: Watching → Timing 시작
        w.execute(img, img_id=1, cycle_id=1, tool_index={watch.tool_id: watch})
        time.sleep(0.05)  # 50ms > 1ms timeout
        # 2회: Timing → Triggered → 자식 실행 → NG
        w.execute(img, img_id=2, cycle_id=1, tool_index={watch.tool_id: watch})
        assert w.rst_state == HyProtocol.JUDGE_NG


# ─────────────────────────────────────────────────────────────────────
# U-12  HyLocator CycleLock
# ─────────────────────────────────────────────────────────────────────

class TestHyLocatorCycleLock:
    """HyLocator UPDATE_CYCLE_LOCK: 동일 cycle_id에서 target_tool 재실행 금지."""

    def _make_locator_with_target(self, tool_id=40):
        """OK 결과를 반환하는 target_tool이 붙은 HyLocator 반환."""
        target = _FixedStateTool(100, HyProtocol.JUDGE_OK)
        target.x     = 50.0
        target.y     = 50.0
        target.angle = 0.0
        target.stat1 = 1.0
        target.allow_rect = None

        loc = HyLocator(tool_id=tool_id)
        loc.update_policy = HyLocator.UPDATE_CYCLE_LOCK
        loc.search_roi    = (0, 0, 640, 480)
        loc.target_tool   = target
        return loc, target

    def test_cycle_lock_prevents_target_rerun(self):
        """동일 cycle_id에서 target_tool._run 이 1회만 호출되는지 확인."""
        loc, target = self._make_locator_with_target(40)
        img = grey_img()

        loc.execute(img, img_id=1, cycle_id=5)
        count_after_first = target.run_count

        # 동일 cycle_id, 다른 img_id → CycleLock 이 target_tool 재실행 차단해야 함
        loc.execute(img, img_id=2, cycle_id=5)
        count_after_second = target.run_count

        assert count_after_first == count_after_second, \
            "CycleLock: 동일 cycle_id에서 target_tool 재실행 발생"

    def test_new_cycle_allows_rerun(self):
        """새 cycle_id에서는 target_tool 재실행이 허용되어야 함."""
        loc, target = self._make_locator_with_target(41)
        img = grey_img()

        loc.execute(img, img_id=1, cycle_id=5)
        count_cycle5 = target.run_count

        loc.execute(img, img_id=2, cycle_id=6)
        count_cycle6 = target.run_count

        assert count_cycle6 > count_cycle5, \
            "새 cycle_id에서 target_tool이 재실행되지 않음"


# ─────────────────────────────────────────────────────────────────────
# U-13  HyFND LUT 디코딩
# ─────────────────────────────────────────────────────────────────────

class TestHyFND:
    def test_fnd_has_seg_lut(self):
        """HyFND.SEG_LUT 클래스 변수 존재 확인."""
        assert hasattr(HyFND, 'SEG_LUT'), "HyFND.SEG_LUT 없음"
        assert isinstance(HyFND.SEG_LUT, dict)

    def test_fnd_lut_covers_0_to_9(self):
        """SEG_LUT에 '0'~'9' 10개가 모두 포함되어야 함."""
        digits = set(HyFND.SEG_LUT.values())
        for ch in '0123456789':
            assert ch in digits, f"SEG_LUT에 '{ch}' 없음"

    def test_fnd_lut_unique_bitmaps(self):
        """각 숫자별 비트맵이 고유해야 함 (중복 키 없음)."""
        assert len(HyFND.SEG_LUT) == len(set(HyFND.SEG_LUT.keys()))


# ─────────────────────────────────────────────────────────────────────
# U-14  RecipeTree DFS 직렬화 + 재조립
# ─────────────────────────────────────────────────────────────────────

class TestRecipeTreeSerialization:
    def test_serialize_produces_32b_packets(self):
        tree = RecipeTree()
        fin  = HyFin(tool_id=1)
        line = HyLine(tool_id=2)
        line.device_id = HyProtocol.DEV_CAMERA
        fin.children.append(line)
        tree.add_tool(fin,  parent_id=0)
        tree.add_tool(line, parent_id=fin.tool_id)

        cmds = tree.serialize_to_commands()
        assert len(cmds) > 0
        for cmd in cmds:
            assert len(cmd) == HyProtocol.CMD_SIZE

    def test_seq_ids_assigned(self):
        tree = RecipeTree()
        fin  = HyFin(tool_id=1)
        line = HyLine(tool_id=2)
        fin.children.append(line)
        tree.add_tool(fin,  parent_id=0)
        tree.add_tool(line, parent_id=fin.tool_id)

        tree.serialize_to_commands()
        assert fin.seq_id  > 0
        assert line.seq_id > 0

    def test_tool_index_intact_after_serialize(self):
        tree = RecipeTree()
        fin  = HyFin(tool_id=5)
        tree.add_tool(fin, parent_id=0)
        tree.serialize_to_commands()
        assert 5 in tree.tool_index


# ─────────────────────────────────────────────────────────────────────
# U-15  RecipeTree State Injection
# ─────────────────────────────────────────────────────────────────────

class TestStateInjection:
    def test_inject_updates_correct_tool(self):
        tree = RecipeTree()
        fin  = HyFin(tool_id=1)
        line = HyLine(tool_id=2)
        fin.children.append(line)
        tree.add_tool(fin,  parent_id=0)
        tree.add_tool(line, parent_id=fin.tool_id)

        burst = [
            {
                'tool_id':   2,
                'rst_done':  HyProtocol.EXEC_DONE,
                'rst_state': HyProtocol.JUDGE_OK,
                'x': 100.0, 'y': 200.0,
                'w': 10.0,  'h': 20.0,
                'angle': 5.0, 'stat1': 0.9,
                'stat2': 0.0, 'stat3': 0.0, 'stat4': 0.0,
                'proc_time': 0,
            }
        ]
        tree.inject_burst(burst, cycle_id=1)

        t = tree.get_tool(2)
        assert t.rst_done  == HyProtocol.EXEC_DONE
        assert t.rst_state == HyProtocol.JUDGE_OK
        assert abs(t.x - 100.0) < 1e-3

    def test_unknown_tool_id_ignored(self):
        tree = RecipeTree()
        burst = [{'tool_id': 999, 'rst_done': 1, 'rst_state': 1,
                  'x': 0.0, 'y': 0.0, 'w': 0.0, 'h': 0.0,
                  'angle': 0.0, 'stat1': 0.0, 'stat2': 0.0,
                  'stat3': 0.0, 'stat4': 0.0, 'proc_time': 0}]
        tree.inject_burst(burst, cycle_id=1)  # 예외 없이 통과
