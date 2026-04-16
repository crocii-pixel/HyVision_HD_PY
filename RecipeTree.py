from PyQt5.QtCore import QPointF, QRectF
from PyQt5.QtGui import QPolygonF, QTransform

class BaseUITool:
    """PC(UI) 측에서 관리되는 단일 비전 툴의 상태 컨테이너"""
    def __init__(self, tool_id, name, tool_type, roi=QRectF(), use_anchor=True):
        self.tool_id = tool_id
        self.name = name
        self.tool_type = tool_type
        self.original_roi = roi      # 티칭 시 그려진 원본 영역
        self.use_anchor = use_anchor # 앵커 추종 여부
        
        # 1. 티칭(Teaching) 시점의 마스터 기준 좌표
        self.oX = 0.0
        self.oY = 0.0
        self.oAngle = 0.0
        
        # 2. 장치(Device)로부터 수신된 최신 결과값
        self.rst_done = False
        self.rst_state = False
        self.x = 0.0
        self.y = 0.0
        self.angle = 0.0
        self.stat1 = 0.0
        self.proc_time = 0

class RecipeManager:
    """모든 비전 툴의 목록을 관리하고 Fixture(동적 좌표계) 변환을 수행하는 마스터 매니저"""
    def __init__(self):
        self.tools = {} # tool_id를 Key로 사용하는 딕셔너리
        self.anchor_tool_id = None
        
        # Fixture 편차 (Delta)
        self.dX = 0.0
        self.dY = 0.0
        self.dAngle = 0.0

    def add_tool(self, tool):
        self.tools[tool.tool_id] = tool

    def set_anchor_tool(self, tool_id):
        self.anchor_tool_id = tool_id

    def set_teaching_anchor(self, oX, oY, oAngle):
        """티칭(TEACH) 모드에서 앵커의 마스터 기준 좌표를 확정합니다."""
        if self.anchor_tool_id in self.tools:
            anchor = self.tools[self.anchor_tool_id]
            anchor.oX = oX
            anchor.oY = oY
            anchor.oAngle = oAngle
            # 티칭 시점에는 편차가 0입니다.
            self.dX = 0.0; self.dY = 0.0; self.dAngle = 0.0

    def update_results_from_burst(self, burst_results):
        """OpenMVWorker에서 수신된 64Byte 파싱 딕셔너리 리스트를 UI 툴 객체에 매핑합니다."""
        for res in burst_results:
            t_id = res['tool_id']
            if t_id in self.tools:
                tool = self.tools[t_id]
                tool.rst_done = res['rst_done']
                tool.rst_state = res['rst_state']
                tool.x = res['x']
                tool.y = res['y']
                tool.angle = res['angle']
                tool.stat1 = res['stat1']
                tool.proc_time = res['proc_time']
                
        # 앵커 편차(Fixture Delta) 갱신
        if self.anchor_tool_id in self.tools:
            anchor = self.tools[self.anchor_tool_id]
            if anchor.rst_done:
                self.dX = anchor.x - anchor.oX
                self.dY = anchor.y - anchor.oY
                self.dAngle = anchor.angle - anchor.oAngle

    def get_fixtured_polygon(self, tool_id):
        """
        [핵심 수학 로직] 
        해당 툴의 원본 ROI에 Fixture 변환 행렬을 적용하여, 기울어지고 이동된 다각형(Polygon)을 반환합니다.
        """
        if tool_id not in self.tools: return QPolygonF()
        tool = self.tools[tool_id]
        roi = tool.original_roi
        
        # 앵커 추종을 안 하거나 앵커가 없으면 원본 그대로 반환
        if not tool.use_anchor or self.anchor_tool_id not in self.tools:
            return QPolygonF(roi)
            
        anchor = self.tools[self.anchor_tool_id]
        
        # PyQt5의 QTransform을 이용한 최적화된 아핀 변환 (행렬 곱)
        t = QTransform()
        
        # 변환 순서 (역순으로 적용됨):
        # 3. 마지막으로 현재 앵커의 위치 편차(dX, dY)만큼 이동
        t.translate(self.dX, self.dY)
        # 2. 티칭된 앵커 원점(oX, oY)을 기준으로 각도 편차(dAngle)만큼 회전
        t.translate(anchor.oX, anchor.oY)
        t.rotate(self.dAngle)
        t.translate(-anchor.oX, -anchor.oY)
        
        # 1. 원본 사각형(ROI) 좌표계를 입력으로 받아 변환된 다각형 배출
        return t.map(QPolygonF(roi))