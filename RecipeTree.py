"""
RecipeTree.py - DCOM 집행관 트리 및 레시피 관리 (v2.0)
단순 dict 목록 → 계층적 집행관 트리로 격상.
"""
import math
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

        # 물리 비전 툴은 루트 금지
        if is_physical_tool(tool) and parent_id == 0:
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
        return True

    def get_tool(self, tool_id: int):
        return self.tool_index.get(tool_id)

    def clear(self):
        self.root_nodes.clear()
        self.tool_index.clear()
        self.anchor = None
        self._next_id = 1

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

    def inject_burst(self, burst_packets: list, cycle_id: int):
        """
        Device Burst (list[dict]) 를 받아 트리 노드에 State Injection.
        burst_packets 의 각 dict 는 HyProtocol.unpack_result() 결과.
        """
        self.current_cycle_id = cycle_id
        for pkt in burst_packets:
            tool_id = pkt.get('tool_id', 0)
            if tool_id == 0:
                continue   # PONG 패킷 등
            tool = self.tool_index.get(tool_id)
            if tool:
                tool.from_packet(pkt)

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

    def __repr__(self):
        return (f"<RecipeTree tools={len(self.tool_index)} "
                f"roots={len(self.root_nodes)} "
                f"anchor={'yes' if self.anchor else 'no'}>")
