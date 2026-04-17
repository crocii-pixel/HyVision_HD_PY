"""
U-20  VisionTaskRunner.rebuild_from_commands — SET_TOOL 파싱 + seq_id 정렬
U-21  VisionTaskRunner.run_cycle — MY_DEVICE 툴 실행 + 바톤 터치
"""
import sys
import os
import struct

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'firmware'))

import pytest
from firmware.FirmwareMain import (
    VisionTaskRunner,
    CMD_SYNC, CMD_FORMAT,
    CMD_SET_TOOL, DEV_CAMERA, DEV_PC,
    EXEC_DONE, EXEC_ERROR,
    JUDGE_OK, JUDGE_NG,
    RST_SYNC,
)


# ─────────────────────────────────────────────────────────────────────────────
# 헬퍼 — SET_TOOL 32B 패킷 생성
# ─────────────────────────────────────────────────────────────────────────────

def _make_set_tool(tool_id, tool_type, device_id, seq_id,
                   roi=(0, 0, 100, 100), rot_angle=0.0, parent_id=0):
    rx, ry, rw, rh = roi
    p0 = ((device_id & 0xFFFF) << 16) | (parent_id & 0xFFFF)
    p1 = seq_id & 0xFFFF_FFFF
    p2 = ((rx & 0xFFFF) << 16) | (ry & 0xFFFF)
    p3 = ((rw & 0xFFFF) << 16) | (rh & 0xFFFF)
    return struct.pack(CMD_FORMAT,
                       CMD_SYNC, CMD_SET_TOOL, tool_id, tool_type,
                       p0, p1, p2, p3,
                       float(rot_angle), 0)


# ─────────────────────────────────────────────────────────────────────────────
# U-20  rebuild_from_commands
# ─────────────────────────────────────────────────────────────────────────────

class TestRebuildFromCommands:
    def test_empty_input_gives_empty_specs(self):
        r = VisionTaskRunner()
        r.rebuild_from_commands([])
        assert r.tool_count == 0

    def test_single_camera_tool_parsed(self):
        r = VisionTaskRunner()
        pkt = _make_set_tool(tool_id=1, tool_type=0x01,
                             device_id=DEV_CAMERA, seq_id=0)
        r.rebuild_from_commands([pkt])
        assert r.tool_count == 1
        spec = r._tool_specs[0]
        assert spec['tool_id']   == 1
        assert spec['tool_type'] == 0x01
        assert spec['device_id'] == DEV_CAMERA
        assert spec['seq_id']    == 0

    def test_seq_id_sorted_ascending(self):
        """seq_id 가 무작위 순서로 들어와도 오름차순 정렬되어야 함."""
        r = VisionTaskRunner()
        cmds = [
            _make_set_tool(3, 0x01, DEV_CAMERA, seq_id=2),
            _make_set_tool(1, 0x01, DEV_CAMERA, seq_id=0),
            _make_set_tool(2, 0x01, DEV_CAMERA, seq_id=1),
        ]
        r.rebuild_from_commands(cmds)
        assert r.tool_count == 3
        ids = [s['seq_id'] for s in r._tool_specs]
        assert ids == sorted(ids)

    def test_roi_decoded_correctly(self):
        r = VisionTaskRunner()
        pkt = _make_set_tool(1, 0x01, DEV_CAMERA, seq_id=0,
                             roi=(10, 20, 30, 40))
        r.rebuild_from_commands([pkt])
        spec = r._tool_specs[0]
        assert spec['roi'] == (10, 20, 30, 40)

    def test_fparam_rot_angle_decoded(self):
        r = VisionTaskRunner()
        pkt = _make_set_tool(1, 0x01, DEV_CAMERA, seq_id=0, rot_angle=45.0)
        r.rebuild_from_commands([pkt])
        assert abs(r._tool_specs[0]['rot_angle'] - 45.0) < 0.01

    def test_invalid_sync_packet_skipped(self):
        r = VisionTaskRunner()
        bad = b'\x00' * 32   # sync != CMD_SYNC
        pkt = _make_set_tool(1, 0x01, DEV_CAMERA, seq_id=0)
        r.rebuild_from_commands([bad, pkt])
        assert r.tool_count == 1   # bad 패킷 무시, 정상 1개

    def test_short_packet_skipped(self):
        r = VisionTaskRunner()
        pkt = _make_set_tool(1, 0x01, DEV_CAMERA, seq_id=0)
        r.rebuild_from_commands([pkt[:10]])  # 잘린 패킷
        assert r.tool_count == 0

    def test_mixed_device_ids_all_parsed(self):
        """Camera 툴과 PC 툴이 섞여도 모두 파싱돼야 함."""
        r = VisionTaskRunner()
        cmds = [
            _make_set_tool(1, 0x01, DEV_CAMERA, seq_id=0),
            _make_set_tool(2, 0x10, DEV_PC,     seq_id=1),
        ]
        r.rebuild_from_commands(cmds)
        assert r.tool_count == 2
        assert r.my_tool_count == 1

    def test_rebuild_resets_results(self):
        """rebuild 후 _results 는 빈 dict 이어야 함."""
        r = VisionTaskRunner()
        r._results[99] = b'\x00' * 64
        r.rebuild_from_commands([])
        assert len(r._results) == 0


# ─────────────────────────────────────────────────────────────────────────────
# U-21  run_cycle
# ─────────────────────────────────────────────────────────────────────────────

class TestRunCycle:
    """run_cycle 의 실행 흐름과 바톤 터치 로직 검증."""

    def _make_runner_with_tools(self, specs_def):
        """specs_def: list of (tool_id, tool_type, device_id, seq_id)"""
        r = VisionTaskRunner()
        cmds = [_make_set_tool(tid, ttype, dev, seq)
                for tid, ttype, dev, seq in specs_def]
        r.rebuild_from_commands(cmds)
        return r

    def test_all_camera_tools_no_baton(self):
        """PC 툴이 없으면 has_more == False."""
        r = self._make_runner_with_tools([
            (1, 0x01, DEV_CAMERA, 0),
            (2, 0x01, DEV_CAMERA, 1),
        ])
        r.register_executor(0x01, lambda spec, img: {'rst_state': JUDGE_OK})

        captured = []
        def _collect(pkts, img, has_more):
            captured.append((len(pkts), has_more))

        has_more = r.run_cycle(None, cycle_id=1, img_id=1,
                               send_burst_fn=_collect, keep_img=False)
        assert not has_more
        assert captured[0][0] == 2          # 2개 패킷 생성
        assert captured[0][1] is False

    def test_pc_tool_triggers_baton(self):
        """PC 툴이 뒤에 있으면 has_more == True 이고 my 패킷만 들어가야 함."""
        r = self._make_runner_with_tools([
            (1, 0x01, DEV_CAMERA, 0),
            (2, 0x10, DEV_PC,     1),   # PC 툴 — 실행 안 됨
        ])
        r.register_executor(0x01, lambda spec, img: {'rst_state': JUDGE_OK})

        captured = []
        def _collect(pkts, img, has_more):
            captured.append((len(pkts), has_more))

        has_more = r.run_cycle(None, cycle_id=1, img_id=1,
                               send_burst_fn=_collect, keep_img=False)
        assert has_more is True
        assert captured[0][0] == 1          # camera 툴 1개만 실행
        assert captured[0][1] is True

    def test_only_pc_tools_zero_packets(self):
        """Camera 툴이 없고 PC 툴만 있으면 패킷 0개 + has_more=True."""
        r = self._make_runner_with_tools([
            (1, 0x10, DEV_PC, 0),
        ])

        captured = []
        def _collect(pkts, img, has_more):
            captured.append((len(pkts), has_more))

        has_more = r.run_cycle(None, cycle_id=1, img_id=1,
                               send_burst_fn=_collect, keep_img=False)
        assert has_more is True
        assert captured[0][0] == 0

    def test_executor_not_registered_gives_exec_error(self):
        """executor 미등록 툴은 EXEC_ERROR 패킷 생성 후 계속."""
        r = self._make_runner_with_tools([(1, 0x01, DEV_CAMERA, 0)])
        # executor 미등록

        captured_pkts = []
        def _collect(pkts, img, has_more):
            captured_pkts.extend(pkts)

        r.run_cycle(None, cycle_id=1, img_id=1,
                    send_burst_fn=_collect, keep_img=False)

        assert len(captured_pkts) == 1
        # RST 패킷에서 rst_done(B) 위치 확인 — offset=17 (H+3I+3H=2+12+6=20 → +B+B=22→17 after checking)
        # RST_FORMAT = "<HIIIHHH BB 5f 4f I 2x"
        # H=2, I*3=12, H*3=6 → offset 20, then B B → rst_done at 20, rst_state at 21
        pkt = captured_pkts[0]
        import struct as _st
        rst_done = _st.unpack_from('<B', pkt, 20)[0]
        assert rst_done == EXEC_ERROR

    def test_results_stored_per_tool_id(self):
        """run_cycle 후 get_result(tool_id) 가 올바른 패킷을 반환."""
        r = self._make_runner_with_tools([(5, 0x01, DEV_CAMERA, 0)])
        r.register_executor(0x01, lambda spec, img: {'rst_state': JUDGE_OK, 'x': 12.0})

        r.run_cycle(None, cycle_id=1, img_id=1,
                    send_burst_fn=lambda *a: None, keep_img=False)

        pkt = r.get_result(5)
        assert pkt is not None and len(pkt) == 64
        # sync 필드 확인
        import struct as _st
        sync = _st.unpack_from('<H', pkt, 0)[0]
        assert sync == RST_SYNC

    def test_send_burst_always_called(self):
        """툴이 0개여도 send_burst_fn 은 반드시 1회 호출."""
        r = VisionTaskRunner()
        r.rebuild_from_commands([])

        call_count = [0]
        def _collect(pkts, img, has_more):
            call_count[0] += 1

        r.run_cycle(None, 1, 1, _collect, keep_img=False)
        assert call_count[0] == 1
