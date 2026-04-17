"""
OverlayPanel.py - 설정 패널 오버레이 위젯 (v3.0)

P4-13: 드래그 핸들, 투명도 슬라이더, 핀 토글, QSettings 위치/투명도 저장
P4-14: 툴별 동적 폼 팩토리 (HyLine/PatMat/Locator/Distance/Contrast/FND/When/Fin/And/Or)
P4-15: OverlayPanelManager — 최대 4개 동시 패널, 핀 해제 시 내용 교체
P4-16: HyFND 3-탭 위저드 (기본 설정 / 기하 보정 / 판정 설정)
P4-17: HyFin I/O 탭 (io_mapping 테이블 — 판정값 → 핀 번호)
"""
from __future__ import annotations

from PyQt5.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QWidget,
    QPushButton, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QSlider, QScrollArea, QSizePolicy, QGroupBox, QLineEdit,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QGraphicsOpacityEffect, QSizePolicy,
)
from PyQt5.QtCore import Qt, pyqtSignal, QPoint, QSettings, QObject
from PyQt5.QtGui import QFont, QCursor

from HyProtocol import HyProtocol
from HyVisionTools import (
    HyTool, HyLine, HyPatMat, HyLinePatMat, HyLocator,
    HyDistance, HyContrast, HyFND,
    HyWhen, HyAnd, HyOr, HyFin,
)

_SETTINGS_ORG = "HyVision"
_SETTINGS_APP = "InspectorApp"

# ─── 공통 스타일 ──────────────────────────────────────────────────────────────
PANEL_QSS = """
QFrame#OverlayPanel {
    background-color: rgba(15,23,42,220);
    border: 1px solid #334155;
    border-radius: 8px;
}
QWidget { background: transparent; color: #e2e8f0; }
QLabel  { color: #94a3b8; font-size: 11px; font-weight: bold; border: none; }
QLabel.value { color: #e2e8f0; font-size: 12px; }
QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit {
    background: #0f172a; border: 1px solid #334155;
    padding: 4px; border-radius: 4px; color: #e2e8f0; font-size: 11px;
}
QSlider::groove:horizontal {
    border: 1px solid #334155; height: 5px;
    background: #1e293b; border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #38bdf8; width: 12px; margin: -4px 0; border-radius: 6px;
}
QSlider::sub-page:horizontal { background: #0ea5e9; border-radius: 2px; }
QCheckBox { font-size: 11px; font-weight: bold; }
QCheckBox::indicator {
    width: 14px; height: 14px;
    border-radius: 3px; border: 1px solid #475569; background: #0f172a;
}
QCheckBox::indicator:checked { background: #38bdf8; border-color: #0ea5e9; }
QPushButton {
    background: #1e293b; border: 1px solid #475569;
    border-radius: 4px; padding: 5px 10px; color: #e2e8f0;
}
QPushButton:hover { background: #334155; border-color: #38bdf8; }
QTabWidget::pane {
    border: 1px solid #334155; border-radius: 4px; background: transparent;
}
QTabBar::tab {
    background: #1e293b; color: #94a3b8;
    padding: 4px 10px; border: 1px solid #334155;
    border-bottom: none; border-radius: 4px 4px 0 0; font-size: 10px;
}
QTabBar::tab:selected { background: #0f172a; color: #e2e8f0; }
QTableWidget {
    background: #0f172a; gridline-color: #1e293b;
    border: 1px solid #334155; color: #e2e8f0; font-size: 11px;
}
QHeaderView::section {
    background: #1e293b; color: #94a3b8;
    border: 1px solid #334155; padding: 3px; font-size: 10px;
}
"""

_JUDGE_LABELS = {
    HyProtocol.JUDGE_NG:      "NG",
    HyProtocol.JUDGE_OK:      "OK",
    HyProtocol.JUDGE_PENDING: "PENDING",
}


def _lbl(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(QFont("Segoe UI", 9, QFont.Bold))
    return lbl


def _sep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("QFrame{color:#1e293b;}")
    return f


# ─────────────────────────────────────────────────────────────────────────────
# OverlayPanel
# ─────────────────────────────────────────────────────────────────────────────

class OverlayPanel(QFrame):
    """
    반투명 플로팅 툴 설정 패널 (v3.0).

    공개 API
    --------
    show_tool(tool)   — 해당 툴의 파라미터 폼을 렌더링하고 패널 표시.
    hide_panel()      — 패널 숨기기 (위치 QSettings 저장).
    current_tool      — 현재 표시 중인 툴 (읽기전용).
    pinned            — 핀 상태 bool (읽기전용).

    내부 슬롯 인덱스 (slot_idx) 는 OverlayPanelManager 가 할당.
    """

    sig_updated = pyqtSignal()
    sig_closed  = pyqtSignal()

    # 헤더 드래그 감지 높이 (px)
    _HDR_H = 38

    def __init__(self, parent=None, slot_idx: int = 0):
        super().__init__(parent)
        self.setObjectName("OverlayPanel")
        self.setStyleSheet(PANEL_QSS)
        self.setFixedWidth(300)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Minimum)
        self.setVisible(False)

        self._slot_idx: int       = slot_idx
        self._current_tool: HyTool | None = None
        self._loading:  bool      = False
        self._pinned:   bool      = False
        self._drag_pos: QPoint | None = None

        self._build_ui()
        self._restore_settings()

    # ─────────────────────────────────────────────────────────────────────────
    # UI 구성
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 10)
        root.setSpacing(6)

        # ── 헤더 ─────────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(4)

        # 드래그 핸들 힌트
        drag_lbl = QLabel("⠿")
        drag_lbl.setFixedWidth(14)
        drag_lbl.setStyleSheet("color:#475569;font-size:14px;")
        drag_lbl.setCursor(QCursor(Qt.SizeAllCursor))
        hdr.addWidget(drag_lbl)

        # 제목
        self._lbl_title = QLabel("Tool Settings")
        self._lbl_title.setFont(QFont("Segoe UI", 11, QFont.Bold))
        self._lbl_title.setStyleSheet("color:#e2e8f0;")
        hdr.addWidget(self._lbl_title, 1)

        # 투명도 슬라이더 (α)
        alpha_lbl = QLabel("α")
        alpha_lbl.setFixedWidth(10)
        alpha_lbl.setStyleSheet("color:#475569;font-size:10px;")
        self._sld_opacity = QSlider(Qt.Horizontal)
        self._sld_opacity.setRange(30, 100)
        self._sld_opacity.setValue(90)
        self._sld_opacity.setFixedWidth(58)
        self._sld_opacity.setToolTip("패널 투명도 (30~100%)")
        self._sld_opacity.valueChanged.connect(self._on_opacity_changed)
        hdr.addWidget(alpha_lbl)
        hdr.addWidget(self._sld_opacity)

        # 핀 버튼
        self._btn_pin = QPushButton("📌")
        self._btn_pin.setFixedSize(24, 24)
        self._btn_pin.setCheckable(True)
        self._btn_pin.setToolTip("패널 고정 (핀 ON = 다른 툴 선택 시 자동 닫힘 방지)")
        self._btn_pin.setStyleSheet(
            "QPushButton{background:transparent;border:none;color:#475569;font-size:13px;}"
            "QPushButton:hover{color:#f59e0b;}"
            "QPushButton:checked{color:#f59e0b;background:rgba(245,158,11,30);border-radius:4px;}"
        )
        self._btn_pin.toggled.connect(self._on_pin_toggled)
        hdr.addWidget(self._btn_pin)

        # 닫기 버튼
        btn_close = QPushButton("✕")
        btn_close.setFixedSize(24, 24)
        btn_close.setStyleSheet(
            "QPushButton{background:transparent;border:none;color:#94a3b8;font-size:14px;}"
            "QPushButton:hover{color:#ef4444;}"
        )
        btn_close.clicked.connect(self._on_close)
        hdr.addWidget(btn_close)

        root.addLayout(hdr)
        root.addWidget(_sep())

        # ── 스크롤 컨텐트 ────────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setSpacing(8)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(self._content)
        root.addWidget(scroll, 1)

    # ─────────────────────────────────────────────────────────────────────────
    # 공개 API
    # ─────────────────────────────────────────────────────────────────────────

    def show_tool(self, tool: HyTool):
        """툴에 맞는 파라미터 폼을 렌더링하고 패널 표시."""
        self._current_tool = tool
        self._lbl_title.setText(f"{tool.name}  [id={tool.tool_id}]")
        self._rebuild_content(tool)
        self.setVisible(True)
        self.adjustSize()
        self.raise_()

    def hide_panel(self):
        """패널 숨기기. 현재 위치/투명도를 QSettings 에 저장."""
        self._save_settings()
        self.setVisible(False)
        self._current_tool = None

    @property
    def current_tool(self) -> HyTool | None:
        return self._current_tool

    @property
    def pinned(self) -> bool:
        return self._pinned

    # ─────────────────────────────────────────────────────────────────────────
    # 드래그 (P4-13)
    # ─────────────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.y() <= self._HDR_H:
            # 글로벌 좌표에서 위젯 좌상단 뺀 오프셋 저장
            self._drag_pos = event.globalPos() - self.mapToGlobal(QPoint(0, 0))
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            new_global = event.globalPos() - self._drag_pos
            if self.parent():
                new_local = self.parent().mapFromGlobal(new_global)
                # 부모 경계 내로 클램프
                pw, ph = self.parent().width(), self.parent().height()
                nx = max(0, min(new_local.x(), pw - self.width()))
                ny = max(0, min(new_local.y(), ph - self.height()))
                self.move(nx, ny)
            else:
                self.move(new_global)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag_pos is not None:
            self._drag_pos = None
            self._save_settings()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    # ─────────────────────────────────────────────────────────────────────────
    # 핀 / 투명도
    # ─────────────────────────────────────────────────────────────────────────

    def _on_pin_toggled(self, checked: bool):
        self._pinned = checked

    def _on_opacity_changed(self, value: int):
        eff = QGraphicsOpacityEffect(self)
        eff.setOpacity(value / 100.0)
        self.setGraphicsEffect(eff)

    # ─────────────────────────────────────────────────────────────────────────
    # QSettings 저장/복원 (P4-13)
    # ─────────────────────────────────────────────────────────────────────────

    def _settings_prefix(self) -> str:
        return f"OverlayPanel/slot{self._slot_idx}"

    def _save_settings(self):
        s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        pfx = self._settings_prefix()
        s.setValue(f"{pfx}/pos",     self.pos())
        s.setValue(f"{pfx}/opacity", self._sld_opacity.value())
        s.setValue(f"{pfx}/pinned",  self._pinned)

    def _restore_settings(self):
        s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        pfx = self._settings_prefix()
        pos = s.value(f"{pfx}/pos")
        if pos is not None:
            self.move(pos)
        opacity = s.value(f"{pfx}/opacity", 90, type=int)
        self._sld_opacity.setValue(opacity)
        # opacity effect 즉시 적용
        self._on_opacity_changed(opacity)
        pinned = s.value(f"{pfx}/pinned", False, type=bool)
        self._btn_pin.setChecked(pinned)

    # ─────────────────────────────────────────────────────────────────────────
    # 컨텐트 재구성 (P4-14: 동적 폼 팩토리)
    # ─────────────────────────────────────────────────────────────────────────

    def _rebuild_content(self, tool: HyTool):
        """기존 컨텐트를 비우고 툴 타입에 맞게 재구성."""
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._loading = True
        try:
            if isinstance(tool, HyLinePatMat):
                self._build_hylinepatmat(tool)
            elif isinstance(tool, HyLine):
                self._build_hyline(tool)
            elif isinstance(tool, HyPatMat):
                self._build_hypatmat(tool)
            elif isinstance(tool, HyLocator):
                self._build_hylocator(tool)
            elif isinstance(tool, HyDistance):
                self._build_hydistance(tool)
            elif isinstance(tool, HyContrast):
                self._build_hycontrast(tool)
            elif isinstance(tool, HyFND):
                self._build_hyfnd(tool)
            elif isinstance(tool, HyWhen):
                self._build_hywhen(tool)
            elif isinstance(tool, HyFin):
                self._build_hyfin(tool)
            elif isinstance(tool, (HyAnd, HyOr)):
                self._build_logic_simple(tool)
            else:
                self._build_generic(tool)

            # ROI 공통 섹션
            self._content_layout.addWidget(_sep())
            self._build_roi_section(tool)
            self._content_layout.addStretch()
        finally:
            self._loading = False

    # ─────────────────────────────────────────────────────────────────────────
    # 툴별 파라미터 폼
    # ─────────────────────────────────────────────────────────────────────────

    def _build_hyline(self, tool: HyLine):
        g = self._group("HyLine 파라미터")
        vb = QVBoxLayout(g)

        row, sb = self._spin_row("분할 수 (num_splits):", 2, 20, tool.num_splits)
        sb.valueChanged.connect(lambda v: (setattr(tool, 'num_splits', v), self._emit()))
        vb.addLayout(row)

        row, dsb = self._dspin_row("임계 비율 (cut_ratio):", 0.1, 1.0, tool.cut_ratio, 0.05)
        dsb.valueChanged.connect(lambda v: (setattr(tool, 'cut_ratio', v), self._emit()))
        vb.addLayout(row)

        row, cmb = self._combo_row("스캔 방향:", ["상→하", "하→상"], tool.scan_dir)
        cmb.currentIndexChanged.connect(lambda i: (setattr(tool, 'scan_dir', i), self._emit()))
        vb.addLayout(row)

        row, cmb2 = self._combo_row("피크 위치:", ["중앙", "시작단", "끝단"], tool.peak_mode)
        cmb2.currentIndexChanged.connect(lambda i: (setattr(tool, 'peak_mode', i), self._emit()))
        vb.addLayout(row)

        chk = QCheckBox("중간점 검증 (mid_check)")
        chk.setChecked(tool.mid_check)
        chk.toggled.connect(lambda v: (setattr(tool, 'mid_check', v), self._emit()))
        vb.addWidget(chk)

        row, dsb2 = self._dspin_row("중간점 임계비 (mid_ratio):", 0.1, 1.0, tool.mid_ratio, 0.05)
        dsb2.valueChanged.connect(lambda v: (setattr(tool, 'mid_ratio', v), self._emit()))
        vb.addLayout(row)

        self._content_layout.addWidget(g)

    def _build_hypatmat(self, tool: HyPatMat):
        g = self._group("HyPatMat 파라미터")
        vb = QVBoxLayout(g)
        row, dsb = self._dspin_row("탐색 임계값 (th_scan):", 0.0, 1.0, tool.th_scan, 0.05)
        dsb.valueChanged.connect(lambda v: (setattr(tool, 'th_scan', v), self._emit()))
        vb.addLayout(row)
        row2, dsb2 = self._dspin_row("매칭 임계값 (th_find):", 0.0, 1.0, tool.th_find, 0.05)
        dsb2.valueChanged.connect(lambda v: (setattr(tool, 'th_find', v), self._emit()))
        vb.addLayout(row2)
        note = QLabel("※ 템플릿은 캔버스에서 우클릭 → Set Template")
        note.setWordWrap(True)
        vb.addWidget(note)
        self._content_layout.addWidget(g)

    def _build_hylinepatmat(self, tool: HyLinePatMat):
        g = self._group("HyLinePatMat 파라미터")
        vb = QVBoxLayout(g)
        row, dsb = self._dspin_row("매칭 임계값 (th_find):", 0.0, 1.0, tool.th_find, 0.05)
        dsb.valueChanged.connect(lambda v: (setattr(tool, 'th_find', v), self._emit()))
        vb.addLayout(row)
        row2, sb = self._spin_row("라인 분할 수 (num_splits):", 2, 20, tool.num_splits)
        sb.valueChanged.connect(lambda v: (setattr(tool, 'num_splits', v), self._emit()))
        vb.addLayout(row2)
        row3, dsb2 = self._dspin_row("컷 비율 (cut_ratio):", 0.1, 0.99, tool.cut_ratio, 0.05)
        dsb2.valueChanged.connect(lambda v: (setattr(tool, 'cut_ratio', v), self._emit()))
        vb.addLayout(row3)
        note = QLabel("※ 템플릿은 캔버스에서 우클릭 → Set Template")
        note.setWordWrap(True)
        vb.addWidget(note)
        self._content_layout.addWidget(g)

    def _build_hylocator(self, tool: HyLocator):
        g = self._group("HyLocator 파라미터")
        vb = QVBoxLayout(g)
        row, cmb = self._combo_row("갱신 정책:", ["Continuous", "CycleLock"], tool.update_policy)
        cmb.currentIndexChanged.connect(lambda i: (setattr(tool, 'update_policy', i), self._emit()))
        vb.addLayout(row)
        note = QLabel("※ 앵커는 시스템에 1개만 존재합니다.")
        note.setWordWrap(True)
        vb.addWidget(note)
        self._content_layout.addWidget(g)

    def _build_hydistance(self, tool: HyDistance):
        g = self._group("HyDistance 파라미터")
        vb = QVBoxLayout(g)
        axes = ["perpendicular", "horizontal", "vertical"]
        idx  = axes.index(tool.projection_axis) if tool.projection_axis in axes else 0
        row, cmb = self._combo_row("투영 축:", axes, idx)
        cmb.currentIndexChanged.connect(lambda i: (setattr(tool, 'projection_axis', axes[i]), self._emit()))
        vb.addLayout(row)
        row2, dsb = self._dspin_row("px→mm 계수:", 0.0, 100.0, tool.px_to_mm, 0.01)
        dsb.valueChanged.connect(lambda v: (setattr(tool, 'px_to_mm', v), self._emit()))
        vb.addLayout(row2)
        row3, dsb_min = self._dspin_row("거리 최솟값 (0=무제한):", 0.0, 9999.0, tool.dist_min, 1.0)
        dsb_min.valueChanged.connect(lambda v: (setattr(tool, 'dist_min', v), self._emit()))
        vb.addLayout(row3)
        row4, dsb_max = self._dspin_row("거리 최댓값 (0=무제한):", 0.0, 9999.0, tool.dist_max, 1.0)
        dsb_max.valueChanged.connect(lambda v: (setattr(tool, 'dist_max', v), self._emit()))
        vb.addLayout(row4)
        row5, dsb_ang = self._dspin_row("최대 각도 편차 (0=무제한):", 0.0, 180.0, tool.angle_max, 1.0)
        dsb_ang.valueChanged.connect(lambda v: (setattr(tool, 'angle_max', v), self._emit()))
        vb.addLayout(row5)
        self._content_layout.addWidget(g)

    def _build_hycontrast(self, tool: HyContrast):
        g = self._group("HyContrast 파라미터")
        vb = QVBoxLayout(g)
        row, dsb_min = self._dspin_row("평균 최솟값:", 0.0, 255.0, tool.mean_range[0], 1.0)
        dsb_min.valueChanged.connect(
            lambda v: (setattr(tool, 'mean_range', (v, tool.mean_range[1])), self._emit()))
        vb.addLayout(row)
        row2, dsb_max = self._dspin_row("평균 최댓값:", 0.0, 255.0, tool.mean_range[1], 1.0)
        dsb_max.valueChanged.connect(
            lambda v: (setattr(tool, 'mean_range', (tool.mean_range[0], v)), self._emit()))
        vb.addLayout(row2)
        row3, dsb_std = self._dspin_row("편차 최댓값 (stdev_max):", 0.0, 255.0, tool.stdev_max, 1.0)
        dsb_std.valueChanged.connect(lambda v: (setattr(tool, 'stdev_max', v), self._emit()))
        vb.addLayout(row3)
        self._content_layout.addWidget(g)

    # ── P4-16: HyFND 3-탭 위저드 ─────────────────────────────────────────────

    def _build_hyfnd(self, tool: HyFND):
        """HyFND 3-탭 위저드: 기본 설정 / 기하 보정 / 판정 설정."""
        tabs = QTabWidget()

        # ── 탭 1: 기본 설정 ──────────────────────────────────────────────────
        t1 = QWidget()
        vb1 = QVBoxLayout(t1)
        vb1.setSpacing(6)

        row, sb = self._spin_row("자릿수 (num_digits):", 1, 16, tool.num_digits)
        sb.valueChanged.connect(lambda v: (setattr(tool, 'num_digits', v), self._emit()))
        vb1.addLayout(row)

        row2, sb2 = self._spin_row("세그먼트 임계값 (threshold):", 1, 255, tool.threshold)
        sb2.valueChanged.connect(lambda v: (setattr(tool, 'threshold', v), self._emit()))
        vb1.addLayout(row2)

        note1 = QLabel("※ threshold: 세그먼트 on/off 판별 밝기 기준.")
        note1.setWordWrap(True)
        vb1.addWidget(note1)
        vb1.addStretch()
        tabs.addTab(t1, "기본")

        # ── 탭 2: 기하 보정 ──────────────────────────────────────────────────
        t2 = QWidget()
        vb2 = QVBoxLayout(t2)
        vb2.setSpacing(6)

        row3, dsb_skew = self._dspin_row("기울기 보정 각도 (°):", -45.0, 45.0,
                                          tool.skew_angle, 0.5)
        dsb_skew.valueChanged.connect(lambda v: (setattr(tool, 'skew_angle', v), self._emit()))
        vb2.addLayout(row3)

        note2 = QLabel(
            "※ 디스플레이가 기울어져 있을 때 각도를 보정합니다.\n"
            "양수(+) = 시계방향, 음수(-) = 반시계방향."
        )
        note2.setWordWrap(True)
        vb2.addWidget(note2)
        vb2.addStretch()
        tabs.addTab(t2, "기하")

        # ── 탭 3: 판정 설정 ──────────────────────────────────────────────────
        t3 = QWidget()
        vb3 = QVBoxLayout(t3)
        vb3.setSpacing(6)

        row4, cmb_mode = self._combo_row(
            "판정 모드:", ["equal (일치)", "range (범위)"],
            0 if tool.judge_mode == "equal" else 1,
        )
        cmb_mode.currentIndexChanged.connect(
            lambda i: (setattr(tool, 'judge_mode', "equal" if i == 0 else "range"),
                       self._emit()))
        vb3.addLayout(row4)

        lbl_tgt = _lbl("목표값 (equal 모드):")
        le_tgt = QLineEdit(str(tool.target_value))
        le_tgt.textChanged.connect(lambda t: (setattr(tool, 'target_value', t), self._emit()))
        vb3.addWidget(lbl_tgt)
        vb3.addWidget(le_tgt)

        row5, dsb_rmin = self._dspin_row("범위 최솟값 (range 모드):", -999999.0, 999999.0,
                                          getattr(tool, 'range_min', 0.0), 1.0)
        dsb_rmin.valueChanged.connect(
            lambda v: (setattr(tool, 'range_min', v), self._emit()))
        vb3.addLayout(row5)

        row6, dsb_rmax = self._dspin_row("범위 최댓값 (range 모드):", -999999.0, 999999.0,
                                          getattr(tool, 'range_max', 9999.0), 1.0)
        dsb_rmax.valueChanged.connect(
            lambda v: (setattr(tool, 'range_max', v), self._emit()))
        vb3.addLayout(row6)

        vb3.addStretch()
        tabs.addTab(t3, "판정")

        self._content_layout.addWidget(tabs)

    # ── P4-17: HyFin 탭 ──────────────────────────────────────────────────────

    def _build_hyfin(self, tool: HyFin):
        """HyFin 2-탭: 기본 설정 / I/O 출력 매핑."""
        tabs = QTabWidget()

        # ── 탭 1: 기본 설정 ──────────────────────────────────────────────────
        t1 = QWidget()
        vb1 = QVBoxLayout(t1)
        vb1.setSpacing(6)

        note = QLabel("※ HyFin: 사이클 종료자.\n하위 노드 AND 집계 → I/O 출력.")
        note.setWordWrap(True)
        vb1.addWidget(note)

        targets = ["status_box", "spec_box", "both"]
        cur_idx = targets.index(tool.broadcast_target) if tool.broadcast_target in targets else 0
        row, cmb = self._combo_row("브로드캐스트 타겟:", targets, cur_idx)
        cmb.currentIndexChanged.connect(
            lambda i: (setattr(tool, 'broadcast_target', targets[i]), self._emit()))
        vb1.addLayout(row)
        vb1.addStretch()
        tabs.addTab(t1, "기본")

        # ── 탭 2: I/O 출력 매핑 ──────────────────────────────────────────────
        t2 = QWidget()
        vb2 = QVBoxLayout(t2)
        vb2.setContentsMargins(4, 4, 4, 4)
        vb2.setSpacing(4)

        note2 = QLabel("판정값 → I/O 핀 매핑 (판정값: 0=NG, 1=OK, 2=PENDING)")
        note2.setWordWrap(True)
        vb2.addWidget(note2)

        # 테이블
        tbl = QTableWidget(0, 2)
        tbl.setHorizontalHeaderLabels(["판정값", "핀 번호"])
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        tbl.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked)
        tbl.setFixedHeight(120)

        # 기존 매핑 로드
        for judge_val, pin_num in tool.io_mapping.items():
            row_idx = tbl.rowCount()
            tbl.insertRow(row_idx)
            tbl.setItem(row_idx, 0, QTableWidgetItem(str(judge_val)))
            tbl.setItem(row_idx, 1, QTableWidgetItem(str(pin_num)))

        def _sync_io_mapping():
            mapping = {}
            for r in range(tbl.rowCount()):
                j_item = tbl.item(r, 0)
                p_item = tbl.item(r, 1)
                if j_item and p_item:
                    try:
                        mapping[int(j_item.text())] = int(p_item.text())
                    except ValueError:
                        pass
            tool.io_mapping = mapping
            self._emit()

        tbl.itemChanged.connect(lambda _: _sync_io_mapping())
        vb2.addWidget(tbl)

        # + / - 버튼
        btn_row = QHBoxLayout()
        btn_add = QPushButton("+ 행 추가")
        btn_add.setFixedHeight(24)
        btn_del = QPushButton("− 선택 삭제")
        btn_del.setFixedHeight(24)

        def _add_row():
            r = tbl.rowCount()
            tbl.insertRow(r)
            tbl.setItem(r, 0, QTableWidgetItem("0"))
            tbl.setItem(r, 1, QTableWidgetItem("0"))
            _sync_io_mapping()

        def _del_row():
            rows = sorted({idx.row() for idx in tbl.selectedIndexes()}, reverse=True)
            for r in rows:
                tbl.removeRow(r)
            _sync_io_mapping()

        btn_add.clicked.connect(_add_row)
        btn_del.clicked.connect(_del_row)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_del)
        vb2.addLayout(btn_row)
        vb2.addStretch()
        tabs.addTab(t2, "I/O 매핑")

        self._content_layout.addWidget(tabs)

    # ── 로직 노드 공통 ────────────────────────────────────────────────────────

    def _build_hywhen(self, tool: HyWhen):
        g = self._group("HyWhen 파라미터")
        vb = QVBoxLayout(g)
        row, sb = self._spin_row("감시 툴 ID (watch_tool_id):", 0, 999, tool.watch_tool_id)
        sb.valueChanged.connect(lambda v: (setattr(tool, 'watch_tool_id', v), self._emit()))
        vb.addLayout(row)
        row2, cmb = self._combo_row("조건 (condition):", ["NG 시 실행", "OK 시 실행"], tool.condition)
        cmb.currentIndexChanged.connect(lambda i: (setattr(tool, 'condition', i), self._emit()))
        vb.addLayout(row2)
        row3, sb3 = self._spin_row("지연 시간 (timeout_ms):", 0, 60000, tool.timeout_ms)
        sb3.valueChanged.connect(lambda v: (setattr(tool, 'timeout_ms', v), self._emit()))
        vb.addLayout(row3)
        row4, cmb4 = self._combo_row("출력 결정 (output_mode):", ["실행 여부", "자식 결과"],
                                     tool.output_mode)
        cmb4.currentIndexChanged.connect(lambda i: (setattr(tool, 'output_mode', i), self._emit()))
        vb.addLayout(row4)
        self._content_layout.addWidget(g)

    def _build_logic_simple(self, tool):
        g = self._group(f"{tool.name} 파라미터")
        vb = QVBoxLayout(g)
        desc = {
            "HyAnd": "순차 AND 집계. NG > PENDING > OK 우선순위.",
            "HyOr":  "순차 OR 집계. OK 발견 즉시 참 반환.",
        }.get(tool.__class__.__name__, "")
        lbl = QLabel(f"※ {desc}\n자식 노드를 트리 패널에서 드래그로 추가하세요.")
        lbl.setWordWrap(True)
        vb.addWidget(lbl)
        self._content_layout.addWidget(g)

    def _build_generic(self, tool: HyTool):
        g = self._group("기본 파라미터")
        vb = QVBoxLayout(g)
        vb.addWidget(_lbl(f"tool_type: {tool.tool_type:#04x}"))
        vb.addWidget(_lbl(f"device_id: {tool.device_id}"))
        self._content_layout.addWidget(g)

    def _build_roi_section(self, tool: HyTool):
        from HyVisionTools import is_physical_tool
        if not is_physical_tool(tool):
            return
        g = self._group("ROI (x, y, w, h)")
        grid = QHBoxLayout()
        x, y, w, h = tool.search_roi
        for lbl_txt, val in [("X", x), ("Y", y), ("W", w), ("H", h)]:
            sb = QSpinBox()
            sb.setRange(0, 4096)
            sb.setValue(int(val))
            sb.setPrefix(f"{lbl_txt}: ")
            idx = ["X", "Y", "W", "H"].index(lbl_txt)

            def _on_change(v, i=idx, t=tool):
                roi = list(t.search_roi)
                roi[i] = v
                t.search_roi = tuple(roi)
                self._emit()

            sb.valueChanged.connect(_on_change)
            grid.addWidget(sb)
        g.setLayout(grid)
        self._content_layout.addWidget(g)

    # ─────────────────────────────────────────────────────────────────────────
    # 위젯 팩토리 헬퍼
    # ─────────────────────────────────────────────────────────────────────────

    def _group(self, title: str) -> QGroupBox:
        g = QGroupBox(title)
        g.setStyleSheet(
            "QGroupBox{background:rgba(15,23,42,80);border:1px solid #1e293b;"
            "border-radius:6px;margin-top:12px;padding-top:18px;padding-bottom:4px;}"
            "QGroupBox::title{subcontrol-origin:margin;left:8px;top:6px;"
            "color:#94a3b8;font-size:10px;font-weight:bold;}"
        )
        return g

    def _spin_row(self, label: str, mn: int, mx: int, val: int):
        row = QHBoxLayout()
        row.addWidget(_lbl(label))
        sb = QSpinBox()
        sb.setRange(mn, mx)
        sb.setValue(val)
        row.addWidget(sb)
        return row, sb

    def _dspin_row(self, label: str, mn: float, mx: float, val: float, step: float = 0.1):
        row = QHBoxLayout()
        row.addWidget(_lbl(label))
        dsb = QDoubleSpinBox()
        dsb.setRange(mn, mx)
        dsb.setValue(val)
        dsb.setSingleStep(step)
        dsb.setDecimals(3)
        row.addWidget(dsb)
        return row, dsb

    def _combo_row(self, label: str, items: list, current: int = 0):
        row = QHBoxLayout()
        row.addWidget(_lbl(label))
        cmb = QComboBox()
        for it in items:
            cmb.addItem(str(it))
        if 0 <= current < len(items):
            cmb.setCurrentIndex(current)
        row.addWidget(cmb)
        return row, cmb

    # ─────────────────────────────────────────────────────────────────────────
    # 내부
    # ─────────────────────────────────────────────────────────────────────────

    def _emit(self):
        if not self._loading:
            self.sig_updated.emit()

    def _on_close(self):
        self.hide_panel()
        self.sig_closed.emit()


# ─────────────────────────────────────────────────────────────────────────────
# P4-15: OverlayPanelManager — 최대 4개 동시 패널 관리
# ─────────────────────────────────────────────────────────────────────────────

class OverlayPanelManager(QObject):
    """
    OverlayPanel 최대 4개 슬롯 관리.

    동작 규칙
    ---------
    1. 이미 해당 툴을 보여주는 패널이 있으면 raise_() 하고 반환.
    2. 핀 해제된(비표시 또는 unpinned) 슬롯 중 첫 번째를 재사용.
    3. 빈 슬롯이 없으면 슬롯 0 강제 교체.

    시그널
    ------
    sig_updated: 어떤 패널에서든 파라미터가 변경되면 방출.
    """

    sig_updated = pyqtSignal()
    MAX_PANELS  = 4

    # 패널 초기 배치 오프셋 (staggered)
    _SLOT_OFFSETS = [(10, 10), (32, 32), (54, 54), (76, 76)]

    def __init__(self, parent_widget):
        super().__init__(parent_widget)
        self._parent = parent_widget
        self._panels: list[OverlayPanel] = []
        for i in range(self.MAX_PANELS):
            p = OverlayPanel(parent_widget, slot_idx=i)
            p.sig_updated.connect(self.sig_updated)
            # 닫기 시 sig_updated 불필요 — 패널 자체가 처리
            ox, oy = self._SLOT_OFFSETS[i]
            p.move(ox, oy)
            self._panels.append(p)

    # ─────────────────────────────────────────────────────────────────────────
    # 공개 API
    # ─────────────────────────────────────────────────────────────────────────

    def show_tool(self, tool: HyTool) -> OverlayPanel:
        """툴에 알맞은 패널 슬롯을 찾아 show_tool() 호출 후 반환."""
        # 1. 이미 이 툴을 표시 중인 패널
        for p in self._panels:
            if p.isVisible() and p.current_tool is tool:
                p.raise_()
                return p

        # 2. 핀 해제되고 숨겨진 슬롯
        for p in self._panels:
            if not p.isVisible() and not p.pinned:
                p.show_tool(tool)
                return p

        # 3. 핀 해제되고 표시 중인 슬롯 (내용 교체)
        for p in self._panels:
            if p.isVisible() and not p.pinned:
                p.show_tool(tool)
                return p

        # 4. 모두 핀됨 → 슬롯 0 강제 교체
        self._panels[0].show_tool(tool)
        return self._panels[0]

    def hide_for_tool(self, tool_id: int):
        """특정 tool_id 를 표시 중인 핀 해제 패널 숨기기."""
        for p in self._panels:
            if (p.isVisible() and not p.pinned
                    and p.current_tool is not None
                    and p.current_tool.tool_id == tool_id):
                p.hide_panel()

    def hide_all_unpinned(self):
        """핀 해제된 모든 패널 숨기기."""
        for p in self._panels:
            if p.isVisible() and not p.pinned:
                p.hide_panel()

    @property
    def panels(self) -> list[OverlayPanel]:
        return list(self._panels)
