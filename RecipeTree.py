"""
RecipeTree.py - DCOM 집행관 트리 및 레시피 관리 (v2.0)
단순 dict 목록 → 계층적 집행관 트리로 격상.
"""
import math
import json
import struct
import zlib
import numpy as np

try:
    from PyQt5.QtGui import QTransform
    HAS_QT = True
except ImportError:
    HAS_QT = False

from HyProtocol import HyProtocol
from HyVisionTools import (HyTool, HyLogicTool, HyLocator,
                            HyWhen, HyAnd, HyOr, HyFin,
                            create_tool, is_logic_tool, is_physical_tool)

# ─────────────────────────────────────────────────────────────────────────────
# .hyv 바이너리 포맷 상수 (P3-01/02)
# MAGIC(4) version(4) n_tools(4) anchor_id(4) next_id(4)
# + N × [tool_id(H) tool_type(H) parent_id(H) device_id(H) seq_id(I)
#          roi×4(4f) rot_angle(f) name_len(H) name(utf-8)
#          extra_len(H) extra_json(utf-8)] + crc32(4)
# ─────────────────────────────────────────────────────────────────────────────
_HYV_MAGIC    = b'HYV1'
_HYV_VERSION  = 1
_HYV_HDR_FMT  = '<4sIIII'      # magic version n_tools anchor_id next_id
_HYV_HDR_SIZE = struct.calcsize(_HYV_HDR_FMT)  # 20
_HYV_TOOL_FMT  = '<HHHHIfffffH' # ids seq_id roi rot_angle name_len
_HYV_TOOL_SIZE = struct.calcsize(_HYV_TOOL_FMT) # 34


def _tool_extra(tool: HyTool) -> dict:
    """툴 타입별 직렬화 추가 속성 반환 (JSON 호환 dict)."""
    d: dict = {}
    if isinstance(tool, HyLocator):
        d['update_policy'] = getattr(tool, 'update_policy', HyLocator.UPDATE_CYCLE_LOCK)
        ar = getattr(tool, 'allow_rect', None)
        d['allow_rect'] = list(ar) if ar is not None else None
        d['allow_angle_range'] = list(getattr(tool, 'allow_angle_range', (-180.0, 180.0)))
    elif isinstance(tool, HyWhen):
        d['watch_tool_id'] = int(getattr(tool, 'watch_tool_id', 0))
        d['timeout_ms']    = int(getattr(tool, 'timeout_ms', 0))
    elif isinstance(tool, HyFin):
        raw = getattr(tool, 'io_mapping', {})
        d['io_mapping']       = {str(k): v for k, v in raw.items()}
        d['broadcast_target'] = getattr(tool, 'broadcast_target', 'status_box')
    return d


def _apply_tool_extra(tool: HyTool, extra: dict) -> None:
    """저장된 dict를 툴 타입별 추가 속성으로 복원."""
    if isinstance(tool, HyLocator):
        tool.update_policy = extra.get('update_policy', HyLocator.UPDATE_CYCLE_LOCK)
        ar = extra.get('allow_rect')
        tool.allow_rect = tuple(ar) if ar is not None else None
        rng = extra.get('allow_angle_range', [-180.0, 180.0])
        tool.allow_angle_range = tuple(rng)
    elif isinstance(tool, HyWhen):
        tool.watch_tool_id = int(extra.get('watch_tool_id', 0))
        tool.timeout_ms    = int(extra.get('timeout_ms', 0))
    elif isinstance(tool, HyFin):
        raw = extra.get('io_mapping', {})
        # JSON은 정수 키를 문자열로 저장 — int 변환 시도
        tool.io_mapping = {}
        for k, v in raw.items():
            try:
                tool.io_mapping[int(k)] = v
            except (ValueError, TypeError):
                tool.io_mapping[k] = v
        tool.broadcast_target = extra.get('broadcast_target', 'status_box')


class RecipeTree:
    """
    DCOM 트리의 구축, 직렬화/역직렬화, State Injection, Fixture 변환을 총괄.
    모든 최상위 노드는 로직 툴(HyAnd/HyOr/HyFin)이어야 함.
    물리 비전 툴은 로직 툴의 자식으로만 배치 가능.
    """

    def __init__(self):
        self.root_nodes: list     = []           # 최상위 노드 (로직 툴만)
        self.tool_index: dict     = {}           # tool_id → HyTool
        self.anchor: HyLocator | None = None     # 단일 마스터 앵커 (시스템에 1개)
        self.current_cycle_id     = 0
        self._next_id             = 1            # 자동 증가 ID
        self._dirty               = False        # P3-04: 미저장 변경 플래그

    @property
    def dirty(self) -> bool:
        """P3-04: 마지막 저장/로드 이후 수정 여부."""
        return self._dirty

    # ─────────────────────────────────────────────────────────────────────────
    # ID 관리
    # ─────────────────────────────────────────────────────────────────────────

    def alloc_id(self) -> int:
        """미사용 ID 발급."""
        while self._next_id in self.tool_index:
            self._next_id += 1
        tid = self._next_id
        self._next_id += 1
        return tid

    # ─────────────────────────────────────────────────────────────────────────
    # CRUD
    # ─────────────────────────────────────────────────────────────────────────

    def add_tool(self, tool: HyTool, parent_id: int = 0) -> HyTool:
        """
        트리에 노드 추가.
        parent_id=0  → 최상위 (루트). 로직 툴만 루트 가능.
        parent_id!=0 → 해당 부모의 children 에 삽입.
        물리 비전 툴은 반드시 부모 로직 툴 내부에만 배치 가능.
        """
        if tool.tool_id in self.tool_index:
            raise ValueError(f"tool_id {tool.tool_id} already exists")

        # 물리 비전 툴은 루트 금지 (단, HyLocator 앵커는 루트 허용 — 시스템 1개)
        if is_physical_tool(tool) and parent_id == 0 and not isinstance(tool, HyLocator):
            raise ValueError("물리 비전 툴은 최상위(루트)에 배치할 수 없습니다. 로직 툴 내부에 넣어주세요.")

        tool.parent_id = parent_id

        if parent_id == 0:
            self.root_nodes.append(tool)
        else:
            parent = self.tool_index.get(parent_id)
            if parent is None:
                raise ValueError(f"부모 툴 {parent_id} 를 찾을 수 없습니다.")
            if not isinstance(parent, HyLogicTool):
                raise ValueError(f"부모 툴 {parent_id} 는 로직 툴이어야 합니다.")
            parent.add_child(tool)

        self.tool_index[tool.tool_id] = tool

        # HyLocator 등록 (시스템에 1개)
        if isinstance(tool, HyLocator):
            self.anchor = tool

        self._dirty = True
        return tool

    def remove_tool(self, tool_id: int) -> bool:
        """
        툴 제거. HyLocator 는 다른 툴 전부 제거 후에만 가능.
        자식이 있는 로직 툴 제거 시 자식도 함께 제거.
        """
        tool = self.tool_index.get(tool_id)
        if tool is None:
            return False

        # HyLocator 보호
        if isinstance(tool, HyLocator):
            non_anchor = [t for t in self.tool_index.values()
                          if not isinstance(t, HyLocator)]
            if non_anchor:
                raise ValueError("HyLocator 는 다른 모든 툴을 제거한 후 삭제하세요.")

        # 자식 재귀 제거
        if isinstance(tool, HyLogicTool):
            for child in list(tool.children):
                self.remove_tool(child.tool_id)

        # 부모에서 분리
        if tool.parent_id == 0:
            self.root_nodes = [t for t in self.root_nodes if t.tool_id != tool_id]
        else:
            parent = self.tool_index.get(tool.parent_id)
            if parent and isinstance(parent, HyLogicTool):
                parent.remove_child(tool_id)

        del self.tool_index[tool_id]
        if self.anchor and self.anchor.tool_id == tool_id:
            self.anchor = None
        self._dirty = True
        return True

    def add_ref(self, logic_id: int, vision_id: int) -> bool:
        """P2-04: 로직 툴 → 비전 툴 참조 추가.
        logic_id 로직 툴의 children 에 vision_id 툴을 삽입.
        이미 참조 중이면 True 반환 (멱등).
        """
        logic = self.tool_index.get(logic_id)
        vision = self.tool_index.get(vision_id)
        if logic is None or vision is None:
            return False
        if not isinstance(logic, HyLogicTool):
            raise ValueError(f"logic_id {logic_id} 는 로직 툴이어야 합니다.")
        # 중복 방지
        if any(c.tool_id == vision_id for c in logic.children):
            return True
        logic.add_child(vision)
        vision.parent_id = logic_id
        self.tool_index[vision_id] = vision
        self._dirty = True
        return True

    def remove_ref(self, logic_id: int, vision_id: int) -> bool:
        """P2-04: 로직 툴 → 비전 툴 참조 제거.
        children 에서만 분리하고 tool_index 에는 유지 (툴 삭제 아님).
        """
        logic = self.tool_index.get(logic_id)
        if logic is None or not isinstance(logic, HyLogicTool):
            return False
        if not any(c.tool_id == vision_id for c in logic.children):
            return False
        logic.remove_child(vision_id)
        # parent_id 초기화 (최상위로 부유)
        vision = self.tool_index.get(vision_id)
        if vision:
            vision.parent_id = 0
        self._dirty = True
        return True

    def get_refs(self, logic_id: int) -> list:
        """logic_id 로직 툴이 참조하는 비전 툴 목록 반환."""
        logic = self.tool_index.get(logic_id)
        if logic is None or not isinstance(logic, HyLogicTool):
            return []
        return list(logic.children)

    def get_tool(self, tool_id: int):
        return self.tool_index.get(tool_id)

    def clear(self):
        self.root_nodes.clear()
        self.tool_index.clear()
        self.anchor = None
        self._next_id = 1
        self._dirty = False

    # ─────────────────────────────────────────────────────────────────────────
    # DFS 직렬화 → SET_TOOL 명령 배열
    # ─────────────────────────────────────────────────────────────────────────

    def serialize_to_commands(self) -> list:
        """DFS 순회하며 32B SET_TOOL 명령 바이트열 리스트 반환."""
        cmds = []
        seq  = [0]

        def _dfs(tool: HyTool):
            seq[0] += 1
            tool.seq_id = seq[0]
            rx, ry = int(tool.search_roi[0]), int(tool.search_roi[1])
            rw, rh = int(tool.search_roi[2]), int(tool.search_roi[3])

            cmd = HyProtocol.pack_command(
                HyProtocol.CMD_SET_TOOL,
                target_id=tool.tool_id,
                target_type=tool.tool_type,
                p0=HyProtocol.encode_tree_info(tool.device_id, tool.parent_id),
                p1=0,
                p2=HyProtocol.encode_xy(rx, ry),
                p3=HyProtocol.encode_wh(rw, rh),
                fparam=getattr(tool, 'rot_angle', 0.0),
            )
            cmds.append(cmd)

            if isinstance(tool, HyLogicTool):
                for child in tool.children:
                    _dfs(child)

        for root in self.root_nodes:
            _dfs(root)

        return cmds

    # ─────────────────────────────────────────────────────────────────────────
    # State Injection (Device Burst → PC 트리 상태 동기화)
    # ─────────────────────────────────────────────────────────────────────────

    def inject_burst(self, burst_packets: list, cycle_id: int, img_id: int = 0):
        """
        Device Burst (list[dict]) 를 받아 트리 노드에 State Injection.
        burst_packets 의 각 dict 는 HyProtocol.unpack_result() 결과.
        _last_img_id/_last_cycle_id 를 함께 커밋해 evaluate() 에서 재실행 방지.
        """
        self.current_cycle_id = cycle_id
        for pkt in burst_packets:
            tool_id = pkt.get('tool_id', 0)
            if tool_id == 0:
                continue   # PONG 패킷 등
            tool = self.tool_index.get(tool_id)
            if tool:
                tool.from_packet(pkt)
                # 캐시 커밋: evaluate() 가 동일 img_id/cycle_id 로 호출 시 재실행 안 함
                tool._last_img_id   = pkt.get('img_id', img_id)
                tool._last_cycle_id = cycle_id

    # ─────────────────────────────────────────────────────────────────────────
    # PC 측 연산 재개 (device_id == DEV_PC 인 툴 실행)
    # ─────────────────────────────────────────────────────────────────────────

    def evaluate(self, img: np.ndarray, cycle_id: int,
                 img_id: int = 0, tx_id_start: int = 1) -> list:
        """
        Burst Injection 후 PC 할당 노드를 실행하고 Result Burst 바이트열 반환.
        Returns list[bytes] — 각 64B 패킷.
        """
        packets = []
        tx_id   = [tx_id_start]

        def _exec(tool: HyTool):
            # PC 할당 툴: 직접 실행
            if tool.device_id == HyProtocol.DEV_PC:
                if isinstance(tool, HyWhen):
                    tool.execute(img, img_id, cycle_id, self.tool_index)
                elif isinstance(tool, HyLogicTool):
                    tool.execute(img, img_id, cycle_id, self.tool_index)
                else:
                    tool.execute(img, img_id, cycle_id)

                packets.append(tool.to_packet(tx_id[0], cycle_id, img_id))
                tx_id[0] = (tx_id[0] % 65535) + 1

            # 자식 재귀
            if isinstance(tool, HyLogicTool):
                for child in tool.children:
                    _exec(child)

        for root in self.root_nodes:
            _exec(root)

        return packets

    # ─────────────────────────────────────────────────────────────────────────
    # UI Preview (TEACH 모드 즉시 렌더링용 — 모든 물리 툴 실행)
    # ─────────────────────────────────────────────────────────────────────────

    def preview(self, img: np.ndarray, img_id: int = 0) -> list:
        """
        TEACH 모드 UI Preview Engine 용.
        모든 물리 비전 툴을 실행하고 Result dict 리스트 반환.
        """
        results = []

        def _exec(tool: HyTool):
            if is_physical_tool(tool):
                tool.execute(img, img_id, 0)
                results.append({
                    'tool_id': tool.tool_id,
                    'rst_done': tool.rst_done,
                    'rst_state': tool.rst_state,
                    'x': tool.x, 'y': tool.y,
                    'w': tool.w, 'h': tool.h,
                    'angle': tool.angle,
                    'stat1': tool.stat1,
                })
            if isinstance(tool, HyLogicTool):
                for child in tool.children:
                    _exec(child)

        for root in self.root_nodes:
            _exec(root)

        # 앵커(HyLocator)도 별도 실행
        if self.anchor and self.anchor.tool_id not in {r['tool_id'] for r in results}:
            self.anchor.execute(img, img_id, 0)
            results.append({
                'tool_id': self.anchor.tool_id,
                'rst_done': self.anchor.rst_done,
                'rst_state': self.anchor.rst_state,
                'x': self.anchor.x, 'y': self.anchor.y,
                'w': self.anchor.w, 'h': self.anchor.h,
                'angle': self.anchor.angle,
                'stat1': self.anchor.stat1,
            })

        return results

    # ─────────────────────────────────────────────────────────────────────────
    # Fixture 변환 (앵커 편차 기반 아핀 변환 행렬)
    # ─────────────────────────────────────────────────────────────────────────

    def get_fixture_transform(self):
        """
        앵커 편차 (Δx, Δy, Δθ) 기반 QTransform 아핀 변환 행렬 반환.
        앵커가 없거나 미실행이면 단위 행렬 반환.
        """
        if not HAS_QT:
            return None

        t = QTransform()
        if self.anchor is None:
            return t
        if self.anchor.rst_done != HyProtocol.EXEC_DONE:
            return t

        # 앵커가 가진 티칭 원점 (search_roi 중심 사용)
        rx, ry, rw, rh = self.anchor.search_roi
        oX = rx + rw / 2.0
        oY = ry + rh / 2.0

        dX = self.anchor.x - oX
        dY = self.anchor.y - oY
        dA = self.anchor.angle   # 절대 각도를 편차로 사용 (티칭 시 0 기준)

        t.translate(dX, dY)
        t.translate(oX, oY)
        t.rotate(dA)
        t.translate(-oX, -oY)
        return t

    # ─────────────────────────────────────────────────────────────────────────
    # 편의 조회
    # ─────────────────────────────────────────────────────────────────────────

    def get_physical_tools(self) -> list:
        """모든 물리 비전 툴 (HyLocator 포함) 리스트."""
        return [t for t in self.tool_index.values() if is_physical_tool(t)]

    def get_logic_tools(self) -> list:
        """모든 로직 집행관 툴 리스트."""
        return [t for t in self.tool_index.values() if is_logic_tool(t)]

    def get_fin_tools(self) -> list:
        """HyFin 노드 리스트."""
        return [t for t in self.tool_index.values() if isinstance(t, HyFin)]

    def find_fin_judgment(self):
        """첫 번째 HyFin 의 판정 결과 반환 (JUDGE_OK/NG/PENDING)."""
        fins = self.get_fin_tools()
        if fins:
            return fins[0].rst_state
        return HyProtocol.JUDGE_NG

    def validate_dependency_order(self) -> list:
        """P2-09: 참조 툴이 참조자보다 먼저 실행되는지 검증.
        serialize_to_commands() 호출 후 seq_id 가 채워진 상태에서 사용.
        반환: 위반 항목 list[str]. 빈 리스트 = 이상 없음.
        """
        violations = []
        for tool in self.tool_index.values():
            if not is_logic_tool(tool):
                continue
            parent_seq = getattr(tool, 'seq_id', None)
            if parent_seq is None:
                continue
            # 로직 툴의 children 참조 검증
            for child in getattr(tool, 'children', []):
                child_seq = getattr(child, 'seq_id', None)
                if child_seq is not None and child_seq >= parent_seq:
                    violations.append(
                        f"순서 위반: {child.name}(seq={child_seq}) → "
                        f"{tool.name}(seq={parent_seq}): 참조자가 먼저 실행됨"
                    )
            # HyWhen 의 watch_tool_id 참조 검증
            watch_id = getattr(tool, 'watch_tool_id', None)
            if watch_id is not None:
                ref = self.tool_index.get(watch_id)
                if ref:
                    ref_seq = getattr(ref, 'seq_id', None)
                    if ref_seq is not None and ref_seq >= parent_seq:
                        violations.append(
                            f"순서 위반(watch): {ref.name}(seq={ref_seq}) → "
                            f"{tool.name}(seq={parent_seq})"
                        )
        return violations

    def reset_all(self):
        """모든 툴 상태 초기화 (새 사이클 시작용)."""
        for tool in self.tool_index.values():
            tool.reset_state()

    # ─────────────────────────────────────────────────────────────────────────
    # P3-01  레시피 파일 저장 (.hyv 바이너리)
    # ─────────────────────────────────────────────────────────────────────────

    def save_to_file(self, path: str) -> bool:
        """
        P3-01: 현재 트리를 .hyv 바이너리 파일로 저장.
        성공 시 True, OS 오류 시 False 반환. 성공 시 dirty 플래그 클리어.
        """
        tools     = list(self.tool_index.values())
        anchor_id = self.anchor.tool_id if self.anchor else 0

        buf = bytearray()

        # 헤더
        buf += struct.pack(_HYV_HDR_FMT,
                           _HYV_MAGIC, _HYV_VERSION,
                           len(tools), anchor_id, self._next_id)

        # 툴 레코드
        for tool in tools:
            rx, ry, rw, rh = (float(v) for v in tool.search_roi)
            name_b  = tool.name.encode('utf-8') if hasattr(tool, 'name') else b''
            extra_b = json.dumps(_tool_extra(tool),
                                 ensure_ascii=False).encode('utf-8')

            buf += struct.pack(_HYV_TOOL_FMT,
                               tool.tool_id & 0xFFFF,
                               tool.tool_type & 0xFFFF,
                               tool.parent_id & 0xFFFF,
                               tool.device_id & 0xFFFF,
                               getattr(tool, 'seq_id', 0),
                               rx, ry, rw, rh,
                               getattr(tool, 'rot_angle', 0.0),
                               len(name_b))
            buf += name_b
            buf += struct.pack('<H', len(extra_b))
            buf += extra_b

        # CRC32 (전체 페이로드 기반)
        crc = zlib.crc32(buf) & 0xFFFF_FFFF
        buf += struct.pack('<I', crc)

        try:
            with open(path, 'wb') as fh:
                fh.write(buf)
            self._dirty = False
            return True
        except OSError:
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # P3-02  레시피 파일 로드 (.hyv 바이너리)
    # ─────────────────────────────────────────────────────────────────────────

    def load_from_file(self, path: str) -> bool:
        """
        P3-02: .hyv 파일에서 레시피 로드. CRC 검증 포함.
        성공 시 현재 트리를 교체하고 True 반환.
        실패(파일 없음·손상·CRC 불일치) 시 현재 상태 유지하고 False 반환.
        """
        try:
            with open(path, 'rb') as fh:
                data = fh.read()
        except OSError:
            return False

        MIN_SIZE = _HYV_HDR_SIZE + 4   # 헤더 + CRC
        if len(data) < MIN_SIZE:
            return False

        # CRC 검증
        stored_crc   = struct.unpack_from('<I', data, len(data) - 4)[0]
        computed_crc = zlib.crc32(data[:-4]) & 0xFFFF_FFFF
        if stored_crc != computed_crc:
            return False

        # 헤더 파싱
        magic, version, n_tools, anchor_id, next_id = \
            struct.unpack_from(_HYV_HDR_FMT, data, 0)
        if magic != _HYV_MAGIC or version != _HYV_VERSION:
            return False

        # 툴 레코드 파싱
        offset = _HYV_HDR_SIZE
        loaded: list = []
        for _ in range(n_tools):
            if offset + _HYV_TOOL_SIZE > len(data) - 4:
                return False
            fields = struct.unpack_from(_HYV_TOOL_FMT, data, offset)
            offset += _HYV_TOOL_SIZE

            (tool_id, tool_type, parent_id, device_id, seq_id,
             rx, ry, rw, rh, rot_angle, name_len) = fields

            if offset + name_len > len(data) - 4:
                return False
            name = data[offset:offset + name_len].decode('utf-8', errors='replace')
            offset += name_len

            if offset + 2 > len(data) - 4:
                return False
            extra_len = struct.unpack_from('<H', data, offset)[0]
            offset += 2

            if offset + extra_len > len(data) - 4:
                return False
            extra_raw = data[offset:offset + extra_len]
            extra = json.loads(extra_raw) if extra_len > 0 else {}
            offset += extra_len

            loaded.append({
                'tool_id':   tool_id,
                'tool_type': tool_type,
                'parent_id': parent_id,
                'device_id': device_id,
                'seq_id':    seq_id,
                'roi':       (rx, ry, rw, rh),
                'rot_angle': rot_angle,
                'name':      name,
                'extra':     extra,
            })

        # 트리 재구성 — 현재 트리를 먼저 클리어
        self.clear()
        self._next_id = next_id

        # Pass 1: 모든 툴 오브젝트 생성
        tool_objs: dict = {}
        for td in loaded:
            try:
                t = create_tool(td['tool_type'], td['tool_id'])
            except ValueError:
                return False           # 알 수 없는 tool_type
            t.device_id  = td['device_id']
            t.seq_id     = td['seq_id']
            t.search_roi = td['roi']
            t.rot_angle  = td['rot_angle']
            if td['name'] and hasattr(t, 'name'):
                t.name = td['name']
            _apply_tool_extra(t, td['extra'])
            tool_objs[t.tool_id] = t

        # Pass 2: 트리 구조 연결
        for td in loaded:
            t = tool_objs[td['tool_id']]
            t.parent_id = td['parent_id']
            self.tool_index[t.tool_id] = t

            if td['parent_id'] == 0:
                self.root_nodes.append(t)
            else:
                parent = tool_objs.get(td['parent_id'])
                if parent is not None and isinstance(parent, HyLogicTool):
                    parent.add_child(t)

        # 앵커 복원
        if anchor_id and anchor_id in tool_objs:
            self.anchor = tool_objs[anchor_id]

        self._dirty = False
        return True

    # ─────────────────────────────────────────────────────────────────────────

    def __repr__(self):
        return (f"<RecipeTree tools={len(self.tool_index)} "
                f"roots={len(self.root_nodes)} "
                f"anchor={'yes' if self.anchor else 'no'}>")
