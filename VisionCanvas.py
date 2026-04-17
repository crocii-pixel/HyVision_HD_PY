"""
VisionCanvas.py - 비전 이미지 뷰포트 위젯 (v2.0)
줌/팬, ROI 드래그·리사이즈(TEACH), OSD 렌더링(RUN).
"""
import math
from PyQt5.QtWidgets import QWidget, QPushButton
from PyQt5.QtCore    import Qt, pyqtSignal, QRectF, QPointF, QSizeF
from PyQt5.QtGui     import (QPainter, QColor, QPen, QBrush,
                              QImage, QFont, QPolygonF, QTransform)

from HyProtocol    import HyProtocol
from HyVisionTools import HyTool, HyLogicTool, HyLocator, HyFin, is_physical_tool


# ─── 색상 팔레트 ─────────────────────────────────────────────────────────────
C_BG      = QColor("#0f172a")
C_OK      = QColor("#10b981")
C_NG      = QColor("#ef4444")
C_PENDING = QColor("#facc15")
C_ANCHOR  = QColor("#f59e0b")
C_TOOL    = QColor("#38bdf8")
C_LOGIC   = QColor("#a78bfa")
C_FIN     = QColor("#fb923c")
C_SEL     = QColor("#ffffff")
C_CROSS   = QColor("#ffffff")

JUDGE_COLOR = {
    HyProtocol.JUDGE_OK:      C_OK,
    HyProtocol.JUDGE_NG:      C_NG,
    HyProtocol.JUDGE_PENDING: C_PENDING,
}


class VisionCanvas(QWidget):
    """비전 이미지 뷰포트, ROI 편집(TEACH), OSD 렌더링(RUN)."""

    MODE_STANDBY = "STANDBY"
    MODE_LIVE    = "LIVE"
    MODE_TEACH   = "TEACH"
    MODE_TEST    = "TEST"
    MODE_RUN     = "RUN"

    # TEACH 모드에서 ROI 가 변경될 때 발행
    sig_roi_changed = pyqtSignal(int, float, float, float, float)   # tool_id, x,y,w,h
    # 툴 클릭됐을 때
    sig_tool_selected = pyqtSignal(int)   # tool_id  (-1 = 선택 해제)

    def __init__(self, recipe_tree=None, parent=None):
        super().__init__(parent)
        self.recipe   = recipe_tree   # RecipeTree
        self.setMinimumSize(640, 480)
        self.setMouseTracking(True)

        self._image          = QImage(640, 480, QImage.Format_RGB888)
        self._image.fill(C_BG)
        self._mode           = self.MODE_STANDBY
        self._active_tool_id = None
        self._burst_results  = []    # list[dict] — 마지막 Burst 결과

        # 줌/팬
        self._zoom  = 1.0
        self._pan   = QPointF(0, 0)
        self._panning    = False
        self._pan_origin = QPointF()

        # TEACH 드래그 상태
        self._drag_action = None   # None | "move" | "resize"
        self._drag_offset = QPointF()

        # OSD 옵션
        self.show_roi_boxes    = True
        self.show_result_cross = True
        self.show_status_box   = True
        self.show_spec_box     = True

        # P4-04: OSD 드래그 상태 (None = 자동 위치)
        self._osd_status_pos: QPointF | None = None   # Status Box 좌상단
        self._osd_spec_pos:   QPointF | None = None   # Spec Box 좌상단
        self._osd_drag_target: str | None = None       # "status" | "spec"
        self._osd_drag_offset = QPointF()

        # Fit 버튼
        self._btn_fit = QPushButton("⛶", self)
        self._btn_fit.setFixedSize(32, 32)
        self._btn_fit.setToolTip("화면에 맞추기 (더블클릭)")
        self._btn_fit.setStyleSheet(
            "QPushButton{background:rgba(15,23,42,180);color:#cbd5e1;"
            "border:1px solid #334155;border-radius:4px;font-size:18px;}"
            "QPushButton:hover{background:rgba(56,189,248,180);color:white;}")
        self._btn_fit.clicked.connect(self.fit_to_screen)

    # ─── 공개 API ────────────────────────────────────────────────────────────

    def set_recipe(self, recipe_tree):
        self.recipe = recipe_tree
        self.update()

    def set_image(self, qimg: QImage, burst_results: list = None):
        if qimg and not qimg.isNull():
            self._image = qimg
        if burst_results is not None:
            self._burst_results = burst_results
        self.update()

    def set_mode(self, mode: str):
        self._mode = mode
        self._active_tool_id = None
        self.update()

    def set_active_tool(self, tool_id):
        self._active_tool_id = tool_id
        self.sig_tool_selected.emit(tool_id if tool_id is not None else -1)
        self.update()

    # ─── 줌/팬 ───────────────────────────────────────────────────────────────

    def fit_to_screen(self):
        self._zoom = 1.0
        self._pan  = QPointF(0, 0)
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._btn_fit.move(self.width() - 42, 10)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.fit_to_screen()

    def wheelEvent(self, event):
        if self._image.isNull():
            return
        factor = 1.12 if event.angleDelta().y() > 0 else 1.0 / 1.12
        cx, cy, s = self._render_params()
        mx, my = event.pos().x(), event.pos().y()
        ix = (mx - cx) / s
        iy = (my - cy) / s
        self._zoom = max(1.0, min(self._zoom * factor, 20.0))
        if self._zoom <= 1.01:
            self.fit_to_screen()
            return
        ns = min(self.width() / self._iw(), self.height() / self._ih()) * self._zoom
        self._pan = QPointF(mx - (self.width()  - self._iw() * ns) / 2 - ix * ns,
                            my - (self.height() - self._ih() * ns) / 2 - iy * ns)
        self.update()

    # ─── 마우스 이벤트 (TEACH ROI 편집) ─────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self._panning    = True
            self._pan_origin = QPointF(event.pos())
            self.setCursor(Qt.ClosedHandCursor)
            return

        # P4-04: RUN 모드 OSD 드래그 시작
        if self._mode == self.MODE_RUN and event.button() == Qt.LeftButton:
            pos = QPointF(event.pos())
            sr  = self._osd_status_rect()
            spec_r = self._osd_spec_rect()
            if sr is not None and sr.contains(pos):
                self._osd_drag_target = "status"
                self._osd_drag_offset = pos - sr.topLeft()
                self.setCursor(Qt.SizeAllCursor)
                return
            if spec_r is not None and spec_r.contains(pos):
                self._osd_drag_target = "spec"
                self._osd_drag_offset = pos - spec_r.topLeft()
                self.setCursor(Qt.SizeAllCursor)
                return

        if self._mode == self.MODE_TEACH and event.button() == Qt.LeftButton:
            tid, action = self._hit_test(QPointF(event.pos()))
            if tid is not None:
                self._active_tool_id = tid
                self._drag_action    = action
                srect = self._screen_rect_of(tid)
                if action == "move":
                    self._drag_offset = QPointF(event.pos()) - srect.topLeft()
                self.sig_tool_selected.emit(tid)
            else:
                self._active_tool_id = None
                self.sig_tool_selected.emit(-1)
            self.update()

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = QPointF(event.pos()) - self._pan_origin
            self._pan       += delta
            self._pan_origin = QPointF(event.pos())
            self.update()
            return

        # P4-04: OSD 박스 드래그
        if self._osd_drag_target and event.buttons() & Qt.LeftButton:
            new_tl = QPointF(event.pos()) - self._osd_drag_offset
            if self._osd_drag_target == "status":
                self._osd_status_pos = new_tl
            elif self._osd_drag_target == "spec":
                self._osd_spec_pos = new_tl
            self.update()
            return

        if self._mode != self.MODE_TEACH:
            return

        if not event.buttons():
            _, action = self._hit_test(QPointF(event.pos()))
            cursors = {"resize": Qt.SizeFDiagCursor,
                       "move":   Qt.SizeAllCursor}
            self.setCursor(cursors.get(action, Qt.ArrowCursor))
            return

        if self._drag_action and self._active_tool_id is not None:
            if self.recipe is None:
                return
            tool = self.recipe.get_tool(self._active_tool_id)
            if tool is None:
                return

            cx, cy, s = self._render_params()
            srect = self._screen_rect_of(self._active_tool_id)
            pos   = QPointF(event.pos())

            if self._drag_action == "move":
                new_tl  = pos - self._drag_offset
                srect.moveTo(new_tl)
            elif self._drag_action == "resize":
                new_w   = max(20.0, pos.x() - srect.x())
                new_h   = max(20.0, pos.y() - srect.y())
                srect.setSize(QSizeF(new_w, new_h))

            roi = self._screen_to_roi(srect)
            tool.search_roi = (roi.x(), roi.y(), roi.width(), roi.height())
            self.sig_roi_changed.emit(
                tool.tool_id,
                roi.x(), roi.y(), roi.width(), roi.height())
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
        if event.button() == Qt.LeftButton:
            if self._osd_drag_target:
                self._osd_drag_target = None
                self.setCursor(Qt.ArrowCursor)
        self._drag_action = None

    # ─── 렌더링 ──────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), C_BG)

        if self._image.isNull():
            self._draw_placeholder(p)
            return

        cx, cy, s = self._render_params()
        iw, ih    = self._iw(), self._ih()
        p.drawImage(QRectF(cx, cy, iw * s, ih * s), self._image)

        if self._mode == self.MODE_TEACH:
            self._draw_teach(p, cx, cy, s)
        elif self._mode in (self.MODE_TEST, self.MODE_LIVE):
            self._draw_live_overlay(p, cx, cy, s)
        elif self._mode == self.MODE_RUN:
            self._draw_live_overlay(p, cx, cy, s)
            self._draw_osd(p)

    def _draw_placeholder(self, p: QPainter):
        p.setPen(QColor("#334155"))
        p.setFont(QFont("Segoe UI", 16))
        p.drawText(self.rect(), Qt.AlignCenter, "장치에 연결하거나 이미지 폴더를 선택하세요")

    def _fixture_transform(self):
        """RecipeTree.get_fixture_transform() 래퍼. 없으면 None."""
        if self.recipe is None:
            return None
        try:
            return self.recipe.get_fixture_transform()
        except Exception:
            return None

    def _roi_to_screen_fixtured(self, roi: QRectF, tf, cx, cy, s) -> QRectF:
        """P4-03: Fixture 변환 적용 후 화면 좌표. tf=None 이면 그대로."""
        if tf is None or tf.isIdentity():
            return self._roi_to_screen(roi, cx, cy, s)
        center = QPointF(roi.x() + roi.width() / 2.0,
                         roi.y() + roi.height() / 2.0)
        center_t = tf.map(center)
        return self._roi_to_screen(
            QRectF(center_t.x() - roi.width() / 2.0,
                   center_t.y() - roi.height() / 2.0,
                   roi.width(), roi.height()), cx, cy, s)

    def _draw_teach(self, p: QPainter, cx, cy, s):
        if self.recipe is None:
            return
        tf = self._fixture_transform()
        for tool in self.recipe.tool_index.values():
            if not is_physical_tool(tool):
                continue
            is_anchor = isinstance(tool, HyLocator)
            is_active = (tool.tool_id == self._active_tool_id)
            color     = C_ANCHOR if is_anchor else C_TOOL

            # P4-03: anchor 보유 시 fixture 변환 적용 (앵커 자체 제외)
            use_tf = tf if (tool.use_anchor and not is_anchor) else None
            srect = self._roi_to_screen_fixtured(QRectF(*tool.search_roi), use_tf, cx, cy, s)

            # ROI 사각형
            pen = QPen(color, 2 if is_active else 1,
                       Qt.SolidLine if is_active else Qt.DashLine)
            p.setPen(pen)
            alpha = 50 if is_active else 0
            p.setBrush(QColor(color.red(), color.green(), color.blue(), alpha))
            p.drawRect(srect)

            # 이름 라벨
            p.setPen(color)
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(srect.topLeft() + QPointF(4, -4), tool.name)

            # 활성 도구: 리사이즈 핸들
            if is_active:
                corner = QRectF(srect.right() - 5, srect.bottom() - 5, 10, 10)
                p.setBrush(color)
                p.setPen(Qt.NoPen)
                p.drawRect(corner)

            # 결과 십자선 (미리보기)
            if self.show_result_cross and tool.rst_done == HyProtocol.EXEC_DONE:
                self._draw_cross(p, cx + tool.x * s, cy + tool.y * s,
                                 JUDGE_COLOR.get(tool.rst_state, C_TOOL))

    def _draw_live_overlay(self, p: QPainter, cx, cy, s):
        if self.recipe is None or not self.show_roi_boxes:
            return
        tf = self._fixture_transform()
        for tool in self.recipe.tool_index.values():
            if not is_physical_tool(tool):
                continue
            is_anchor = isinstance(tool, HyLocator)
            color = C_ANCHOR if is_anchor else C_TOOL

            # P4-03: Fixture 적용 ROI
            use_tf = tf if (tool.use_anchor and not is_anchor) else None
            srect = self._roi_to_screen_fixtured(QRectF(*tool.search_roi), use_tf, cx, cy, s)
            p.setPen(QPen(color, 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(srect)

            # 결과 십자선
            if self.show_result_cross and tool.rst_done == HyProtocol.EXEC_DONE:
                jc = JUDGE_COLOR.get(tool.rst_state, C_TOOL)
                self._draw_cross(p, cx + tool.x * s, cy + tool.y * s, jc)
                # 각도 텍스트
                p.setPen(jc)
                p.setFont(QFont("Consolas", 8))
                p.drawText(QPointF(cx + tool.x * s + 8, cy + tool.y * s - 4),
                           f"{tool.angle:.1f}°")

    def _osd_status_rect(self) -> QRectF | None:
        """Status Box 현재 위치 QRectF. recipe/fin 없으면 None."""
        if self.recipe is None or not self.recipe.get_fin_tools():
            return None
        tl = self._osd_status_pos or QPointF(self.width() - 230, 10)
        return QRectF(tl, QSizeF(220, 90))

    def _osd_spec_rect(self) -> QRectF | None:
        """Spec Box 현재 위치 QRectF. tools 없으면 None."""
        if self.recipe is None:
            return None
        tools = self.recipe.get_physical_tools()
        if not tools:
            return None
        box_h = 16 * len(tools) + 16
        tl = self._osd_spec_pos or QPointF(10, self.height() - box_h - 10)
        return QRectF(tl, QSizeF(300, box_h))

    def _draw_osd(self, p: QPainter):
        if self.recipe is None:
            return
        fins = self.recipe.get_fin_tools()
        if not fins:
            return

        # P4-04: Status Box — 드래그 가능 (좌상단 고정 기준)
        if self.show_status_box:
            fin   = fins[0]
            state = fin.rst_state
            color = JUDGE_COLOR.get(state, C_NG)
            text  = {HyProtocol.JUDGE_OK: "OK",
                     HyProtocol.JUDGE_NG: "NG",
                     HyProtocol.JUDGE_PENDING: "..."}.get(state, "?")

            text_rect = self._osd_status_rect() or QRectF(self.width() - 230, 10, 220, 90)
            p.setFont(QFont("Segoe UI", 64, QFont.Bold))
            p.setPen(QPen(color, 2))
            p.setBrush(QColor(0, 0, 0, 160))
            p.drawRoundedRect(text_rect, 8, 8)
            p.setPen(color)
            p.drawText(text_rect, Qt.AlignCenter, text)

        # P4-04: Spec Box — 드래그 가능
        if self.show_spec_box:
            tools = self.recipe.get_physical_tools()
            if tools:
                lines = []
                for t in tools:
                    st = {HyProtocol.JUDGE_OK: "OK",
                          HyProtocol.JUDGE_NG: "NG",
                          HyProtocol.JUDGE_PENDING: "..."}.get(t.rst_state, "?")
                    lines.append(
                        f"{t.name[:12]:12s}  {st}  ({t.x:.1f},{t.y:.1f})  {t.angle:.1f}°")
                box_rect = self._osd_spec_rect() or QRectF(
                    10, self.height() - len(lines) * 16 - 26, 300,
                    16 * len(lines) + 16)
                p.setBrush(QColor(0, 0, 0, 160))
                p.setPen(Qt.NoPen)
                p.drawRoundedRect(box_rect, 6, 6)
                p.setFont(QFont("Consolas", 9))
                for i, line in enumerate(lines):
                    p.setPen(C_OK)
                    p.drawText(QPointF(box_rect.x() + 8,
                                       box_rect.y() + 14 + i * 16), line)

    # ─── 내부 헬퍼 ──────────────────────────────────────────────────────────

    def _iw(self) -> float:
        return float(self._image.width()  or 640)

    def _ih(self) -> float:
        return float(self._image.height() or 480)

    def _render_params(self):
        """(cx, cy, scale) — 이미지 좌상단 화면 좌표 + 스케일."""
        s  = min(self.width() / self._iw(), self.height() / self._ih()) * self._zoom
        cx = (self.width()  - self._iw() * s) / 2 + self._pan.x()
        cy = (self.height() - self._ih() * s) / 2 + self._pan.y()
        return cx, cy, s

    def _roi_to_screen(self, roi: QRectF, cx, cy, s) -> QRectF:
        return QRectF(cx + roi.x() * s, cy + roi.y() * s,
                      roi.width() * s,  roi.height() * s)

    def _screen_to_roi(self, srect: QRectF) -> QRectF:
        cx, cy, s = self._render_params()
        return QRectF((srect.x() - cx) / s, (srect.y() - cy) / s,
                      srect.width() / s,     srect.height() / s)

    def _screen_rect_of(self, tool_id: int) -> QRectF:
        cx, cy, s = self._render_params()
        tool = self.recipe.get_tool(tool_id) if self.recipe else None
        if tool is None:
            return QRectF()
        return self._roi_to_screen(QRectF(*tool.search_roi), cx, cy, s)

    def _corner_rect(self, srect: QRectF) -> QRectF:
        return QRectF(srect.right() - 5, srect.bottom() - 5, 10, 10)

    def _hit_test(self, pos: QPointF):
        """TEACH 모드 히트 테스트. (tool_id, action) | (None, None)."""
        if self.recipe is None:
            return None, None

        def _check(tool):
            if not is_physical_tool(tool):
                return None, None
            srect = self._screen_rect_of(tool.tool_id)
            if self._corner_rect(srect).contains(pos):
                return tool.tool_id, "resize"
            if srect.contains(pos):
                return tool.tool_id, "move"
            return None, None

        # 활성 툴 우선
        if self._active_tool_id and self.recipe.get_tool(self._active_tool_id):
            tid, act = _check(self.recipe.get_tool(self._active_tool_id))
            if tid is not None:
                return tid, act

        for tool in self.recipe.tool_index.values():
            tid, act = _check(tool)
            if tid is not None:
                return tid, act

        return None, None

    def _draw_cross(self, p: QPainter, x: float, y: float, color: QColor,
                    size: float = 8.0):
        p.setPen(QPen(color, 1.5))
        p.drawLine(QPointF(x - size, y), QPointF(x + size, y))
        p.drawLine(QPointF(x, y - size), QPointF(x, y + size))
        p.drawEllipse(QPointF(x, y), 3.0, 3.0)
