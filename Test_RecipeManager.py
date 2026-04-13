import sys
from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QSlider, QLabel, QHBoxLayout, QFrame
from PyQt5.QtCore import Qt, QRectF, QPointF
from PyQt5.QtGui import QPainter, QColor, QPen, QPolygonF, QTransform
from RecipeManager import RecipeManager, BaseUITool

class FixtureVisualizer(QFrame):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.setMinimumSize(600, 400)
        self.setStyleSheet("background-color: #0f172a; border: 2px solid #334155;")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        anchor = self.manager.tools[self.manager.anchor_tool_id]
        dep_tool = self.manager.tools[2] # 종속된 툴
        
        # 1. 티칭 시점의 원본 위치 그리기 (흐릿하게)
        painter.setPen(QPen(QColor(255, 255, 255, 50), 1, Qt.DashLine))
        painter.drawRect(anchor.original_roi)
        painter.drawRect(dep_tool.original_roi)
        painter.drawText(anchor.original_roi.topLeft() + QPointF(0, -5), "Original Anchor")
        
        # 2. 현재 앵커 위치 그리기 (노란색)
        painter.setPen(QPen(QColor("#facc15"), 2))
        curr_anchor_rect = QRectF(anchor.original_roi.x() + self.manager.dX, 
                                  anchor.original_roi.y() + self.manager.dY, 
                                  anchor.original_roi.width(), anchor.original_roi.height())
        
        # 앵커 회전 반영을 위한 폴리곤 변환
        t = QTransform()
        t.translate(curr_anchor_rect.center().x(), curr_anchor_rect.center().y())
        t.rotate(anchor.angle)
        t.translate(-curr_anchor_rect.center().x(), -curr_anchor_rect.center().y())
        anchor_poly = t.map(QPolygonF(curr_anchor_rect))
        
        painter.drawPolygon(anchor_poly)
        painter.setBrush(QColor("#facc15")); painter.drawEllipse(QPointF(anchor.x, anchor.y), 4, 4)
        painter.setBrush(Qt.NoBrush)
        painter.drawText(anchor_poly.boundingRect().topLeft() + QPointF(0, -5), f"Current Anchor (Angle: {anchor.angle} deg)")
        
        # 3. 종속 툴(Fixture) 변환 결과 그리기 (파란색)
        fixtured_poly = self.manager.get_fixtured_polygon(2)
        
        painter.setPen(QPen(QColor("#0ea5e9"), 3))
        painter.setBrush(QColor(14, 165, 233, 40))
        painter.drawPolygon(fixtured_poly)
        painter.setPen(QColor("#0ea5e9"))
        painter.drawText(fixtured_poly.boundingRect().bottomLeft() + QPointF(0, 15), "Fixtured Dependent Tool")

class TestRecipeManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QA Test: Fixture Math Visualizer")
        
        # 레시피 매니저 세팅
        self.manager = RecipeManager()
        
        # 앵커 툴 등록 (ID: 1)
        anchor_tool = BaseUITool(1, "Master Locator", 3, roi=QRectF(200, 150, 100, 50))
        self.manager.add_tool(anchor_tool)
        self.manager.set_anchor_tool(1)
        
        # 티칭: 기준 원점(oX, oY)을 앵커 툴 중앙으로 설정
        self.manager.set_teaching_anchor(250.0, 175.0, 0.0)
        
        # 종속 툴 등록 (ID: 2) - 앵커보다 오른쪽 아래에 위치
        dependent_tool = BaseUITool(2, "Inspection ROI", 1, roi=QRectF(350, 250, 150, 80), use_anchor=True)
        self.manager.add_tool(dependent_tool)

        # UI 세팅
        main_widget = QWidget(); self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        
        self.canvas = FixtureVisualizer(self.manager)
        layout.addWidget(self.canvas)
        
        # 조작 슬라이더들
        layout.addLayout(self._make_slider("앵커 이동 X (dX):", -100, 100, 0, self.on_dx_change))
        layout.addLayout(self._make_slider("앵커 이동 Y (dY):", -100, 100, 0, self.on_dy_change))
        layout.addLayout(self._make_slider("앵커 회전 각도 (dAngle):", -180, 180, 0, self.on_angle_change))
        
        # 초기화 호출
        self._update_burst()

    def _make_slider(self, label, min_val, max_val, default_val, callback):
        hbox = QHBoxLayout()
        lbl = QLabel(label); lbl.setFixedWidth(150)
        sl = QSlider(Qt.Horizontal); sl.setRange(min_val, max_val); sl.setValue(default_val)
        sl.valueChanged.connect(callback)
        val_lbl = QLabel(str(default_val)); val_lbl.setFixedWidth(40)
        sl.valueChanged.connect(lambda v, l=val_lbl: l.setText(str(v)))
        hbox.addWidget(lbl); hbox.addWidget(sl); hbox.addWidget(val_lbl)
        return hbox

    def on_dx_change(self, val): self.manager.tools[1].x = self.manager.tools[1].oX + val; self._update_burst()
    def on_dy_change(self, val): self.manager.tools[1].y = self.manager.tools[1].oY + val; self._update_burst()
    def on_angle_change(self, val): self.manager.tools[1].angle = val; self._update_burst()

    def _update_burst(self):
        # 가상의 장치 패킷 수신 시뮬레이션
        anchor = self.manager.tools[1]
        dummy_burst = [
            {'tool_id': 1, 'rst_done': True, 'rst_state': True, 'x': anchor.x, 'y': anchor.y, 'angle': anchor.angle, 'stat1': 0, 'proc_time': 10}
        ]
        self.manager.update_results_from_burst(dummy_burst)
        self.canvas.update()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = TestRecipeManager()
    window.resize(650, 600)
    window.show()
    sys.exit(app.exec_())