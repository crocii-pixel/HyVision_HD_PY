import sys
import os
import serial
import serial.tools.list_ports
import time
import struct
import json
import ctypes 

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QLabel, QFrame, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                             QGroupBox, QTextEdit, QComboBox)
from PyQt5.QtCore import Qt, QPointF, QEvent, QRectF, QTimer # 💡 QTimer 임포트 추가
from PyQt5.QtGui import QColor, QTransform

from StatusLED import StatusLED
from VisionMap import VisionMap
from OpenMVWorker import OpenMVWorker
from WinUtil import WinUtil
from RecipeManager import RecipeManager, BaseUITool 

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.default_title = "HyVision Pro-Inspector (Board-Master Version)"
        self.setWindowTitle(self.default_title)
        self.setStyleSheet("background-color: #020617; color: #f1f5f9; font-family: 'Segoe UI', sans-serif;")
        self._set_dark_titlebar()
        
        self.worker = None
        self.pending_target = "" 
        
        # 💡 [추가] 모드별 독립 상태 플래그 및 타이머
        self.is_live = False
        self.is_teach = False
        self.is_test = False
        self.test_timer = QTimer(self)
        self.test_timer.timeout.connect(self._send_test_trigger)
        
        self.recipe_manager = RecipeManager()
        self._init_default_recipe()
        
        self.init_ui()
        self.refresh_ports()

    def _init_default_recipe(self):
        # ID 1: 마스터 앵커 툴
        anchor = BaseUITool(1, "Master Locator", 3, roi=QRectF(200, 150, 100, 50), use_anchor=False)
        self.recipe_manager.add_tool(anchor)
        self.recipe_manager.set_anchor_tool(1)
        
        # ID 2, 3: 종속된 라인 툴들
        t_obj = BaseUITool(2, "Object Line", 1, roi=QRectF(150, 250, 150, 80), use_anchor=True)
        t_shd = BaseUITool(3, "Shadow Line", 1, roi=QRectF(350, 250, 150, 80), use_anchor=True)
        self.recipe_manager.add_tool(t_obj)
        self.recipe_manager.add_tool(t_shd)

    def _set_dark_titlebar(self):
        try:
            if sys.platform == "win32":
                hwnd = int(self.winId())
                value = ctypes.c_int(1) 
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(value), ctypes.sizeof(value))
        except Exception: pass 

    def init_ui(self):
        main_widget = QWidget(); self.setCentralWidget(main_widget)
        layout = QHBoxLayout(main_widget); layout.setContentsMargins(15, 15, 15, 15)

        left_layout = QVBoxLayout()
        self.status_card = QFrame(); self.status_card.setFixedHeight(50); self.status_card.setStyleSheet("background-color: #0f172a; border-radius: 8px; border: 1px solid #1e293b;")
        status_layout = QHBoxLayout(self.status_card)
        
        self.mode_color_block = QFrame(); self.mode_color_block.setFixedSize(12, 24); self.mode_color_block.setStyleSheet("background-color: #64748b; border-radius: 8px;"); status_layout.addWidget(self.mode_color_block)
        self.lbl_mode = QLabel("STANDBY"); self.lbl_mode.setStyleSheet("font-weight: bold; color: #94a3b8; font-size: 18px; border-radius: 8px"); status_layout.addWidget(self.lbl_mode)
        status_layout.addStretch(1)

        self.btn_img_setup = QPushButton("⚙️ Image Setup"); self.btn_img_setup.setFixedWidth(135); self.btn_img_setup.setStyleSheet("QPushButton { background-color: #1e293b; color: #cbd5e1; border: 1px solid #475569; border-radius: 4px; padding: 6px; font-weight: bold; } QPushButton:hover { background-color: #334155; color: white; border-color: #38bdf8; }")
        self.btn_img_setup.clicked.connect(lambda: self.vision_map.toggle_setup_panel()); self.btn_img_setup.hide(); status_layout.addWidget(self.btn_img_setup)
        
        btn_style_tmpl = "QPushButton { background-color: %s; color: white; font-weight: bold; border-radius: 4px; padding: 6px; border: 1px solid %s; } QPushButton:hover { background-color: %s; }"
        
        self.btn_sel_anchor = QPushButton("⬚ Anchor Tool"); self.btn_sel_anchor.setFixedWidth(135); self.btn_sel_anchor.setStyleSheet(btn_style_tmpl % ("#d97706", "#fbbf24", "#b45309"))
        self.btn_sel_anchor.clicked.connect(lambda: self.vision_map.set_active_tool(1)); self.btn_sel_anchor.hide(); status_layout.addWidget(self.btn_sel_anchor)
        
        self.btn_sel_obj = QPushButton("━ Object Line"); self.btn_sel_obj.setFixedWidth(135); self.btn_sel_obj.setStyleSheet(btn_style_tmpl % ("#0ea5e9", "#7dd3fc", "#0284c7"))
        self.btn_sel_obj.clicked.connect(lambda: self.vision_map.set_active_tool(2)); self.btn_sel_obj.hide(); status_layout.addWidget(self.btn_sel_obj)

        self.btn_sel_shd = QPushButton("━ Shadow Line"); self.btn_sel_shd.setFixedWidth(135); self.btn_sel_shd.setStyleSheet(btn_style_tmpl % ("#f97316", "#fdba74", "#ea580c"))
        self.btn_sel_shd.clicked.connect(lambda: self.vision_map.set_active_tool(3)); self.btn_sel_shd.hide(); status_layout.addWidget(self.btn_sel_shd)

        left_layout.addWidget(self.status_card)

        self.vision_map = VisionMap(self.recipe_manager)
        left_layout.addWidget(self.vision_map)
        layout.addLayout(left_layout, stretch=1)

        right_panel = QWidget(); right_panel.setFixedWidth(360)
        right_layout = QVBoxLayout(right_panel); right_layout.setContentsMargins(0, 0, 0, 0)
        grp_style = "QGroupBox { background-color: #0b1120; border: 1px solid #1e293b; border-radius: 8px; margin-top: 15px; padding-top: 25px; padding-bottom: 5px; } QGroupBox::title { subcontrol-origin: margin; left: 12px; top: 8px; color: #94a3b8; font-weight: bold; font-size: 11px; }"

        conn_group = QGroupBox("HARDWARE CONNECTION"); conn_group.setStyleSheet(grp_style)
        vbox_conn = QVBoxLayout()
        hbox_port = QHBoxLayout()
        
        self.led_status = StatusLED(); hbox_port.addWidget(self.led_status)

        self.cmb_ports = QComboBox(); self.cmb_ports.setStyleSheet("background-color: #020617; border: 1px solid #1e293b; padding: 6px; border-radius: 4px; color: #cbd5e1;")
        self.btn_refresh = QPushButton("🔄"); self.btn_refresh.setFixedSize(32, 32); self.btn_refresh.setStyleSheet("QPushButton { background-color: #334155; color: white; border-radius: 4px; font-weight: bold; } QPushButton:hover { background-color: #475569; }")
        self.btn_refresh.clicked.connect(self.refresh_ports)
        hbox_port.addWidget(self.cmb_ports, 1); hbox_port.addWidget(self.btn_refresh); vbox_conn.addLayout(hbox_port)

        hbox_connect = QHBoxLayout()
        self.btn_connect = QPushButton("CONNECT DEVICE"); self.btn_connect.setCheckable(True); self.btn_connect.setFixedHeight(45)
        self.btn_connect.setStyleSheet("QPushButton { background-color: #2563eb; color: white; font-weight: bold; border-radius: 6px; border: none; font-size: 13px; } QPushButton:hover { background-color: #1d4ed8; } QPushButton:checked { background-color: #0f766e; border: 2px solid #38bdf8; color: #e0f2fe; } QPushButton:disabled { background-color: #1e293b; color: #475569; }")
        self.btn_connect.clicked.connect(self.on_connect_toggled)
        hbox_connect.addWidget(self.btn_connect)
        
        vbox_conn.addLayout(hbox_connect)
        conn_group.setLayout(vbox_conn); right_layout.addWidget(conn_group)

        # 버튼 생성 (스타일은 헬퍼 함수에서 동적 적용)
        self.btn_live = QPushButton(); self.btn_live.setFixedHeight(40)
        self.btn_teach = QPushButton(); self.btn_teach.setFixedHeight(40)
        self.btn_test = QPushButton(); self.btn_test.setFixedHeight(40)
        
        self._set_btn_style(self.btn_live, False, "#38bdf8", "#0284c7", "1. START LIVE STREAM")
        self._set_btn_style(self.btn_teach, False, "#fbbf24", "#b45309", "2. ENTER TEACH MODE")
        self._set_btn_style(self.btn_test, False, "#a78bfa", "#6d28d9", "3. RUN RESULT TEST")
        
        self.btn_live.setEnabled(False)
        self.btn_teach.setEnabled(False)
        self.btn_test.setEnabled(False)
        
        self.btn_live.clicked.connect(self.toggle_live)
        self.btn_teach.clicked.connect(self.toggle_teach)
        self.btn_test.clicked.connect(self.toggle_test)
        
        right_layout.addWidget(self.btn_live); right_layout.addWidget(self.btn_teach); right_layout.addWidget(self.btn_test)

        self.txt_log = QTextEdit(); self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet("background-color: #000000; color: #10b981; font-family: 'Consolas'; font-size: 11px; border: 1px solid #1e293b; border-radius: 6px; margin-top: 10px;")
        right_layout.addWidget(self.txt_log, stretch=1); layout.addWidget(right_panel)

    # =========================================================================
    # [UI 상태 관리 헬퍼]
    # =========================================================================
    def _set_btn_style(self, btn, is_active, color_border, color_bg_active, text):
        btn.setText(text)
        if is_active:
            btn.setStyleSheet(f"QPushButton {{ background-color: {color_bg_active}; border-left: 5px solid {color_border}; color: white; font-weight: bold; text-align: left; padding-left: 15px; border-radius: 4px; }}")
        else:
            btn.setStyleSheet(f"QPushButton {{ background-color: #0f172a; border-left: 5px solid {color_border}; color: #e2e8f0; font-weight: bold; text-align: left; padding-left: 15px; border-radius: 4px; }} QPushButton:hover {{ background-color: #1e293b; }}")

    def _update_mode_disp(self, text, color_hex):
        self.lbl_mode.setText(text)
        self.mode_color_block.setStyleSheet(f"background-color: {color_hex}; border-radius: 3px;")

    def _set_mode(self, mode_str):
        self.vision_map.set_mode(mode_str)

    def _reset_teach_ui(self):
        self.btn_img_setup.hide(); self.btn_sel_anchor.hide()
        self.btn_sel_obj.hide(); self.btn_sel_shd.hide()
        self.vision_map.setup_panel.setVisible(False)

    def _update_vision_map_mode(self):
        """현재 켜진 기능에 따라 화면 모드 텍스트를 업데이트 (TEACH > TEST > LIVE 우선순위)"""
        if self.is_teach:
            self._set_mode("TEACH")
            self._update_mode_disp("TEACHING MODE", "#f59e0b")
        elif self.is_test:
            self._set_mode("TEST")
            self._update_mode_disp("TESTING", "#8b5cf6")
        elif self.is_live:
            self._set_mode("LIVE")
            self._update_mode_disp("LIVE VIEWING", "#0ea5e9")
        else:
            self._set_mode("STANDBY")
            self._update_mode_disp("STANDBY", "#64748b")

    # =========================================================================
    # [장치 제어 버튼 로직] - 상태 분리 (독립적 토글)
    # =========================================================================
    def toggle_live(self):
        if not self.worker or not self.worker.running: return

        if not self.is_live:
            if self.is_test: self.toggle_test() # TEST 작동 중이면 끄기 (하드웨어 동시 구동 불가)
            self.is_live = True
            self._set_btn_style(self.btn_live, True, "#38bdf8", "#0284c7", "1. STOP LIVE STREAM")
            self.btn_img_setup.show()
            self.worker.send_command(b'l')
            self.log_msg("라이브 스트리밍을 시작합니다.", "success")
        else:
            self.is_live = False
            self._set_btn_style(self.btn_live, False, "#38bdf8", "#0284c7", "1. START LIVE STREAM")
            if not self.is_teach: self.btn_img_setup.hide()
            self.worker.send_command(b'x')
            self.log_msg("라이브 스트리밍을 중지합니다. (STANDBY)", "info")
            
        self._update_vision_map_mode()

    def toggle_teach(self):
        if not self.is_teach:
            self.is_teach = True
            self._set_btn_style(self.btn_teach, True, "#fbbf24", "#b45309", "2. EXIT TEACH MODE")
            self.btn_sel_anchor.show(); self.btn_sel_obj.show(); self.btn_sel_shd.show()
            self.btn_img_setup.show()
            
            # 스트림이 모두 꺼져있다면, 스냅샷용 프레임 1장(c) 요청
            if not self.is_live and not self.is_test and self.worker:
                self.worker.send_command(b'c')
                self.log_msg("티칭용 정지 스냅샷을 캡처했습니다.", "process")
                
            # 앵커 좌표계 초기화 
            anchor = self.recipe_manager.tools[self.recipe_manager.anchor_tool_id]
            self.recipe_manager.set_teaching_anchor(anchor.original_roi.center().x(), anchor.original_roi.center().y(), 0.0)
            self.log_msg("티칭(Teaching) 모드 진입. 실시간 영상에서도 세팅 가능합니다.", "success")
        else:
            self.is_teach = False
            self._set_btn_style(self.btn_teach, False, "#fbbf24", "#b45309", "2. ENTER TEACH MODE")
            self._reset_teach_ui()
            if self.is_live: self.btn_img_setup.show()
            self.log_msg("티칭 모드를 종료합니다.", "info")
            
        self._update_vision_map_mode()
        
    def toggle_test(self):
        if not self.worker or not self.worker.running: return

        if not self.is_test:
            if self.is_live: self.toggle_live() # LIVE 작동 중이면 끄기
            self.is_test = True
            self._set_btn_style(self.btn_test, True, "#a78bfa", "#6d28d9", "3. STOP RESULT TEST")
            self.test_timer.start(100) # 💡 [타이머 시작] 100ms 마다 Burst 요청
            self.log_msg("테스트 구동(Burst)을 시작합니다.", "success")
        else:
            self.is_test = False
            self._set_btn_style(self.btn_test, False, "#a78bfa", "#6d28d9", "3. RUN RESULT TEST")
            self.test_timer.stop() # 💡 [타이머 중지]
            self.worker.send_command(b'x')
            self.log_msg("테스트 구동을 중지합니다.", "info")
            
        self._update_vision_map_mode()

    def _send_test_trigger(self):
        """타이머에 의해 주기적으로 호출되어 장비에 검사 명령을 내림"""
        if self.worker and self.worker.running:
            self.worker.send_command(b't')

    # =========================================================================
    # [통신 관련 로직]
    # =========================================================================
    def refresh_ports(self):
        self.cmb_ports.clear()
        for p in serial.tools.list_ports.comports(): 
            self.cmb_ports.addItem(f"{p.device} - {p.description if p.description else 'Unknown Device'}", p.device)

    def on_connect_toggled(self, checked=False):
        if self.worker is None or not self.worker.running:
            if self.cmb_ports.count() == 0: self.btn_connect.setChecked(False); return
            port_name = self.cmb_ports.currentData()
            self.log_msg(f"[{port_name}] 연결 시도 중...", "system")
            self.worker = OpenMVWorker(port_name)
            self.worker.log_signal.connect(self.log_msg)
            
            self.worker.frame_signal.connect(self.vision_map.set_image)
            self.worker.burst_results_signal.connect(self._on_burst_results)
            
            self.worker.connected_signal.connect(self._on_status_change)
            self.worker.start()
        else:
            self.worker.stop(); self.worker = None; self._on_status_change(0)

    def _on_burst_results(self, results, cycle_id):
        self.recipe_manager.update_results_from_burst(results)
        self.vision_map.update()

    def _on_status_change(self, state):
        self.led_status.set_state(state); connected = (state == 1)
        self.btn_live.setEnabled(connected); self.btn_teach.setEnabled(connected); self.btn_test.setEnabled(connected)
        self.cmb_ports.setEnabled(not connected); self.btn_refresh.setEnabled(not connected)
        
        if not connected:
            self.test_timer.stop()
            self.is_live = False; self.is_teach = False; self.is_test = False
            self.btn_connect.setChecked(False); self._reset_teach_ui()
            
            self._set_btn_style(self.btn_live, False, "#38bdf8", "#0284c7", "1. START LIVE STREAM")
            self._set_btn_style(self.btn_teach, False, "#fbbf24", "#b45309", "2. ENTER TEACH MODE")
            self._set_btn_style(self.btn_test, False, "#a78bfa", "#6d28d9", "3. RUN RESULT TEST")
            self._update_vision_map_mode()
            
            if state == 2: self.log_msg("장치와의 연결이 비정상적으로 종료되었습니다.", "error")
            else: self.log_msg("연결이 해제되었습니다.", "info")

    def log_msg(self, text, type="info"):
        color = "#cbd5e1"
        if type=="success": color="#34d399"
        elif type=="error": color="#ef4444"
        elif type=="system": color="#38bdf8"
        elif type=="process": color="#a78bfa"
        self.txt_log.append(f"<span style='color:{color}'>[{time.strftime('%H:%M:%S')}] {text}</span>")

    def closeEvent(self, event):
        self.test_timer.stop()
        if self.worker: self.worker.stop()
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.showMaximized()
    sys.exit(app.exec_())