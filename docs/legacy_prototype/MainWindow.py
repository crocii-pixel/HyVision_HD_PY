import sys
import os
import serial
import serial.tools.list_ports
import time
import struct
import json
import ctypes  # 💡 [추가] 윈도우 네이티브 API 호출용

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QLabel, QFrame, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                             QGroupBox, QTextEdit, QComboBox)
from PyQt5.QtCore import Qt, QPointF, QEvent
from PyQt5.QtGui import QColor

from StatusLED import StatusLED
from VisionMap import VisionMap
from OpenMVWorker import OpenMVWorker
from WinUtil import WinUtil

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.default_title = "HyVision Pro-Inspector (Board-Master Version)"
        self.setWindowTitle(self.default_title)
        self.setStyleSheet("background-color: #020617; color: #f1f5f9; font-family: 'Segoe UI', sans-serif;")
        
        # 💡 [추가] 윈도우 테마를 감지하여 제목 표시줄을 완벽한 다크 모드로 전환
        self._set_dark_titlebar()
        
        self.worker = None
        self.pending_target = "" 
        self._last_load_time = 0 
        
        self._fps_last_time = time.time()
        self._fps_frames = 0
        self._fps_value = 0.0
        self._last_frame_time = time.time()
        
        self.init_ui()
        self.refresh_ports()

    # 💡 [추가] Windows DWM API를 이용해 네이티브 제목 표시줄 다크 모드 강제 적용
    def _set_dark_titlebar(self):
        try:
            if sys.platform == "win32":
                hwnd = int(self.winId())
                value = ctypes.c_int(1) # 1 = 다크 모드 활성화
                # Windows 11 및 최신 Windows 10 (빌드 1809 이상)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
                # 구형 Windows 10 버전을 위한 Fallback
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass # 지원하지 않는 OS거나 권한 문제가 있을 경우 조용히 무시 (일반 창으로 뜸)

    def init_ui(self):
        main_widget = QWidget(); self.setCentralWidget(main_widget)
        layout = QHBoxLayout(main_widget); layout.setContentsMargins(15, 15, 15, 15)

        left_layout = QVBoxLayout()
        self.status_card = QFrame(); self.status_card.setFixedHeight(50); self.status_card.setStyleSheet("background-color: #0f172a; border-radius: 8px; border: 1px solid #1e293b;")
        status_layout = QHBoxLayout(self.status_card)
        
        self.mode_color_block = QFrame(); self.mode_color_block.setFixedSize(12, 24); self.mode_color_block.setStyleSheet("background-color: #64748b; border-radius: 8px;"); status_layout.addWidget(self.mode_color_block)
        self.lbl_mode = QLabel("STANDBY"); self.lbl_mode.setStyleSheet("font-weight: bold; color: #94a3b8; font-size: 18px; border-radius: 8px"); status_layout.addWidget(self.lbl_mode)
        
        status_layout.addStretch(1)
        self.lbl_indicator = QLabel("")
        self.lbl_indicator.setStyleSheet("font-size: 12px; font-weight: bold; background: transparent; border-radius: 8px;")
        self.lbl_indicator.setAlignment(Qt.AlignCenter)
        status_layout.addWidget(self.lbl_indicator)
        status_layout.addStretch(1)

        self.btn_auto_find = QPushButton("🔍 Find Model")
        self.btn_auto_find.setFixedWidth(135)
        self.btn_auto_find.setStyleSheet("QPushButton { background-color: #314361; color: white; border: 1px solid #475569; border-radius: 4px; padding: 6px; font-weight: bold; } QPushButton:hover { background-color: #334155; color: white; border-color: #38bdf8; }")
        self.btn_auto_find.clicked.connect(self.on_auto_find_clicked)
        self.btn_auto_find.hide()
        status_layout.addWidget(self.btn_auto_find)
        status_layout.addSpacing(20) 

        self.btn_img_setup = QPushButton("⚙️ Image Setup"); self.btn_img_setup.setFixedWidth(135); self.btn_img_setup.setStyleSheet("QPushButton { background-color: #1e293b; color: #cbd5e1; border: 1px solid #475569; border-radius: 4px; padding: 6px; font-weight: bold; } QPushButton:hover { background-color: #334155; color: white; border-color: #38bdf8; }")
        self.btn_img_setup.clicked.connect(lambda: self.vision_map.toggle_setup_panel()); self.btn_img_setup.hide(); status_layout.addWidget(self.btn_img_setup)
        
        self.btn_res_setup = QPushButton("📊 Result Setup"); self.btn_res_setup.setFixedWidth(135); self.btn_res_setup.setStyleSheet(self.btn_img_setup.styleSheet())
        self.btn_res_setup.clicked.connect(lambda: self.vision_map.toggle_result_panel()); self.btn_res_setup.hide(); status_layout.addWidget(self.btn_res_setup)

        btn_style_tmpl = "QPushButton { background-color: %s; color: white; font-weight: bold; border-radius: 4px; padding: 6px; border: 1px solid %s; } QPushButton:hover { background-color: %s; }"
        self.btn_sel_model = QPushButton("■ Model Region"); self.btn_sel_model.setFixedWidth(135); self.btn_sel_model.setStyleSheet(btn_style_tmpl % ("#0284c7", "#38bdf8", "#0369a1")); self.btn_sel_model.clicked.connect(lambda: self.vision_map.set_active_roi("MODEL")); self.btn_sel_model.hide(); status_layout.addWidget(self.btn_sel_model)
        self.btn_sel_align = QPushButton("⬚ Align Region"); self.btn_sel_align.setFixedWidth(135); self.btn_sel_align.setStyleSheet(btn_style_tmpl % ("#d97706", "#fbbf24", "#b45309")); self.btn_sel_align.clicked.connect(lambda: self.vision_map.set_active_roi("ALIGN")); self.btn_sel_align.hide(); status_layout.addWidget(self.btn_sel_align)
        self.sep_teach = QFrame(); self.sep_teach.setFrameShape(QFrame.VLine); self.sep_teach.setStyleSheet("color: #475569; margin: 0 5px;"); self.sep_teach.hide(); status_layout.addWidget(self.sep_teach)
        self.btn_sel_obj_line = QPushButton("━ Object Line"); self.btn_sel_obj_line.setFixedWidth(135); self.btn_sel_obj_line.setStyleSheet(btn_style_tmpl % ("#0ea5e9", "#7dd3fc", "#0284c7")); self.btn_sel_obj_line.clicked.connect(lambda: self.vision_map.set_active_roi("OBJ_LINE")); self.btn_sel_obj_line.hide(); status_layout.addWidget(self.btn_sel_obj_line)
        self.btn_sel_shd_line = QPushButton("━ Shadow Line"); self.btn_sel_shd_line.setFixedWidth(135); self.btn_sel_shd_line.setStyleSheet(btn_style_tmpl % ("#f97316", "#fdba74", "#ea580c")); self.btn_sel_shd_line.clicked.connect(lambda: self.vision_map.set_active_roi("SHD_LINE")); self.btn_sel_shd_line.hide(); status_layout.addWidget(self.btn_sel_shd_line)

        left_layout.addWidget(self.status_card)

        self.vision_map = VisionMap()
        self.vision_map.setup_panel.img_config_updated.connect(lambda cfg: self.worker.push_task('SET_IMG_STRUCT', cfg) if self.worker and self.worker.running else None)
        self.vision_map.stats_updated.connect(self._update_test_stats)
        self.vision_map.roi_updated.connect(self._on_roi_updated)

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

        lbl_fmt = QLabel("이미지 전송 포맷:"); lbl_fmt.setStyleSheet("font-size: 11px; color: #94a3b8; margin-top: 5px;"); vbox_conn.addWidget(lbl_fmt)
        self.cmb_format = QComboBox(); self.cmb_format.addItem("Compressed (JPEG)", 0); self.cmb_format.addItem("Original (Raw RGB565)", 1)
        self.cmb_format.setStyleSheet(self.cmb_ports.styleSheet()); self.cmb_format.currentIndexChanged.connect(self.on_format_changed); vbox_conn.addWidget(self.cmb_format)

        hbox_connect = QHBoxLayout()
        
        self.btn_connect = QPushButton("CONNECT DEVICE"); self.btn_connect.setCheckable(True); self.btn_connect.setFixedHeight(45)
        self.btn_connect.setStyleSheet("QPushButton { background-color: #2563eb; color: white; font-weight: bold; border-radius: 6px; border: none; font-size: 13px; } QPushButton:hover { background-color: #1d4ed8; } QPushButton:checked { background-color: #0f766e; border: 2px solid #38bdf8; color: #e0f2fe; } QPushButton:disabled { background-color: #1e293b; color: #475569; }")
        self.btn_connect.clicked.connect(self.on_connect_toggled)
        hbox_connect.addWidget(self.btn_connect, 7)
        
        self.btn_reset = QPushButton("RESET")
        self.btn_reset.setFixedHeight(45)
        self.btn_reset.setStyleSheet("QPushButton { background-color: #2563eb; color: white; font-weight: bold; border-radius: 6px; border: none; font-size: 13px; } QPushButton:hover { background-color: #0369a1; } QPushButton:disabled { background-color: #1e293b; color: #475569; }")
        self.btn_reset.clicked.connect(self.on_reset_clicked)
        self.btn_reset.setEnabled(False)
        hbox_connect.addWidget(self.btn_reset, 3) 
        
        vbox_conn.addLayout(hbox_connect)
        conn_group.setLayout(vbox_conn); right_layout.addWidget(conn_group)

        model_group = QGroupBox("MODEL LIBRARY"); model_group.setStyleSheet(grp_style)
        vbox_model = QVBoxLayout()
        self.list_models = QListWidget(); self.list_models.setStyleSheet("background-color: #020617; border: 1px solid #1e293b; height: 50px; border-radius: 4px; padding: 4px;"); vbox_model.addWidget(self.list_models)
        
        self.list_models.itemClicked.connect(self._on_model_selected)

        sub_btn_style = "QPushButton { background-color: #334155; color: #e2e8f0; border-radius: 4px; padding: 6px; font-weight: bold; font-size: 11px; } QPushButton:hover { background-color: #475569; }"
        hbox_list = QHBoxLayout()
        self.btn_sync = QPushButton("🔄 Refresh"); self.btn_sync.setStyleSheet(sub_btn_style); self.btn_sync.clicked.connect(lambda: self.worker.push_task('GET_MODELS') if self.worker else None)
        self.btn_delete = QPushButton("🗑️ Delete"); self.btn_delete.setStyleSheet(sub_btn_style); self.btn_delete.clicked.connect(self.delete_selected_model)
        hbox_list.addWidget(self.btn_sync); hbox_list.addWidget(self.btn_delete); vbox_model.addLayout(hbox_list)
        
        self.txt_reg_name = QLineEdit(); self.txt_reg_name.setPlaceholderText("New Model Name (e.g. indoor)")
        self.txt_reg_name.setStyleSheet("background-color: #020617; border: 1px solid #1e293b; padding: 8px; border-radius: 4px;"); vbox_model.addWidget(self.txt_reg_name)
        
        hbox_reg = QHBoxLayout()
        self.btn_register = QPushButton("REGISTER MODEL")
        self.btn_register.setFixedHeight(45)
        self.btn_register.setStyleSheet(self.btn_connect.styleSheet())
        self.btn_register.clicked.connect(self.register_model)
        self.btn_register.setEnabled(False) 
        
        self.btn_save_setup = QPushButton("SAVE SETUP")
        self.btn_save_setup.setFixedHeight(45)
        self.btn_save_setup.setStyleSheet(self.btn_connect.styleSheet())
        self.btn_save_setup.clicked.connect(self.update_setup)
        self.btn_save_setup.setEnabled(False) 
        
        hbox_reg.addWidget(self.btn_register)
        hbox_reg.addWidget(self.btn_save_setup)
        vbox_model.addLayout(hbox_reg)
        
        model_group.setLayout(vbox_model); right_layout.addWidget(model_group)

        self.btn_live = self._create_action_btn("1. START LIVE STREAM", "#0ea5e9")
        self.btn_teach = self._create_action_btn("2. ENTER TEACH MODE", "#f59e0b")
        self.btn_test = self._create_action_btn("3. RUN RESULT TEST", "#8b5cf6")
        self.btn_start = self._create_action_btn("4. RUN AUTO INSPECTION", "#10b981")
        
        self.btn_live.clicked.connect(self.toggle_live)
        self.btn_teach.clicked.connect(self.toggle_teach)
        self.btn_test.clicked.connect(self.toggle_test)
        self.btn_start.clicked.connect(self.toggle_start)
        
        right_layout.addWidget(self.btn_live); right_layout.addWidget(self.btn_teach); right_layout.addWidget(self.btn_test); right_layout.addWidget(self.btn_start)

        self.txt_log = QTextEdit(); self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet("background-color: #000000; color: #10b981; font-family: 'Consolas'; font-size: 11px; border: 1px solid #1e293b; border-radius: 6px; margin-top: 10px;")
        right_layout.addWidget(self.txt_log, stretch=1); layout.addWidget(right_panel)

        self.vision_map.setup_panel.installEventFilter(self)

    def on_reset_clicked(self):
        if self.worker and self.worker.running:
            reply = QMessageBox.question(self, "보드 하드웨어 리셋", 
                                         "OpenMV 보드를 강제로 재부팅하시겠습니까?\n(연결이 끊어지며 다시 CONNECT 버튼을 눌러야 합니다.)", 
                                         QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.log_msg("보드에 하드웨어 리셋 명령을 전송합니다...", "error")
                self.worker.push_task('RESET_BOARD')
                self.btn_connect.setChecked(False)
                self.on_connect_toggled()

    def on_auto_find_clicked(self):
        if not self.worker: return
        if not self.pending_target:
            QMessageBox.warning(self, "오류", "모델이 선택되어 있어야 찾을 수 있습니다.")
            return
        
        self.tracking_model = True
        self.log_msg("위치찾기 1회 수행 중...", "process")
        self.worker.push_task('TEST_MODE', self.pending_target)

    def eventFilter(self, obj, event):
        if obj == self.vision_map.setup_panel:
            if event.type() == QEvent.Show:
                self.btn_save_setup.setEnabled(True)
            elif event.type() == QEvent.Hide:
                if self.vision_map.mode in ["TEST", "TEACH"]:
                    self.btn_save_setup.setEnabled(True)
                else:
                    self.btn_save_setup.setEnabled(False)
        return super().eventFilter(obj, event)

    def _create_action_btn(self, text, color):
        btn = QPushButton(text); btn.setFixedHeight(40) 
        btn.setStyleSheet(f"QPushButton {{ background-color: #0f172a; border-left: 5px solid {color}; color: #e2e8f0; font-weight: bold; text-align: left; padding-left: 15px; border-radius: 4px; }} QPushButton:hover {{ background-color: #1e293b; }}")
        btn.setEnabled(False); return btn

    def _update_mode_disp(self, text, color_hex):
        self.lbl_mode.setText(text)
        self.mode_color_block.setStyleSheet(f"background-color: {color_hex}; border-radius: 3px;")

    def _on_roi_updated(self, roi_type, rx, ry, rw, rh):
        if roi_type in ["RESULT", "SPEC", "STATUS"]:
            self.vision_map.setup_panel.update_box_coords(roi_type, rx, ry, rw, rh)

    def _set_mode(self, mode_str):
        self.lbl_indicator.setVisible(False)
        self.vision_map.set_mode(mode_str)
        
    def _on_model_selected(self, item):
        if not item or not self.worker: return
        
        self.pending_target = item.data(Qt.UserRole)
        disp_name = item.text()
        self.log_msg(f"'{disp_name}' 선택: 통신 채널 확보 및 정보 초기화...", "process")
        
        self.worker.push_task('STOP_ALL')
        
        self._reset_teach_ui()
        self._reset_all_buttons()
        self._set_mode("STANDBY")
        self._update_mode_disp("STANDBY", "#64748b")

        self.vision_map.setup_panel.reset_to_defaults()
        self.vision_map.reset_to_defaults()
        
        self.worker.push_task('LOAD_META', self.pending_target)
        
        self.list_models.blockSignals(True)
        self.list_models.setCurrentItem(None)
        self.list_models.clearSelection()
        self.list_models.blockSignals(False)
        
    def _update_test_stats(self, result, dist, is_aligned):
        now = time.time()
        frame_time_ms = (now - getattr(self, '_last_frame_time', now)) * 1000.0
        self._last_frame_time = now

        self._fps_frames += 1
        if now - self._fps_last_time >= 1.0:
            self._fps_value = self._fps_frames / (now - self._fps_last_time)
            self._fps_frames = 0
            self._fps_last_time = now

        # 💡 [문자열 포맷팅] 숫자가 흔들리지 않도록 고정폭 + 빈칸을 HTML 공백(&nbsp;)으로 치환
        ft_str = f"{frame_time_ms:5.1f}".replace(' ', '&nbsp;')
        fps_str = f"{self._fps_value:5.1f}".replace(' ', '&nbsp;')

        if self.vision_map.mode == "TEACH" and getattr(self, 'tracking_model', False):
            self.tracking_model = False
            if result and result.get('isFound'):
                cx, cy, sx, sy = self.vision_map._get_render_params()
                res_cx = cx + (result['x'] + result['w']/2.0) * sx
                res_cy = cy + (result['y'] + result['h']/2.0) * sy
                if not self.vision_map.model_roi.isEmpty():
                    mod_cx = self.vision_map.model_roi.center().x()
                    mod_cy = self.vision_map.model_roi.center().y()
                    dx = res_cx - mod_cx
                    dy = res_cy - mod_cy
                    self.vision_map.model_roi.translate(dx, dy)
                    self.vision_map.obj_line_roi.translate(dx, dy)
                    self.vision_map.shd_line_roi.translate(dx, dy)
                    self.vision_map.roi_updated.emit("MODEL", *self.vision_map.get_real_roi(self.vision_map.model_roi))

                    new_mx, new_my, new_mw, new_mh = self.vision_map.get_real_roi(self.vision_map.model_roi)
                    self.vision_map.ref_model_center = QPointF(new_mx + new_mw / 2.0, new_my + new_mh / 2.0)

                self.worker.push_task('STOP_ALL')
                self.log_msg("모델 추적 및 위치 동기화 완료", "success")
        elif self.vision_map.mode in ["STANDBY", "TEACH"] or (self.vision_map.mode == "AUTO" and not self.vision_map.setup_panel.rst_cfg.get('view_indicator', True)):
            self.lbl_indicator.setVisible(False)
            return
        elif self.vision_map.mode == "LIVE":
            self.lbl_indicator.setVisible(True)
            fmt_idx = self.cmb_format.currentIndex()
            if fmt_idx == 0:
                qual = self.vision_map.setup_panel.img_cfg.get('quality', 50)
                comp_str = f"JPEG ({qual}%)"
            else:
                comp_str = "RAW (RGB565)"
                
            html = f"<span style='color:#94a3b8;'>Compression: </span><span style='color:#10b981;'>{comp_str}</span>"
            html += f" &nbsp;&nbsp;<span style='color:#475569;'>|</span>&nbsp;&nbsp; <span style='color:#94a3b8;'>FPS: </span><span style='color:#38bdf8; font-family: Consolas;'>{fps_str}</span>"
            html += f" &nbsp;&nbsp;<span style='color:#475569;'>|</span>&nbsp;&nbsp; <span style='color:#94a3b8;'>Frame Time: </span><span style='color:#e2e8f0; font-family: Consolas;'>{ft_str} ms</span>"
            self.lbl_indicator.setText(html)
            
        elif self.vision_map.mode == "AUTO":
            self.lbl_indicator.setVisible(True)
            res_color = self.vision_map.res_color.name()
            res_text = self.vision_map.res_text
            state_str = self.vision_map.internal_state
            state_color = self.vision_map.state_color.name()
            
            elapsed_val = self.vision_map.procTime
            el_str = f"{str(elapsed_val):>4}".replace(' ', '&nbsp;')

            html = f"<span style='color:#94a3b8;'>Result: </span><span style='color:{res_color}; font-weight:900;'>{res_text}</span> &nbsp;&nbsp;<span style='color:#475569;'>|</span>&nbsp;&nbsp; "
            html += f"<span style='color:#94a3b8;'>Progress: </span><span style='color:{state_color};'>{state_str}</span> &nbsp;&nbsp;<span style='color:#475569;'>|</span>&nbsp;&nbsp; "
            html += f"<span style='color:#94a3b8;'>Frame Time: </span><span style='color:#e2e8f0; font-family: Consolas;'>{ft_str} ms</span> &nbsp;&nbsp;<span style='color:#475569;'>|</span>&nbsp;&nbsp; "
            html += f"<span style='color:#94a3b8;'>Elapsed: </span><span style='color:#e2e8f0; font-family: Consolas;'>{el_str} ms</span>"
            self.lbl_indicator.setText(html)
                
        elif self.vision_map.mode == "TEST" and result and result.get('isFound'):
            self.lbl_indicator.setVisible(True)
            st = result.get('status', 1)
            
            if st == 3: loc_text = "LOCK"; color = "#10b981"
            elif st == 2: loc_text = "WEAK"; color = "#facc15"
            else: loc_text = "FAIL"; color = "#ef4444"
            
            align_text = "OK" if is_aligned else "FAIL"
            align_color = "#10b981" if is_aligned else "#ef4444"
            
            dist_str = f"{dist:5.2f}".replace(' ', '&nbsp;')
            elapsed_pt = result.get('procTime', self.vision_map.procTime)
            el_str = f"{str(elapsed_pt):>4}".replace(' ', '&nbsp;')
            
            html = f"<span style='color:#94a3b8;'>Location: </span><span style='color:{color};'>{loc_text}</span>"
            html += f" &nbsp;&nbsp;<span style='color:#475569;'>|</span>&nbsp;&nbsp; <span style='color:#94a3b8;'>Alignment: </span><span style='color:{align_color};'>{align_text}</span>"
            html += f" &nbsp;&nbsp;<span style='color:#475569;'>|</span>&nbsp;&nbsp; <span style='color:#94a3b8;'>STD Diff: </span><span style='color:#e2e8f0; font-family: Consolas;'>{result.get('stdev', 0):>3}</span>"
            html += f" &nbsp;&nbsp;<span style='color:#475569;'>|</span>&nbsp;&nbsp; <span style='color:#94a3b8;'>SIG Diff: </span><span style='color:#e2e8f0; font-family: Consolas;'>{result.get('diffVal', 0):>3}</span>"
            html += f" &nbsp;&nbsp;<span style='color:#475569;'>|</span>&nbsp;&nbsp; <span style='color:#38bdf8;'>Distance: <span style='font-family: Consolas;'>{dist_str}</span> px</span>"
            html += f" &nbsp;&nbsp;<span style='color:#475569;'>|</span>&nbsp;&nbsp; <span style='color:#94a3b8;'>Frame Time: </span><span style='color:#e2e8f0; font-family: Consolas;'>{ft_str} ms</span>"
            html += f" &nbsp;&nbsp;<span style='color:#475569;'>|</span>&nbsp;&nbsp; <span style='color:#94a3b8;'>Elapsed: </span><span style='color:#e2e8f0; font-family: Consolas;'>{el_str} ms</span>"
            
            self.lbl_indicator.setText(html)
        else:
            self.lbl_indicator.setVisible(False)

    def _save_last_port(self, port_text):
        try:
            os.makedirs("config", exist_ok=True)
            with open("config/init.cfg", "w", encoding="utf-8") as f:
                f.write(port_text)
        except: pass

    def _load_last_port(self):
        try:
            if os.path.exists("config/init.cfg"):
                with open("config/init.cfg", "r", encoding="utf-8") as f:
                    return f.read().strip()
        except: pass
        return ""

    def refresh_ports(self):
        self.cmb_ports.clear()
        for p in serial.tools.list_ports.comports(): 
            self.cmb_ports.addItem(f"{p.device} - {p.description if p.description else 'Unknown Device'}", p.device)
            
        last_port = self._load_last_port()
        if last_port:
            idx = self.cmb_ports.findText(last_port)
            if idx >= 0:
                self.cmb_ports.setCurrentIndex(idx)

    def on_format_changed(self, index):
        if self.worker and self.worker.running: self.worker.push_task('SET_IMG_FORMAT', self.cmb_format.itemData(index))

    def on_connect_toggled(self, checked=False):
        if self.worker is None or not self.worker.running:
            if self.cmb_ports.count() == 0: self.btn_connect.setChecked(False); return
            port_name = self.cmb_ports.currentData()
            self.log_msg(f"[{port_name}] 연결 시도 중...", "system")
            self.worker = OpenMVWorker(port_name)
            self.worker.log_signal.connect(self.log_msg)
            self.worker.frame_signal.connect(self.vision_map.set_image)
            self.worker.connected_signal.connect(self._on_status_change)
            self.worker.models_signal.connect(self._update_list)
            self.worker.meta_signal.connect(self._on_meta_loaded)
            self.worker.rst_signal.connect(self._on_rst_loaded)
            self.worker.info_signal.connect(self._on_info_loaded) 
            self.worker.start()
        else:
            self.worker.stop(); self.worker = None; self._on_status_change(0)

    def _on_info_loaded(self, info_data):
        fw_version = info_data.get('fw', 'Unknown FW')
        self.setWindowTitle(f"{self.default_title}  -  [ Connected: {fw_version} ]")
        self.log_msg(f"장치 펌웨어 정보 동기화: {fw_version}", "system")
        if 'img_cfg' in info_data:
            self.vision_map.setup_panel.load_settings({'image': info_data['img_cfg']})
            self.log_msg("보드 초기 이미지 설정 로드 및 UI 동기화 완료", "info")

    def _on_status_change(self, state):
        self.led_status.set_state(state); connected = (state == 1)
        self.btn_live.setEnabled(connected); self.btn_teach.setEnabled(connected); self.btn_test.setEnabled(connected); self.btn_start.setEnabled(connected)
        self.cmb_ports.setEnabled(not connected); self.btn_refresh.setEnabled(not connected)
        
        self.btn_reset.setEnabled(connected)
        
        if connected:
            self._save_last_port(self.cmb_ports.currentText())
            self.on_format_changed(self.cmb_format.currentIndex())
        else:
            self.btn_connect.setChecked(False); self._reset_teach_ui(); self.list_models.clear(); self._set_mode("STANDBY")
            self._update_mode_disp("STANDBY", "#64748b")
            self.setWindowTitle(self.default_title) 
            
            if state == 2: self.log_msg("장치와의 연결이 비정상적으로 종료되었습니다.", "error")
            else: self.log_msg("연결이 안전하게 해제되었습니다.", "info")

    def _update_list(self, models):
        self.list_models.clear()
        for m in models:
            disp_name = m[:-4] if m.endswith('.mdl') else m
            item = QListWidgetItem(disp_name)
            item.setData(Qt.UserRole, m)
            self.list_models.addItem(item)
        self.log_msg(f"보드 모델 목록 갱신 ({len(models)}개)", "info")

    def _get_meta_payload(self):
        mx, my, mw, mh = self.vision_map.get_real_roi(self.vision_map.model_roi)
        ax, ay, aw, ah = self.vision_map.get_real_roi(self.vision_map.align_roi)
        coord_data = struct.pack('<HHHHHHHH', mx, my, mw, mh, ax, ay, aw, ah)
        ox, oy, ow, oh = self.vision_map.get_real_roi(self.vision_map.obj_line_roi)
        sx, sy, sw, sh = self.vision_map.get_real_roi(self.vision_map.shd_line_roi)

        meta_data = {
            "align": {"ax": ax, "ay": ay, "aw": aw, "ah": ah},
            "model": {"mx": mx, "my": my, "mw": mw, "mh": mh},
            "model_th": {"lock": self.vision_map.setup_panel.model_lock_th, "weak": self.vision_map.setup_panel.model_weak_th},
            "rois": {"obj_line": {"x": ox, "y": oy, "w": ow, "h": oh}, "shd_line": {"x": sx, "y": sy, "w": sw, "h": sh}},
            "image": self.vision_map.setup_panel.img_cfg,
            "obj_line": self.vision_map.setup_panel.obj_cfg,
            "shd_line": self.vision_map.setup_panel.shd_cfg
        }
        return json.dumps(meta_data), coord_data

    def _get_img_payload(self):
        return self._get_meta_payload()

    def _execute_dismount(self):
        self.log_msg("디스크 충돌 방지를 위해 OPENMV 볼륨 강제 언마운트를 수행합니다...", "system")
        success, logs = WinUtil.dismount_openmv()
        for log_text in logs:
            msg_type = "system"
            if "성공" in log_text or "발견" in log_text: msg_type = "success"
            elif "실패" in log_text or "거부" in log_text or "없습니다" in log_text: msg_type = "error"
            self.log_msg(log_text, msg_type)

    def register_model(self):
        if not self.worker: return
        name = self.txt_reg_name.text().strip()
        if not name or self.vision_map.model_roi.isEmpty(): QMessageBox.warning(self, "오류", "이름을 입력하고 화면에 ROI를 그려주세요."); return
        if not name.endswith('.mdl'): name += '.mdl'
        
        self._execute_dismount()
        
        self.log_msg(f"'{name}' 좌표 및 파라미터를 보드(플래시 메모리)로 전송 중...", "process")
        meta_data, coord_data = self._get_meta_payload()
        self.worker.push_task('UPLOAD_MODEL', (name, coord_data, meta_data))
        
        rst_str = json.dumps(self.vision_map.setup_panel.rst_cfg)
        self.worker.push_task('UPLOAD_RST', (name, rst_str))
        
        self.txt_reg_name.clear()
        if self.vision_map.mode == "TEACH": self.toggle_teach()

    def update_setup(self):
        if not self.worker: return
        name = self.pending_target
        if not name:
            QMessageBox.warning(self, "오류", "업데이트할 대상을 모델 목록에서 선택해주세요.")
            return
        
        current_idx = self.vision_map.setup_panel.stack.currentIndex()
        is_panel_visible = self.vision_map.setup_panel.isVisible()
        current_mode = self.vision_map.mode

        if current_mode == "LIVE":
            if is_panel_visible and current_idx == 0:
                self.log_msg(f"'{name}' 이미지 설정(META) 갱신 중...", "process")
                self.worker.push_task('STOP_ALL')
                
                self._execute_dismount()
                
                img_data, coord_data = self._get_img_payload()
                self.worker.push_task('UPLOAD_IMGSETUP', (name, img_data))
                self.worker.push_task('LIVE')
                self.log_msg(f"'{name}' 이미지 설정 업데이트 명령 전송 완료", "success")

        elif self.vision_map.mode == "TEACH":
            self.log_msg(f"'{name}' 설정 정보(메타/파라미터/ROI) 갱신 중...", "process")
            meta_data, coord_data = self._get_meta_payload()
            
            self._execute_dismount()
            
            self.worker.push_task('UPLOAD_META', (name, meta_data))
            
            self.log_msg(f"'{name}' 설정(vision) 업데이트 명령 전송 완료", "success")
            
        elif self.vision_map.mode == "TEST":
            self.worker.push_task('STOP_ALL')
            
            self._execute_dismount()
            
            if is_panel_visible and current_idx == 0:
                self.log_msg(f"'{name}' 이미지 설정(META) 갱신 중...", "process")
                meta_data, coord_data = self._get_img_payload()
                self.worker.push_task('UPLOAD_IMGSETUP', (name, meta_data))
            self.log_msg(f"'{name}' 결과 설정(RST) 갱신 중...", "process")
            rst_str = json.dumps(self.vision_map.setup_panel.rst_cfg)
            self.worker.push_task('UPLOAD_RST', (name, rst_str))
            self.worker.push_task('TEST_MODE', name)
            self.log_msg(f"'{name}' 업데이트 명령 전송 완료", "success")
        else:
            self.log_msg("설정 저장은 LIVE, TEACH, TEST 모드에서만 가능합니다.", "error")

    def delete_selected_model(self):
        if not self.worker or not self.pending_target: return
        name = self.pending_target
        if QMessageBox.Yes == QMessageBox.question(self, "삭제", f"'{name}' 모델을 보드에서 삭제하시겠습니까?", QMessageBox.Yes|QMessageBox.No):
            self._execute_dismount()
            self.worker.push_task('DELETE_MODEL', name)
            self.pending_target = ""

    def log_msg(self, text, type="info"):
        color = "#cbd5e1"
        if type=="success": color="#34d399"
        elif type=="error": color="#ef4444"
        elif type=="system": color="#38bdf8"
        elif type=="process": color="#a78bfa"
        self.txt_log.append(f"<span style='color:{color}'>[{time.strftime('%H:%M:%S')}] {text}</span>")

    def _reset_all_buttons(self):
        btn_style = "QPushButton { background-color: #0f172a; border-left: 5px solid %s; color: #e2e8f0; font-weight: bold; text-align: left; padding-left: 15px; border-radius: 4px; } QPushButton:hover { background-color: #1e293b; }"
        self.btn_live.setText("1. START LIVE STREAM"); self.btn_live.setStyleSheet(btn_style % "#0ea5e9")
        self.btn_teach.setText("2. ENTER TEACH MODE"); self.btn_teach.setStyleSheet(btn_style % "#f59e0b")
        self.btn_test.setText("3. RUN RESULT TEST"); self.btn_test.setStyleSheet(btn_style % "#8b5cf6")
        self.btn_start.setText("4. RUN AUTO INSPECTION"); self.btn_start.setStyleSheet(btn_style % "#10b981")

    def _reset_teach_ui(self):
        self.btn_img_setup.hide(); self.btn_sel_model.hide(); self.btn_sel_align.hide()
        self.btn_sel_obj_line.hide(); self.btn_sel_shd_line.hide()
        self.sep_teach.hide()
        self.btn_res_setup.hide()
        
        self.btn_auto_find.hide()
        self.lbl_indicator.setVisible(False)
        self._fps_frames = 0
        self._fps_last_time = time.time()
        self._last_frame_time = time.time()
        
        self.btn_register.setEnabled(False) 
        
        self.vision_map.setup_panel.setVisible(False)

    def _on_meta_loaded(self, meta_data, _):
        if not meta_data:
            self.log_msg(f"'{self.pending_target}' 메타 정보 수신 실패 (파일 없음)", "error")
        else:
            self.log_msg(f"'{self.pending_target}' 메타 정보 수신 성공", "success")
            self.vision_map.setup_panel.load_settings(meta_data)
            al = meta_data.get('align', {})
            if al.get('aw', 0) > 0: self.vision_map.set_real_roi('ALIGN', al['ax'], al['ay'], al['aw'], al['ah'])
            mo = meta_data.get('model', {})
            if mo.get('mw', 0) > 0: 
                self.vision_map.set_real_roi('MODEL', mo['mx'], mo['my'], mo['mw'], mo['mh'])
                self.vision_map.ref_model_center = QPointF(mo['mx'] + mo['mw'] / 2.0, mo['my'] + mo['mh'] / 2.0)
            rois = meta_data.get('rois', {})
            ol = rois.get('obj_line', {})
            if ol.get('w', 0) > 0: self.vision_map.set_real_roi('OBJ_LINE', ol['x'], ol['y'], ol['w'], ol['h'])
            sl = rois.get('shd_line', {})
            if sl.get('w', 0) > 0: self.vision_map.set_real_roi('SHD_LINE', sl['x'], sl['y'], sl['w'], sl['h'])
            
        if self.worker and self.worker.running:
            self.worker.push_task('LOAD_RST', self.pending_target)

    def _on_rst_loaded(self, rst_data, _):
        if not rst_data:
            self.log_msg(f"'{self.pending_target}' 결과 설정(.rst) 수신 실패 (기본값 사용)", "error")
        else:
            self.log_msg(f"'{self.pending_target}' 결과 설정(.rst) 수신 성공", "success")
            self.vision_map.setup_panel.load_rst_settings(rst_data)
            
            self.vision_map.set_real_roi('RESULT', rst_data.get('res_x', 480), rst_data.get('res_y', 20), rst_data.get('res_w', 140), rst_data.get('res_h', 60))
            self.vision_map.set_real_roi('SPEC', rst_data.get('spec_x', 10), rst_data.get('spec_y', 205), rst_data.get('spec_w', 620), rst_data.get('spec_h', 265))
            self.vision_map.set_real_roi('STATUS', rst_data.get('status_x', 120), rst_data.get('status_y', 20), rst_data.get('status_w', 400), rst_data.get('status_h', 100))

    def toggle_live(self):
        if not self.worker.is_live:
            self._reset_all_buttons(); self._reset_teach_ui(); self.worker.push_task('STOP_ALL')
            self._set_mode("LIVE"); self._update_mode_disp("LIVE VIEWING", "#0ea5e9")
            self.btn_live.setText("1. STOP LIVE STREAM")
            self.btn_live.setStyleSheet("QPushButton { background-color: #0284c7; border-left: 5px solid #38bdf8; color: white; font-weight: bold; text-align: left; padding-left: 15px; border-radius: 4px; }")
            self.btn_img_setup.show(); self.worker.push_task('LIVE')
        else:
            self._reset_all_buttons(); self._reset_teach_ui(); self._set_mode("STANDBY"); self._update_mode_disp("STANDBY", "#64748b"); self.worker.push_task('STOP_ALL')

    def toggle_teach(self):
        if self.vision_map.mode != "TEACH":
            self._reset_all_buttons(); self._reset_teach_ui(); self.worker.push_task('STOP_ALL')
            
            self._set_mode("TEACH"); self._update_mode_disp("TEACHING MODE", "#f59e0b")
            self.btn_teach.setText("2. EXIT TEACH MODE")
            self.btn_teach.setStyleSheet("QPushButton { background-color: #b45309; border-left: 5px solid #fbbf24; color: white; font-weight: bold; text-align: left; padding-left: 15px; border-radius: 4px; }")
            self.btn_sel_model.show(); self.btn_sel_align.show(); self.sep_teach.show()
            self.btn_sel_obj_line.show(); self.btn_sel_shd_line.show()
            
            self.btn_auto_find.show()
            
            self.btn_register.setEnabled(True) 
            self.btn_save_setup.setEnabled(True) 
            
            self.worker.push_task('CAP_REF')
        else:
            self._reset_all_buttons(); self._reset_teach_ui(); self._set_mode("STANDBY"); self._update_mode_disp("STANDBY", "#64748b")

    def toggle_test(self):
        if self.vision_map.mode != "TEST":
            if not self.pending_target:
                self.log_msg("테스트할 모델을 먼저 선택해주세요.", "error")
                return
            
            self._reset_teach_ui(); self._reset_all_buttons(); self.worker.push_task('STOP_ALL')
            
            disp_name = self.pending_target[:-4] if self.pending_target.endswith('.mdl') else self.pending_target
            self._set_mode("TEST"); self._update_mode_disp(f"TESTING - {disp_name}", "#8b5cf6")
            self.btn_test.setText("3. STOP RESULT TEST")
            self.btn_test.setStyleSheet("QPushButton { background-color: #6d28d9; border-left: 5px solid #a78bfa; color: white; font-weight: bold; text-align: left; padding-left: 15px; border-radius: 4px; }")
            self.btn_res_setup.show()
            self.btn_img_setup.show()
            
            self.btn_save_setup.setEnabled(True) 

            self.worker.push_task('TEST_MODE', self.pending_target)
        else:
            self._reset_all_buttons(); self._reset_teach_ui(); self._set_mode("STANDBY"); self._update_mode_disp("STANDBY", "#64748b"); self.worker.push_task('STOP_ALL')

    def toggle_start(self):
        if self.vision_map.mode != "AUTO":
            if not self.pending_target:
                self.log_msg("자동 검사할 모델을 먼저 선택해주세요.", "error")
                return
            
            self._reset_teach_ui()
            self._reset_all_buttons()
            self.worker.push_task('STOP_ALL')
            
            disp_name = self.pending_target[:-4] if self.pending_target.endswith('.mdl') else self.pending_target
            self._set_mode("AUTO")
            self._update_mode_disp(f"AUTO INSPECT - {disp_name}", "#047857")
            
            self.btn_start.setText("4. STOP AUTO INSPECTION")
            self.btn_start.setStyleSheet("QPushButton { background-color: #047857; border-left: 5px solid #34d399; color: white; font-weight: bold; text-align: left; padding-left: 15px; border-radius: 4px; }")
            
            self.btn_res_setup.hide()
            self.btn_img_setup.hide()
            
            self.worker.push_task('TEST_MODE', self.pending_target)
        else:
            self._reset_all_buttons()
            self._reset_teach_ui()
            self._set_mode("STANDBY")
            self._update_mode_disp("STANDBY", "#64748b")
            self.worker.push_task('STOP_ALL')

    def closeEvent(self, event):
        if self.worker: self.worker.stop()
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.showMaximized()
    sys.exit(app.exec_())