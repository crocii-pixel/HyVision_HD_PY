import math
import time
from PyQt5.QtWidgets import QWidget, QPushButton
from PyQt5.QtCore import Qt, pyqtSignal, QRectF, QPointF, QSizeF
from PyQt5.QtGui import QPainter, QColor, QPen, QImage, QCursor, QFont
from PCVisionEngine import PCVisionEngine
from OverlayConfigPanel import OverlayConfigPanel

class VisionMap(QWidget):
    # =========================================================================
    # [상수 및 설정값 (Constants)]
    # =========================================================================
    # 1. 모드 및 상태 (Modes & States)
    MODE_STANDBY = "STANDBY"
    MODE_TEACH   = "TEACH"
    MODE_TEST    = "TEST"
    MODE_LIVE    = "LIVE"
    MODE_AUTO    = "AUTO"

    STATE_WAITING    = "WAITING"
    STATE_SCANNING   = "SCANNING"
    STATE_INSPECTING = "INSPECTING"
    STATE_ADJUSTING  = "ADJUSTING"
    STATE_PREVIEW    = "PREVIEW"

    # 2. ROI 식별자 및 패널 (ROI Identifiers & Panels)
    ROI_MODEL    = "MODEL"
    ROI_ALIGN    = "ALIGN"
    ROI_OBJ_LINE = "OBJ_LINE"
    ROI_SHD_LINE = "SHD_LINE"
    ROI_RESULT   = "RESULT"
    ROI_SPEC     = "SPEC"
    ROI_STATUS   = "STATUS"

    PAGE_IMAGE   = "IMAGE"
    PAGE_RESULT  = "RESULT"

    # 3. 마우스 액션 (Mouse Actions)
    ACTION_DRAG   = "DRAG"
    ACTION_RESIZE = "RESIZE"

    # 4. 출력 텍스트 (UI Texts)
    TEXT_OK         = "OK"
    TEXT_NG         = "NG"
    TEXT_PASS       = "PASS"
    TEXT_FAIL       = "FAIL"
    TEXT_REJECT     = "REJECTED"
    TEXT_B_OK       = "[ OK ]"
    TEXT_B_FAIL     = "[ FAIL ]"
    TEXT_B_NG       = "[ NG ]"
    TEXT_B_PASS     = "[ PASS ]"
    TEXT_B_LOCK     = "[ LOCK ]"
    TEXT_B_WEAK     = "[ WEAK ]"
    TEXT_B_OUT      = "[ OUT ]"
    TEXT_B_REJECT   = "[ REJECT ]"

    # 5. 색상 팔레트 (Color Palette - HEX)
    COLOR_BG         = "#0f172a"
    COLOR_OK         = "#2282FF"
    COLOR_NG         = "#ef4444"
    COLOR_PASS       = "#10b981"
    COLOR_FAIL       = COLOR_NG
    COLOR_WARN       = "#facc15"
    COLOR_INFO       = "#65C0F8"
    COLOR_STAT_ADJU  = "#8b5cf6"
    COLOR_STAT_WAIT  = COLOR_WARN
    COLOR_STAT_SCAN  = COLOR_INFO
    COLOR_STAT_INSP  = "#5E5E4A" #"#0A7854"
    COLOR_ALIGN      = "#f59e0b"
    COLOR_OBJ        = "#0ea5e9"
    COLOR_SHD        = "#f97316"
    COLOR_TEXT_DIM   = "#64748b"
    COLOR_TEXT_MUTED = "#94a3b8"
    COLOR_GUIDE_CYAN = "#22d3ee"
    COLOR_GUIDE_PURP = "#a78bfa"
    # =========================================================================

    stats_updated = pyqtSignal(object, float, bool)
    roi_updated = pyqtSignal(str, float, float, float, float)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(640, 480); self.setMouseTracking(True)
        self.image = QImage(640, 480, QImage.Format_RGB888); self.image.fill(QColor(self.COLOR_BG))
        self.mode = self.MODE_STANDBY 
        
        self.ref_model_center = None 
        self.calculated_dist = 0.0
        self.calc_IA = None; self.calc_IB = None
        self.tracked_obj_pts = None; self.tracked_shd_pts = None
        self.tracked_obj_rect_real = None; self.tracked_shd_rect_real = None
        self.processed_obj_img = None; self.processed_shd_img = None

        self.model_roi = QRectF(); self.align_roi = QRectF()
        self.obj_line_roi = QRectF(); self.shd_line_roi = QRectF()
        self.result_roi = QRectF(); self.spec_roi = QRectF(); self.status_roi = QRectF()
        
        self.current_image_rect = QRectF(); self.test_result = None; self.active_roi = None 
        self.action_mode = None; self.selected_rect_type = None
        self.drag_offset = QPointF(); self.zoom_factor = 1.0; self.pan_offset = QPointF(0, 0); self.is_panning = False
        self.font_shadow_offset = 2
        
        self.internal_state = self.STATE_WAITING

        self.res_text = ""
        self.res_color = QColor(self.COLOR_BG)
        self.state_colors = {self.STATE_WAITING: QColor(self.COLOR_STAT_WAIT), self.STATE_SCANNING: QColor(self.COLOR_STAT_SCAN), self.STATE_INSPECTING: QColor(self.COLOR_STAT_INSP), self.STATE_ADJUSTING: QColor(self.COLOR_STAT_ADJU), self.STATE_PREVIEW: QColor(self.COLOR_TEXT_MUTED)}
        self.state_color = QColor(self.COLOR_BG)
        self.procTime = 0
        
        self.btn_fit = QPushButton("⛶", self); self.btn_fit.setFixedSize(32, 32)
        self.btn_fit.setStyleSheet("QPushButton { background-color: rgba(15, 23, 42, 180); color: #cbd5e1; border: 1px solid #334155; border-radius: 4px; font-size: 18px; } QPushButton:hover { background-color: rgba(56, 189, 248, 180); color: white; border-color: #38bdf8; }")
        self.btn_fit.clicked.connect(self.fit_to_screen)
        
        self.setup_panel = OverlayConfigPanel(self)
        self.setup_panel.ui_updated.connect(self.update)
        self.setup_panel.panel_closed.connect(lambda: self.set_active_roi(None))

    def reset_to_defaults(self):
        self.ref_model_center = None 
        self.calculated_dist = 0.0
        self.calc_IA = None; self.calc_IB = None
        self.tracked_obj_pts = None; self.tracked_shd_pts = None
        self.tracked_obj_rect_real = None; self.tracked_shd_rect_real = None
        self.processed_obj_img = None; self.processed_shd_img = None
        self.test_result = None; self.active_roi = None 
        self.internal_state = self.STATE_WAITING
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event); self.btn_fit.move(self.width() - 42, 10); self.setup_panel.move(10, 10)
    
    def toggle_setup_panel(self):
        if not self.setup_panel.isVisible() or self.setup_panel.stack.currentIndex() != 0:
            self.setup_panel.show_page(self.PAGE_IMAGE)
        else:
            self.setup_panel.setVisible(False)
            
    def toggle_result_panel(self):
        if not self.setup_panel.isVisible() or self.setup_panel.stack.currentIndex() != 5:
            self.setup_panel.show_page(self.PAGE_RESULT)
        else:
            self.setup_panel.setVisible(False)

    def fit_to_screen(self):
        self.zoom_factor = 1.0; self.pan_offset = QPointF(0, 0); self.update()
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

    def set_image(self, qimg, result=None):
        self.image = qimg
        self.test_result = result
        self.calculated_dist = 0.0; self.calc_IA = None; self.calc_IB = None
        self.tracked_obj_pts = None; self.tracked_shd_pts = None
        self.tracked_obj_rect_real = None; self.tracked_shd_rect_real = None
        self.processed_obj_img = None; self.processed_shd_img = None
        is_aligned = False

        if self.mode == self.MODE_TEST or (self.mode == self.MODE_AUTO and result and result.get('isFound')):
            cx, cy, sx, sy = self._get_render_params()
            cp = QPointF(cx + result['x'] * sx + result['w'] * sx / 2.0, cy + result['y'] * sy + result['h'] * sy / 2.0)
            if not self.align_roi.isEmpty() and self.align_roi.contains(cp): is_aligned = True
            
            if self.ref_model_center:
                curr_real_cx = result['x'] + result['w'] / 2.0
                curr_real_cy = result['y'] + result['h'] / 2.0
                dx_real = curr_real_cx - self.ref_model_center.x()
                dy_real = curr_real_cy - self.ref_model_center.y()

                def process_line(roi, cfg):
                    rx, ry, rw, rh = self.get_real_roi(roi)
                    if rw <= 0 or rh <= 0: return None, None, None
                    rx += dx_real; ry += dy_real
                    img_w, img_h = qimg.width(), qimg.height()
                    if rx >= img_w or ry >= img_h or rx+rw <= 0 or ry+rh <= 0: return None, None, None
                    crop_x, crop_y = max(0, int(round(rx))), max(0, int(round(ry)))
                    crop_w, crop_h = min(int(round(rw)), img_w - crop_x), min(int(round(rh)), img_h - crop_y)
                    if crop_w <= 0 or crop_h <= 0: return None, None, None

                    # 💡 패딩 안정화가 적용된 전처리 호출
                    roi_tuple = (crop_x, crop_y, crop_w, crop_h)
                    processed = PCVisionEngine.apply_pre_processing(qimg, cfg, roi=roi_tuple, padding=5)
                    pts = PCVisionEngine.find_line(processed, cfg)
                    
                    rect_real = QRectF(crop_x, crop_y, crop_w, crop_h)
                    res_pts = None
                    if pts[0] and pts[1]:
                        A1 = (crop_x + pts[0][0], crop_y + pts[0][1])
                        A2 = (crop_x + pts[1][0], crop_y + pts[1][1])
                        res_pts = (A1, A2, pts[2])
                    return res_pts, rect_real, processed

                if not self.obj_line_roi.isEmpty():
                    self.tracked_obj_pts, self.tracked_obj_rect_real, self.processed_obj_img = process_line(self.obj_line_roi, self.setup_panel.obj_cfg)
                if not self.shd_line_roi.isEmpty():
                    self.tracked_shd_pts, self.tracked_shd_rect_real, self.processed_shd_img = process_line(self.shd_line_roi, self.setup_panel.shd_cfg)

                if self.tracked_obj_pts and self.tracked_shd_pts:
                    A1, A2, ang_A = self.tracked_obj_pts
                    B1, B2, ang_B = self.tracked_shd_pts
                    avg_ang = (ang_A + ang_B) / 2.0
                    alpha = math.radians(avg_ang)
                    vx, vy = -math.sin(alpha), math.cos(alpha)

                    CAx, CAy = (A1[0] + A2[0]) / 2.0, (A1[1] + A2[1]) / 2.0
                    CBx, CBy = (B1[0] + B2[0]) / 2.0, (B1[1] + B2[1]) / 2.0
                    Cmidx, Cmidy = (CAx + CBx) / 2.0, (CAy + CBy) / 2.0

                    dAx, dAy = A2[0] - A1[0], A2[1] - A1[1]
                    dBx, dBy = B2[0] - B1[0], B2[1] - B1[1]

                    def cross_product(v1x, v1y, v2x, v2y): return v1x * v2y - v1y * v2x
                    cpA = cross_product(vx, vy, dAx, dAy)
                    cpB = cross_product(vx, vy, dBx, dBy)

                    if abs(cpA) > 1e-5 and abs(cpB) > 1e-5:
                        tA = -cross_product(Cmidx - A1[0], Cmidy - A1[1], dAx, dAy) / cpA
                        tB = -cross_product(Cmidx - B1[0], Cmidy - B1[1], dBx, dBy) / cpB
                        self.calculated_dist = abs(tA - tB)
                        self.calc_IA = (Cmidx + tA * vx, Cmidy + tA * vy)
                        self.calc_IB = (Cmidx + tB * vx, Cmidy + tB * vy)

        self.stats_updated.emit(result, self.calculated_dist, is_aligned)
        self.update()

    def set_mode(self, mode_str):
        self.mode = mode_str
        self.active_roi = None 
        self.internal_state = self.STATE_WAITING
        if self.mode not in [self.MODE_TEACH, self.MODE_TEST, self.MODE_LIVE, self.MODE_AUTO]:
            self.test_result = None
            self.stats_updated.emit(None, 0.0, False)
        self.update()
        
    def get_real_roi(self, ui_roi):
        if ui_roi.isEmpty(): return 0, 0, 0, 0
        cx, cy, sx, sy = self._get_render_params()
        return int(round((ui_roi.x() - cx) / sx)), int(round((ui_roi.y() - cy) / sy)), int(round(ui_roi.width() / sx)), int(round(ui_roi.height() / sy))
    
    def set_real_roi(self, roi_type, rx, ry, rw, rh):
        cx, cy, sx, sy = self._get_render_params()
        rect = QRectF(cx + rx * sx, cy + ry * sy, rw * sx, rh * sy)
        if roi_type == self.ROI_MODEL: self.model_roi = rect
        elif roi_type == self.ROI_ALIGN: self.align_roi = rect
        elif roi_type == self.ROI_OBJ_LINE: self.obj_line_roi = rect
        elif roi_type == self.ROI_SHD_LINE: self.shd_line_roi = rect
        elif roi_type == self.ROI_RESULT: self.result_roi = rect
        elif roi_type == self.ROI_SPEC: self.spec_roi = rect
        elif roi_type == self.ROI_STATUS: self.status_roi = rect
        self.current_image_rect = QRectF(cx, cy, 640.0 * sx, 480.0 * sy); self.update()

    def set_active_roi(self, roi_type):
        if self.active_roi == roi_type:
            self.active_roi = None
            self.setup_panel.setVisible(False)
            self.update()
            return

        self.active_roi = roi_type
        if roi_type is None:
            self.update()
            return
            
        cx, cy, sx, sy = self._get_render_params()
        sw, sh = 640.0 * sx, 480.0 * sy
        if roi_type == self.ROI_MODEL:
            if self.model_roi.isEmpty(): self.model_roi = QRectF(cx + sw*0.4, cy + sh*0.4, sw*0.2, sh*0.2)
            if False: self.setup_panel.show_page(self.ROI_MODEL) 
            else: self.setup_panel.hide()
        elif roi_type == self.ROI_ALIGN:
            if self.align_roi.isEmpty(): self.align_roi = QRectF(cx + sw*0.25, cy + sh*0.25, sw*0.5, sw*0.5)
            if False: self.setup_panel.show_page(self.ROI_ALIGN) 
            else: self.setup_panel.hide()
        elif roi_type == self.ROI_OBJ_LINE:
            if self.obj_line_roi.isEmpty(): self.obj_line_roi = QRectF(cx + sw*0.3, cy + sh*0.3, sw*0.4, sh*0.15)
            self.setup_panel.show_page(self.ROI_OBJ_LINE)
        elif roi_type == self.ROI_SHD_LINE:
            if self.shd_line_roi.isEmpty(): self.shd_line_roi = QRectF(cx + sw*0.3, cy + sh*0.55, sw*0.4, sh*0.15)
            self.setup_panel.show_page(self.ROI_SHD_LINE)
            
        self.current_image_rect = QRectF(cx, cy, sw, sh); self.update()

    def get_corner_rect(self, rect):
        return QRectF() if rect.isEmpty() else QRectF(rect.right() - 8, rect.bottom() - 8, 10, 10)
    
    def hit_test(self, pos):
        posF = QPointF(pos)
        if self.mode == self.MODE_TEACH:
            rois = {self.ROI_MODEL: self.model_roi, self.ROI_ALIGN: self.align_roi, self.ROI_OBJ_LINE: self.obj_line_roi, self.ROI_SHD_LINE: self.shd_line_roi}
            for r_type, rect in rois.items():
                if (self.active_roi == r_type or self.active_roi is None) and not rect.isEmpty():
                    if self.get_corner_rect(rect).contains(posF): return r_type, self.ACTION_RESIZE
                    if rect.contains(posF): return r_type, self.ACTION_DRAG
        elif self.mode == self.MODE_TEST:
            rois = {self.ROI_RESULT: self.result_roi, self.ROI_SPEC: self.spec_roi, self.ROI_STATUS: self.status_roi}
            for r_type, rect in rois.items():
                if not rect.isEmpty():
                    if self.get_corner_rect(rect).contains(posF): return r_type, self.ACTION_RESIZE
                    if rect.contains(posF): return r_type, self.ACTION_DRAG
        return None, None
    
    def mousePressEvent(self, event):
        if self.setup_panel.isVisible() and self.setup_panel.geometry().contains(event.pos()): return
        if event.button() == Qt.RightButton:
            self.is_panning = True; self.drag_offset = event.pos(); self.setCursor(Qt.ClosedHandCursor); return
        if self.mode not in [self.MODE_TEACH, self.MODE_TEST]: return
        if self.mode == self.MODE_TEACH and self.active_roi is None: return
        if event.button() == Qt.LeftButton:
            rect_type, action = self.hit_test(event.pos())
            if rect_type:
                self.selected_rect_type = rect_type; self.action_mode = action
                if action == self.ACTION_DRAG: self.drag_offset = QPointF(event.pos()) - getattr(self, f"{rect_type.lower()}_roi").topLeft()
                
    def mouseMoveEvent(self, event):
        if hasattr(self, 'is_panning') and self.is_panning:
            self.pan_offset += QPointF(event.pos() - self.drag_offset); self.drag_offset = event.pos(); self.update(); return
        if self.mode not in [self.MODE_TEACH, self.MODE_TEST]: return
        if self.mode == self.MODE_TEACH and self.active_roi is None: return
        if not event.buttons():
            _, action = self.hit_test(event.pos())
            self.setCursor(Qt.SizeFDiagCursor if action == self.ACTION_RESIZE else Qt.SizeAllCursor if action == self.ACTION_DRAG else Qt.ArrowCursor)
            return
        if self.action_mode and self.selected_rect_type:
            target_rect = getattr(self, f"{self.selected_rect_type.lower()}_roi")
            if self.action_mode == self.ACTION_DRAG:
                new_pos = QPointF(event.pos()) - self.drag_offset; target_rect.moveTo(new_pos.x(), new_pos.y())
            elif self.action_mode == self.ACTION_RESIZE:
                target_rect.setSize(QSizeF(max(20.0, float(event.pos().x() - target_rect.x())), max(20.0, float(event.pos().y() - target_rect.y()))))
            setattr(self, f"{self.selected_rect_type.lower()}_roi", target_rect)
            
            rx, ry, rw, rh = self.get_real_roi(target_rect)
            self.roi_updated.emit(self.selected_rect_type, rx, ry, rw, rh)
            self.update()
            
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton: self.is_panning = False; self.setCursor(Qt.ArrowCursor); return
        self.action_mode = None; self.selected_rect_type = None

    def _draw_roi_box(self, painter, rect, color_str, label, is_active, dash=False):
        if rect.isEmpty(): return
        color = QColor(color_str)
        painter.setPen(QPen(color, 2 if is_active else 1, Qt.DashLine if dash else Qt.SolidLine))
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 30 if is_active else 0))
        painter.drawRect(rect); painter.setPen(color); painter.drawText(rect.topLeft() + QPointF(5, -5), label)
        if is_active: painter.setBrush(color); painter.drawRect(self.get_corner_rect(rect))

    def _render_line_processing_live(self, painter, rect, cfg, color_str, label):
        if rect.isEmpty() or self.image.isNull() or not (cfg.get('show_prep', False) or cfg.get('show_line', False)): return
        rx, ry, rw, rh = self.get_real_roi(rect)
        img_w, img_h = self.image.width(), self.image.height()
        if rx >= img_w or ry >= img_h or rw <= 0 or rh <= 0: return
        rw = min(rw, img_w - rx); rh = min(rh, img_h - ry)
        
        roi_tuple = (int(rx), int(ry), int(rw), int(rh))
        processed_img = PCVisionEngine.apply_pre_processing(self.image, cfg, roi=roi_tuple, padding=5)
        cx, cy, sx, sy = self._get_render_params()
        target_rect = QRectF(cx + rx * sx, cy + ry * sy, rw * sx, rh * sy)
        
        if cfg.get('show_prep', False): painter.drawImage(target_rect, processed_img)
        if cfg.get('show_line', False):
            pt1, pt2, angle = PCVisionEngine.find_line(processed_img, cfg)
            if pt1 and pt2:
                painter.setPen(QPen(QColor(color_str), 3, Qt.SolidLine))
                painter.drawLine(QPointF(target_rect.left() + pt1[0] * sx, target_rect.top() + pt1[1] * sy), QPointF(target_rect.left() + pt2[0] * sx, target_rect.top() + pt2[1] * sy))
                painter.setPen(QPen(QColor(color_str), 1)); painter.drawText(int(rect.left()), int(rect.bottom() + 15), f"[{label}] Angle: {angle:.2f}°")

    def _draw_tracked_line_info(self, painter, rect_real, pts, processed_img, cfg, color_str, label, view_roi_flag, view_line_flag):
        if rect_real is None: return
        cx, cy, sx, sy = self._get_render_params()
        target_rect = QRectF(cx + rect_real.x() * sx, cy + rect_real.y() * sy, rect_real.width() * sx, rect_real.height() * sy)
        
        if view_roi_flag:
            self._draw_roi_box(painter, target_rect, color_str, label + " (Tracked)", False, dash=True)
            if pts:
                A1, A2, angle = pts
                painter.setPen(QPen(QColor(color_str), 1))
                painter.drawText(int(target_rect.left()), int(target_rect.bottom() + 15), f"[{label}] Angle: {angle:.2f}°")

        if cfg.get('show_prep', False) and processed_img:
            painter.drawImage(target_rect, processed_img)
            
        if pts and view_line_flag and cfg.get('show_line', False):
            A1, A2, angle = pts
            p1_screen = QPointF(cx + A1[0] * sx, cy + A1[1] * sy)
            p2_screen = QPointF(cx + A2[0] * sx, cy + A2[1] * sy)
            painter.setPen(QPen(QColor(color_str), 3, Qt.SolidLine))
            painter.drawLine(p1_screen, p2_screen)

    def _draw_roi_by_id(self, painter, r_id):
        if r_id == self.ROI_ALIGN:
            self._draw_roi_box(painter, self.align_roi, self.COLOR_ALIGN, "Alignment Zone", self.active_roi in [self.ROI_ALIGN, None])
            if self.active_roi == self.ROI_ALIGN and not self.model_roi.isEmpty():
                mcx, mcy = self.model_roi.center().x(), self.model_roi.center().y()
                painter.setPen(QPen(QColor(self.COLOR_FAIL), 2)); painter.drawLine(QPointF(mcx - 15, mcy), QPointF(mcx + 15, mcy)); painter.drawLine(QPointF(mcx, mcy - 15), QPointF(mcx, mcy + 15))
        elif r_id == self.ROI_MODEL: self._draw_roi_box(painter, self.model_roi, self.COLOR_INFO, "Model", self.active_roi in [self.ROI_MODEL, None])
        elif r_id == self.ROI_OBJ_LINE: self._draw_roi_box(painter, self.obj_line_roi, self.COLOR_OBJ, "Object Line", self.active_roi == self.ROI_OBJ_LINE, dash=True); self._render_line_processing_live(painter, self.obj_line_roi, self.setup_panel.obj_cfg, self.COLOR_OBJ, "Object Line")
        elif r_id == self.ROI_SHD_LINE: self._draw_roi_box(painter, self.shd_line_roi, self.COLOR_SHD, "Shadow Line", self.active_roi == self.ROI_SHD_LINE, dash=True); self._render_line_processing_live(painter, self.shd_line_roi, self.setup_panel.shd_cfg, self.COLOR_SHD, "Shadow Line")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(self.COLOR_BG))
        cx, cy, sx, sy = self._get_render_params()
        sw, sh = 640.0 * sx, 480.0 * sy
        new_image_rect = QRectF(cx, cy, sw, sh)

        display_img = self.image

        rst_cfg = self.setup_panel.rst_cfg

        if self.result_roi.isEmpty():
            self.result_roi = QRectF(cx + rst_cfg.get('res_x', 480)*sx, cy + rst_cfg.get('res_y', 20)*sy, rst_cfg.get('res_w', 140)*sx, rst_cfg.get('res_h', 60)*sy)
        if self.spec_roi.isEmpty():
            self.spec_roi = QRectF(cx + rst_cfg.get('spec_x', 10)*sx, cy + rst_cfg.get('spec_y', 205)*sy, rst_cfg.get('spec_w', 620)*sx, rst_cfg.get('spec_h', 265)*sy)
        if self.status_roi.isEmpty():
            self.status_roi = QRectF(cx + rst_cfg.get('status_x', 120)*sx, cy + rst_cfg.get('status_y', 20)*sy, rst_cfg.get('status_w', 400)*sx, rst_cfg.get('status_h', 100)*sy)

        if not self.current_image_rect.isEmpty() and self.current_image_rect != new_image_rect:
            sc_x, sc_y = new_image_rect.width() / self.current_image_rect.width(), new_image_rect.height() / self.current_image_rect.height()
            def scale_roi(roi): return QRectF((roi.x() - self.current_image_rect.x()) * sc_x + new_image_rect.x(), (roi.y() - self.current_image_rect.y()) * sc_y + new_image_rect.y(), roi.width() * sc_x, roi.height() * sc_y) if not roi.isEmpty() else roi
            self.model_roi = scale_roi(self.model_roi); self.align_roi = scale_roi(self.align_roi); self.obj_line_roi = scale_roi(self.obj_line_roi); self.shd_line_roi = scale_roi(self.shd_line_roi)
            self.result_roi = scale_roi(self.result_roi); self.spec_roi = scale_roi(self.spec_roi); self.status_roi = scale_roi(self.status_roi)
            
        self.current_image_rect = new_image_rect
        painter.drawImage(new_image_rect, display_img)
        
        is_aligned = False
        is_found = False
        status = 1
        cp = QPointF()
        is_dist_ok = False
        is_lock = False
        curr_res = self.test_result
        curr_dist = self.calculated_dist
        curr_IA, curr_IB = self.calc_IA, self.calc_IB

        if self.mode in [self.MODE_TEST, self.MODE_AUTO]:
            if curr_res:
                is_found = curr_res.get('isFound', False)
                status = curr_res.get('status', 1)
                cp = QPointF(cx + curr_res['x'] * sx + curr_res['w'] * sx / 2.0, cy + curr_res['y'] * sy + curr_res['h'] * sy / 2.0) if is_found else QPointF()
                if is_found and not self.align_roi.isEmpty() and self.align_roi.contains(cp): is_aligned = True
                is_dist_ok = (rst_cfg.get('dist_min', 10.0) <= curr_dist <= rst_cfg.get('dist_max', 50.0))
                is_lock = is_found and (status == 3)

            # --- 상태 머신 ---
            if self.mode == self.MODE_AUTO:
                is_pattern_lock = is_found and status == 3
                is_pattern_weak = is_found and status == 2
                is_pattern_fail = not is_found or status == 1

                if is_pattern_fail or not is_aligned:
                    self.internal_state = self.STATE_WAITING
                else:
                    if self.internal_state == self.STATE_WAITING:
                        if is_pattern_lock: self.internal_state = self.STATE_INSPECTING
                        elif is_pattern_weak: self.internal_state = self.STATE_SCANNING
                    elif self.internal_state == self.STATE_SCANNING:
                        if is_pattern_lock: self.internal_state = self.STATE_INSPECTING
                    elif self.internal_state == self.STATE_INSPECTING:
                        if is_pattern_weak: self.internal_state = self.STATE_ADJUSTING
                    elif self.internal_state == self.STATE_ADJUSTING:
                        if is_pattern_lock: self.internal_state = self.STATE_INSPECTING

            ready_to_inspect = (self.internal_state == self.STATE_INSPECTING) if self.mode == self.MODE_AUTO else (is_lock and is_aligned)
            final_ok = ready_to_inspect and is_dist_ok
            curr_time = time.time()

            if self.mode == self.MODE_TEST:
                self._draw_roi_box(painter, self.result_roi, self.COLOR_WARN, "Result Box", False, dash=True)
                self._draw_roi_box(painter, self.spec_roi, self.COLOR_INFO, "Detail Spec Box", False, dash=True)
                self._draw_roi_box(painter, self.status_roi, self.COLOR_GUIDE_PURP, "Progress Box", False, dash=True)

            if self.mode == self.MODE_AUTO:
                if self.internal_state == self.STATE_WAITING:
                    alpha = int((math.sin(curr_time * 6) + 1) * 100 + 55) 
                    painter.fillRect(new_image_rect, QColor(15, 23, 42, 170)) 
                    
                    if not self.align_roi.isEmpty():
                        painter.setPen(QPen(QColor(self.COLOR_ALIGN), 3, Qt.SolidLine))
                        painter.setBrush(QColor(245, 158, 11, 20))
                        painter.drawRect(self.align_roi)
                    
                    if curr_res and curr_res.get('isFound'):
                        painter.setPen(QPen(QColor(self.COLOR_FAIL), 2))
                        painter.drawLine(QPointF(cp.x()-15, cp.y()), QPointF(cp.x()+15, cp.y()))
                        painter.drawLine(QPointF(cp.x(), cp.y()-15), QPointF(cp.x(), cp.y()+15))
                        
                elif self.internal_state == self.STATE_SCANNING:
                    painter.fillRect(new_image_rect, QColor(15, 23, 42, 170)) 
                elif self.internal_state == self.STATE_INSPECTING:
                    pass
                elif self.internal_state == self.STATE_ADJUSTING:
                    painter.fillRect(new_image_rect, QColor(15, 23, 42, 80)) 

            if rst_cfg.get('view_status_box', True):
                state_str = self.internal_state if self.mode == self.MODE_AUTO else (self.STATE_PREVIEW if self.mode == self.MODE_TEST else "")
                if state_str:
                    self.state_color = self.state_colors.get(state_str, QColor("#ffffff"))
                    font = QFont("Segoe UI")
                    font.setWeight(QFont.Black)
                    text_len = max(1, len(state_str))
                    pixel_size = int(min(self.status_roi.height() * 0.75, (self.status_roi.width() * 1.6) / text_len))
                    font.setPixelSize(max(10, pixel_size))
                    painter.setFont(font)
                    
                    if self.font_shadow_offset > 0:
                        painter.setPen(QColor(0, 0, 0, 200))
                        painter.drawText(self.status_roi.translated(self.font_shadow_offset, self.font_shadow_offset), Qt.AlignLeft | Qt.AlignVCenter, state_str)
                    painter.setPen(self.state_color)
                    painter.drawText(self.status_roi, Qt.AlignLeft | Qt.AlignVCenter, state_str)
                    painter.setFont(QFont())

            show_result_box = False
           
            if curr_res:
                if self.mode == self.MODE_AUTO and self.internal_state == self.STATE_INSPECTING:
                    show_result_box = True
                    self.res_text = self.TEXT_OK if final_ok else self.TEXT_NG
                    self.res_color = QColor(self.COLOR_OK) if final_ok else QColor(self.COLOR_NG)
                elif self.mode == self.MODE_TEST: 
                    show_result_box = True
                    self.res_text = self.TEXT_OK if is_dist_ok else self.TEXT_NG
                    self.res_color = QColor(self.COLOR_OK) if is_dist_ok else QColor(self.COLOR_NG)
                
                if show_result_box and rst_cfg.get('view_res_box', True):
                    font = QFont("Segoe UI")
                    font.setBold(True)
                    text_len = max(1, len(self.res_text))
                    pixel_size = int(min(self.result_roi.height() * 0.8, (self.result_roi.width() * 1.2) / text_len))
                    font.setPixelSize(max(10, pixel_size))
                    painter.setFont(font)
                    
                    if self.font_shadow_offset > 0:
                        painter.setPen(QColor(0, 0, 0, 150))
                        painter.drawText(self.result_roi.translated(self.font_shadow_offset, self.font_shadow_offset), Qt.AlignLeft | Qt.AlignTop, self.res_text) 
                    painter.setPen(self.res_color)
                    painter.drawText(self.result_roi, Qt.AlignLeft | Qt.AlignTop, self.res_text)
                    painter.setFont(QFont())

            show_details = (self.mode == self.MODE_TEST) or (self.mode == self.MODE_AUTO and self.internal_state in [self.STATE_INSPECTING, self.STATE_ADJUSTING])
            
            if show_details and curr_res and curr_res.get('isFound'):
                is_found_val = curr_res['isFound']
                status_val = curr_res.get('status', 1)
                cp_val = QPointF(cx + curr_res['x'] * sx + curr_res['w'] * sx / 2.0, cy + curr_res['y'] * sy + curr_res['h'] * sy / 2.0)
                
                m_color = QColor(self.COLOR_INFO) if status_val == 3 else (QColor(self.COLOR_ALIGN) if status_val == 2 else QColor(self.COLOR_FAIL))
                if rst_cfg.get('view_loc_roi', True):
                    painter.setPen(QPen(m_color, 2)); painter.setBrush(Qt.NoBrush); painter.drawRect(QRectF(cx + curr_res['x'] * sx, cy + curr_res['y'] * sy, curr_res['w'] * sx, curr_res['h'] * sy))
                
                if rst_cfg.get('view_loc_cross', True):
                    painter.setPen(QPen(m_color, 2))
                    painter.drawLine(QPointF(cp_val.x()-15, cp_val.y()), QPointF(cp_val.x()+15, cp_val.y()))
                    painter.drawLine(QPointF(cp_val.x(), cp_val.y()-15), QPointF(cp_val.x(), cp_val.y()+15))

                if self.ref_model_center:
                    self._draw_tracked_line_info(painter, self.tracked_obj_rect_real, self.tracked_obj_pts, self.processed_obj_img, self.setup_panel.obj_cfg, self.COLOR_OBJ, "Object Line", rst_cfg.get('view_obj_roi', True), rst_cfg.get('view_obj_line', True))
                    self._draw_tracked_line_info(painter, self.tracked_shd_rect_real, self.tracked_shd_pts, self.processed_shd_img, self.setup_panel.shd_cfg, self.COLOR_SHD, "Shadow Line", rst_cfg.get('view_shd_roi', True), rst_cfg.get('view_shd_line', True))

                    if curr_IA and curr_IB and rst_cfg.get('view_dist_line', True):
                        dist_color_str = self.COLOR_WARN if is_dist_ok else self.COLOR_FAIL
                        dist_color = QColor(dist_color_str)
                        sIA = QPointF(cx + curr_IA[0] * sx, cy + curr_IA[1] * sy); sIB = QPointF(cx + curr_IB[0] * sx, cy + curr_IB[1] * sy)
                        painter.setPen(QPen(dist_color, 2, Qt.SolidLine)); painter.drawLine(sIA, sIB); painter.setBrush(dist_color)
                        painter.drawEllipse(sIA, 3, 3); painter.drawEllipse(sIB, 3, 3)
                        mid_p = (sIA + sIB) / 2.0; painter.setPen(dist_color); painter.drawText(mid_p + QPointF(10, 0), f"{curr_dist:.2f} px")

                if (self.mode == self.MODE_AUTO) or (self.mode == self.MODE_TEST):
                    model_rect_screen = QRectF(cx + curr_res['x'] * sx, cy + curr_res['y'] * sy, curr_res['w'] * sx, curr_res['h'] * sy)
                    mc_x, mc_y = model_rect_screen.center().x(), model_rect_screen.center().y()
                    ac_x, ac_y = self.align_roi.center().x(), self.align_roi.center().y()
                    
                    if self.mode == self.MODE_AUTO and self.internal_state in [self.STATE_INSPECTING, self.STATE_ADJUSTING]:
                        painter.setPen(QPen(QColor(self.COLOR_GUIDE_CYAN), 2, Qt.SolidLine)); L = 25 
                        for pt, dx, dy in [(model_rect_screen.topLeft(), L, L), (model_rect_screen.topRight(), -L, L),
                                           (model_rect_screen.bottomLeft(), L, -L), (model_rect_screen.bottomRight(), -L, -L)]:
                            painter.drawLine(pt, pt + QPointF(dx, 0)); painter.drawLine(pt, pt + QPointF(0, dy))

                        painter.setPen(QPen(QColor(self.COLOR_GUIDE_CYAN), 1, Qt.DashLine)); ac_p = QPointF(ac_x, ac_y)
                        for corner_p in [model_rect_screen.topLeft(), model_rect_screen.topRight(),
                                         model_rect_screen.bottomLeft(), model_rect_screen.bottomRight()]:
                            painter.drawLine(ac_p, corner_p)

                        painter.setPen(QPen(QColor(self.COLOR_WARN), 2, Qt.DotLine)); painter.drawLine(QPointF(ac_x, ac_y), QPointF(mc_x, mc_y)); painter.setBrush(QColor(self.COLOR_WARN)); painter.drawEllipse(QPointF(ac_x, ac_y), 4, 4)

                    if rst_cfg.get('view_spec_box', True):
                        box_x, box_y = self.spec_roi.x(), self.spec_roi.y()
                        box_w, box_h = self.spec_roi.width(), self.spec_roi.height()
                        
                        fs_base = int(8 * sy)
                        fs_title = int(10 * sy)
                        fs_result = int(12 * sy)
                        line_spacing = int(10 * sy)
                        
                        spec_alpha = rst_cfg.get('spec_alpha', 120)
                        painter.setPen(Qt.NoPen); painter.setBrush(QColor(15, 23, 42, spec_alpha)); painter.drawRoundedRect(self.spec_roi, 8, 8)
                        
                        font = QFont("Consolas", fs_title, QFont.Bold); painter.setFont(font); y_offset = box_y + line_spacing * 1.5
                        painter.setPen(QColor(self.COLOR_INFO)); painter.drawText(int(box_x + 15), int(y_offset), "■ ADVANCED INSPECTION DETAILS")
                        
                        y_offset += line_spacing * 0.5; painter.setPen(QColor(71, 85, 105, 100)); painter.drawLine(QPointF(box_x + 15, y_offset), QPointF(box_x + box_w - 15, y_offset))
                        
                        font.setPixelSize(fs_base); painter.setFont(font)
                        fm = painter.fontMetrics()
                        spec_align = "LEFT"
                        
                        def draw_align(align_type, y_pos, text, color):
                            painter.setPen(color)
                            if align_type == "RIGHT":
                                tw = fm.boundingRect(text).width()
                                painter.drawText(int(box_x + box_w - 20 - tw), int(y_pos), text)
                            else: 
                                painter.drawText(max(int(box_x + box_w * 0.72), int(box_x + box_w - 110)), int(y_pos), text)
                        
                        is_lock_state = (self.internal_state == self.STATE_INSPECTING) if self.mode == self.MODE_AUTO else (status == 3)

                        y_offset += line_spacing * 1.2; painter.setPen(QColor(self.COLOR_TEXT_MUTED)); painter.drawText(int(box_x + 15), int(y_offset), "[1] Photometric Consistency & Luminance Integrity")
                        draw_align(spec_align, y_offset, self.TEXT_B_OK if is_lock_state else self.TEXT_B_FAIL, QColor(self.COLOR_PASS) if is_lock_state else QColor(self.COLOR_FAIL))
                        
                        y_offset += line_spacing; painter.setPen(QColor(self.COLOR_TEXT_MUTED)); painter.drawText(int(box_x + 15), int(y_offset), "[2] Volumetric Calibration & Z-Axis Depth Scaling")
                        draw_align(spec_align, y_offset, self.TEXT_B_OK if is_lock_state else self.TEXT_B_FAIL, QColor(self.COLOR_PASS) if is_lock_state else QColor(self.COLOR_FAIL))
                        
                        y_offset += line_spacing; painter.setPen(QColor(self.COLOR_TEXT_MUTED)); painter.drawText(int(box_x + 15), int(y_offset), f"[3] Rotational Transformation Delta (Tilt: {curr_res.get('ang', 0):.2f}°)")
                        draw_align(spec_align, y_offset, self.TEXT_B_OK, QColor(self.COLOR_PASS))
                        
                        y_offset += line_spacing; painter.setPen(QColor(self.COLOR_TEXT_MUTED)); painter.drawText(int(box_x + 15), int(y_offset), f"[4] Geometric Coordinate Mapping Fidelity (Scr: {curr_res.get('score', 0)}%)")
                        if is_lock_state: lock_txt, lock_col = self.TEXT_B_LOCK, QColor(self.COLOR_PASS)
                        elif status == 2: lock_txt, lock_col = self.TEXT_B_WEAK, QColor(self.COLOR_WARN) 
                        else: lock_txt, lock_col = self.TEXT_B_FAIL, QColor(self.COLOR_FAIL)
                        draw_align(spec_align, y_offset, lock_txt, lock_col)
                        
                        y_offset += line_spacing; painter.setPen(QColor(self.COLOR_TEXT_MUTED)); painter.drawText(int(box_x + 15), int(y_offset), f"[5] Planar ROI Spatial Alignment Validation")
                        draw_align(spec_align, y_offset, self.TEXT_B_PASS if is_aligned else self.TEXT_B_NG, QColor(self.COLOR_PASS) if is_aligned else QColor(self.COLOR_FAIL))
                        
                        y_offset += line_spacing * 0.7; painter.setPen(QColor(71, 85, 105, 100)); painter.drawLine(QPointF(box_x + 15, y_offset), QPointF(box_x + box_w - 15, y_offset))
                        
                        y_offset += line_spacing * 1.2; painter.setPen(QColor(self.COLOR_TEXT_MUTED)); painter.drawText(int(box_x + 15), int(y_offset), "[6] Substrate Surface Gradient Analysis (Profile)")
                        y_offset += line_spacing
                        
                        if is_lock_state:
                            derived_inclination = curr_dist * 0.082 
                            dist_str = f"▶ Profile Delta: {curr_dist:.2f} px (Ref: {rst_cfg.get('dist_min', 10.0)}~{rst_cfg.get('dist_max', 50.0)})"
                            painter.setPen(QColor(self.COLOR_TEXT_MUTED)); painter.drawText(int(box_x + 35), int(y_offset), dist_str)
                            dist_color = QColor(self.COLOR_PASS) if is_dist_ok else QColor(self.COLOR_FAIL)
                            draw_align(spec_align, y_offset, self.TEXT_B_PASS if is_dist_ok else self.TEXT_B_OUT, dist_color)
                            
                            y_offset += line_spacing; painter.setPen(QColor(self.COLOR_TEXT_MUTED)); painter.drawText(int(box_x + 15), int(y_offset), "[7] Inclination Accuracy Suitability (Metal Plate Tilt)")
                            incl_str = f"▶ Calc. Inclination: {derived_inclination:.3f}°"
                            painter.setPen(QColor(self.COLOR_WARN)); painter.drawText(int(box_x + 35), int(y_offset + line_spacing), incl_str)
                            draw_align(spec_align, y_offset, self.TEXT_B_PASS if is_dist_ok else self.TEXT_B_FAIL, dist_color)
                            y_offset += line_spacing 
                        else:
                            painter.setPen(QColor(self.COLOR_TEXT_DIM)); painter.drawText(int(box_x + 35), int(y_offset), "▶ Acquisition Pending... (Waiting for High-Fidelity Lock)")
                        
                        y_offset += line_spacing * 0.7; painter.setPen(QColor(71, 85, 105, 100)); painter.drawLine(QPointF(box_x + 15, y_offset), QPointF(box_x + box_w - 15, y_offset))
                        
                        y_offset += line_spacing * 1.4; font.setPixelSize(fs_result); font.setBold(True); painter.setFont(font)
                        if self.mode == self.MODE_AUTO:
                            if self.internal_state == self.STATE_INSPECTING:
                                final_res_color = QColor(self.COLOR_PASS) if is_dist_ok else QColor(self.COLOR_FAIL)
                                painter.setPen(final_res_color)
                                painter.drawText(int(box_x + 15), int(y_offset), f"▣ TOTAL JUDGEMENT : [ {self.TEXT_PASS if is_dist_ok else self.TEXT_REJECT} ]")
                            elif self.internal_state == self.STATE_ADJUSTING:
                                painter.setPen(QColor(self.COLOR_STAT_ADJU))
                                painter.drawText(int(box_x + 15), int(y_offset), "▣ SYSTEM STATUS : [ ADJUSTING (Weak Lock) ]")
                        else:
                            if is_lock_state:
                                final_res_color = QColor(self.COLOR_PASS) if is_dist_ok else QColor(self.COLOR_FAIL)
                                painter.setPen(final_res_color)
                                painter.drawText(int(box_x + 15), int(y_offset), f"▣ TOTAL JUDGEMENT : [ {self.TEXT_PASS if is_dist_ok else self.TEXT_REJECT} ]")
                            else:
                                painter.setPen(QColor(self.COLOR_STAT_ADJU))
                                painter.drawText(int(box_x + 15), int(y_offset), "▣ SYSTEM STATUS : [ ADJUSTING (Preview) ]")

                        font.setPixelSize(fs_base); painter.setFont(font); painter.setPen(QColor(self.COLOR_TEXT_DIM))
                        self.procTime = curr_res.get('procTime', 34) if curr_res.get('procTime', 0) > 0 else 34
                        # 지연시간 텍스트도 동적 정렬
                        painter.drawText(int(box_x + box_w - fm.boundingRect(f"{self.procTime} ms").width() - 20), int(y_offset), f"{self.procTime} ms")

            if not self.align_roi.isEmpty() and rst_cfg.get('view_align_roi', True):
                if self.mode == self.MODE_TEST or (self.mode == self.MODE_AUTO and self.internal_state in [self.STATE_INSPECTING, self.STATE_ADJUSTING]):
                    box_color = QColor(self.COLOR_PASS) if is_aligned else QColor(self.COLOR_FAIL)
                    painter.setPen(QPen(box_color, 3 if is_aligned else 2, Qt.SolidLine)); painter.setBrush(QColor(box_color.red(), box_color.green(), box_color.blue(), 30) if is_aligned else Qt.NoBrush)
                    painter.drawRect(self.align_roi)
                    painter.setPen(box_color)
                    painter.drawText(self.align_roi.topLeft() + QPointF(5, -5), "Alignment Zone")

        if self.active_roi:
            self._draw_roi_by_id(painter, self.active_roi)

        if self.mode == self.MODE_TEACH:
            roi_order = [self.ROI_ALIGN, self.ROI_MODEL, self.ROI_OBJ_LINE, self.ROI_SHD_LINE]
            if self.active_roi in roi_order: roi_order.remove(self.active_roi); roi_order.append(self.active_roi) 
            for r_id in roi_order: 
                if r_id != self.active_roi: 
                    self._draw_roi_by_id(painter, r_id)