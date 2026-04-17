"""
InspectorApp.py - HyVision Pro-Inspector 메인 윈도우 (v2.0)
LIVE / TEACH / TEST / RUN 4대 모드 통합 UI.
"""
import sys
import os
import time
import ctypes

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QPushButton, QLabel, QFrame, QComboBox, QTextEdit,
    QFileDialog, QMessageBox, QListWidget, QListWidgetItem,
    QTreeWidget, QTreeWidgetItem, QAbstractItemView, QSizePolicy,
    QGroupBox, QToolButton, QMenu, QAction, QDialog, QDialogButtonBox
)
from PyQt5.QtCore  import Qt, QTimer, QSize, QMimeData, QPoint, QSettings
from PyQt5.QtGui   import QColor, QFont, QIcon, QDrag, QCursor, QImage, QKeySequence
from PyQt5.QtWidgets import QShortcut

from HyProtocol    import HyProtocol
from HyVisionTools import (HyTool, HyLogicTool, HyLine, HyPatMat, HyLocator,
                            HyIntersection, HyLinePatMat, HyDistance, HyContrast, HyFND,
                            HyWhen, HyAnd, HyOr, HyFin,
                            create_tool, is_logic_tool, is_physical_tool)
from RecipeTree    import RecipeTree
from VirtualMachine import VirtualMachine, FolderProvider, LiveCameraProvider
from HyLink        import HyLink
from VisionCanvas  import VisionCanvas
from OverlayPanel  import OverlayPanel


# ─── 공통 스타일 ─────────────────────────────────────────────────────────────
APP_QSS = """
QMainWindow, QWidget {
    background-color: #020617;
    color: #f1f5f9;
    font-family: 'Segoe UI', sans-serif;
}
QGroupBox {
    background-color: #0b1120;
    border: 1px solid #1e293b;
    border-radius: 8px;
    margin-top: 15px;
    padding-top: 24px;
    padding-bottom: 6px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px; top: 8px;
    color: #94a3b8;
    font-weight: bold;
    font-size: 10px;
}
QTextEdit {
    background-color: #000;
    color: #10b981;
    font-family: Consolas;
    font-size: 11px;
    border: 1px solid #1e293b;
    border-radius: 6px;
}
QComboBox {
    background-color: #0f172a;
    border: 1px solid #1e293b;
    padding: 5px;
    border-radius: 4px;
    color: #cbd5e1;
}
QComboBox::drop-down { border: none; }
QListWidget, QTreeWidget {
    background: #0b1120;
    border: 1px solid #1e293b;
    border-radius: 4px;
    color: #e2e8f0;
}
QListWidget::item:selected, QTreeWidget::item:selected {
    background: #1e3a5f;
}
QSplitter::handle { background: #1e293b; }
"""

BTN_MODE_BASE = (
    "QPushButton{background-color:%s;color:white;font-weight:bold;"
    "border-radius:4px;padding:7px 14px;border:none;font-size:12px;}"
    "QPushButton:hover{background-color:%s;}"
    "QPushButton:checked{background-color:%s;"
    "border-bottom:3px solid %s;}")

MODE_STYLES = {
    "LIVE":  ("#0f172a", "#1e293b", "#0284c7", "#38bdf8"),
    "TEACH": ("#0f172a", "#1e293b", "#b45309", "#fbbf24"),
    "TEST":  ("#0f172a", "#1e293b", "#6d28d9", "#a78bfa"),
    "RUN":   ("#0f172a", "#1e293b", "#065f46", "#10b981"),
}


def _mode_btn(label: str, color_key: str) -> QPushButton:
    c = MODE_STYLES[color_key]
    btn = QPushButton(label)
    btn.setCheckable(True)
    btn.setFixedHeight(36)
    btn.setStyleSheet(BTN_MODE_BASE % c)
    return btn


def _icon_btn(text: str, tooltip: str = "") -> QPushButton:
    b = QPushButton(text)
    b.setFixedHeight(30)
    b.setToolTip(tooltip)
    b.setStyleSheet(
        "QPushButton{background:#1e293b;border:1px solid #334155;"
        "border-radius:4px;color:#e2e8f0;padding:0 8px;font-size:11px;}"
        "QPushButton:hover{background:#334155;border-color:#38bdf8;}")
    return b


# =============================================================================
# 드래그 가능한 물리 툴 리스트 (TEST 모드 좌측 패널)
# =============================================================================

class PhysToolList(QListWidget):
    """물리 비전 툴을 나열하는 읽기 전용 드래그 소스."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setFixedWidth(200)

    def startDrag(self, actions):
        item = self.currentItem()
        if item is None:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(str(item.data(Qt.UserRole)))   # tool_id
        drag.setMimeData(mime)
        drag.exec_(Qt.CopyAction)


# =============================================================================
# 로직 트리 위젯 (TEST 모드 중앙 패널)
# =============================================================================

class LogicTreeWidget(QTreeWidget):
    """로직 집행관 트리. 물리 툴을 드롭해 자식으로 배치 가능."""

    TOOL_TYPE_ICONS = {
        HyProtocol.TOOL_WHEN: "⏱",
        HyProtocol.TOOL_AND:  "∧",
        HyProtocol.TOOL_OR:   "∨",
        HyProtocol.TOOL_FIN:  "[Fin]",
        HyProtocol.TOOL_LINE: "━",
        HyProtocol.TOOL_PATMAT:   "▣",
        HyProtocol.TOOL_LOCATOR:  "⬚",
        HyProtocol.TOOL_DISTANCE: "↔",
        HyProtocol.TOOL_CONTRAST: "◑",
        HyProtocol.TOOL_FND:      "7",
    }

    def __init__(self, recipe: RecipeTree, parent=None):
        super().__init__(parent)
        self.recipe = recipe
        self.setColumnCount(3)
        self.setHeaderLabels(["툴", "상태", "결과"])
        self.setColumnWidth(0, 200)
        self.setColumnWidth(1, 60)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self.header().setStyleSheet("QHeaderView::section{background:#0b1120;color:#94a3b8;border:none;}")

    # ─── 트리 재구성 ─────────────────────────────────────────────────────────

    def rebuild(self):
        self.clear()
        for root in self.recipe.root_nodes:
            item = self._make_item(root)
            self.addTopLevelItem(item)
            self._build_children(item, root)
        self.expandAll()

    def _make_item(self, tool: HyTool) -> QTreeWidgetItem:
        icon = self.TOOL_TYPE_ICONS.get(tool.tool_type, "?")
        name = f"{icon}  {tool.name}"
        item = QTreeWidgetItem([name, "", ""])
        item.setData(0, Qt.UserRole, tool.tool_id)

        state_color = {
            HyProtocol.JUDGE_OK:      "#10b981",
            HyProtocol.JUDGE_NG:      "#ef4444",
            HyProtocol.JUDGE_PENDING: "#facc15",
        }.get(tool.rst_state, "#64748b")
        state_text = {
            HyProtocol.JUDGE_OK: "OK", HyProtocol.JUDGE_NG: "NG",
            HyProtocol.JUDGE_PENDING: "...",
        }.get(tool.rst_state, "-")
        item.setText(1, state_text)
        item.setForeground(1, QColor(state_color))

        if hasattr(tool, 'x'):
            item.setText(2, f"({tool.x:.0f},{tool.y:.0f}) {tool.angle:.1f}°")
        return item

    def _build_children(self, parent_item: QTreeWidgetItem, tool: HyTool):
        if not isinstance(tool, HyLogicTool):
            return
        for child in tool.children:
            child_item = self._make_item(child)
            parent_item.addChild(child_item)
            self._build_children(child_item, child)

    # ─── 드롭 처리 ────────────────────────────────────────────────────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not event.mimeData().hasText():
            return
        try:
            tool_id = int(event.mimeData().text())
        except ValueError:
            return

        target_item = self.itemAt(event.pos())
        parent_id   = 0
        if target_item:
            parent_id = target_item.data(0, Qt.UserRole) or 0

        # 이미 트리에 있는 툴을 다시 드롭하면 부모 이동
        existing = self.recipe.get_tool(tool_id)
        if existing:
            try:
                # 기존 부모에서 분리 후 새 부모에 추가
                old_parent_id = existing.parent_id
                if old_parent_id != 0:
                    old_parent = self.recipe.get_tool(old_parent_id)
                    if old_parent and isinstance(old_parent, HyLogicTool):
                        old_parent.remove_child(tool_id)
                        existing.parent_id = 0
                        if tool_id in [r.tool_id for r in self.recipe.root_nodes]:
                            pass
                        else:
                            self.recipe.root_nodes.append(existing)
                if parent_id != 0:
                    parent_tool = self.recipe.get_tool(parent_id)
                    if parent_tool and isinstance(parent_tool, HyLogicTool):
                        if existing in self.recipe.root_nodes:
                            self.recipe.root_nodes.remove(existing)
                        parent_tool.add_child(existing)
                        existing.parent_id = parent_id
            except Exception as e:
                QMessageBox.warning(self, "이동 오류", str(e))
        self.rebuild()
        event.acceptProposedAction()

    # ─── 컨텍스트 메뉴 ────────────────────────────────────────────────────────

    def _context_menu(self, pos: QPoint):
        item = self.itemAt(pos)
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#0f172a;border:1px solid #334155;color:#e2e8f0;}"
            "QMenu::item:selected{background:#1e293b;}")

        if item:
            tool_id = item.data(0, Qt.UserRole)
            tool    = self.recipe.get_tool(tool_id)

            act_del = menu.addAction("🗑 삭제")
            act_del.triggered.connect(lambda: self._delete_tool(tool_id))

            if isinstance(tool, HyLogicTool):
                menu.addSeparator()
                sub = menu.addMenu("+ 자식 추가")
                for ttype, tname in [
                    (HyProtocol.TOOL_AND,  "HyAnd"),
                    (HyProtocol.TOOL_OR,   "HyOr"),
                    (HyProtocol.TOOL_WHEN, "HyWhen"),
                    (HyProtocol.TOOL_FIN,  "HyFin"),
                ]:
                    act = sub.addAction(tname)
                    act.triggered.connect(
                        lambda checked, tt=ttype, pid=tool_id: self._add_child(tt, pid))
        else:
            # 빈 곳 클릭 → 루트 로직 툴 추가
            sub = menu.addMenu("+ 루트 노드 추가")
            for ttype, tname in [
                (HyProtocol.TOOL_AND, "HyAnd"),
                (HyProtocol.TOOL_OR,  "HyOr"),
                (HyProtocol.TOOL_FIN, "HyFin [Fin]"),
            ]:
                act = sub.addAction(tname)
                act.triggered.connect(
                    lambda checked, tt=ttype: self._add_root(tt))

        menu.exec_(self.viewport().mapToGlobal(pos))

    def _add_root(self, tool_type: int):
        tid  = self.recipe.alloc_id()
        tool = create_tool(tool_type, tid)
        self.recipe.add_tool(tool, parent_id=0)
        self.rebuild()

    def _add_child(self, tool_type: int, parent_id: int):
        tid  = self.recipe.alloc_id()
        tool = create_tool(tool_type, tid)
        self.recipe.add_tool(tool, parent_id=parent_id)
        self.rebuild()

    def _delete_tool(self, tool_id: int):
        try:
            self.recipe.remove_tool(tool_id)
        except ValueError as e:
            QMessageBox.warning(self, "삭제 실패", str(e))
        self.rebuild()


# =============================================================================
# InspectorApp — 메인 윈도우
# =============================================================================

class InspectorApp(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("HyVision Pro-Inspector v2.0")
        self.setStyleSheet(APP_QSS)
        self._set_dark_titlebar()

        # ─ 코어 객체 ─────────────────────────────────────────────────────────
        self.recipe    = RecipeTree()
        self.link: HyLink | None = None
        self.vm:   VirtualMachine | None = None

        # ─ 레시피 저장 상태 (P4-27) ──────────────────────────────────────────
        self._recipe_path: str | None = None  # 현재 열린 .hyv 파일 경로

        # ─ 모드 상태 ──────────────────────────────────────────────────────────
        self._mode     = "STANDBY"   # STANDBY / LIVE / TEACH / TEST / RUN
        self._connected = False

        # ─ 주기 타이머 (TEST/RUN) ─────────────────────────────────────────────
        self._run_timer = QTimer(self)
        self._run_timer.timeout.connect(self._on_run_tick)

        # ─ UI 구성 ────────────────────────────────────────────────────────────
        self._build_ui()
        self.resize(1400, 860)

        # Ctrl+S 단축키 — 레시피 저장
        sc = QShortcut(QKeySequence.Save, self)
        sc.activated.connect(self._save_recipe)

    # ─────────────────────────────────────────────────────────────────────────
    # UI 구성
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_vbox = QVBoxLayout(central)
        root_vbox.setContentsMargins(8, 6, 8, 6)
        root_vbox.setSpacing(4)

        # ── 상단 툴바 ─────────────────────────────────────────────────────────
        root_vbox.addLayout(self._build_toolbar())
        root_vbox.addWidget(self._hsep())

        # ── 본문 (모드별 스택) ────────────────────────────────────────────────
        from PyQt5.QtWidgets import QStackedWidget
        self._stack = QStackedWidget()

        self._page_standby = self._build_page_standby()
        self._page_live    = self._build_page_live()
        self._page_teach   = self._build_page_teach()
        self._page_test    = self._build_page_test()
        self._page_run     = self._build_page_run()

        for page in [self._page_standby, self._page_live,
                     self._page_teach, self._page_test, self._page_run]:
            self._stack.addWidget(page)

        root_vbox.addWidget(self._stack, 1)

        # ── 하단 로그 패널 ────────────────────────────────────────────────────
        root_vbox.addWidget(self._hsep())
        root_vbox.addLayout(self._build_log_bar())

    # ─── 툴바 ─────────────────────────────────────────────────────────────────

    def _build_toolbar(self) -> QHBoxLayout:
        hb = QHBoxLayout()
        hb.setSpacing(4)

        # 모드 버튼
        self._btn_live  = _mode_btn("▶  LIVE",  "LIVE")
        self._btn_teach = _mode_btn("✏  TEACH", "TEACH")
        self._btn_test  = _mode_btn("⚙  TEST",  "TEST")
        self._btn_run   = _mode_btn("▶▶  RUN",  "RUN")

        for btn, m in [(self._btn_live, "LIVE"), (self._btn_teach, "TEACH"),
                       (self._btn_test, "TEST"), (self._btn_run, "RUN")]:
            btn.clicked.connect(lambda checked, mode=m: self._set_mode(mode))
            btn.setEnabled(False)
            hb.addWidget(btn)

        hb.addStretch(1)

        # 연결 영역
        self._cmb_port = QComboBox()
        self._cmb_port.setMinimumWidth(240)
        self._cmb_port.setMaximumWidth(320)

        self._btn_refresh = _icon_btn("🔄", "포트 목록 새로고침")
        self._btn_refresh.setFixedWidth(36)
        self._btn_refresh.clicked.connect(self._refresh_ports)

        self._btn_connect = QPushButton("CONNECT")
        self._btn_connect.setFixedSize(110, 32)
        self._btn_connect.setStyleSheet(
            "QPushButton{background:#2563eb;color:white;font-weight:bold;"
            "border-radius:4px;border:none;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:checked{background:#0f766e;border:2px solid #38bdf8;color:#e0f2fe;}")
        self._btn_connect.setCheckable(True)
        self._btn_connect.clicked.connect(self._on_connect_toggle)

        # 상태 LED (텍스트)
        self._lbl_status = QLabel("● DISCONNECTED")
        self._lbl_status.setStyleSheet("color:#ef4444;font-weight:bold;font-size:11px;")

        hb.addWidget(self._cmb_port)
        hb.addWidget(self._btn_refresh)
        hb.addWidget(self._btn_connect)
        hb.addWidget(self._lbl_status)

        # P4-27: 레시피 저장/로드 버튼
        hb.addSpacing(8)
        self._btn_recipe_open = _icon_btn("📂 열기", "레시피 파일 열기  (Ctrl+O)")
        self._btn_recipe_open.setFixedWidth(72)
        self._btn_recipe_open.clicked.connect(self._load_recipe)
        hb.addWidget(self._btn_recipe_open)

        self._btn_recipe_save = _icon_btn("💾 저장", "레시피 저장  (Ctrl+S)")
        self._btn_recipe_save.setFixedWidth(72)
        self._btn_recipe_save.clicked.connect(self._save_recipe)
        hb.addWidget(self._btn_recipe_save)

        self._btn_recipe_saveas = _icon_btn("💾 다른이름", "레시피 다른 이름으로 저장")
        self._btn_recipe_saveas.setFixedWidth(96)
        self._btn_recipe_saveas.clicked.connect(self._save_recipe_as)
        hb.addWidget(self._btn_recipe_saveas)

        # 저장 상태 레이블 (💾/✅/⚠)
        self._lbl_recipe_status = QLabel("⚠ 미저장")
        self._lbl_recipe_status.setStyleSheet(
            "color:#94a3b8;font-size:10px;min-width:90px;")
        hb.addWidget(self._lbl_recipe_status)

        self._refresh_ports()
        return hb

    # ─── 페이지들 ─────────────────────────────────────────────────────────────

    def _build_page_standby(self) -> QWidget:
        w = QWidget()
        vb = QVBoxLayout(w)
        vb.setAlignment(Qt.AlignCenter)
        lbl = QLabel("장치에 연결하거나\n이미지 폴더를 선택하세요")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("color:#334155;font-size:22px;")

        btn_folder = QPushButton("📁  가상 장치 (폴더 이미지) 열기")
        btn_folder.setFixedSize(280, 46)
        btn_folder.setStyleSheet(
            "QPushButton{background:#1e293b;border:1px solid #475569;"
            "border-radius:6px;color:#e2e8f0;font-size:13px;font-weight:bold;}"
            "QPushButton:hover{background:#334155;border-color:#38bdf8;color:white;}")
        btn_folder.clicked.connect(self._open_folder_vvm)

        vb.addStretch(2)
        vb.addWidget(lbl)
        vb.addSpacing(24)
        vb.addWidget(btn_folder, alignment=Qt.AlignCenter)
        vb.addStretch(3)
        return w

    def _build_page_live(self) -> QWidget:
        w = QWidget()
        vb = QVBoxLayout(w)
        vb.setContentsMargins(0, 0, 0, 0)
        self._canvas_live = VisionCanvas(self.recipe)
        vb.addWidget(self._canvas_live)
        return w

    def _build_page_teach(self) -> QWidget:
        w = QWidget()
        hb = QHBoxLayout(w)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(4)

        # 좌측: 비전 캔버스
        self._canvas_teach = VisionCanvas(self.recipe)
        self._canvas_teach.sig_roi_changed.connect(self._on_roi_changed)
        self._canvas_teach.sig_tool_selected.connect(self._on_tool_selected_teach)

        # 우측: 툴 관리 패널
        right = QWidget()
        right.setFixedWidth(280)
        right_vb = QVBoxLayout(right)
        right_vb.setContentsMargins(4, 0, 0, 0)
        right_vb.setSpacing(6)

        # 툴 추가 버튼들
        grp_add = QGroupBox("물리 비전 툴 추가")
        add_vb  = QVBoxLayout(grp_add)

        self._tool_add_menu = QMenu(self)
        self._tool_add_menu.setStyleSheet(
            "QMenu{background:#0f172a;border:1px solid #334155;color:#e2e8f0;}"
            "QMenu::item:selected{background:#1e293b;}")
        tool_defs = [
            ("━  HyLine",          HyProtocol.TOOL_LINE),
            ("▣  HyPatMat",        HyProtocol.TOOL_PATMAT),
            ("↻▣ HyLinePatMat",    HyProtocol.TOOL_LINE_PATMAT),
            ("⬚  HyLocator",       HyProtocol.TOOL_LOCATOR),
            ("✕  HyIntersection",  HyProtocol.TOOL_INTERSECTION),
            ("↔  HyDistance",      HyProtocol.TOOL_DISTANCE),
            ("◑  HyContrast",      HyProtocol.TOOL_CONTRAST),
            ("7  HyFND",           HyProtocol.TOOL_FND),
        ]
        for lbl, ttype in tool_defs:
            act = QAction(lbl, self)
            act.triggered.connect(lambda checked, tt=ttype: self._add_physical_tool(tt))
            self._tool_add_menu.addAction(act)

        btn_add = QPushButton("+ 툴 추가  ▾")
        btn_add.setStyleSheet(
            "QPushButton{background:#1e293b;border:1px solid #475569;"
            "border-radius:4px;color:#e2e8f0;font-weight:bold;padding:6px;}"
            "QPushButton:hover{background:#334155;border-color:#38bdf8;}")
        btn_add.clicked.connect(
            lambda: self._tool_add_menu.exec_(
                btn_add.mapToGlobal(QPoint(0, btn_add.height()))))
        add_vb.addWidget(btn_add)

        # TEACH 컨트롤 버튼
        btn_snap = _icon_btn("📷 스냅샷 캡처", "장치에서 1장 캡처")
        btn_snap.clicked.connect(self._teach_snap)
        add_vb.addWidget(btn_snap)

        right_vb.addWidget(grp_add)

        # 툴 목록
        grp_list = QGroupBox("툴 목록")
        list_vb  = QVBoxLayout(grp_list)
        self._teach_tool_list = QListWidget()
        self._teach_tool_list.clicked.connect(self._on_teach_list_click)
        list_vb.addWidget(self._teach_tool_list)

        btn_del = _icon_btn("🗑 선택 툴 삭제")
        btn_del.clicked.connect(self._delete_selected_teach_tool)
        list_vb.addWidget(btn_del)
        right_vb.addWidget(grp_list, 1)

        # 오버레이 패널
        self._overlay = OverlayPanel(self._canvas_teach)
        self._overlay.sig_updated.connect(self._on_overlay_changed)
        self._overlay.sig_closed.connect(lambda: self._canvas_teach.set_active_tool(None))

        hb.addWidget(self._canvas_teach, 1)
        hb.addWidget(right)
        return w

    def _build_page_test(self) -> QWidget:
        w = QWidget()
        vb = QVBoxLayout(w)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(4)

        splitter = QSplitter(Qt.Horizontal)

        # 좌측: 물리 툴 목록 (드래그 소스)
        left = QWidget()
        left_vb = QVBoxLayout(left)
        left_vb.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel("물리 비전 툴")
        lbl.setStyleSheet("color:#94a3b8;font-size:10px;font-weight:bold;padding:4px;")
        self._phys_list = PhysToolList()
        self._phys_list.setToolTip("드래그 → 트리에 배치")
        left_vb.addWidget(lbl)
        left_vb.addWidget(self._phys_list)
        left.setMaximumWidth(220)

        # 중앙: 로직 트리
        center = QWidget()
        center_vb = QVBoxLayout(center)
        center_vb.setContentsMargins(0, 0, 0, 0)
        lbl2 = QLabel("로직 집행관 트리  (우클릭 → 노드 추가/삭제)")
        lbl2.setStyleSheet("color:#94a3b8;font-size:10px;font-weight:bold;padding:4px;")
        self._logic_tree = LogicTreeWidget(self.recipe)
        self._logic_tree.itemClicked.connect(self._on_tree_item_clicked)
        center_vb.addWidget(lbl2)
        center_vb.addWidget(self._logic_tree, 1)

        splitter.addWidget(left)
        splitter.addWidget(center)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        vb.addWidget(splitter, 1)

        # 하단: HyFin 설정 패널 (클릭 시 표시)
        self._fin_panel_placeholder = QLabel("HyFin 노드를 클릭하면 I/O 설정 패널이 열립니다.")
        self._fin_panel_placeholder.setStyleSheet(
            "color:#334155;font-size:11px;padding:8px;")
        self._fin_panel_placeholder.setFixedHeight(36)
        vb.addWidget(self._fin_panel_placeholder)
        return w

    def _build_page_run(self) -> QWidget:
        w = QWidget()
        vb = QVBoxLayout(w)
        vb.setContentsMargins(0, 0, 0, 0)
        self._canvas_run = VisionCanvas(self.recipe)
        self._canvas_run.show_status_box   = True
        self._canvas_run.show_spec_box     = True
        self._canvas_run.show_roi_boxes    = True
        self._canvas_run.show_result_cross = True
        vb.addWidget(self._canvas_run)
        return w

    # ─── 로그 바 ──────────────────────────────────────────────────────────────

    def _build_log_bar(self) -> QHBoxLayout:
        hb = QHBoxLayout()
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFixedHeight(90)
        hb.addWidget(self._log)
        return hb

    # ─────────────────────────────────────────────────────────────────────────
    # 연결 / 포트
    # ─────────────────────────────────────────────────────────────────────────

    def _refresh_ports(self):
        self._cmb_port.clear()
        # 물리 COM 포트
        try:
            import serial.tools.list_ports
            for p in serial.tools.list_ports.comports():
                desc = p.description or "Unknown"
                self._cmb_port.addItem(f"{p.device} — {desc}", ("serial", p.device))
        except Exception:
            pass
        # 가상 장치 자리 (folder 선택은 별도 버튼)
        self._cmb_port.addItem("📁  가상 장치 (폴더 선택…)", ("virtual", ""))

    def _on_connect_toggle(self, checked: bool):
        if checked:
            data = self._cmb_port.currentData()
            if data and data[0] == "virtual" and not data[1]:
                # 가상 장치 → 폴더 선택 다이얼로그
                self._btn_connect.setChecked(False)
                self._open_folder_vvm()
                return
            elif data and data[0] == "serial":
                self._connect_serial(data[1])
        else:
            self._disconnect()

    def _open_folder_vvm(self):
        folder = QFileDialog.getExistingDirectory(
            self, "이미지 폴더 선택", os.path.expanduser("~"))
        if not folder:
            return
        provider = FolderProvider(folder)
        if not provider.is_available:
            QMessageBox.warning(self, "폴더 오류", "폴더에 이미지가 없습니다.")
            return
        self._connect_virtual(provider, label=f"VVM [{os.path.basename(folder)}]")

    def _connect_virtual(self, provider, label: str = "VVM"):
        self._disconnect()
        self.vm = VirtualMachine(provider)
        self.link = HyLink()
        self.link.sig_log.connect(self.log)
        self.link.sig_frame.connect(self._on_frame)
        self.link.sig_connected.connect(self._on_connected)
        self.link.connect_virtual(self.vm)
        self.log(f"가상 장치 연결: {label}", "system")
        # 포트 콤보에 추가
        self._cmb_port.insertItem(0, f"● {label}", ("virtual", label))
        self._cmb_port.setCurrentIndex(0)

    def _connect_serial(self, port: str):
        self._disconnect()
        self.link = HyLink()
        self.link.sig_log.connect(self.log)
        self.link.sig_frame.connect(self._on_frame)
        self.link.sig_connected.connect(self._on_connected)
        self.link.connect_serial(port)

    def _disconnect(self):
        self._run_timer.stop()
        if self.link:
            self.link.stop()
            self.link = None
        if self.vm:
            self.vm.stop()
            self.vm = None
        self._on_connected(0)

    # ─────────────────────────────────────────────────────────────────────────
    # 모드 전환
    # ─────────────────────────────────────────────────────────────────────────

    def _set_mode(self, mode: str):
        if mode == self._mode:
            # 같은 버튼 재클릭 → STANDBY 로 복귀
            mode = "STANDBY"

        self._run_timer.stop()
        prev = self._mode
        self._mode = mode

        # 버튼 상태 동기화
        for btn, m in [(self._btn_live,  "LIVE"),
                       (self._btn_teach, "TEACH"),
                       (self._btn_test,  "TEST"),
                       (self._btn_run,   "RUN")]:
            btn.setChecked(m == mode)

        # 장치 명령 전송
        if self.link:
            if mode == "LIVE":
                self.link.send_command(HyProtocol.CMD_LIVE)
            elif mode == "TEACH":
                self.link.send_command(HyProtocol.CMD_TEACH_SNAP)
            elif mode == "TEST":
                self.link.send_command(HyProtocol.CMD_TEST)
                self._run_timer.start(50)
            elif mode == "RUN":
                self.link.send_command(HyProtocol.CMD_RUN)
                self._run_timer.start(33)
            elif mode == "STANDBY":
                self.link.send_command(HyProtocol.CMD_STOP)

        # 스택 페이지 전환
        pages = {
            "STANDBY": 0, "LIVE": 1, "TEACH": 2, "TEST": 3, "RUN": 4
        }
        self._stack.setCurrentIndex(pages.get(mode, 0))

        # TEACH 모드 진입 시 툴 목록 갱신
        if mode == "TEACH":
            self._canvas_teach.set_mode("TEACH")
            self._refresh_teach_list()
            self._sync_recipe_to_vm()
        elif mode == "TEST":
            self._canvas_teach.set_mode("STANDBY")
            self._refresh_test_phys_list()
            self._logic_tree.rebuild()
        elif mode == "LIVE":
            self._canvas_live.set_mode("LIVE")
        elif mode == "RUN":
            self._canvas_run.set_mode("RUN")
            self._sync_recipe_to_vm()

        self.log(f"모드 전환: {prev} → {mode}")

    # ─────────────────────────────────────────────────────────────────────────
    # 장치 통신 콜백
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _qimage_to_numpy(qimg: QImage):
        """QImage → numpy BGR uint8 array (cv2 compatible)."""
        import numpy as np
        qimg = qimg.convertToFormat(QImage.Format_RGB888)
        w, h = qimg.width(), qimg.height()
        ptr = qimg.bits()
        ptr.setsize(h * w * 3)
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 3))
        return arr[:, :, ::-1].copy()  # RGB → BGR

    def _on_frame(self, qimg, burst_results: list):
        """HyLink.sig_frame 수신 → 캔버스 업데이트 + PC 측 연산."""
        # PC 측 연산 (로직 툴, 측정 툴)
        if burst_results and not qimg.isNull():
            self.recipe.inject_burst(burst_results, burst_results[0].get('cycle_id', 0))
            img = self._qimage_to_numpy(qimg)
            self.recipe.evaluate(img, self.recipe.current_cycle_id,
                                 burst_results[0].get('img_id', 0))

        # 캔버스 갱신
        if self._mode == "LIVE":
            self._canvas_live.set_image(qimg, burst_results)
        elif self._mode == "TEACH":
            self._canvas_teach.set_image(qimg, burst_results)
            # UI Preview
            self._run_preview()
        elif self._mode in ("TEST", "RUN"):
            canvas = self._canvas_run if self._mode == "RUN" else None
            if canvas:
                canvas.set_image(qimg, burst_results)
            # TEST 모드: 트리 상태 업데이트
            if self._mode == "TEST":
                self._logic_tree.rebuild()

    def _on_run_tick(self):
        """TEST/RUN 타이머 — 주기적 결과 요청."""
        if self.link and self._mode in ("TEST", "RUN"):
            cmd = HyProtocol.CMD_TEST if self._mode == "TEST" else HyProtocol.CMD_RUN
            self.link.send_command(cmd)

    def _on_connected(self, state: int):
        connected = (state == 1)
        self._connected = connected
        for btn in [self._btn_live, self._btn_teach, self._btn_test, self._btn_run]:
            btn.setEnabled(connected)
        self._btn_connect.setChecked(connected)
        self._cmb_port.setEnabled(not connected)
        self._btn_refresh.setEnabled(not connected)

        if connected:
            self._lbl_status.setText("● CONNECTED")
            self._lbl_status.setStyleSheet("color:#10b981;font-weight:bold;font-size:11px;")
        else:
            self._lbl_status.setText("● DISCONNECTED")
            self._lbl_status.setStyleSheet("color:#ef4444;font-weight:bold;font-size:11px;")
            self._run_timer.stop()
            if state == 2:
                self.log("장치 연결 비정상 종료", "error")

    # ─────────────────────────────────────────────────────────────────────────
    # TEACH 모드 - 툴 CRUD
    # ─────────────────────────────────────────────────────────────────────────

    def _add_physical_tool(self, tool_type: int):
        # HyLocator: 이미 존재하면 거부
        if tool_type == HyProtocol.TOOL_LOCATOR and self.recipe.anchor:
            QMessageBox.warning(self, "HyLocator", "HyLocator는 시스템에 1개만 존재합니다.")
            return

        # 물리 툴은 루트 불가 → 로직 툴이 없으면 자동 HyAnd 생성 후 배치
        parent_id = 0
        if tool_type not in (HyProtocol.TOOL_LOCATOR,):
            logic_roots = [t for t in self.recipe.root_nodes if is_logic_tool(t)]
            if not logic_roots:
                # 자동 컨테이너 생성
                and_id = self.recipe.alloc_id()
                and_tool = HyAnd(and_id)
                and_tool.name = "Recipe Root"
                self.recipe.add_tool(and_tool, parent_id=0)
                parent_id = and_id
            else:
                parent_id = logic_roots[0].tool_id

        tid  = self.recipe.alloc_id()
        tool = create_tool(tool_type, tid)
        # 기본 ROI 중앙 배치
        tool.search_roi = (220, 180, 200, 120)

        try:
            if tool_type == HyProtocol.TOOL_LOCATOR:
                # HyLocator는 루트에 직접 배치 (독립 노드)
                # parent_id=0 지만 물리툴 루트 금지 규칙을 우회하기 위해
                # recipe에 직접 삽입
                tool.parent_id = 0
                self.recipe.tool_index[tid] = tool
                self.recipe.root_nodes.append(tool)
                self.recipe.anchor = tool
            else:
                self.recipe.add_tool(tool, parent_id=parent_id)
        except ValueError as e:
            QMessageBox.warning(self, "추가 실패", str(e))
            return

        self._refresh_teach_list()
        self._canvas_teach.set_active_tool(tid)
        self._overlay.show_tool(tool)
        self._sync_recipe_to_vm()
        self._update_recipe_status()
        self.log(f"툴 추가: {tool.name} (id={tid})", "success")

    def _delete_selected_teach_tool(self):
        item = self._teach_tool_list.currentItem()
        if item is None:
            return
        tool_id = item.data(Qt.UserRole)
        try:
            self.recipe.remove_tool(tool_id)
        except ValueError as e:
            QMessageBox.warning(self, "삭제 실패", str(e))
            return
        self._overlay.hide_panel()
        self._canvas_teach.set_active_tool(None)
        self._refresh_teach_list()
        self._sync_recipe_to_vm()
        self._update_recipe_status()
        self.log(f"툴 삭제 (id={tool_id})", "info")

    def _teach_snap(self):
        if self.link:
            self.link.send_command(HyProtocol.CMD_TEACH_SNAP)
            self.log("스냅샷 캡처 요청", "process")

    def _refresh_teach_list(self):
        self._teach_tool_list.clear()
        # HyLocator 먼저
        if self.recipe.anchor:
            t = self.recipe.anchor
            it = QListWidgetItem(f"⬚  {t.name} (id={t.tool_id})")
            it.setForeground(QColor("#f59e0b"))
            it.setData(Qt.UserRole, t.tool_id)
            self._teach_tool_list.addItem(it)
        # 나머지 물리 툴
        for t in self.recipe.get_physical_tools():
            if isinstance(t, HyLocator):
                continue
            icon = {HyProtocol.TOOL_LINE: "━",
                    HyProtocol.TOOL_PATMAT: "▣",
                    HyProtocol.TOOL_DISTANCE: "↔",
                    HyProtocol.TOOL_CONTRAST: "◑",
                    HyProtocol.TOOL_FND: "7"}.get(t.tool_type, "?")
            it = QListWidgetItem(f"{icon}  {t.name} (id={t.tool_id})")
            it.setForeground(QColor("#38bdf8"))
            it.setData(Qt.UserRole, t.tool_id)
            self._teach_tool_list.addItem(it)

    def _on_teach_list_click(self, index):
        item = self._teach_tool_list.currentItem()
        if item is None:
            return
        tid  = item.data(Qt.UserRole)
        tool = self.recipe.get_tool(tid)
        if tool:
            self._canvas_teach.set_active_tool(tid)
            self._overlay.show_tool(tool)

    def _on_tool_selected_teach(self, tool_id: int):
        if tool_id < 0:
            self._overlay.hide_panel()
            return
        tool = self.recipe.get_tool(tool_id)
        if tool:
            self._overlay.show_tool(tool)

    def _on_roi_changed(self, tool_id, x, y, w, h):
        """ROI 드래그 완료 → VVM 에 SET_TOOL 전송 + Preview 실행."""
        if self.link:
            tool = self.recipe.get_tool(tool_id)
            tt   = tool.tool_type if tool else 0
            rot  = getattr(tool, 'rot_angle', 0.0) if tool else 0.0
            pid  = tool.parent_id if tool else 0
            did  = getattr(tool, 'device_id', 1) if tool else 1
            self.link.send_command(
                HyProtocol.CMD_SET_TOOL,
                target_id=tool_id,
                target_type=tt,
                params=[
                    HyProtocol.encode_tree_info(did, pid),
                    0,
                    HyProtocol.encode_xy(int(x), int(y)),
                    HyProtocol.encode_wh(int(w), int(h)),
                ],
                fparam=rot,
            )
        self._update_recipe_status()
        self._run_preview()

    def _on_overlay_changed(self):
        """파라미터 패널 변경 → UI Preview 즉시 재실행."""
        self._run_preview()

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 모드
    # ─────────────────────────────────────────────────────────────────────────

    def _refresh_test_phys_list(self):
        self._phys_list.clear()
        for t in self.recipe.get_physical_tools():
            icon = {HyProtocol.TOOL_LINE: "━",
                    HyProtocol.TOOL_PATMAT: "▣",
                    HyProtocol.TOOL_LOCATOR: "⬚",
                    HyProtocol.TOOL_DISTANCE: "↔",
                    HyProtocol.TOOL_CONTRAST: "◑",
                    HyProtocol.TOOL_FND: "7"}.get(t.tool_type, "?")
            it = QListWidgetItem(f"{icon}  {t.name}  [id={t.tool_id}]")
            it.setData(Qt.UserRole, t.tool_id)
            self._phys_list.addItem(it)

    def _on_tree_item_clicked(self, item, col):
        tid  = item.data(0, Qt.UserRole)
        tool = self.recipe.get_tool(tid)
        if isinstance(tool, HyFin):
            # HyFin 클릭 → Fin 설정 패널 표시 (하단)
            self._fin_panel_placeholder.setText(
                f"[Fin] {tool.name}  id={tid}  |  broadcast: {tool.broadcast_target}")

    # ─────────────────────────────────────────────────────────────────────────
    # UI Preview Engine (TEACH 모드 즉시 렌더링)
    # ─────────────────────────────────────────────────────────────────────────

    def _run_preview(self):
        """현재 캔버스 이미지로 모든 물리 툴을 즉시 실행 → 결과 렌더링."""
        canvas = self._canvas_teach
        if canvas._image.isNull():
            return
        try:
            import numpy as np
            import cv2
            qimg  = canvas._image.convertToFormat(canvas._image.Format_RGB888)
            w, h  = qimg.width(), qimg.height()
            ptr   = qimg.bits()
            ptr.setsize(h * w * 3)
            arr   = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 3)).copy()
            bgr   = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            self.recipe.preview(bgr, img_id=0)
            canvas.update()
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # VVM 레시피 동기화
    # ─────────────────────────────────────────────────────────────────────────

    def _sync_recipe_to_vm(self):
        """PC 레시피 트리를 VVM 에 SET_TOOL 명령으로 동기화."""
        if self.link is None:
            return
        # 기존 전부 삭제
        self.link.send_command(HyProtocol.CMD_CLEAR_TOOLS)
        # DFS 직렬화 후 전송
        for cmd in self.recipe.serialize_to_commands():
            self.link._cmd_q.put(cmd)

    # ─────────────────────────────────────────────────────────────────────────
    # P4-27 레시피 저장 / 로드 UI
    # ─────────────────────────────────────────────────────────────────────────

    def _save_recipe(self):
        """Ctrl+S / 💾 저장 버튼. 파일 경로가 없으면 다른 이름으로 저장."""
        if self._recipe_path is None:
            self._save_recipe_as()
            return
        ok = self.recipe.save_to_file(self._recipe_path)
        if ok:
            self.log(f"레시피 저장 완료: {os.path.basename(self._recipe_path)}", "success")
        else:
            QMessageBox.critical(self, "저장 실패",
                                 f"파일을 저장할 수 없습니다:\n{self._recipe_path}")
        self._update_recipe_status()

    def _save_recipe_as(self):
        """다른 이름으로 저장 다이얼로그."""
        settings   = QSettings("HyVision", "ProInspector")
        last_dir   = settings.value("last_recipe_dir",
                                    os.path.expanduser("~"), type=str)
        path, _    = QFileDialog.getSaveFileName(
            self, "레시피 저장", last_dir,
            "HyVision 레시피 (*.hyv);;모든 파일 (*)")
        if not path:
            return
        if not path.endswith('.hyv'):
            path += '.hyv'
        ok = self.recipe.save_to_file(path)
        if ok:
            self._recipe_path = path
            settings.setValue("last_recipe_dir", os.path.dirname(path))
            self.log(f"레시피 저장: {os.path.basename(path)}", "success")
        else:
            QMessageBox.critical(self, "저장 실패",
                                 f"파일을 저장할 수 없습니다:\n{path}")
        self._update_recipe_status()

    def _load_recipe(self):
        """📂 열기 버튼 — 파일 다이얼로그 → 레시피 로드."""
        # 변경 사항 확인
        if self.recipe.dirty:
            reply = QMessageBox.question(
                self, "변경 내용 저장",
                "저장하지 않은 변경 내용이 있습니다. 계속 진행하면 변경 내용이 사라집니다.",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save)
            if reply == QMessageBox.Save:
                self._save_recipe()
                return
            elif reply == QMessageBox.Cancel:
                return

        settings = QSettings("HyVision", "ProInspector")
        last_dir = settings.value("last_recipe_dir",
                                  os.path.expanduser("~"), type=str)
        path, _  = QFileDialog.getOpenFileName(
            self, "레시피 열기", last_dir,
            "HyVision 레시피 (*.hyv);;모든 파일 (*)")
        if not path:
            return

        ok = self.recipe.load_from_file(path)
        if ok:
            self._recipe_path = path
            settings.setValue("last_recipe_dir", os.path.dirname(path))
            self.log(f"레시피 로드: {os.path.basename(path)}", "success")
            # UI 갱신
            self._refresh_teach_list()
            self._refresh_test_phys_list()
            self._logic_tree.rebuild()
            self._sync_recipe_to_vm()
        else:
            QMessageBox.critical(self, "로드 실패",
                                 f"레시피 파일을 열 수 없습니다 (CRC 오류 또는 손상된 파일):\n{path}")
        self._update_recipe_status()

    def _update_recipe_status(self):
        """레시피 상태 레이블 갱신 (💾/✅/⚠)."""
        if not self.recipe.tool_index:
            text  = "⚠ 비어있음"
            color = "#64748b"
        elif self.recipe.dirty:
            fname = os.path.basename(self._recipe_path) if self._recipe_path else "미저장"
            text  = f"💾 {fname}"
            color = "#facc15"
        else:
            fname = os.path.basename(self._recipe_path) if self._recipe_path else ""
            text  = f"✅ {fname}" if fname else "✅ 저장됨"
            color = "#34d399"
        self._lbl_recipe_status.setText(text)
        self._lbl_recipe_status.setStyleSheet(
            f"color:{color};font-size:10px;min-width:90px;")

    # ─────────────────────────────────────────────────────────────────────────
    # 로그
    # ─────────────────────────────────────────────────────────────────────────

    def log(self, text: str, level: str = "info"):
        color = {"info": "#cbd5e1", "success": "#34d399", "error": "#ef4444",
                 "system": "#38bdf8", "process": "#a78bfa",
                 "warn": "#facc15"}.get(level, "#cbd5e1")
        ts = time.strftime("%H:%M:%S")
        self._log.append(f"<span style='color:{color}'>[{ts}] {text}</span>")

    # ─────────────────────────────────────────────────────────────────────────
    # 유틸
    # ─────────────────────────────────────────────────────────────────────────

    def _hsep(self) -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.HLine)
        f.setFixedHeight(1)
        f.setStyleSheet("QFrame{color:#1e293b;}")
        return f

    def _set_dark_titlebar(self):
        try:
            if sys.platform == "win32":
                hwnd  = int(self.winId())
                val   = ctypes.c_int(1)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))
        except Exception:
            pass

    def closeEvent(self, event):
        self._run_timer.stop()
        self._disconnect()
        event.accept()


# =============================================================================
# 진입점
# =============================================================================

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = InspectorApp()
    win.show()
    sys.exit(app.exec_())
