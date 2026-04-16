import math
import time
from PyQt5.QtWidgets import QWidget, QPushButton
from PyQt5.QtCore import Qt, pyqtSignal, QRectF, QPointF, QSizeF
from PyQt5.QtGui import QPainter, QColor, QPen, QImage, QCursor, QFont, QPolygonF
from OverlayPanel import OverlayConfigPanel

class VisionCanvas(QWidget):
    # =========================================================================
    # [상수 및 설정값]
    # =========================================================================
    MODE_STANDBY = "STANDBY"
    MODE_TEACH   = "TEACH"
    MODE_TEST    = "TEST"
    MODE_LIVE    = "LIVE"
    MODE_AUTO    = "AUTO"

    ACTION_DRAG   = "DRAG"
    ACTION_RESIZE = "RESIZE"

    COLOR_BG    = "#0f172a"
    COLOR_OK    = "#10b981"
    COLOR_NG    = "#ef4444"
    COLOR_WARN  = "#facc15"
    COLOR_ANCHOR= "#f59e0b"
    COLOR_TOOL  = "#0ea5e9"
    # =========================================================================

    stats_updated = pyqtSignal(object, float, bool)
    roi_updated = pyqtSignal(int, float, float, float, float) # tool_id, x, y, w, h
    
    def __init__(self, recipe_manager, parent=None):
        super().__init__(parent)
        self.recipe = recipe_manager  # 💡 [핵심] RecipeManager 연동
        self.setMinimumSize(640, 480)
        self.setMouseTracking(True)
        
        self.image = QImage(640, 480, QImage.Format_RGB888)
        self.image.fill(QColor(self.COLOR_BG))
        self.mode = self.MODE_STANDBY 
        
        self.active_tool_id = None 
        self.action_mode = None
        self.drag_offset = QPointF()
        
        self.zoom_factor = 1.0
        self.pan_offset = QPointF(0, 0)
        self.is_panning = False
        
        self.current_image_rect = QRectF()
        
        self.btn_fit = QPushButton("⛶", self)
        self.btn_fit.setFixedSize(32, 32)
        self.btn_fit.setStyleSheet("QPushButton { background-color: rgba(15, 23, 42, 180); color: #cbd5e1; border: 1px solid #334155; border-radius: 4px; font-size: 18px; } QPushButton:hover { background-color: rgba(56, 189, 248, 180); color: white; border-color: #38bdf8; }")
        self.btn_fit.clicked.connect(self.fit_to_screen)
        
        self.setup_panel = OverlayConfigPanel(self)
        self.setup_panel.ui_updated.connect(self.update)
        self.setup_panel.panel_closed.connect(lambda: self.set_active_tool(None))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.btn_fit.move(self.width() - 42, 10)
        self.setup_panel.move(10, 10)
        
    def toggle_setup_panel(self):
        if not self.setup_panel.isVisible() or self.setup_panel.stack.currentIndex() != 0:
            self.setup_panel.show_page("IMAGE")
        else:
            self.setup_panel.setVisible(False)
            
    def toggle_result_panel(self):
        if not self.setup_panel.isVisible() or self.setup_panel.stack.currentIndex() != 5:
            self.setup_panel.show_page("RESULT")
        else:
            self.setup_panel.setVisible(False)

    def fit_to_screen(self):
        self.zoom_factor = 1.0
        self.pan_offset = QPointF(0, 0)
        self.update()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton: self.fit_to_screen()

    def wheelEvent(self, event):
        if self.image.isNull(): return
        factor = 1.1 if event.angleDelta().y() > 0 else 1.0 / 1.1
        old_cx, old_cy, old_s, _ = self._get_render_params()
        mouse_x, mouse_y = event.pos().x(), event.pos().y()
        img_mouse_x, img_mouse_y = (mouse_x - old_cx) / old_s, (mouse_y - old_cy) / old_s
        self.zoom_factor = max(1.0, min(self.zoom_factor * factor, 15.0))
        if self.zoom_factor <= 1.01: self.fit_to_screen() 
        else:
            new_s = min(self.width() / 640.0, self.height() / 480.0) * self.zoom_factor
            self.pan_offset = QPointF(mouse_x - (self.width() - 640.0 * new_s) / 2.0 - img_mouse_x * new_s, mouse_y - (self.height() - 480.0 * new_s) / 2.0 - img_mouse_y * new_s)
        self.update()

    def _get_render_params(self):
        s = min(self.width() / 640.0, self.height() / 480.0) * self.zoom_factor
        return (self.width() - 640.0 * s) / 2.0 + self.pan_offset.x(), (self.height() - 480.0 * s) / 2.0 + self.pan_offset.y(), s, s

    def set_image(self, qimg, img_id=0):
        self.image = qimg
        self.update()

    def set_mode(self, mode_str):
        self.mode = mode_str
        self.active_tool_id = None 
        self.update()

    def set_active_tool(self, tool_id):
        self.active_tool_id = tool_id
        if tool_id is None:
            self.setup_panel.setVisible(False)
        self.update()

    # --- 마우스 및 ROI 조작 (TEACH 모드 전용) ---
    def get_screen_rect(self, roi):
        """원본 ROI를 현재 줌/팬 상태의 화면 좌표계 사각형으로 변환"""
        cx, cy, sx, sy = self._get_render_params()
        return QRectF(cx + roi.x() * sx, cy + roi.y() * sy, roi.width() * sx, roi.height() * sy)

    def get_real_roi(self, screen_rect):
        """화면 좌표계 사각형을 원본 이미지 좌표계 ROI로 변환"""
        cx, cy, sx, sy = self._get_render_params()
        return QRectF((screen_rect.x() - cx) / sx, (screen_rect.y() - cy) / sy, screen_rect.width() / sx, screen_rect.height() / sy)

    def get_corner_rect(self, rect):
        return QRectF() if rect.isEmpty() else QRectF(rect.right() - 8, rect.bottom() - 8, 10, 10)
    
    def hit_test(self, pos):
        if self.mode != self.MODE_TEACH: return None, None
        posF = QPointF(pos)
        
        # 활성화된 툴이 있으면 우선 판정
        if self.active_tool_id and self.active_tool_id in self.recipe.tools:
            t = self.recipe.tools[self.active_tool_id]
            s_rect = self.get_screen_rect(t.original_roi)
            if self.get_corner_rect(s_rect).contains(posF): return t.tool_id, self.ACTION_RESIZE
            if s_rect.contains(posF): return t.tool_id, self.ACTION_DRAG
            
        # 활성화된 툴이 없으면 전체 검사
        for t_id, t in self.recipe.tools.items():
            s_rect = self.get_screen_rect(t.original_roi)
            if self.get_corner_rect(s_rect).contains(posF): return t_id, self.ACTION_RESIZE
            if s_rect.contains(posF): return t_id, self.ACTION_DRAG
        return None, None
    
    def mousePressEvent(self, event):
        if self.setup_panel.isVisible() and self.setup_panel.geometry().contains(event.pos()): return
        if event.button() == Qt.RightButton:
            self.is_panning = True; self.drag_offset = event.pos(); self.setCursor(Qt.ClosedHandCursor); return
        
        if self.mode == self.MODE_TEACH and event.button() == Qt.LeftButton:
            t_id, action = self.hit_test(event.pos())
            if t_id:
                self.active_tool_id = t_id
                self.action_mode = action
                s_rect = self.get_screen_rect(self.recipe.tools[t_id].original_roi)
                if action == self.ACTION_DRAG: 
                    self.drag_offset = QPointF(event.pos()) - s_rect.topLeft()
                
    def mouseMoveEvent(self, event):
        if self.is_panning:
            self.pan_offset += QPointF(event.pos() - self.drag_offset); self.drag_offset = event.pos(); self.update(); return
            
        if self.mode != self.MODE_TEACH: return
        
        if not event.buttons():
            _, action = self.hit_test(event.pos())
            self.setCursor(Qt.SizeFDiagCursor if action == self.ACTION_RESIZE else Qt.SizeAllCursor if action == self.ACTION_DRAG else Qt.ArrowCursor)
            return
            
        if self.action_mode and self.active_tool_id:
            tool = self.recipe.tools[self.active_tool_id]
            s_rect = self.get_screen_rect(tool.original_roi)
            
            if self.action_mode == self.ACTION_DRAG:
                new_pos = QPointF(event.pos()) - self.drag_offset
                s_rect.moveTo(new_pos.x(), new_pos.y())
            elif self.action_mode == self.ACTION_RESIZE:
                s_rect.setSize(QSizeF(max(20.0, float(event.pos().x() - s_rect.x())), max(20.0, float(event.pos().y() - s_rect.y()))))
                
            tool.original_roi = self.get_real_roi(s_rect)
            self.roi_updated.emit(tool.tool_id, tool.original_roi.x(), tool.original_roi.y(), tool.original_roi.width(), tool.original_roi.height())
            self.update()
            
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton: self.is_panning = False; self.setCursor(Qt.ArrowCursor); return
        self.action_mode = None

    # --- 렌더링 ---
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(self.COLOR_BG))
        
        cx, cy, sx, sy = self._get_render_params()
        sw, sh = 640.0 * sx, 480.0 * sy
        new_image_rect = QRectF(cx, cy, sw, sh)
        self.current_image_rect = new_image_rect
        
        painter.drawImage(new_image_rect, self.image)

        # 💡 [핵심] RecipeManager의 툴들을 화면에 렌더링
        for t_id, tool in self.recipe.tools.items():
            is_active = (t_id == self.active_tool_id)
            is_anchor = (t_id == self.recipe.anchor_tool_id)
            
            color = QColor(self.COLOR_ANCHOR) if is_anchor else QColor(self.COLOR_TOOL)
            
            if self.mode == self.MODE_TEACH:
                # TEACH 모드에서는 원본 ROI를 그립니다 (Fixture 미적용)
                s_rect = self.get_screen_rect(tool.original_roi)
                painter.setPen(QPen(color, 2 if is_active else 1, Qt.DashLine))
                painter.setBrush(QColor(color.red(), color.green(), color.blue(), 30 if is_active else 0))
                painter.drawRect(s_rect)
                painter.setPen(color)
                painter.drawText(s_rect.topLeft() + QPointF(5, -5), tool.name)
                
                if is_active: 
                    painter.setBrush(color)
                    painter.drawRect(self.get_corner_rect(s_rect))
            
            elif self.mode in [self.MODE_TEST, self.MODE_AUTO]:
                # TEST/AUTO 모드에서는 Fixture가 적용된 동적 폴리곤을 그립니다
                if tool.rst_done:
                    # 결과 좌표가 있으면 해당 좌표에 마킹
                    res_p = QPointF(cx + tool.x * sx, cy + tool.y * sy)
                    painter.setPen(QPen(color, 2))
                    painter.drawEllipse(res_p, 4, 4)
                    painter.drawText(res_p + QPointF(5, 15), f"Ang: {tool.angle:.1f}")

                fixtured_poly = self.recipe.get_fixtured_polygon(t_id)
                if not fixtured_poly.isEmpty():
                    # 폴리곤을 화면 좌표계로 변환
                    t = QTransform()
                    t.translate(cx, cy)
                    t.scale(sx, sy)
                    screen_poly = t.map(fixtured_poly)
                    
                    painter.setPen(QPen(color, 2, Qt.SolidLine))
                    painter.setBrush(QColor(color.red(), color.green(), color.blue(), 30))
                    painter.drawPolygon(screen_poly)
                    painter.setPen(color)
                    painter.drawText(screen_poly.boundingRect().topLeft() + QPointF(5, -5), tool.name)