"""
OverlayPanel.py - 설정 패널 오버레이 위젯 (v2.0)
VisionCanvas 위에 떠 있는 반투명 패널. 툴별 파라미터 편집 페이지를 스택으로 관리.
"""
from PyQt5.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QStackedWidget,
    QPushButton, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QSlider, QScrollArea, QWidget, QSizePolicy, QGroupBox, QLineEdit
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui  import QFont

from HyProtocol    import HyProtocol
from HyVisionTools import (HyTool, HyLine, HyPatMat, HyLinePatMat, HyLocator,
                            HyDistance, HyContrast, HyFND,
                            HyWhen, HyAnd, HyOr, HyFin)

# ─── 공통 스타일 ─────────────────────────────────────────────────────────────
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
"""


def _lbl(text: str) -> QLabel:
    l = QLabel(text)
    l.setFont(QFont("Segoe UI", 9, QFont.Bold))
    return l


def _sep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("QFrame{color:#1e293b;}")
    return f


class OverlayPanel(QFrame):
    """설정 오버레이 패널. show_tool(tool) 로 해당 툴의 파라미터 페이지를 표시."""

    sig_updated = pyqtSignal()     # 파라미터 변경 시 → UI Preview 트리거
    sig_closed  = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("OverlayPanel")
        self.setStyleSheet(PANEL_QSS)
        self.setFixedWidth(300)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Minimum)
        self.setVisible(False)

        self._current_tool: HyTool | None = None
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # 헤더
        hdr = QHBoxLayout()
        self._lbl_title = QLabel("Tool Settings")
        self._lbl_title.setFont(QFont("Segoe UI", 11, QFont.Bold))
        self._lbl_title.setStyleSheet("color:#e2e8f0;")
        btn_close = QPushButton("✕")
        btn_close.setFixedSize(24, 24)
        btn_close.setStyleSheet(
            "QPushButton{background:transparent;border:none;color:#94a3b8;font-size:14px;}"
            "QPushButton:hover{color:#ef4444;}")
        btn_close.clicked.connect(self._on_close)
        hdr.addWidget(self._lbl_title)
        hdr.addStretch()
        hdr.addWidget(btn_close)
        layout.addLayout(hdr)
        layout.addWidget(_sep())

        # 스크롤 가능한 컨텐트 영역
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setSpacing(8)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(self._content)
        layout.addWidget(scroll, 1)

    # ─────────────────────────────────────────────────────────────────────────
    # 공개 API
    # ─────────────────────────────────────────────────────────────────────────

    def show_tool(self, tool: HyTool):
        """툴에 맞는 파라미터 페이지를 렌더링하고 패널을 표시."""
        self._current_tool = tool
        self._lbl_title.setText(f"{tool.name}  [id={tool.tool_id}]")
        self._rebuild_content(tool)
        self.setVisible(True)
        self.adjustSize()

    def hide_panel(self):
        self.setVisible(False)
        self._current_tool = None

    # ─────────────────────────────────────────────────────────────────────────
    # 컨텐트 재구성
    # ─────────────────────────────────────────────────────────────────────────

    def _rebuild_content(self, tool: HyTool):
        """현재 컨텐트 레이아웃을 비우고 툴에 맞게 재구성."""
        # 기존 위젯 전부 제거
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
    # 툴별 파라미터 섹션
    # ─────────────────────────────────────────────────────────────────────────

    def _build_hyline(self, tool: HyLine):
        g = self._group("HyLine 파라미터")
        vb = QVBoxLayout(g)

        # num_splits
        row, sb = self._spin_row("분할 수 (num_splits):", 2, 20, tool.num_splits)
        sb.valueChanged.connect(lambda v: (setattr(tool, 'num_splits', v), self._emit()))
        vb.addLayout(row)

        # cut_ratio
        row, dsb = self._dspin_row("임계 비율 (cut_ratio):", 0.1, 1.0, tool.cut_ratio, 0.05)
        dsb.valueChanged.connect(lambda v: (setattr(tool, 'cut_ratio', v), self._emit()))
        vb.addLayout(row)

        # scan_dir
        row, cmb = self._combo_row("스캔 방향:", ["상→하", "하→상"], tool.scan_dir)
        cmb.currentIndexChanged.connect(lambda i: (setattr(tool, 'scan_dir', i), self._emit()))
        vb.addLayout(row)

        # peak_mode
        row, cmb2 = self._combo_row("피크 위치:", ["중앙", "시작단", "끝단"], tool.peak_mode)
        cmb2.currentIndexChanged.connect(lambda i: (setattr(tool, 'peak_mode', i), self._emit()))
        vb.addLayout(row)

        # mid_check
        chk = QCheckBox("중간점 검증 (mid_check)")
        chk.setChecked(tool.mid_check)
        chk.toggled.connect(lambda v: (setattr(tool, 'mid_check', v), self._emit()))
        vb.addWidget(chk)

        # mid_ratio
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
        lbl = QLabel("※ 템플릿은 캔버스에서 우클릭 → Set Template")
        lbl.setWordWrap(True)
        vb.addWidget(lbl)
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
        lbl = QLabel("※ 템플릿은 캔버스에서 우클릭 → Set Template")
        lbl.setWordWrap(True)
        vb.addWidget(lbl)
        self._content_layout.addWidget(g)

    def _build_hylocator(self, tool: HyLocator):
        g = self._group("HyLocator 파라미터")
        vb = QVBoxLayout(g)
        row, cmb = self._combo_row("갱신 정책:", ["Continuous", "CycleLock"], tool.update_policy)
        cmb.currentIndexChanged.connect(lambda i: (setattr(tool, 'update_policy', i), self._emit()))
        vb.addLayout(row)
        lbl = QLabel("※ 앵커는 시스템에 1개만 존재합니다.")
        lbl.setWordWrap(True)
        vb.addWidget(lbl)
        self._content_layout.addWidget(g)

    def _build_hydistance(self, tool: HyDistance):
        g = self._group("HyDistance 파라미터")
        vb = QVBoxLayout(g)
        row, cmb = self._combo_row("투영 축:", ["perpendicular", "horizontal", "vertical"],
                                   ["perpendicular", "horizontal", "vertical"].index(tool.projection_axis))
        axes = ["perpendicular", "horizontal", "vertical"]
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

    def _build_hyfnd(self, tool: HyFND):
        g = self._group("HyFND 파라미터")
        vb = QVBoxLayout(g)
        row, sb = self._spin_row("자릿수 (num_digits):", 1, 16, tool.num_digits)
        sb.valueChanged.connect(lambda v: (setattr(tool, 'num_digits', v), self._emit()))
        vb.addLayout(row)
        row2, sb2 = self._spin_row("세그먼트 임계값:", 1, 255, tool.threshold)
        sb2.valueChanged.connect(lambda v: (setattr(tool, 'threshold', v), self._emit()))
        vb.addLayout(row2)
        row2b, dsb_skew = self._dspin_row("기울기 보정 각도 (°):", -45.0, 45.0, tool.skew_angle, 0.5)
        dsb_skew.valueChanged.connect(lambda v: (setattr(tool, 'skew_angle', v), self._emit()))
        vb.addLayout(row2b)
        row3, cmb = self._combo_row("판정 모드:", ["equal", "range"],
                                    0 if tool.judge_mode == "equal" else 1)
        cmb.currentIndexChanged.connect(
            lambda i: (setattr(tool, 'judge_mode', "equal" if i == 0 else "range"), self._emit()))
        vb.addLayout(row3)
        lbl_tgt = _lbl("목표값 (equal 모드):")
        le = QLineEdit(str(tool.target_value))
        le.textChanged.connect(lambda t: (setattr(tool, 'target_value', t), self._emit()))
        vb.addWidget(lbl_tgt)
        vb.addWidget(le)
        self._content_layout.addWidget(g)

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
        row4, cmb4 = self._combo_row("출력 결정 (output_mode):", ["실행 여부", "자식 결과"], tool.output_mode)
        cmb4.currentIndexChanged.connect(lambda i: (setattr(tool, 'output_mode', i), self._emit()))
        vb.addLayout(row4)
        self._content_layout.addWidget(g)

    def _build_hyfin(self, tool: HyFin):
        g = self._group("HyFin [Fin] 파라미터")
        vb = QVBoxLayout(g)
        lbl = QLabel("※ HyFin: 사이클 종료자. 하위 노드 AND 집계 → I/O 출력.")
        lbl.setWordWrap(True)
        vb.addWidget(lbl)
        row, cmb = self._combo_row("브로드캐스트 타겟:",
                                   ["status_box", "spec_box", "both"],
                                   ["status_box", "spec_box", "both"].index(
                                       tool.broadcast_target
                                       if tool.broadcast_target in ["status_box", "spec_box", "both"]
                                       else "status_box"))
        targets = ["status_box", "spec_box", "both"]
        cmb.currentIndexChanged.connect(
            lambda i: (setattr(tool, 'broadcast_target', targets[i]), self._emit()))
        vb.addLayout(row)
        self._content_layout.addWidget(g)

    def _build_logic_simple(self, tool):
        g = self._group(f"{tool.name} 파라미터")
        vb = QVBoxLayout(g)
        cls_name = tool.__class__.__name__
        desc = {
            "HyAnd": "순차 AND 집계. NG > PENDING > OK 우선순위.",
            "HyOr":  "순차 OR 집계. OK 발견 즉시 참 반환.",
        }.get(cls_name, "")
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
        for lbl_txt, val, attr in [("X", x, "search_roi"),
                                    ("Y", y, None),
                                    ("W", w, None),
                                    ("H", h, None)]:
            sb = QSpinBox()
            sb.setRange(0, 4096)
            sb.setValue(int(val))
            sb.setPrefix(f"{lbl_txt}: ")
            # 실시간 ROI 업데이트 closure
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
            "color:#94a3b8;font-size:10px;font-weight:bold;}")
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
