# PC(CPython)와 장치(MicroPython) 양방향 호환을 위한 통합 프로토콜 클래스
try:
    import struct
except ImportError:
    import ustruct as struct

class HyProtocol:
    """
    [64 Bytes 고정 길이 프로토콜 명세]
    - Header (2)   : 0xAA55 (동기화 헤더)
    - txID (4)     : 통신 트랜잭션 ID (uint32)
    - cycleID (4)  : 부품 생명주기 ID (uint32)
    - imgID (4)    : 프레임 캡처 ID (uint32)
    - seqID (2)    : 레시피 실행 순서 ID (uint16)
    - tool_id (2)  : 비전 툴 고유 ID (uint16)
    - tool_type (2): 툴 종류 (1:Line, 2:PatMat, 3:FND 등) (uint16)
    - rst_done (1) : 툴 실행 완료 여부 (0:미완료/에러, 1:완료) (uint8)
    - rst_state (1): 툴 논리 판정 결과 (0:False/Fail, 1:True/Pass) (uint8)
    - Geometry (20): x, y, w, h, angle (float32 * 5)
    - Stats (16)   : stat1, stat2, stat3, stat4 (float32 * 4) (다목적 union용)
    - proc_time(4) : 소요 시간 ms (uint32)
    - padding (2)  : 64바이트 정렬용 더미 바이트
    """
    
    # < : 리틀 엔디안
    # H(2) I(4) I(4) I(4) H(2) H(2) H(2) B(1) B(1) 5f(20) 4f(16) I(4) 2x(2) = 64 Bytes
    PACKET_FORMAT = "<H I I I H H H B B 5f 4f I 2x"
    PACKET_SIZE = 64
    SYNC_HEADER = 0xAA55

    @staticmethod
    def pack_result(tx_id, cycle_id, img_id, seq_id, tool_id, tool_type, 
                    rst_done, rst_state, 
                    x, y, w, h, angle, 
                    stat1, stat2, stat3, stat4, proc_time):
        
        return struct.pack(HyProtocol.PACKET_FORMAT,
                           HyProtocol.SYNC_HEADER,
                           tx_id, cycle_id, img_id,
                           seq_id, tool_id, tool_type,
                           int(rst_done), int(rst_state),
                           float(x), float(y), float(w), float(h), float(angle),
                           float(stat1), float(stat2), float(stat3), float(stat4),
                           int(proc_time))

    @staticmethod
    def unpack_result(byte_data):
        if len(byte_data) != HyProtocol.PACKET_SIZE:
            return None
        
        unpacked = struct.unpack(HyProtocol.PACKET_FORMAT, byte_data)
        
        if unpacked[0] != HyProtocol.SYNC_HEADER:
            return None
            
        return {
            'txID': unpacked[1],
            'cycleID': unpacked[2],
            'imgID': unpacked[3],
            'seqID': unpacked[4],
            'tool_id': unpacked[5],
            'tool_type': unpacked[6],
            'rst_done': bool(unpacked[7]),
            'rst_state': bool(unpacked[8]),
            'x': unpacked[9],
            'y': unpacked[10],
            'w': unpacked[11],
            'h': unpacked[12],
            'angle': unpacked[13],
            'stat1': unpacked[14],
            'stat2': unpacked[15],
            'stat3': unpacked[16],
            'stat4': unpacked[17],
            'proc_time': unpacked[18]
        }