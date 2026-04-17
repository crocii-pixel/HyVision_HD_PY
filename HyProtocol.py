"""
HyProtocol.py - HyVision Pro-Inspector 통신 프로토콜 (v3.0)
PC ↔ Device 간 32B Command + 64B Result 고정 길이 이진 구조체.
PC(CPython) / Device(MicroPython) 양방향 호환.

v3.0 변경사항 (2026-04-16):
  - CMD_FORMAT: "<H B B 4i I 8x" → "<4H 4i f I"
  - target_type 헤더 승격 (구 param[1] 슬롯에서 분리)
  - fparam 신설 (rot_angle 기본 용도, float32)
  - cmd_id / target_id uint8 → uint16 확장
"""
try:
    import struct
except ImportError:
    import ustruct as struct


class HyProtocol:
    # ─────────────────────────────────────────────────────────────────────────
    # 포맷 상수 (v3.0)
    # ─────────────────────────────────────────────────────────────────────────
    # PC → Device : 32 Bytes  "<4H 4i f I"
    # sync(H) cmd_id(H) target_id(H) target_type(H) param[0..3](4i) fparam(f) payload_len(I)
    CMD_FORMAT = "<4H 4i f I"
    CMD_SIZE   = 32
    CMD_SYNC   = 0xBB66

    # Device → PC : 64 Bytes  "<H I I I H H H B B 5f 4f I 2x"
    RST_FORMAT = "<H I I I H H H B B 5f 4f I 2x"
    RST_SIZE   = 64
    RST_SYNC   = 0xAA55

    # ─────────────────────────────────────────────────────────────────────────
    # Command ID (cmd_id) - 시스템 상태 제어
    # ─────────────────────────────────────────────────────────────────────────
    CMD_STOP         = 0x00   # 모든 연산/송출 중지 → STANDBY
    CMD_LIVE         = 0x01   # 비전 연산 OFF, 원시 영상 지속 송출
    CMD_TEACH_SNAP   = 0x02   # 현재 프레임 1장 캡처 후 STANDBY
    CMD_TEST         = 0x03   # 비전 연산 ON + 결과(Burst) + 압축 영상 동시 송출
    CMD_RUN          = 0x04   # 비전 연산 ON + 결과(Burst)만 초고속 송출 (영상 OFF)
    # 레시피 DCOM 트리 프로비저닝
    CMD_SET_TOOL     = 0x10   # target_id 해당 비전 툴 노드 생성/수정
    CMD_SET_TEMPLATE = 0x11   # 패턴 매칭용 템플릿 이미지 세팅
    CMD_SET_CAMERA   = 0x12   # 카메라 센서 설정 (노출, 게인 등)
    CMD_SAVE_RECIPE  = 0x18   # 현재 RAM 상의 레시피를 Flash에 영구 저장
    CMD_CLEAR_TOOLS  = 0x19   # 장치 RAM에 등록된 모든 비전 툴 초기화
    # 진단/유틸리티
    CMD_PING         = 0xF0   # 연결 상태 확인 → 0xF1 PONG 응답
    CMD_RESET        = 0xFF   # 장치 하드웨어 소프트 리셋

    # ─────────────────────────────────────────────────────────────────────────
    # Tool Type 코드
    # ─────────────────────────────────────────────────────────────────────────
    # 물리 비전 툴 (Device=1 할당)
    TOOL_LINE         = 0x01
    TOOL_PATMAT       = 0x02
    TOOL_LOCATOR      = 0x03
    TOOL_INTERSECTION = 0x04
    TOOL_LINE_PATMAT  = 0x05
    # 정밀 측정 툴 (PC=2 할당)
    TOOL_DISTANCE     = 0x10
    TOOL_CONTRAST     = 0x11
    TOOL_FND          = 0x12
    # 로직 집행관 툴 (PC=2 할당)
    TOOL_WHEN         = 0x20
    TOOL_AND          = 0x21
    TOOL_OR           = 0x22
    TOOL_FIN          = 0x23

    # ─────────────────────────────────────────────────────────────────────────
    # rst_done — 실행 완료 상태 (Tri-State)
    # ─────────────────────────────────────────────────────────────────────────
    EXEC_IDLE    = 0   # 미실행 / 단축 평가에 의한 스킵
    EXEC_DONE    = 1   # 정상 완료
    EXEC_ERROR   = 2   # 실행 중 에러 발생
    EXEC_PENDING = 3   # 진행 중 / 대기 (HyWhen 타이머 가동 중 등)

    # ─────────────────────────────────────────────────────────────────────────
    # rst_state — 논리 판정 결과 (Tri-State)
    # ─────────────────────────────────────────────────────────────────────────
    JUDGE_NG      = 0   # NG / False / 불합격
    JUDGE_OK      = 1   # OK / True  / 합격
    JUDGE_PENDING = 2   # 미확정 (HyWhen 타이머 미완료 등)

    # ─────────────────────────────────────────────────────────────────────────
    # Device ID
    # ─────────────────────────────────────────────────────────────────────────
    DEV_CAMERA = 1
    DEV_PC     = 2

    # ─────────────────────────────────────────────────────────────────────────
    # Tool Type → 이름 매핑 (UI용)
    # ─────────────────────────────────────────────────────────────────────────
    TOOL_NAMES = {
        0x01: "HyLine",
        0x02: "HyPatMat",
        0x03: "HyLocator",
        0x04: "HyIntersection",
        0x05: "HyLinePatMat",
        0x10: "HyDistance",
        0x11: "HyContrast",
        0x12: "HyFND",
        0x20: "HyWhen",
        0x21: "HyAnd",
        0x22: "HyOr",
        0x23: "HyFin",
    }

    # ─────────────────────────────────────────────────────────────────────────
    # SET_TOOL param 인코딩 헬퍼
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def encode_tree_info(device_id: int, parent_tool_id: int) -> int:
        """param[0]: 상위16bit=device_id, 하위16bit=parent_tool_id"""
        return ((device_id & 0xFFFF) << 16) | (parent_tool_id & 0xFFFF)

    @staticmethod
    def decode_tree_info(param0: int):
        """→ (device_id, parent_tool_id)"""
        return (param0 >> 16) & 0xFFFF, param0 & 0xFFFF

    @staticmethod
    def encode_xy(x: int, y: int) -> int:
        """param[2]: 상위16bit=x, 하위16bit=y"""
        return ((int(x) & 0xFFFF) << 16) | (int(y) & 0xFFFF)

    @staticmethod
    def encode_wh(w: int, h: int) -> int:
        """param[3]: 상위16bit=w, 하위16bit=h"""
        return ((int(w) & 0xFFFF) << 16) | (int(h) & 0xFFFF)

    # ─────────────────────────────────────────────────────────────────────────
    # 32B Command Pack / Unpack  (v3.0: <4H 4i f I)
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def pack_command(cmd_id: int, target_id: int = 0, target_type: int = 0,
                     p0: int = 0, p1: int = 0, p2: int = 0, p3: int = 0,
                     fparam: float = 0.0, payload_len: int = 0) -> bytes:
        """32B Command Struct 패킹 (v3.0).
        sync(H) cmd_id(H) target_id(H) target_type(H)
        param[0..3](4i) fparam(f) payload_len(I)
        """
        return struct.pack(
            HyProtocol.CMD_FORMAT,
            HyProtocol.CMD_SYNC,
            int(cmd_id)      & 0xFFFF,
            int(target_id)   & 0xFFFF,
            int(target_type) & 0xFFFF,
            int(p0), int(p1), int(p2), int(p3),
            float(fparam),
            int(payload_len),
        )

    @staticmethod
    def unpack_command(data: bytes):
        """32B Command Struct 언패킹 → dict | None (v3.0)"""
        if len(data) < HyProtocol.CMD_SIZE:
            return None
        u = struct.unpack(HyProtocol.CMD_FORMAT, data[:HyProtocol.CMD_SIZE])
        if u[0] != HyProtocol.CMD_SYNC:
            return None
        # u: sync, cmd_id, target_id, target_type, p0, p1, p2, p3, fparam, payload_len
        return {
            'sync':        u[0],
            'cmd_id':      u[1],
            'target_id':   u[2],
            'target_type': u[3],
            'params':      [u[4], u[5], u[6], u[7]],
            'fparam':      u[8],
            'payload_len': u[9],
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 64B Result Pack / Unpack
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def pack_result(tx_id, cycle_id, img_id,
                    seq_id, tool_id, tool_type,
                    rst_done, rst_state,
                    x=0.0, y=0.0, w=0.0, h=0.0, angle=0.0,
                    stat1=0.0, stat2=0.0, stat3=0.0, stat4=0.0,
                    proc_time=0) -> bytes:
        """64B Result Struct 패킹."""
        return struct.pack(
            HyProtocol.RST_FORMAT,
            HyProtocol.RST_SYNC,
            int(tx_id), int(cycle_id), int(img_id),
            int(seq_id), int(tool_id), int(tool_type),
            int(rst_done), int(rst_state),
            float(x), float(y), float(w), float(h), float(angle),
            float(stat1), float(stat2), float(stat3), float(stat4),
            int(proc_time)
        )

    @staticmethod
    def unpack_result(data: bytes):
        """64B Result Struct 언패킹 → dict | None
        P1-04: 입력이 RST_SIZE보다 길 경우 1바이트씩 슬라이딩해
        RST_SYNC(0xAA55)가 나타나는 첫 위치에서 파싱 시도.
        """
        sz = HyProtocol.RST_SIZE
        if len(data) < sz:
            return None
        # 슬라이딩 스캔: 최대 len(data)-sz+1 회 시도
        sync_bytes = struct.pack('<H', HyProtocol.RST_SYNC)
        end = len(data) - sz + 1
        for i in range(end):
            if data[i:i+2] != sync_bytes:
                continue
            try:
                u = struct.unpack(HyProtocol.RST_FORMAT, data[i:i+sz])
            except Exception:
                continue
            if u[0] != HyProtocol.RST_SYNC:
                continue
            return {
                'sync':      u[0],
                'tx_id':     u[1],
                'cycle_id':  u[2],
                'img_id':    u[3],
                'seq_id':    u[4],
                'tool_id':   u[5],
                'tool_type': u[6],
                'rst_done':  u[7],
                'rst_state': u[8],
                'x': u[9],  'y': u[10], 'w': u[11], 'h': u[12], 'angle': u[13],
                'stat1': u[14], 'stat2': u[15], 'stat3': u[16], 'stat4': u[17],
                'proc_time': u[18],
            }
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # Burst 스트림 Pack / Unpack
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def pack_burst(result_packets: list, image_bytes: bytes = b'') -> bytes:
        """
        [0xAA55(2B)] + [패킷수 N(2B)] + [64B×N] + [이미지크기 M(4B)] + [M바이트 JPEG]
        result_packets: list of bytes (각 64B)
        """
        n = len(result_packets)
        header = struct.pack('<HH', HyProtocol.RST_SYNC, n)
        body   = b''.join(result_packets)
        img_hdr = struct.pack('<I', len(image_bytes))
        return header + body + img_hdr + image_bytes

    # P1-05 OOM 방어 상한
    MAX_BURST_PACKETS = 128          # 한 Burst 내 최대 Result 패킷 수
    MAX_IMAGE_BYTES   = 2 * 1024 * 1024  # 2 MiB

    @staticmethod
    def unpack_burst(data: bytes):
        """bytes → (list[dict], image_bytes) | None
        P1-05: result 패킷 수 > MAX_BURST_PACKETS 이거나
               image_size > MAX_IMAGE_BYTES 이면 버퍼 폐기(None 반환).
        """
        if len(data) < 4:
            return None
        sync, n = struct.unpack_from('<HH', data, 0)
        if sync != HyProtocol.RST_SYNC:
            return None
        # OOM 방어: 이상적으로 큰 패킷 수 차단
        if n > HyProtocol.MAX_BURST_PACKETS:
            return None
        offset  = 4
        results = []
        for _ in range(n):
            end = offset + HyProtocol.RST_SIZE
            if end > len(data):
                break
            parsed = HyProtocol.unpack_result(data[offset:end])
            if parsed:
                results.append(parsed)
            offset = end
        if offset + 4 > len(data):
            return results, b''
        img_size = struct.unpack_from('<I', data, offset)[0]
        # OOM 방어: 이미지 크기 상한
        if img_size > HyProtocol.MAX_IMAGE_BYTES:
            return None
        offset  += 4
        image_bytes = data[offset:offset + img_size]
        return results, image_bytes

    # ─────────────────────────────────────────────────────────────────────────
    # PONG 패킷 생성 헬퍼
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def make_pong(tx_id: int = 0, cycle_id: int = 0) -> bytes:
        """PING 에 대한 PONG 응답 (tool_id=0, rst_done=EXEC_DONE)"""
        return HyProtocol.pack_result(
            tx_id, cycle_id, 0,
            0, 0, 0,
            HyProtocol.EXEC_DONE, HyProtocol.JUDGE_OK
        )
