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

    # P4-02: 회전 핸들 설정
    _ROT_HANDLE_OFFSET = 28   # ROI 상단 중앙으로부터 위쪽 오프셋 (px)
    _ROT_HANDLE_RADIUS = 7    # 핸들 원 반지름 (px)

    # TEACH 모드에서 ROI 가 변경될 때 발행
    sig_roi_changed = pyqtSignal(int, float, float, float, float)   # tool_id, x,y,w,h
    # 툴 클릭됐을 때
    sig_tool_selected = pyqtSignal(int)   # tool_id  (-1 = 선택 해제)
    # P4-18: OSD 박스 더블클릭 → OSD 설정 패널 요청
    sig_osd_settings_requested = pyqtSignal(str)   # "status" | "spec"

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

        # TEACH 드래그 상태 (P4-01: Rotating 추가)
        self._drag_action = None   # None | "move" | "resize" | "rotate"
        self._drag_offset = QPointF()
        self._rotate_center_screen = QPointF()   # rotate 드래그 시 ROI 중심 화면 좌표

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

        # P4-24: RUN 모드 지표
        self._run_total = 0
        self._run_ok    = 0
        self._run_ng    = 0

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
        if mode != self.MODE_RUN:
            self._run_total = self._run_ok = self._run_ng = 0
        self.update()

    def set_run_metrics(self, total: int, ok: int, ng: int):
        """P4-24: RUN 모드 지표 갱신 → 다음 paintEvent 에서 반영."""
        self._run_total = total
        self._run_ok    = ok
        self._run_ng    = ng
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
            # P4-18: RUN 모드에서 OSD 박스 더블클릭 → 설정 패널 요청
            if self._mode == self.MODE_RUN:
                pos = QPointF(event.pos())
                sr   = self._osd_status_rect()
                specr = self._osd_spec_rect()
                if sr is not None and sr.contains(pos):
                    self.sig_osd_settings_requested.emit("status")
                    return
                if specr is not None and specr.contains(pos):
                    self.sig_osd_settings_requested.emit("spec")
                    return
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
                elif action == "rotate":
                    self._rotate_center_screen = srect.center()
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
                       "move":   Qt.SizeAllCursor,
                       "rotate": Qt.PointingHandCursor}
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
            elif self._drag_action == "rotate":
                # P4-02: 각도 = atan2(dy, dx) + 90° (상단=0°)
                dx = pos.x() - self._rotate_center_screen.x()
                dy = pos.y() - self._rotate_center_screen.y()
                angle = math.degrees(math.atan2(dy, dx)) + 90.0
                # -180..180 정규화
                while angle > 180.0:
                    angle -= 360.0
                while angle < -180.0:
                    angle += 360.0
                tool.rot_angle = angle
                roi = tool.search_roi
                self.sig_roi_changed.emit(
                    tool.tool_id, roi[0], roi[1], roi[2], roi[3])
                self.update()
                return

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
            angle     = getattr(tool, 'rot_angle', 0.0)

            # P4-03: anchor 보유 시 fixture 변환 적용 (앵커 자체 제외)
            use_tf = tf if (tool.use_anchor and not is_anchor) else None
            srect = self._roi_to_screen_fixtured(QRectF(*tool.search_roi), use_tf, cx, cy, s)

            # P4-02: ROI 사각형 (회전 적용)
            pen = QPen(color, 2 if is_active else 1,
                       Qt.SolidLine if is_active else Qt.DashLine)
            alpha = 50 if is_active else 0
            brush = QBrush(QColor(color.red(), color.green(), color.blue(), alpha))
            center = srect.center()
            if angle != 0.0:
                p.save()
                p.translate(center.x(), center.y())
                p.rotate(angle)
                p.translate(-center.x(), -center.y())
                p.setPen(pen)
                p.setBrush(brush)
                p.drawRect(srect)
                p.restore()
            else:
                p.setPen(pen)
                p.setBrush(brush)
                p.drawRect(srect)

            # 이름 라벨
            p.setPen(color)
            p.setFont(QFont("Segoe UI", 9))
            lbl_pos = self._rotate_point(
                srect.topLeft() + QPointF(4, -4), center, angle)
            p.drawText(lbl_pos, tool.name)

            # 활성 도구: 리사이즈 핸들 + 회전 핸들 (P4-02)
            if is_active:
                # 리사이즈 핸들 (우하단, 회전 적용)
                br_rot = self._rotate_point(srect.bottomRight(), center, angle)
                corner = QRectF(br_rot.x() - 5, br_rot.y() - 5, 10, 10)
                p.setBrush(color)
                p.setPen(Qt.NoPen)
                p.drawRect(corner)

                # 회전 핸들 연결선
                top_center_rot = self._rotate_point(
                    QPointF(center.x(), srect.top()), center, angle)
                rot_hp = self._rot_handle_screen(srect, angle)
                p.setPen(QPen(color, 1, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawLine(top_center_rot, rot_hp)

                # 회전 핸들 원
                p.setPen(QPen(C_SEL, 1.5))
                p.setBrush(QColor(color.red(), color.green(), color.blue(), 200))
                p.drawEllipse(rot_hp,
                              float(self._ROT_HANDLE_RADIUS),
                              float(self._ROT_HANDLE_RADIUS))

                # 각도 텍스트 (0°가 아닐 때만)
                if abs(angle) > 0.05:
                    p.setPen(color)
                    p.setFont(QFont("Consolas", 8))
                    p.drawText(rot_hp + QPointF(self._ROT_HANDLE_RADIUS + 3, 4),
                               f"{angle:.1f}°")

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

        # P4-24: RUN 모드 지표 (우하단)
        if self._mode == self.MODE_RUN and self._run_total > 0:
            ng_rate = self._run_ng / self._run_total * 100.0
            metrics = [
                f"TOTAL  {self._run_total:>6}",
                f"OK     {self._run_ok:>6}  ({100-ng_rate:5.1f}%)",
                f"NG     {self._run_ng:>6}  ({ng_rate:5.1f}%)",
            ]
            m_h = 16 * len(metrics) + 12
            m_w = 200
            m_rect = QRectF(self.width() - m_w - 10,
                            self.height() - m_h - 10, m_w, m_h)
            p.setBrush(QColor(0, 0, 0, 160))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(m_rect, 6, 6)
            p.setFont(QFont("Consolas", 9))
            for i, line in enumerate(metrics):
                color = C_OK if i == 1 else (C_NG if i == 2 else QColor("#94a3b8"))
                p.setPen(color)
                p.drawText(QPointF(m_rect.x() + 8, m_rect.y() + 14 + i * 16), line)

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

    def _corner_rect(self, srect: QRectF, angle_deg: float = 0.0) -> QRectF:
        """리사이즈 핸들 히트 박스 (우하단 꼭짓점 회전 후)."""
        br = (self._rotate_point(srect.bottomRight(), srect.center(), angle_deg)
              if angle_deg != 0.0 else srect.bottomRight())
        return QRectF(br.x() - 5, br.y() - 5, 10, 10)

    def _hit_test(self, pos: QPointF):
        """TEACH 모드 히트 테스트. (tool_id, action) | (None, None).
        우선순위: rotate > resize > move."""
        if self.recipe is None:
            return None, None

        def _check(tool, check_rotate: bool = False):
            if not is_physical_tool(tool):
                return None, None
            srect = self._screen_rect_of(tool.tool_id)
            angle = getattr(tool, 'rot_angle', 0.0)
            # P4-02: 회전 핸들 (활성 툴에서만)
            if check_rotate and self._rot_handle_rect(srect, angle).contains(pos):
                return tool.tool_id, "rotate"
            if self._corner_rect(srect, angle).contains(pos):
                return tool.tool_id, "resize"
            if srect.contains(pos):
                return tool.tool_id, "move"
            return None, None

        # 활성 툴 우선 (회전 핸들 포함)
        if self._active_tool_id:
            active = self.recipe.get_tool(self._active_tool_id)
            if active:
                tid, act = _check(active, check_rotate=True)
                if tid is not None:
                    return tid, act

        for tool in self.recipe.tool_index.values():
            tid, act = _check(tool)
            if tid is not None:
                return tid, act

        return None, None

    # ─── P4-02: 회전 핸들 헬퍼 ──────────────────────────────────────────────

    @staticmethod
    def _rotate_point(point: QPointF, center: QPointF, angle_deg: float) -> QPointF:
        """point 를 center 주위로 angle_deg (CW+) 만큼 회전."""
        if angle_deg == 0.0:
            return point
        rad = math.radians(angle_deg)
        cos_a, sin_a = math.cos(rad), math.sin(rad)
        dx, dy = point.x() - center.x(), point.y() - center.y()
        return QPointF(
            center.x() + dx * cos_a - dy * sin_a,
            center.y() + dx * sin_a + dy * cos_a,
        )

    def _rot_handle_screen(self, srect: QRectF, angle_deg: float) -> QPointF:
        """회전 핸들의 화면 좌표 (ROI 상단 중앙 + OFFSET 위쪽, 각도 회전 적용)."""
        center   = srect.center()
        unrot_hp = QPointF(center.x(), srect.top() - self._ROT_HANDLE_OFFSET)
        return self._rotate_point(unrot_hp, center, angle_deg)

    def _rot_handle_rect(self, srect: QRectF, angle_deg: float) -> QRectF:
        """회전 핸들 히트 박스 (반지름 R 의 정사각형 근사)."""
        hp = self._rot_handle_screen(srect, angle_deg)
        r  = float(self._ROT_HANDLE_RADIUS)
        return QRectF(hp.x() - r, hp.y() - r, r * 2, r * 2)

    def _draw_cross(self, p: QPainter, x: float, y: float, color: QColor,
                    size: float = 8.0):
        p.setPen(QPen(color, 1.5))
        p.drawLine(QPointF(x - size, y), QPointF(x + size, y))
        p.drawLine(QPointF(x, y - size), QPointF(x, y + size))
        p.drawEllipse(QPointF(x, y), 3.0, 3.0)
