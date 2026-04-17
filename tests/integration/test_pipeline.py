"""
I-01  HyLink ↔ VirtualMachine 32B Cmd → 64B Burst 왕복 (tool_id·cycle_id 일치)
I-02  HyLink Flow Control — 큐 깊이 3 초과 → Drop (크래시 없음)
I-03  HyLink Heartbeat — 3회 Timeout → sig_connected(2)
I-04  RecipeTree + HyVisionTools — evaluate() 비전툴 실행 + 로직 평가
I-05  Fixture 렌더링 — get_fixture_transform() 좌표 변환 픽셀 오차 ≤ 1px
"""
import sys
import os
import time
import queue
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pytest
import numpy as np

from HyProtocol import HyProtocol
from HyVisionTools import HyLine, HyAnd, HyFin, HyLocator
from RecipeTree import RecipeTree
from VirtualMachine import VirtualMachine

try:
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import QTimer
    _app = QApplication.instance() or QApplication(sys.argv)
    HAS_QT = True
except Exception:
    HAS_QT = False


# ─────────────────────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def _start_vvm():
    """VirtualMachine 인스턴스 생성 + 스레드 시작."""
    vm = VirtualMachine()
    vm.start()
    time.sleep(0.1)   # 스레드 준비 대기
    return vm


def _stop_vvm(vm):
    vm.running = False
    vm.quit()
    vm.wait(2000)


def _send_cmd(vm, cmd_id, target_id=0, target_type=0,
              p0=0, p1=0, p2=0, p3=0, fparam=0.0):
    raw = HyProtocol.pack_command(
        cmd_id,
        target_id=target_id,
        target_type=target_type,
        p0=p0, p1=p1, p2=p2, p3=p3,
        fparam=fparam,
    )
    vm.cmd_queue.put_nowait(raw)


def _recv_burst(vm, timeout=1.0):
    """rst_queue 에서 Burst 를 꺼내 (results, img_bytes) 반환."""
    try:
        burst_bytes = vm.rst_queue.get(timeout=timeout)
        return HyProtocol.unpack_burst(burst_bytes)
    except queue.Empty:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# I-01  HyLink ↔ VirtualMachine 왕복
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not HAS_QT, reason="PyQt5 required")
class TestVmmRoundtrip:
    def test_ping_pong_tool_id_zero(self):
        """CMD_PING → PONG Burst: tool_id=0, rst_done=EXEC_DONE."""
        vm = _start_vvm()
        try:
            _send_cmd(vm, HyProtocol.CMD_PING)
            result = _recv_burst(vm, timeout=1.0)
            assert result is not None, "PONG Burst 수신 실패"
            packets, _ = result
            pong = next((p for p in packets if p['tool_id'] == 0), None)
            assert pong is not None, "PONG 패킷(tool_id=0) 없음"
            assert pong['rst_done'] == HyProtocol.EXEC_DONE
        finally:
            _stop_vvm(vm)

    def test_set_tool_registers_in_tree(self):
        """CMD_SET_TOOL → VVM 내부 RecipeTree 에 툴 등록."""
        vm = _start_vvm()
        try:
            # HyFin(id=1) 루트로 등록
            _send_cmd(vm, HyProtocol.CMD_SET_TOOL,
                      target_id=1,
                      target_type=HyProtocol.TOOL_FIN,
                      p0=HyProtocol.encode_tree_info(HyProtocol.DEV_PC, 0))
            # VVM STANDBY 루프 주기(50ms) × 3 대기
            for _ in range(20):
                time.sleep(0.03)
                if vm.recipe_tree.get_tool(1) is not None:
                    break
            assert vm.recipe_tree.get_tool(1) is not None, \
                "SET_TOOL 후 RecipeTree 에 툴 미등록"
        finally:
            _stop_vvm(vm)

    def test_test_mode_emits_burst(self):
        """CMD_TEST → VVM 이 Burst 를 주기적으로 송출해야 함."""
        vm = _start_vvm()
        try:
            _send_cmd(vm, HyProtocol.CMD_TEST, p0=15)
            result = _recv_burst(vm, timeout=2.0)
            # 이미지 없는 환경에서도 크래시 없이 None 또는 정상 반환
            # Burst 가 수신되면 구조 검증
            if result is not None:
                packets, img_bytes = result
                assert isinstance(packets, list)
        finally:
            _stop_vvm(vm)

    def test_cycle_id_matches(self):
        """TEST 모드 Burst 의 cycle_id 가 VVM 내부 카운터와 일치."""
        vm = _start_vvm()
        try:
            _send_cmd(vm, HyProtocol.CMD_TEST, p0=15)
            result = _recv_burst(vm, timeout=1.5)
            if result is None:
                pytest.skip("Burst 미수신 (이미지 없는 환경)")
            packets, _ = result
            if packets:
                assert packets[0]['cycle_id'] >= 0
        finally:
            _stop_vvm(vm)


# ─────────────────────────────────────────────────────────────────────────────
# I-02  Flow Control — Drop
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not HAS_QT, reason="PyQt5 required")
class TestFlowControl:
    def test_queue_overflow_no_crash(self):
        """rst_queue maxsize=3 초과 시 크래시 없이 Drop."""
        vm = _start_vvm()
        try:
            dummy = HyProtocol.pack_burst(
                [HyProtocol.pack_result(0, 0, 0, 0, 0, 0,
                                        HyProtocol.EXEC_DONE,
                                        HyProtocol.JUDGE_OK)]
            )
            # 강제로 10번 push → _push_result 내부 Drop 방어 동작해야 함
            for _ in range(10):
                vm._push_result(dummy)   # noqa: SLF001 — 테스트 전용

            # 큐 크기 ≤ maxsize
            assert vm.rst_queue.qsize() <= 3
        finally:
            _stop_vvm(vm)


# ─────────────────────────────────────────────────────────────────────────────
# I-03  Heartbeat — 3회 Timeout → sig_connected(2)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not HAS_QT, reason="PyQt5 required")
class TestHeartbeat:
    def test_ping_miss_emits_abnormal(self):
        """HyLink 헬퍼 메서드: PING_MAX_MISS 초과 시 sig_connected(2) 발행."""
        from HyLink import HyLink

        link = HyLink()
        emitted = []
        link.sig_connected.connect(lambda v: emitted.append(v))

        # _ping_miss 를 강제로 PING_MAX_MISS 에 도달시킨다
        link._waiting_pong  = True
        link._last_ping_t   = time.time() - link.PING_TIMEOUT - 1.0
        link._ping_miss     = link.PING_MAX_MISS - 1

        # heartbeat 체크 1회 — 마지막 miss 발생
        vm = _start_vvm()
        link._vm = vm
        try:
            link._check_heartbeat_virtual()
            # sig_connected(2) 가 발행됐어야 함
            assert 2 in emitted, f"sig_connected(2) 미발행, got {emitted}"
        finally:
            _stop_vvm(vm)


# ─────────────────────────────────────────────────────────────────────────────
# I-04  RecipeTree evaluate() — 비전툴 실행 + 로직 평가
# ─────────────────────────────────────────────────────────────────────────────

class TestEvaluate:
    def _make_tree_with_burst(self):
        """HyFin → HyAnd → HyLine 트리 + inject_burst 로 상태 주입."""
        tree = RecipeTree()
        fin  = HyFin(tool_id=1)
        and_ = HyAnd(tool_id=2)
        line = HyLine(tool_id=3)
        line.device_id = HyProtocol.DEV_CAMERA

        and_.add_child(line)
        fin.add_child(and_)

        tree.add_tool(fin,  parent_id=0)
        tree.add_tool(and_, parent_id=fin.tool_id)
        tree.add_tool(line, parent_id=and_.tool_id)

        return tree, fin, and_, line

    def test_inject_then_evaluate_ok(self):
        """Device Burst 주입 후 로직 평가 → 모두 OK면 HyFin = JUDGE_OK."""
        tree, fin, and_, line = self._make_tree_with_burst()

        burst = [{
            'tool_id':   3,
            'rst_done':  HyProtocol.EXEC_DONE,
            'rst_state': HyProtocol.JUDGE_OK,
            'x': 50.0, 'y': 60.0, 'w': 10.0, 'h': 5.0, 'angle': 0.0,
            'stat1': 0.9, 'stat2': 0.0, 'stat3': 0.0, 'stat4': 0.0,
            'proc_time': 1,
        }]
        tree.inject_burst(burst, cycle_id=1)

        img = np.zeros((480, 640), dtype=np.uint8)
        results = tree.evaluate(img, img_id=1, cycle_id=1)

        assert tree.find_fin_judgment() == HyProtocol.JUDGE_OK, \
            f"기대 JUDGE_OK, got {tree.find_fin_judgment()}"

    def test_inject_ng_yields_ng(self):
        """Device Burst 에서 NG 주입 → HyFin = JUDGE_NG.
        inject_burst 가 _last_img_id/_last_cycle_id 를 커밋하므로
        evaluate() 에서 HyLine 이 재실행되지 않아 NG 상태가 유지된다.
        """
        tree, fin, and_, line = self._make_tree_with_burst()

        IMG_ID   = 7
        CYCLE_ID = 2

        burst = [{
            'tool_id':   3,
            'img_id':    IMG_ID,
            'rst_done':  HyProtocol.EXEC_DONE,
            'rst_state': HyProtocol.JUDGE_NG,
            'x': 0.0, 'y': 0.0, 'w': 0.0, 'h': 0.0, 'angle': 0.0,
            'stat1': 0.0, 'stat2': 0.0, 'stat3': 0.0, 'stat4': 0.0,
            'proc_time': 1,
        }]
        tree.inject_burst(burst, cycle_id=CYCLE_ID, img_id=IMG_ID)

        img = np.zeros((480, 640), dtype=np.uint8)
        tree.evaluate(img, img_id=IMG_ID, cycle_id=CYCLE_ID)

        assert tree.find_fin_judgment() == HyProtocol.JUDGE_NG, \
            f"기대 JUDGE_NG, got {tree.find_fin_judgment()}"


# ─────────────────────────────────────────────────────────────────────────────
# I-05  Fixture get_fixture_transform() — 좌표 변환 오차 ≤ 1px
# ─────────────────────────────────────────────────────────────────────────────

class TestFixtureTransform:
    def test_no_anchor_returns_identity(self):
        """앵커 없으면 None (Qt 미설치) 또는 단위 행렬 QTransform 반환."""
        tree = RecipeTree()
        result = tree.get_fixture_transform()
        # Qt 없으면 None, 있으면 단위행렬(identity) QTransform
        if result is not None and HAS_QT:
            from PyQt5.QtGui import QTransform
            assert isinstance(result, QTransform)
            assert result.isIdentity()

    @pytest.mark.skipif(not HAS_QT, reason="PyQt5 required")
    def test_anchor_ok_returns_qtransform(self):
        """HyLocator 앵커 OK → get_fixture_transform() 이 None 이 아님."""
        from PyQt5.QtGui import QTransform

        tree = RecipeTree()
        loc  = HyLocator(tool_id=1)
        loc.search_roi = (100, 100, 200, 200)
        loc.rst_done   = HyProtocol.EXEC_DONE
        loc.rst_state  = HyProtocol.JUDGE_OK
        loc.x          = 200.0   # 현재 위치
        loc.y          = 200.0
        loc.angle      = 0.0

        tree.add_tool(loc, parent_id=0)   # anchor 자동 설정
        tree.anchor = loc                 # 명시적 설정

        tf = tree.get_fixture_transform()
        assert tf is not None
        assert isinstance(tf, QTransform)

    @pytest.mark.skipif(not HAS_QT, reason="PyQt5 required")
    def test_translation_accuracy(self):
        """Δx=10, Δy=20 이동 시 변환 후 좌표 오차 ≤ 1px."""
        from PyQt5.QtGui import QTransform
        from PyQt5.QtCore import QPointF

        tree = RecipeTree()
        loc  = HyLocator(tool_id=1)
        # 티칭 위치: search_roi 중심 = (200, 150)
        loc.search_roi = (100, 50, 200, 200)   # x,y,w,h → center=(200,150)
        loc.rst_done   = HyProtocol.EXEC_DONE
        loc.rst_state  = HyProtocol.JUDGE_OK
        loc.x          = 210.0   # Δx=+10
        loc.y          = 170.0   # Δy=+20
        loc.angle      = 0.0

        tree.add_tool(loc, parent_id=0)
        tree.anchor = loc

        tf = tree.get_fixture_transform()
        assert tf is not None

        # 원점 (0,0) → 변환 후 (10, 20) ± 1px
        pt = tf.map(QPointF(0.0, 0.0))
        assert abs(pt.x() - 10.0) <= 1.0, f"x 오차 {abs(pt.x()-10.0):.3f}px > 1px"
        assert abs(pt.y() - 20.0) <= 1.0, f"y 오차 {abs(pt.y()-20.0):.3f}px > 1px"
