"""
U-01  HyProtocol 32B Command 라운드트립
U-02  HyProtocol 64B Result 라운드트립
U-03  Sync 헤더 슬라이딩 스캔 복구
U-04  OOM 방어: payload_len / img_size 상한
"""
import struct
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pytest
from HyProtocol import HyProtocol


# ──────────────────────────────────────────────────────────────────────────────
# U-01  32B Command 라운드트립
# ──────────────────────────────────────────────────────────────────────────────
class TestPackCommand:
    def test_roundtrip_defaults(self):
        raw = HyProtocol.pack_command(HyProtocol.CMD_SET_TOOL)
        assert len(raw) == HyProtocol.CMD_SIZE
        d = HyProtocol.unpack_command(raw)
        assert d is not None
        assert d['cmd_id']      == HyProtocol.CMD_SET_TOOL
        assert d['target_id']   == 0
        assert d['target_type'] == 0
        assert d['params']      == [0, 0, 0, 0]
        assert abs(d['fparam'] - 0.0) < 1e-6
        assert d['payload_len'] == 0

    def test_roundtrip_all_fields(self):
        raw = HyProtocol.pack_command(
            cmd_id=0x10, target_id=7, target_type=2,
            p0=100, p1=-200, p2=300, p3=-400,
            fparam=45.5, payload_len=128,
        )
        d = HyProtocol.unpack_command(raw)
        assert d['cmd_id']      == 0x10
        assert d['target_id']   == 7
        assert d['target_type'] == 2
        assert d['params']      == [100, -200, 300, -400]
        assert abs(d['fparam'] - 45.5) < 1e-4
        assert d['payload_len'] == 128

    def test_sync_mismatch_returns_none(self):
        raw = bytearray(HyProtocol.CMD_SIZE)  # all zeros → sync 불일치
        assert HyProtocol.unpack_command(bytes(raw)) is None

    def test_too_short_returns_none(self):
        assert HyProtocol.unpack_command(b'\xBB\x66' * 5) is None


# ──────────────────────────────────────────────────────────────────────────────
# U-02  64B Result 라운드트립
# ──────────────────────────────────────────────────────────────────────────────
class TestPackResult:
    def test_roundtrip(self):
        raw = HyProtocol.pack_result(
            tx_id=1, cycle_id=42, img_id=99,
            seq_id=3, tool_id=5, tool_type=HyProtocol.TOOL_LINE,
            rst_done=HyProtocol.EXEC_DONE, rst_state=HyProtocol.JUDGE_OK,
            x=10.5, y=20.3, w=50.0, h=30.0, angle=1.5,
            stat1=0.98, stat2=0.0, stat3=0.0, stat4=0.0,
            proc_time=12345,
        )
        assert len(raw) == HyProtocol.RST_SIZE
        d = HyProtocol.unpack_result(raw)
        assert d is not None
        assert d['tx_id']     == 1
        assert d['cycle_id']  == 42
        assert d['tool_id']   == 5
        assert d['rst_done']  == HyProtocol.EXEC_DONE
        assert d['rst_state'] == HyProtocol.JUDGE_OK
        assert abs(d['x']     - 10.5) < 1e-3
        assert abs(d['angle'] - 1.5)  < 1e-3
        assert abs(d['stat1'] - 0.98) < 1e-4
        assert d['proc_time'] == 12345

    def test_all_tri_state_combinations(self):
        for rst_done in (HyProtocol.EXEC_IDLE, HyProtocol.EXEC_DONE,
                         HyProtocol.EXEC_ERROR, HyProtocol.EXEC_PENDING):
            for rst_state in (HyProtocol.JUDGE_NG, HyProtocol.JUDGE_OK,
                              HyProtocol.JUDGE_PENDING):
                raw = HyProtocol.pack_result(0, 0, 0, 0, 0, 0,
                                             rst_done, rst_state)
                d = HyProtocol.unpack_result(raw)
                assert d['rst_done']  == rst_done
                assert d['rst_state'] == rst_state


# ──────────────────────────────────────────────────────────────────────────────
# U-03  Sync 헤더 슬라이딩 스캔 복구
# ──────────────────────────────────────────────────────────────────────────────
class TestSlidingScan:
    def test_junk_prefix_10_bytes(self):
        """쓰레기 10바이트 뒤에 유효한 64B 패킷이 있어야 정상 복구."""
        valid = HyProtocol.pack_result(1, 2, 3, 4, 5, 1,
                                       HyProtocol.EXEC_DONE,
                                       HyProtocol.JUDGE_OK,
                                       x=7.7)
        junk  = bytes([0x00, 0x11, 0x22, 0x33, 0x44, 0x55,
                       0x66, 0x77, 0x88, 0x99])
        d = HyProtocol.unpack_result(junk + valid)
        assert d is not None
        assert abs(d['x'] - 7.7) < 1e-3

    def test_exact_size_no_junk(self):
        """정확히 64B 입력 → 슬라이딩 없이 바로 파싱."""
        valid = HyProtocol.pack_result(0, 0, 0, 0, 1, 2,
                                       HyProtocol.EXEC_DONE,
                                       HyProtocol.JUDGE_OK)
        d = HyProtocol.unpack_result(valid)
        assert d is not None
        assert d['tool_id'] == 1

    def test_all_junk_returns_none(self):
        """유효한 sync 없으면 None."""
        junk = bytes(100)
        assert HyProtocol.unpack_result(junk) is None


# ──────────────────────────────────────────────────────────────────────────────
# U-04  OOM 방어
# ──────────────────────────────────────────────────────────────────────────────
class TestOomDefense:
    def _make_burst(self, n_packets: int, img_size: int) -> bytes:
        packets = [
            HyProtocol.pack_result(0, 0, 0, i, i, 1,
                                   HyProtocol.EXEC_DONE,
                                   HyProtocol.JUDGE_OK)
            for i in range(n_packets)
        ]
        header   = struct.pack('<HH', HyProtocol.RST_SYNC, n_packets)
        body     = b''.join(packets)
        img_hdr  = struct.pack('<I', img_size)
        img_data = bytes(img_size)
        return header + body + img_hdr + img_data

    def test_packet_count_over_limit_returns_none(self):
        burst = self._make_burst(HyProtocol.MAX_BURST_PACKETS + 1, 0)
        assert HyProtocol.unpack_burst(burst) is None

    def test_packet_count_at_limit_ok(self):
        burst = self._make_burst(HyProtocol.MAX_BURST_PACKETS, 0)
        result = HyProtocol.unpack_burst(burst)
        assert result is not None

    def test_image_size_over_limit_returns_none(self):
        # 유효 패킷 1개 + 이미지 크기 초과
        packet  = HyProtocol.pack_result(0, 0, 0, 0, 1, 1,
                                         HyProtocol.EXEC_DONE,
                                         HyProtocol.JUDGE_OK)
        header  = struct.pack('<HH', HyProtocol.RST_SYNC, 1)
        img_hdr = struct.pack('<I', HyProtocol.MAX_IMAGE_BYTES + 1)
        burst   = header + packet + img_hdr
        assert HyProtocol.unpack_burst(burst) is None

    def test_normal_burst_ok(self):
        burst = self._make_burst(3, 0)
        result = HyProtocol.unpack_burst(burst)
        assert result is not None
        packets, img = result
        assert len(packets) == 3
        assert img == b''
