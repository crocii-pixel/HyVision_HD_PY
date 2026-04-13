import sys
import serial
import serial.tools.list_ports
import time
import struct
import queue
import os
import re
import math
import json
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QLabel, QComboBox, QTextEdit, QGroupBox, QFrame,
                             QLineEdit, QListWidget, QMessageBox, QSlider, QCheckBox, QGridLayout,
                             QStackedWidget, QSpinBox, QDoubleSpinBox, QSizePolicy, QScrollArea)
from PyQt5.QtCore import Qt, QTimer, QRect, QRectF, QThread, pyqtSignal, QPoint, QPointF, QSize, QSizeF
from PyQt5.QtGui import QPainter, QColor, QPen, QImage, QCursor, QBrush, QRadialGradient, QFont, QIcon

# ==========================================
# [모듈화] PC 기반 비전 연산 엔진
# ==========================================
class PCVisionEngine:
    @staticmethod
    def apply_blur(qimg, passes):
        if passes <= 0: return qimg
        orig_fmt = qimg.format()
        res = qimg.convertToFormat(QImage.Format_RGB32)
        for _ in range(passes):
            temp = QImage(res.size(), QImage.Format_RGB32)
            temp.fill(Qt.black)
            p = QPainter(temp)
            p.setRenderHint(QPainter.Antialiasing, False)
            p.setRenderHint(QPainter.SmoothPixmapTransform, False)
            step = 1
            for dx in [-1, 0, 1]:
                for dy in [-1, 0, 1]:
                    p.setOpacity(1.0 / step)
                    p.drawImage(dx, dy, res)
                    step += 1
            p.end()
            res = temp
        return res.convertToFormat(orig_fmt)

    @staticmethod
    def apply_morphology(qimg, kernel_idx):
        if kernel_idx == 0: return qimg
        kernels = [
            [0, 0, 0, 0, 1, 0, 0, 0, 0], [-1, -1, -1, -1, 8, -1, -1, -1, -1], [1, 1, 1, 1, -8, 1, 1, 1, 1],
            [-1, -1, -1, -1, 9, -1, -1, -1, -1], [-1, -1, -1, -1, 10, -1, -1, -1, -1], [0, 1, 0, 1, -4, 1, 0, 1, 0],
            [1, 1, 1, 1, -8, 1, 1, 1, 1], [-1, -1, -1, 0, 6, 0, -1, -1, -1], [-1, 0, -1, -1, 6, -1, -1, 0, -1],
            [0, -1, -1, -1, 6, -1, -1, -1, 0], [-1, -1, 0, -1, 6, -1, 0, -1, -1], [-1, -1, -1, 0, 3, 0, 0, 0, 0],
            [0, 0, 0, 0, 3, 0, -1, -1, -1], [-1, 0, 0, -1, 3, 0, -1, 0, 0], [0, 0, -1, 0, 3, -1, 0, 0, -1],
            [0, -1, 0, -1, 5, -1, 0, -1, 0], [0, -1, 0, -1, 15, -1, 0, -1, 0], [-2, -1, 0, -1, 1, 1, 0, 1, 2],
            [-1, -1, -1, 0, 0, 0, 1, 1, 1], [1, 1, 1, 0, 0, 0, -1, -1, -1], [-1, -1, -1, 0, 0, 0, 1, 1, 1],
            [1, 1, 1, 1, -6, 1, 1, 1, 1]
        ]
        kernel = kernels[kernel_idx] if 0 <= kernel_idx < len(kernels) else kernels[0]
        if qimg.format() not in (QImage.Format_Grayscale8, QImage.Format_RGB888):
            qimg = qimg.convertToFormat(QImage.Format_RGB888)
        w, h = qimg.width(), qimg.height()
        if w < 3 or h < 3: return qimg
        is_color = (qimg.format() == QImage.Format_RGB888)
        bpl = qimg.bytesPerLine()
        ptr = qimg.constBits()
        ptr.setsize(bpl * h)
        src = memoryview(ptr)
        fmt = QImage.Format_RGB888 if is_color else QImage.Format_Grayscale8
        res_img = QImage(w, h, fmt)
        res_bpl = res_img.bytesPerLine()
        res_ptr = res_img.bits()
        res_ptr.setsize(res_bpl * h)
        res_buf = bytearray(res_bpl * h)
        k0, k1, k2 = kernel[0], kernel[1], kernel[2]
        k3, k4, k5 = kernel[3], kernel[4], kernel[5]
        k6, k7, k8 = kernel[6], kernel[7], kernel[8]
        for y in range(1, h - 1):
            src_offset = y * bpl
            res_offset = y * res_bpl
            for x in range(1, w - 1):
                if is_color:
                    for c in range(3):
                        idx = src_offset + x * 3 + c
                        v = (src[idx - bpl - 3] * k0 + src[idx - bpl] * k1 + src[idx - bpl + 3] * k2 +
                             src[idx - 3]       * k3 + src[idx]       * k4 + src[idx + 3]       * k5 +
                             src[idx + bpl - 3] * k6 + src[idx + bpl] * k7 + src[idx + bpl + 3] * k8)
                        res_buf[res_offset + x * 3 + c] = 255 if v > 255 else (0 if v < 0 else int(v))
                else:
                    idx = src_offset + x
                    v = (src[idx - bpl - 1] * k0 + src[idx - bpl] * k1 + src[idx - bpl + 1] * k2 +
                         src[idx - 1]       * k3 + src[idx]       * k4 + src[idx + 1]       * k5 +
                         src[idx + bpl - 1] * k6 + src[idx + bpl] * k7 + src[idx + bpl + 1] * k8)
                    res_buf[res_offset + x] = 255 if v > 255 else (0 if v < 0 else int(v))
        res_ptr[:] = res_buf
        return res_img

    @staticmethod
    def apply_pre_processing(qimg, cfg):
        processed = qimg
        if cfg.get('grayscale', True): processed = processed.convertToFormat(QImage.Format_Grayscale8)
        else: processed = processed.convertToFormat(QImage.Format_RGB888)
        blur_level = cfg.get('blur', 1)
        if blur_level > 0:
            processed = PCVisionEngine.apply_blur(processed, blur_level)
        if cfg.get('morph', True):
            kernel_idx = cfg.get('kernel', 0)
            processed = PCVisionEngine.apply_morphology(processed, kernel_idx)
        if cfg.get('invert', False):
            processed.invertPixels()
        return processed

    @staticmethod
    def find_line(qimg, cfg):
        w, h = qimg.width(), qimg.height()
        if w < 5 or h < 5: return None, None, 0.0
        if qimg.format() != QImage.Format_Grayscale8: qimg = qimg.convertToFormat(QImage.Format_Grayscale8)
        bpl = qimg.bytesPerLine()
        ptr = qimg.constBits()
        ptr.setsize(bpl * h)
        src = memoryview(ptr)

        NUM_SPLITS = 5
        split_w = w // NUM_SPLITS
        cut_ratio = cfg.get('cut_ratio', 0.55)
        max_dev = cfg.get('max_dev', 10)
        en_mid = cfg.get('mid_check', False)
        mid_ratio = cfg.get('mid_ratio', 0.7)
        
        profiles = []
        thresholds = []
        step_x = max(1, split_w // 10)
        for s in range(NUM_SPLITS):
            col_p = []
            start_x = s * split_w
            for y in range(h):
                row_sum = 0
                y_offset = y * bpl
                for x in range(start_x, start_x + split_w, step_x): row_sum += src[y_offset + x]
                col_p.append(row_sum / (split_w // step_x))
            p_min, p_max = min(col_p), max(col_p)
            th = p_min + (p_max - p_min) * cut_ratio
            thresholds.append(max(th, 20))
            profiles.append(col_p)
            
        def get_peaks(profile, th):
            peaks = []
            in_band = False
            st = 0
            for i, val in enumerate(profile):
                if val >= th:
                    if not in_band: st = i; in_band = True
                else:
                    if in_band: peaks.append((st + i - 1) // 2); in_band = False
            if in_band: peaks.append((st + len(profile) - 1) // 2)
            return peaks

        mid_idx = NUM_SPLITS // 2
        seeds = get_peaks(profiles[mid_idx], thresholds[mid_idx])
        scan_dir = cfg.get('scan_dir', 0)
        seeds.sort(reverse=(scan_dir == 1))
        best_line = []
        
        for seed_y in seeds:
            line_pts = [(mid_idx * split_w + split_w//2, seed_y)]
            curr_y = seed_y
            curr_x = mid_idx * split_w + split_w // 2
            for s in range(mid_idx + 1, NUM_SPLITS):
                peaks = get_peaks(profiles[s], thresholds[s])
                closest_py = None
                min_d = float('inf')
                target_x = s * split_w + split_w // 2
                for py in peaks:
                    d = abs(py - curr_y)
                    if d < min_d: min_d = d; closest_py = py
                if closest_py is not None and min_d <= max_dev:
                    is_valid = True
                    if en_mid:
                        mx, my = (curr_x + target_x) // 2, (curr_y + closest_py) // 2
                        if 1 <= mx < w-1 and 1 <= my < h-1:
                            m_off = my * bpl + mx
                            mid_val = max(src[m_off-bpl-1], src[m_off-bpl], src[m_off-bpl+1],
                                          src[m_off-1],     src[m_off],     src[m_off+1],
                                          src[m_off+bpl-1], src[m_off+bpl], src[m_off+bpl+1])
                        else: mid_val = src[my * bpl + mx] if (0<=mx<w and 0<=my<h) else 0
                        if mid_val < thresholds[s] * mid_ratio: is_valid = False
                    if is_valid:
                        line_pts.append((target_x, closest_py))
                        curr_y = closest_py
                        curr_x = target_x
                    else: break
                else: break 
                    
            curr_y = seed_y
            curr_x = mid_idx * split_w + split_w // 2
            for s in range(mid_idx - 1, -1, -1):
                peaks = get_peaks(profiles[s], thresholds[s])
                closest_py = None
                min_d = float('inf')
                target_x = s * split_w + split_w // 2
                for py in peaks:
                    d = abs(py - curr_y)
                    if d < min_d: min_d = d; closest_py = py
                if closest_py is not None and min_d <= max_dev:
                    is_valid = True
                    if en_mid:
                        mx, my = (curr_x + target_x) // 2, (curr_y + closest_py) // 2
                        if 1 <= mx < w-1 and 1 <= my < h-1:
                            m_off = my * bpl + mx
                            mid_val = max(src[m_off-bpl-1], src[m_off-bpl], src[m_off-bpl+1],
                                          src[m_off-1],     src[m_off],     src[m_off+1],
                                          src[m_off+bpl-1], src[m_off+bpl], src[m_off+bpl+1])
                        else: mid_val = src[my * bpl + mx] if (0<=mx<w and 0<=my<h) else 0
                        if mid_val < thresholds[s] * mid_ratio: is_valid = False
                    if is_valid:
                        line_pts.insert(0, (target_x, closest_py))
                        curr_y = closest_py
                        curr_x = target_x
                    else: break
                else: break
                    
            if len(line_pts) >= 2: best_line = line_pts; break
                
        if len(best_line) >= 2:
            n = len(best_line)
            sum_x = sum(p[0] for p in best_line)
            sum_y = sum(p[1] for p in best_line)
            sum_xx = sum(p[0] * p[0] for p in best_line)
            sum_xy = sum(p[0] * p[1] for p in best_line)
            denom = (n * sum_xx - sum_x * sum_x)
            if denom == 0: m = 0; b = sum_y / n
            else: m = (n * sum_xy - sum_x * sum_y) / denom; b = (sum_y - m * sum_x) / n
            angle = math.degrees(math.atan(m))
            y1 = b
            y2 = m * w + b
            return (0, y1), (w, y2), angle
        return None, None, 0.0

# ==========================================
# 백그라운드 시리얼 통신 쓰레드
# ==========================================
class OpenMVWorker(QThread):
    log_signal = pyqtSignal(str, str)
    frame_signal = pyqtSignal(QImage, object)
    connected_signal = pyqtSignal(int)
    models_signal = pyqtSignal(list)
    meta_signal = pyqtSignal(dict, str) 
    rst_signal = pyqtSignal(dict, str)
    info_signal = pyqtSignal(dict)

    def __init__(self, port_name):
        super().__init__()
        self.port_name = port_name
        self.serial_port = None
        self.running = False
        self.is_live = False
        self.is_test = False
        self.img_format = 0 
        self.cmd_queue = queue.Queue()
        
    def push_task(self, cmd, data=None):
        self.cmd_queue.put((cmd, data))

    def run(self):
        self.running = True
        try:
            self.serial_port = serial.Serial(self.port_name, baudrate=115200, timeout=1.0)
            self.serial_port.dtr = True
            self.serial_port.rts = True
            time.sleep(0.5)
            self.log_signal.emit(f"포트 연결 성공: {self.port_name}", "success")
            self.connected_signal.emit(1)
            
            self.serial_port.write(b'x') 
            time.sleep(0.1)
            self.serial_port.read_all()
            
            self.push_task('GET_INFO')
            self.push_task('GET_MODELS')

        except Exception as e:
            self.log_signal.emit(f"연결 오류: {e}", "error")
            self.connected_signal.emit(2)
            return

        while self.running:
            try:
                if not self.serial_port.is_open: 
                    break
                try:
                    cmd, data = self.cmd_queue.get(timeout=0.01)
                    self._handle_cmd(cmd, data)
                except queue.Empty:
                    if self.is_live: 
                        self._read_live_frame()
                    elif self.is_test: 
                        self._read_test_frame()
            except (serial.SerialException, OSError) as e:
                self.log_signal.emit(f"하드웨어 연결 끊김 감지: {e}", "error")
                self.connected_signal.emit(2)
                self.running = False
                break
                
        self.running = False
        self.connected_signal.emit(0)

    def _wait_for_response(self, timeout=3.0):
        start_t = time.time()
        while time.time() - start_t < timeout:
            if self.serial_port.in_waiting:
                res = self.serial_port.read_all()
                if b'OK' in res: return b'OK'
                if b'ER' in res: return b'ER'
            time.sleep(0.01)
        return b'TO'

    def _handle_cmd(self, cmd, data):
        if not self.serial_port.is_open: return

        if cmd == 'LIVE':
            self.serial_port.write(b'l')
            self.is_live = True
            self.is_test = False
            
        elif cmd == 'STOP_ALL':
            self.serial_port.write(b'x')
            self.is_live = False
            self.is_test = False
            
        elif cmd == 'SET_IMG_FORMAT':
            self.img_format = data
            self.serial_port.write(b'f' + struct.pack('<B', data))
            self._wait_for_response()
            
        elif cmd == 'SET_IMG_STRUCT':
            cfg = data
            payload = struct.pack('<iiiiiiiii', cfg['exp_auto'], cfg['exp_val'], cfg['gain_auto'], cfg['gain_val'], 
                                  cfg['contrast'], cfg['brightness'], cfg['vflip'], cfg['hmirror'], cfg['quality'])
            self.serial_port.write(b'i' + payload)
            self._wait_for_response(0.5)
            
        elif cmd == 'GET_INFO':
            self.serial_port.write(b'I')
            len_data = self.serial_port.read(4)
            if len(len_data) == 4:
                jl = struct.unpack('<I', len_data)[0]
                if 0 < jl < 10000:
                    try:
                        self.info_signal.emit(json.loads(self.serial_port.read(jl).decode('utf-8')))
                    except: pass

        elif cmd == 'GET_MODELS':
            self.serial_port.write(b'm')
            len_data = self.serial_port.read(4)
            if len(len_data) == 4:
                sl = struct.unpack('<I', len_data)[0]
                if sl > 0:
                    self.models_signal.emit(self.serial_port.read(sl).decode('utf-8').split(','))
                else:
                    self.models_signal.emit([])

        # 💡 [버그 픽스] LOAD_META 시 OS 수신 버퍼 청소 및 체인 분리
        elif cmd == 'LOAD_META':
            target_name = data
            self.serial_port.read_all() # 찌꺼기 청소
            self.serial_port.write(b'j' + struct.pack('<I', len(target_name)) + target_name.encode('utf-8'))
            ld = self.serial_port.read(4)
            meta_data = {}
            if len(ld) == 4:
                jl = struct.unpack('<I', ld)[0]
                if jl > 0:
                    try:
                        meta_data = json.loads(self.serial_port.read(jl).decode('utf-8'))
                    except: pass
            # 성공이든 실패든 빈 딕셔너리라도 무조건 에밋 (Deadlock 방지)
            self.meta_signal.emit(meta_data, "")

        # 💡 [버그 픽스] LOAD_RST 시 OS 수신 버퍼 청소 및 체인 분리
        elif cmd == 'LOAD_RST':
            target_name = data
            self.serial_port.read_all() # 찌꺼기 청소
            self.serial_port.write(b'R' + struct.pack('<I', len(target_name)) + target_name.encode('utf-8'))
            ld = self.serial_port.read(4)
            rst_data = {}
            if len(ld) == 4:
                jl = struct.unpack('<I', ld)[0]
                if jl > 0:
                    try:
                        rst_data = json.loads(self.serial_port.read(jl).decode('utf-8'))
                    except: pass
            self.rst_signal.emit(rst_data, "")

        elif cmd == 'TEST_MODE':
            name = data
            self.serial_port.read_all() # 찌꺼기 청소
            self.serial_port.write(b't' + struct.pack('<I', len(name)) + name.encode('utf-8'))
            if self._wait_for_response(5.0) == b'OK':
                self.is_test = True
                self.is_live = False
            else:
                self.log_signal.emit(f"'{name}' 테스트 모드 진입 실패", "error")

        elif cmd == 'CAP_REF':
            self.serial_port.write(b'c')
            self.is_live = False
            self.is_test = False
            if self._wait_sync(): 
                self._read_image_payload(is_jpeg=True)

        elif cmd == 'UPLOAD_MODEL':
            name, coord_data, json_str = data
            payload = b'u' + struct.pack('<I', len(name)) + name.encode('utf-8') + coord_data + struct.pack('<I', len(json_str)) + json_str.encode('utf-8')
            self.serial_port.write(payload)
            if self._wait_for_response(5.0) == b'OK':
                self.log_signal.emit(f"'{name}' 모델 저장 완료.", "success")
                self.push_task('GET_MODELS')
            else:
                self.log_signal.emit("모델 업로드 에러", "error")

        elif cmd == 'UPLOAD_RST':
            name, json_str = data
            payload = b'W' + struct.pack('<I', len(name)) + name.encode('utf-8') + struct.pack('<I', len(json_str)) + json_str.encode('utf-8')
            self.serial_port.write(payload)
            if self._wait_for_response(2.0) == b'OK':
                self.log_signal.emit(f"'{name}' Result(.rst) 설정이 보드에 저장되었습니다.", "success")
            else:
                self.log_signal.emit("Result 저장 에러", "error")

        elif cmd == 'DELETE_MODEL':
            name = data
            self.serial_port.write(b'd' + struct.pack('<I', len(name)) + name.encode('utf-8'))
            self._wait_for_response()
            self.push_task('GET_MODELS')

    def _wait_sync(self):
        st = time.time()
        while time.time() - st < 2.0:
            if self.serial_port.read(1) == b'\x55' and self.serial_port.read(1) == b'\xAA': 
                return True
        return False

    def _read_live_frame(self):
        if self._wait_sync(): 
            self._read_image_payload(is_jpeg=(self.img_format == 0))

    def _read_test_frame(self):
        if self._wait_sync():
            hd = self.serial_port.read(8)
            if len(hd) == 8:
                _, _, sz = struct.unpack('<HHI', hd)
                rd = self.serial_port.read(41)
                if len(rd) == 41:
                    isF, st, sc, x, y, w, h, ang, std, sig, pt = struct.unpack('<Biiiiiifiii', rd)
                    self._read_image_payload(sz, is_jpeg=(self.img_format==0), 
                                             result={'isFound':isF, 'status':st, 'score':sc, 'x':x, 'y':y, 'w':w, 'h':h, 'stdev':std, 'diffVal':sig})

    def _read_image_payload(self, sz=None, is_jpeg=True, result=None):
        if sz is None:
            ld = self.serial_port.read(4)
            sz = struct.unpack('<I', ld)[0] if len(ld)==4 else 0
        if 0 < sz < 2000000:
            dat = bytearray()
            while len(dat) < sz: 
                dat += self.serial_port.read(min(sz - len(dat), 16384))
                
            img = QImage.fromData(dat, "JPG") if is_jpeg else QImage(dat, 640, 480, QImage.Format_RGB16).copy()
            if img and not img.isNull(): 
                self.frame_signal.emit(img, result)

    def stop(self): 
        self.running = False
        self.wait()

# ==========================================
# 커스텀 LED 상태 표시기
# ==========================================
class StatusLED(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self.state = 0 
    def set_state(self, state):
        self.state = state; self.update()
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor("#475569") 
        if self.state == 1: color = QColor("#10b981") 
        elif self.state == 2: color = QColor("#ef4444") 
        gradient = QRadialGradient(8, 8, 8, 8, 8)
        gradient.setColorAt(0, color.lighter(150)); gradient.setColorAt(0.5, color); gradient.setColorAt(1, color.darker(200))
        painter.setBrush(QBrush(gradient)); painter.setPen(Qt.NoPen); painter.drawEllipse(0, 0, 16, 16)
        painter.setBrush(QBrush(QColor(255, 255, 255, 100))); painter.drawEllipse(3, 2, 6, 4)

# ==========================================
# 통합 오버레이 패널 
# ==========================================
class OverlayConfigPanel(QFrame):
    img_config_updated = pyqtSignal(dict) 
    ui_updated = pyqtSignal() 
    result_rect_toggled = pyqtSignal()
    save_rst_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(320, 500) 
        self.setVisible(False) 
        self.setObjectName("MainPanel")
        self.setStyleSheet("""
            QFrame#MainPanel { background-color: #0f172a; border: 1px solid #334155; border-radius: 8px; }
            QWidget { background-color: transparent; color: #e2e8f0; }
            QLabel { border: none; background: transparent; font-size: 11px; font-weight: bold; color: #94a3b8; }
            QSlider::groove:horizontal { border: 1px solid #334155; height: 6px; background: #1e293b; border-radius: 3px; }
            QSlider::handle:horizontal { background: #38bdf8; border: none; width: 14px; margin: -4px 0; border-radius: 7px; }
            QSlider::add-page:horizontal { background: #1e293b; border-radius: 3px; }
            QSlider::sub-page:horizontal { background: #0ea5e9; border-radius: 3px; }
            QCheckBox { font-size: 11px; font-weight: bold; border: none; background: transparent; }
            QCheckBox::indicator { width: 14px; height: 14px; border-radius: 3px; border: 1px solid #475569; background: #0f172a; }
            QCheckBox::indicator:checked { background: #38bdf8; border-color: #0ea5e9; }
            QSpinBox, QDoubleSpinBox, QComboBox { background-color: #0f172a; border: 1px solid #334155; padding: 4px; border-radius: 4px; color: white; font-size: 11px; }
            QStackedWidget { border: none; background: transparent; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        self.img_cfg = {}
        self.obj_cfg = {}
        self.shd_cfg = {}
        self.rst_cfg = {}
        self.model_lock_th = 90
        self.model_weak_th = 70
        self._is_loading = False

        self.page_image = self._create_image_setup_page()
        self.page_model = self._create_model_setup_page()
        self.page_align = self._create_align_setup_page()
        self.page_obj_line, self.page_obj_line_ui = self._create_line_setup_page("━ OBJECT LINE SETTINGS", self.obj_cfg)
        self.page_shd_line, self.page_shd_line_ui = self._create_line_setup_page("━ SHADOW LINE SETTINGS", self.shd_cfg)
        self.page_result = self._create_result_setup_page()

        self.stack.addWidget(self.page_image)    
        self.stack.addWidget(self.page_model)    
        self.stack.addWidget(self.page_align)    
        self.stack.addWidget(self.page_obj_line) 
        self.stack.addWidget(self.page_shd_line) 
        self.stack.addWidget(self.page_result)
        
        self.reset_to_defaults() # 💡 인스턴스 생성 시 기본값 초기화 수행

    # 💡 [신규] 기본값 완전 초기화 로직
    def reset_to_defaults(self):
        self._is_loading = True
        self.img_cfg = {'exp_auto': 1, 'exp_val': 200000, 'gain_auto': 0, 'gain_val': 10, 'contrast': 0, 'brightness': 0, 'vflip': 0, 'hmirror': 0, 'quality': 50}
        self.obj_cfg = {'grayscale': True, 'blur': 1, 'morph': True, 'invert': False, 'scan_dir': 0, 'kernel': 11, 'cut_ratio': 0.55, 'max_dev': 10, 'show_prep': False, 'show_line': True}
        self.shd_cfg = {'grayscale': True, 'blur': 1, 'morph': True, 'invert': False, 'scan_dir': 0, 'kernel': 12, 'cut_ratio': 0.55, 'max_dev': 10, 'show_prep': False, 'show_line': True}
        self.rst_cfg = {
            'std_weak': 4, 'std_fail': 12, 'sig_weak': 4, 'sig_fail': 7,
            'dist_min': 10.0, 'dist_max': 50.0,
            'view_result': True, 'res_x': 480, 'res_y': 20, 'res_w': 140, 'res_h': 60,
            'view_loc_roi': True, 'view_loc_cross': True,
            'view_align_roi': True,
            'view_obj_roi': True, 'view_obj_line': True,
            'view_shd_roi': True, 'view_shd_line': True,
            'view_dist_line': True
        }
        self.model_lock_th = 90
        self.model_weak_th = 70

        # UI 요소 초기화
        self.chk_auto_exp.setChecked(True); self.sl_exp.setValue(200000)
        self.chk_auto_gain.setChecked(False); self.sl_gain.setValue(10)
        self.sl_contrast.setValue(0); self.sl_brightness.setValue(0); self.sl_quality.setValue(50)
        self.chk_vflip.setChecked(False); self.chk_hmirror.setChecked(False)

        def update_line_ui(cfg, ui_dict):
            ui_dict['grayscale'].setChecked(cfg['grayscale'])
            ui_dict['blur'].setValue(cfg['blur'])
            ui_dict['morph'].setChecked(cfg['morph'])
            ui_dict['invert'].setChecked(cfg['invert'])
            ui_dict['scan_dir'].setCurrentIndex(cfg['scan_dir'])
            ui_dict['kernel'].setCurrentIndex(cfg['kernel'])
            ui_dict['cut_ratio'].setValue(cfg['cut_ratio'])
            ui_dict['max_dev'].setValue(cfg['max_dev'])
            ui_dict['show_prep'].setChecked(cfg['show_prep'])
            ui_dict['show_line'].setChecked(cfg['show_line'])

        update_line_ui(self.obj_cfg, self.page_obj_line_ui)
        update_line_ui(self.shd_cfg, self.page_shd_line_ui)

        self.sp_std_weak.setValue(self.rst_cfg['std_weak'])
        self.sp_std_fail.setValue(self.rst_cfg['std_fail'])
        self.sp_sig_weak.setValue(self.rst_cfg['sig_weak'])
        self.sp_sig_fail.setValue(self.rst_cfg['sig_fail'])
        self.sp_dist_min.setValue(self.rst_cfg['dist_min'])
        self.sp_dist_max.setValue(self.rst_cfg['dist_max'])
        
        self.chk_view_res.setChecked(self.rst_cfg['view_result'])
        self.chk_view_loc_roi.setChecked(self.rst_cfg['view_loc_roi'])
        self.chk_view_loc_cross.setChecked(self.rst_cfg['view_loc_cross'])
        self.chk_view_align.setChecked(self.rst_cfg['view_align_roi'])
        self.chk_view_obj_roi.setChecked(self.rst_cfg['view_obj_roi'])
        self.chk_view_obj_line.setChecked(self.rst_cfg['view_obj_line'])
        self.chk_view_shd_roi.setChecked(self.rst_cfg['view_shd_roi'])
        self.chk_view_shd_line.setChecked(self.rst_cfg['view_shd_line'])
        self.chk_view_dist.setChecked(self.rst_cfg['view_dist_line'])
        
        self.update_res_labels(self.rst_cfg['res_x'], self.rst_cfg['res_y'], self.rst_cfg['res_w'], self.rst_cfg['res_h'])

        self._is_loading = False
        self.ui_updated.emit()
        self.img_config_updated.emit(self.img_cfg)

    def _apply_image_cfg(self, _=None):
        if self._is_loading: return 
        self.img_cfg['exp_auto'] = 1 if self.chk_auto_exp.isChecked() else 0
        self.img_cfg['exp_val'] = self.sl_exp.value()
        self.img_cfg['gain_auto'] = 1 if self.chk_auto_gain.isChecked() else 0
        self.img_cfg['gain_val'] = self.sl_gain.value()
        self.img_cfg['contrast'] = self.sl_contrast.value()
        self.img_cfg['brightness'] = self.sl_brightness.value()
        self.img_cfg['vflip'] = 1 if self.chk_vflip.isChecked() else 0
        self.img_cfg['hmirror'] = 1 if self.chk_hmirror.isChecked() else 0
        self.img_cfg['quality'] = self.sl_quality.value()
        self.img_config_updated.emit(self.img_cfg)

    def load_settings(self, meta_data):
        self._is_loading = True 
        try:
            if 'image' in meta_data:
                self.img_cfg.update(meta_data['image'])
                self.chk_auto_exp.setChecked(self.img_cfg.get('exp_auto', 1) == 1)
                self.sl_exp.setValue(self.img_cfg.get('exp_val', 200000))
                self.chk_auto_gain.setChecked(self.img_cfg.get('gain_auto', 0) == 1)
                self.sl_gain.setValue(self.img_cfg.get('gain_val', 10))
                self.sl_contrast.setValue(self.img_cfg.get('contrast', 0))
                self.sl_brightness.setValue(self.img_cfg.get('brightness', 0))
                self.chk_vflip.setChecked(self.img_cfg.get('vflip', 0) == 1)
                self.chk_hmirror.setChecked(self.img_cfg.get('hmirror', 0) == 1)
                self.sl_quality.setValue(self.img_cfg.get('quality', 50))
            def update_line_ui(cfg, ui_dict):
                ui_dict['grayscale'].setChecked(cfg.get('grayscale', True))
                ui_dict['blur'].setValue(cfg.get('blur', 1))
                ui_dict['morph'].setChecked(cfg.get('morph', True))
                ui_dict['invert'].setChecked(cfg.get('invert', False))
                ui_dict['scan_dir'].setCurrentIndex(cfg.get('scan_dir', 0))
                ui_dict['kernel'].setCurrentIndex(cfg.get('kernel', 0))
                ui_dict['cut_ratio'].setValue(cfg.get('cut_ratio', 0.55))
                ui_dict['max_dev'].setValue(cfg.get('max_dev', 10))
            if 'obj_line' in meta_data:
                self.obj_cfg.update(meta_data['obj_line'])
                update_line_ui(self.obj_cfg, self.page_obj_line_ui)
            if 'shd_line' in meta_data:
                self.shd_cfg.update(meta_data['shd_line'])
                update_line_ui(self.shd_cfg, self.page_shd_line_ui)
        finally:
            self._is_loading = False
            self.img_config_updated.emit(self.img_cfg) 

    def load_rst_settings(self, rst_data):
        self._is_loading = True
        try:
            self.rst_cfg.update(rst_data)
            self.sp_std_weak.setValue(self.rst_cfg.get('std_weak', 4))
            self.sp_std_fail.setValue(self.rst_cfg.get('std_fail', 12))
            self.sp_sig_weak.setValue(self.rst_cfg.get('sig_weak', 4))
            self.sp_sig_fail.setValue(self.rst_cfg.get('sig_fail', 7))
            self.sp_dist_min.setValue(self.rst_cfg.get('dist_min', 10.0))
            self.sp_dist_max.setValue(self.rst_cfg.get('dist_max', 50.0))
            
            self.chk_view_res.setChecked(self.rst_cfg.get('view_result', True))
            self.chk_view_loc_roi.setChecked(self.rst_cfg.get('view_loc_roi', True))
            self.chk_view_loc_cross.setChecked(self.rst_cfg.get('view_loc_cross', True))
            self.chk_view_align.setChecked(self.rst_cfg.get('view_align_roi', True))
            self.chk_view_obj_roi.setChecked(self.rst_cfg.get('view_obj_roi', True))
            self.chk_view_obj_line.setChecked(self.rst_cfg.get('view_obj_line', True))
            self.chk_view_shd_roi.setChecked(self.rst_cfg.get('view_shd_roi', True))
            self.chk_view_shd_line.setChecked(self.rst_cfg.get('view_shd_line', True))
            self.chk_view_dist.setChecked(self.rst_cfg.get('view_dist_line', True))
            
            self.update_res_labels(self.rst_cfg.get('res_x', 480), self.rst_cfg.get('res_y', 20), 
                                   self.rst_cfg.get('res_w', 140), self.rst_cfg.get('res_h', 60))
        finally:
            self._is_loading = False
            self.ui_updated.emit()

    def update_res_labels(self, x, y, w, h):
        self.rst_cfg['res_x'], self.rst_cfg['res_y'] = int(x), int(y)
        self.rst_cfg['res_w'], self.rst_cfg['res_h'] = int(w), int(h)
        self.lbl_res_coords.setText(f"X:{int(x)} Y:{int(y)} W:{int(w)} H:{int(h)}")

    def show_page(self, page_name):
        pages = {"IMAGE": 0, "MODEL": 1, "ALIGN": 2, "OBJ_LINE": 3, "SHD_LINE": 4, "RESULT": 5}
        if page_name in pages: self.stack.setCurrentIndex(pages[page_name]); self.setVisible(True)

    def _create_image_setup_page(self):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0,0,0,0)
        title = QLabel("⚙️ VISION PRE-PROCESSING")
        title.setStyleSheet("color: #38bdf8; font-size: 14px; font-weight: 900; border-bottom: 1px solid #334155; padding-bottom: 8px; margin-bottom: 5px;")
        layout.addWidget(title)
        self.chk_auto_exp = QCheckBox("Auto Exposure"); self.chk_auto_exp.setChecked(self.img_cfg.get('exp_auto',1)==1)
        self.chk_auto_exp.toggled.connect(lambda c: [self.sl_exp.setEnabled(not c), self._apply_image_cfg()]); layout.addWidget(self.chk_auto_exp)
        self.sl_exp = self._create_slider(1000, 300000, self.img_cfg.get('exp_val',200000)); self.sl_exp.setEnabled(not self.chk_auto_exp.isChecked())
        self.sl_exp.valueChanged.connect(lambda v: self.lbl_exp_val.setText(f"{v:,} us")); self.sl_exp.sliderReleased.connect(self._apply_image_cfg)      
        self.lbl_exp_val = QLabel(f"{self.img_cfg.get('exp_val',200000):,} us"); self.lbl_exp_val.setAlignment(Qt.AlignRight)
        layout.addLayout(self._wrap_slider("Exposure Time", self.sl_exp, self.lbl_exp_val))
        self.chk_auto_gain = QCheckBox("Auto Gain"); self.chk_auto_gain.setChecked(self.img_cfg.get('gain_auto',0)==1)
        self.chk_auto_gain.toggled.connect(lambda c: [self.sl_gain.setEnabled(not c), self._apply_image_cfg()]); layout.addWidget(self.chk_auto_gain)
        self.sl_gain = self._create_slider(0, 32, self.img_cfg.get('gain_val',10)); self.sl_gain.setEnabled(not self.chk_auto_gain.isChecked())
        self.sl_gain.valueChanged.connect(lambda v: self.lbl_gain_val.setText(f"{v} dB")); self.sl_gain.sliderReleased.connect(self._apply_image_cfg)
        self.lbl_gain_val = QLabel(f"{self.img_cfg.get('gain_val',10)} dB"); self.lbl_gain_val.setAlignment(Qt.AlignRight)
        layout.addLayout(self._wrap_slider("Gain Limit", self.sl_gain, self.lbl_gain_val))
        self.sl_contrast = self._create_slider(-3, 3, self.img_cfg.get('contrast',0))
        self.sl_contrast.valueChanged.connect(lambda v: self.lbl_contrast_val.setText(str(v))); self.sl_contrast.sliderReleased.connect(self._apply_image_cfg)
        self.lbl_contrast_val = QLabel(str(self.img_cfg.get('contrast',0))); self.lbl_contrast_val.setAlignment(Qt.AlignRight)
        layout.addLayout(self._wrap_slider("Contrast", self.sl_contrast, self.lbl_contrast_val))
        self.sl_brightness = self._create_slider(-3, 3, self.img_cfg.get('brightness',0))
        self.sl_brightness.valueChanged.connect(lambda v: self.lbl_brightness_val.setText(str(v))); self.sl_brightness.sliderReleased.connect(self._apply_image_cfg)
        self.lbl_brightness_val = QLabel(str(self.img_cfg.get('brightness',0))); self.lbl_brightness_val.setAlignment(Qt.AlignRight)
        layout.addLayout(self._wrap_slider("Brightness", self.sl_brightness, self.lbl_brightness_val))
        self.sl_quality = self._create_slider(10, 100, self.img_cfg.get('quality',50))
        self.sl_quality.valueChanged.connect(lambda v: self.lbl_quality_val.setText(f"{v} %")); self.sl_quality.sliderReleased.connect(self._apply_image_cfg)
        self.lbl_quality_val = QLabel(f"{self.img_cfg.get('quality',50)} %"); self.lbl_quality_val.setAlignment(Qt.AlignRight)
        layout.addLayout(self._wrap_slider("JPEG Quality", self.sl_quality, self.lbl_quality_val))
        hbox_flip = QHBoxLayout()
        self.chk_vflip = QCheckBox("V-Flip"); self.chk_vflip.setChecked(self.img_cfg.get('vflip',0)==1); self.chk_vflip.toggled.connect(self._apply_image_cfg)
        self.chk_hmirror = QCheckBox("H-Mirror"); self.chk_hmirror.setChecked(self.img_cfg.get('hmirror',0)==1); self.chk_hmirror.toggled.connect(self._apply_image_cfg)
        hbox_flip.addWidget(self.chk_vflip); hbox_flip.addWidget(self.chk_hmirror); layout.addLayout(hbox_flip)
        layout.addStretch(); return page

    def _create_model_setup_page(self):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0,0,0,0)
        title = QLabel("■ MODEL ROI SETTINGS"); title.setStyleSheet("color: #38bdf8; font-size: 14px; font-weight: 900; border-bottom: 1px solid #334155; padding-bottom: 8px; margin-bottom: 5px;")
        layout.addWidget(title)
        lbl_info = QLabel("설정할 항목이 없습니다.\n(화면에서 모델 영역을 직접 조작하세요.)")
        lbl_info.setAlignment(Qt.AlignCenter); lbl_info.setStyleSheet("color: #64748b; font-size: 12px; padding: 10px 0;")
        layout.addWidget(lbl_info); layout.addStretch(); return page

    def _create_align_setup_page(self):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0,0,0,0)
        title = QLabel("⬚ ALIGNMENT SETTINGS"); title.setStyleSheet("color: #38bdf8; font-size: 14px; font-weight: 900; border-bottom: 1px solid #334155; padding-bottom: 8px; margin-bottom: 5px;")
        layout.addWidget(title)
        lbl_info = QLabel("설정할 항목이 없습니다.\n(좌표 및 크기는 화면에서 직접 조작)"); lbl_info.setAlignment(Qt.AlignCenter); lbl_info.setStyleSheet("color: #64748b; font-size: 12px; padding: 10px 0;")
        layout.addWidget(lbl_info); layout.addStretch(); return page

    def _create_line_setup_page(self, title_text, cfg_dict):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0,0,0,0)
        title = QLabel(title_text); title.setStyleSheet("color: #38bdf8; font-size: 14px; font-weight: 900; border-bottom: 1px solid #334155; padding-bottom: 8px; margin-bottom: 5px;")
        layout.addWidget(title)
        ui_dict = {}; grid = QGridLayout(); grid.setSpacing(10); row = 0

        chk_gray = QCheckBox("Enable Grayscale"); ui_dict['grayscale'] = chk_gray
        chk_gray.toggled.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'grayscale', v)); grid.addWidget(chk_gray, row, 0, 1, 2); row += 1
        grid.addWidget(QLabel("Gaussian Blur:"), row, 0)
        spin_blur = QSpinBox(); spin_blur.setRange(0, 5); ui_dict['blur'] = spin_blur
        spin_blur.valueChanged.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'blur', v)); grid.addWidget(spin_blur, row, 1); row += 1
        chk_morph = QCheckBox("Enable Morphology"); ui_dict['morph'] = chk_morph
        chk_morph.toggled.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'morph', v)); grid.addWidget(chk_morph, row, 0, 1, 2); row += 1
        chk_invert = QCheckBox("Invert"); ui_dict['invert'] = chk_invert
        chk_invert.toggled.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'invert', v)); grid.addWidget(chk_invert, row, 0, 1, 2); row += 1

        line1 = QFrame(); line1.setFrameShape(QFrame.HLine); line1.setStyleSheet("color: #334155; margin: 5px 0;")
        grid.addWidget(line1, row, 0, 1, 2); row += 1

        grid.addWidget(QLabel("Scan Direction:"), row, 0)
        cmb_dir = QComboBox(); cmb_dir.addItems(["Top -> Bottom", "Bottom -> Top"]); ui_dict['scan_dir'] = cmb_dir
        cmb_dir.currentIndexChanged.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'scan_dir', v)); grid.addWidget(cmb_dir, row, 1); row += 1
        grid.addWidget(QLabel("Kernel Type:"), row, 0)
        cmb_kernel = QComboBox(); kernels = ["ORIGINAL", "OUTLINE_STD", "OUTLINE_NEG", "OUTLINE_STR", "OUTLINE_MAX", "EDGE_DET_4", "EDGE_DET_8", "HORZ_REINF", "VERT_REINF", "DIAG_1_LT_RB", "DIAG_2_RT_LB", "HORZ_D2B", "HORZ_B2D", "VERT_D2B", "VERT_B2D", "SHARPEN_L", "SHARPEN_H", "EMBOSS_STD", "EMBOSS_HORZ", "EMBOSS_HORZ_D2B", "EMBOSS_HORZ_B2D", "BLUR_HPF"]
        cmb_kernel.addItems(kernels); ui_dict['kernel'] = cmb_kernel
        cmb_kernel.currentIndexChanged.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'kernel', v)); chk_morph.toggled.connect(cmb_kernel.setEnabled); grid.addWidget(cmb_kernel, row, 1); row += 1

        grid.addWidget(QLabel("Brightness Cut Ratio:"), row, 0)
        spin_cut = QDoubleSpinBox(); spin_cut.setRange(0.0, 1.0); spin_cut.setSingleStep(0.05); ui_dict['cut_ratio'] = spin_cut
        spin_cut.valueChanged.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'cut_ratio', v)); grid.addWidget(spin_cut, row, 1); row += 1
        grid.addWidget(QLabel("Max Deviation (px):"), row, 0)
        spin_dev = QSpinBox(); spin_dev.setRange(1, 50); ui_dict['max_dev'] = spin_dev
        spin_dev.valueChanged.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'max_dev', v)); grid.addWidget(spin_dev, row, 1); row += 1

        line2 = QFrame(); line2.setFrameShape(QFrame.HLine); line2.setStyleSheet("color: #334155; margin: 15px 0 5px 0;")
        grid.addWidget(line2, row, 0, 1, 2); row += 1
        chk_show_prep = QCheckBox("Show Pre-processing"); ui_dict['show_prep'] = chk_show_prep
        chk_show_prep.toggled.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'show_prep', v)); grid.addWidget(chk_show_prep, row, 0, 1, 2); row += 1
        chk_show_line = QCheckBox("Show Line Result"); ui_dict['show_line'] = chk_show_line
        chk_show_line.toggled.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'show_line', v)); grid.addWidget(chk_show_line, row, 0, 1, 2); row += 1
        
        layout.addLayout(grid); layout.addStretch(); return page, ui_dict

    def _create_result_setup_page(self):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0,0,0,0)
        title = QLabel("📊 RESULT SETUP")
        title.setStyleSheet("color: #38bdf8; font-size: 14px; font-weight: 900; border-bottom: 1px solid #334155; padding-bottom: 8px; margin-bottom: 5px;")
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; } QScrollBar:vertical { width: 8px; background: #0f172a; }")
        
        content = QWidget()
        vbox = QVBoxLayout(content)
        vbox.setContentsMargins(0, 0, 5, 0)
        vbox.setSpacing(8)

        def add_line():
            f = QFrame(); f.setFrameShape(QFrame.HLine); f.setStyleSheet("color: #334155; margin: 4px 0;"); vbox.addWidget(f)

        grid1 = QGridLayout()
        grid1.addWidget(QLabel("STD Diff:"), 0, 0)
        self.sp_std_weak = QSpinBox(); self.sp_std_weak.setRange(0, 100); self.sp_std_weak.setPrefix("WEAK: ")
        self.sp_std_weak.valueChanged.connect(lambda v: self._update_dict_and_emit(self.rst_cfg, 'std_weak', v)); grid1.addWidget(self.sp_std_weak, 0, 1)
        self.sp_std_fail = QSpinBox(); self.sp_std_fail.setRange(0, 100); self.sp_std_fail.setPrefix("FAIL: ")
        self.sp_std_fail.valueChanged.connect(lambda v: self._update_dict_and_emit(self.rst_cfg, 'std_fail', v)); grid1.addWidget(self.sp_std_fail, 0, 2)
        
        grid1.addWidget(QLabel("SIG Diff:"), 1, 0)
        self.sp_sig_weak = QSpinBox(); self.sp_sig_weak.setRange(0, 100); self.sp_sig_weak.setPrefix("WEAK: ")
        self.sp_sig_weak.valueChanged.connect(lambda v: self._update_dict_and_emit(self.rst_cfg, 'sig_weak', v)); grid1.addWidget(self.sp_sig_weak, 1, 1)
        self.sp_sig_fail = QSpinBox(); self.sp_sig_fail.setRange(0, 100); self.sp_sig_fail.setPrefix("FAIL: ")
        self.sp_sig_fail.valueChanged.connect(lambda v: self._update_dict_and_emit(self.rst_cfg, 'sig_fail', v)); grid1.addWidget(self.sp_sig_fail, 1, 2)
        vbox.addLayout(grid1)

        add_line()

        grid2 = QGridLayout()
        grid2.addWidget(QLabel("Distance Range:"), 0, 0)
        self.sp_dist_min = QDoubleSpinBox(); self.sp_dist_min.setRange(0, 500); self.sp_dist_min.setPrefix("Min: ")
        self.sp_dist_min.valueChanged.connect(lambda v: self._update_dict_and_emit(self.rst_cfg, 'dist_min', v)); grid2.addWidget(self.sp_dist_min, 0, 1)
        self.sp_dist_max = QDoubleSpinBox(); self.sp_dist_max.setRange(0, 500); self.sp_dist_max.setPrefix("Max: ")
        self.sp_dist_max.valueChanged.connect(lambda v: self._update_dict_and_emit(self.rst_cfg, 'dist_max', v)); grid2.addWidget(self.sp_dist_max, 0, 2)
        vbox.addLayout(grid2)

        add_line()
        
        self.chk_view_res = QCheckBox("View Result Bounds (OK/NG)")
        self.chk_view_res.toggled.connect(lambda v: self._update_dict_and_emit(self.rst_cfg, 'view_result', v)); vbox.addWidget(self.chk_view_res)
        
        hbox_res = QHBoxLayout()
        self.btn_res_rect = QPushButton("🔲 Result Rect"); self.btn_res_rect.setStyleSheet("background-color: #334155; padding: 4px; border-radius: 4px;")
        self.btn_res_rect.clicked.connect(self.result_rect_toggled.emit) 
        self.lbl_res_coords = QLabel("X:0 Y:0 W:0 H:0")
        self.lbl_res_coords.setStyleSheet("color: #94a3b8; font-size: 10px;")
        hbox_res.addWidget(self.btn_res_rect); hbox_res.addWidget(self.lbl_res_coords); vbox.addLayout(hbox_res)

        add_line()

        def make_chk(text, key):
            c = QCheckBox(text)
            c.toggled.connect(lambda v, k=key: self._update_dict_and_emit(self.rst_cfg, k, v))
            vbox.addWidget(c); return c

        self.chk_view_loc_roi = make_chk("View Location Region", 'view_loc_roi')
        self.chk_view_loc_cross = make_chk("View Location Cross", 'view_loc_cross')
        self.chk_view_align = make_chk("View Alignment Region", 'view_align_roi')
        self.chk_view_obj_roi = make_chk("View Object Line Region", 'view_obj_roi')
        self.chk_view_obj_line = make_chk("View Object Line", 'view_obj_line')
        self.chk_view_shd_roi = make_chk("View Shadow Line Region", 'view_shd_roi')
        self.chk_view_shd_line = make_chk("View Shadow Line", 'view_shd_line')
        self.chk_view_dist = make_chk("View Distance Line", 'view_dist_line')

        add_line()
        
        self.btn_save_rst = QPushButton("SAVE")
        self.btn_save_rst.setStyleSheet("background-color: #2563eb; color: white; padding: 8px; border-radius: 4px; font-weight: bold; margin-top: 5px;")
        self.btn_save_rst.clicked.connect(self.save_rst_requested.emit)
        vbox.addWidget(self.btn_save_rst)

        vbox.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)

        return page

    def _update_dict_and_emit(self, cfg_dict, key, val):
        if self._is_loading: return
        cfg_dict[key] = val; self.ui_updated.emit()
    def _create_slider(self, min_v, max_v, default_v):
        sl = QSlider(Qt.Horizontal); sl.setRange(min_v, max_v); sl.setValue(default_v); return sl
    def _wrap_slider(self, text, slider, val_label):
        vbox = QVBoxLayout(); hbox = QHBoxLayout(); hbox.addWidget(QLabel(text)); hbox.addStretch(); hbox.addWidget(val_label)
        vbox.addLayout(hbox); vbox.addWidget(slider); return vbox

# ==========================================
# 커스텀 비전 맵
# ==========================================
class VisionMap(QWidget):
    stats_updated = pyqtSignal(object, float, bool)
    roi_updated = pyqtSignal(str, float, float, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(640, 480); self.setMouseTracking(True)
        self.image = QImage(640, 480, QImage.Format_RGB888); self.image.fill(QColor("#0f172a"))
        self.mode = "STANDBY" 
        
        self.ref_model_center = None 
        self.calculated_dist = 0.0
        self.calc_IA = None; self.calc_IB = None
        self.tracked_obj_pts = None; self.tracked_shd_pts = None
        self.tracked_obj_rect_real = None; self.tracked_shd_rect_real = None
        self.processed_obj_img = None; self.processed_shd_img = None

        self.model_roi = QRectF(); self.align_roi = QRectF()
        self.obj_line_roi = QRectF(); self.shd_line_roi = QRectF(); self.res_roi = QRectF()
        
        self.current_image_rect = QRectF(); self.test_result = None; self.active_roi = None 
        self.action_mode = None; self.selected_rect_type = None
        self.drag_offset = QPointF(); self.zoom_factor = 1.0; self.pan_offset = QPointF(0, 0); self.is_panning = False
        
        self.btn_fit = QPushButton("⛶", self); self.btn_fit.setFixedSize(32, 32)
        self.btn_fit.setStyleSheet("QPushButton { background-color: rgba(15, 23, 42, 180); color: #cbd5e1; border: 1px solid #334155; border-radius: 4px; font-size: 18px; } QPushButton:hover { background-color: rgba(56, 189, 248, 180); color: white; border-color: #38bdf8; }")
        self.btn_fit.clicked.connect(self.fit_to_screen)
        
        self.setup_panel = OverlayConfigPanel(self)
        self.setup_panel.ui_updated.connect(self.update)
        self.setup_panel.result_rect_toggled.connect(lambda: self.set_active_roi("RESULT"))

    # 💡 [신규] VisionMap 완전 초기화 로직
    def reset_to_defaults(self):
        self.ref_model_center = None 
        self.calculated_dist = 0.0
        self.calc_IA = None; self.calc_IB = None
        self.tracked_obj_pts = None; self.tracked_shd_pts = None
        self.tracked_obj_rect_real = None; self.tracked_shd_rect_real = None
        self.processed_obj_img = None; self.processed_shd_img = None

        self.model_roi = QRectF(); self.align_roi = QRectF()
        self.obj_line_roi = QRectF(); self.shd_line_roi = QRectF(); self.res_roi = QRectF()
        
        self.test_result = None; self.active_roi = None 
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event); self.btn_fit.move(self.width() - 42, 10); self.setup_panel.move(10, 10)
    
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

        if self.mode == "TEST" and result and result.get('isFound'):
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
                    crop_x, crop_y = max(0, int(rx)), max(0, int(ry))
                    crop_w, crop_h = min(int(rw), img_w - crop_x), min(int(rh), img_h - crop_y)
                    if crop_w <= 0 or crop_h <= 0: return None, None, None

                    roi_tuple = (crop_x, crop_y, crop_w, crop_h)
                    processed = PCVisionEngine.apply_pre_processing(qimg, cfg, roi=roi_tuple, padding=2)
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
        if self.mode not in ["TEACH", "TEST"]:
            self.active_roi = None; self.test_result = None; self.current_image_rect = QRectF()
            self.model_roi = QRectF(); self.align_roi = QRectF(); self.obj_line_roi = QRectF(); self.shd_line_roi = QRectF()
            self.stats_updated.emit(None, 0.0, False)
        self.update()
        
    def get_real_roi(self, ui_roi):
        if ui_roi.isEmpty(): return 0, 0, 0, 0
        cx, cy, sx, sy = self._get_render_params()
        return max(0, int((ui_roi.x() - cx) / sx)), max(0, int((ui_roi.y() - cy) / sy)), int(ui_roi.width() / sx), int(ui_roi.height() / sy)
    
    def set_real_roi(self, roi_type, rx, ry, rw, rh):
        cx, cy, sx, sy = self._get_render_params()
        rect = QRectF(cx + rx * sx, cy + ry * sy, rw * sx, rh * sy)
        if roi_type == 'MODEL': self.model_roi = rect
        elif roi_type == 'ALIGN': self.align_roi = rect
        elif roi_type == 'OBJ_LINE': self.obj_line_roi = rect
        elif roi_type == 'SHD_LINE': self.shd_line_roi = rect
        elif roi_type == 'RESULT': self.res_roi = rect
        self.current_image_rect = QRectF(cx, cy, 640.0 * sx, 480.0 * sy); self.update()

    def set_active_roi(self, roi_type):
        self.active_roi = roi_type
        cx, cy, sx, sy = self._get_render_params()
        sw, sh = 640.0 * sx, 480.0 * sy
        if roi_type == "MODEL":
            if self.model_roi.isEmpty(): self.model_roi = QRectF(cx + sw*0.4, cy + sh*0.4, sw*0.2, sh*0.2)
            self.setup_panel.show_page("MODEL")
        elif roi_type == "ALIGN":
            if self.align_roi.isEmpty(): self.align_roi = QRectF(cx + sw*0.25, cy + sh*0.25, sw*0.5, sh*0.5)
            self.setup_panel.show_page("ALIGN")
        elif roi_type == "OBJ_LINE":
            if self.obj_line_roi.isEmpty(): self.obj_line_roi = QRectF(cx + sw*0.3, cy + sh*0.3, sw*0.4, sh*0.15)
            self.setup_panel.show_page("OBJ_LINE")
        elif roi_type == "SHD_LINE":
            if self.shd_line_roi.isEmpty(): self.shd_line_roi = QRectF(cx + sw*0.3, cy + sh*0.55, sw*0.4, sh*0.15)
            self.setup_panel.show_page("SHD_LINE")
        elif roi_type == "RESULT":
            if self.res_roi.isEmpty(): 
                rx, ry, rw, rh = self.setup_panel.rst_cfg['res_x'], self.setup_panel.rst_cfg['res_y'], self.setup_panel.rst_cfg['res_w'], self.setup_panel.rst_cfg['res_h']
                self.res_roi = QRectF(cx + rx * sx, cy + ry * sy, rw * sx, rh * sy)
            self.setup_panel.show_page("RESULT")
        self.current_image_rect = QRectF(cx, cy, sw, sh); self.update()

    def get_corner_rect(self, rect):
        return QRectF() if rect.isEmpty() else QRectF(rect.right() - 8, rect.bottom() - 8, 10, 10)
    
    def hit_test(self, pos):
        posF = QPointF(pos)
        if self.mode != "TEACH": return None, None
        rois = {"MODEL": self.model_roi, "ALIGN": self.align_roi, "OBJ_LINE": self.obj_line_roi, "SHD_LINE": self.shd_line_roi, "RESULT": self.res_roi}
        for r_type, rect in rois.items():
            if (self.active_roi == r_type or self.active_roi is None) and not rect.isEmpty():
                if self.get_corner_rect(rect).contains(posF): return r_type, "RESIZE"
                if rect.contains(posF): return r_type, "DRAG"
        return None, None
    
    def mousePressEvent(self, event):
        if self.setup_panel.isVisible() and self.setup_panel.geometry().contains(event.pos()): return
        if event.button() == Qt.RightButton:
            self.is_panning = True; self.drag_offset = event.pos(); self.setCursor(Qt.ClosedHandCursor); return
        if self.mode != "TEACH": return
        if event.button() == Qt.LeftButton:
            rect_type, action = self.hit_test(event.pos())
            if rect_type:
                self.selected_rect_type = rect_type; self.action_mode = action
                if action == "DRAG": self.drag_offset = QPointF(event.pos()) - getattr(self, f"{rect_type.lower()}_roi").topLeft()
                
    def mouseMoveEvent(self, event):
        if hasattr(self, 'is_panning') and self.is_panning:
            self.pan_offset += QPointF(event.pos() - self.drag_offset); self.drag_offset = event.pos(); self.update(); return
        if self.mode != "TEACH": return
        if not event.buttons():
            _, action = self.hit_test(event.pos())
            self.setCursor(Qt.SizeFDiagCursor if action == "RESIZE" else Qt.SizeAllCursor if action == "DRAG" else Qt.ArrowCursor)
            return
        if self.action_mode and self.selected_rect_type:
            target_rect = getattr(self, f"{self.selected_rect_type.lower()}_roi")
            if self.action_mode == "DRAG":
                new_pos = QPointF(event.pos()) - self.drag_offset; target_rect.moveTo(new_pos.x(), new_pos.y())
            elif self.action_mode == "RESIZE":
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
        
        processed_img = PCVisionEngine.apply_pre_processing(self.image.copy(int(rx), int(ry), int(rw), int(rh)), cfg)
        cx, cy, sx, sy = self._get_render_params()
        target_rect = QRectF(cx + rx * sx, cy + ry * sy, rw * sx, rh * sy)
        
        if cfg.get('show_prep', False): painter.drawImage(target_rect, processed_img)
        if cfg.get('show_line', False):
            pt1, pt2, angle = PCVisionEngine.find_line(processed_img, cfg)
            if pt1 and pt2:
                painter.setPen(QPen(QColor(color_str), 3, Qt.SolidLine))
                painter.drawLine(int(target_rect.left() + pt1[0] * sx), int(target_rect.top() + pt1[1] * sy), int(target_rect.left() + pt2[0] * sx), int(target_rect.top() + pt2[1] * sy))
                painter.setPen(QPen(QColor(color_str), 1)); painter.drawText(int(rect.left()), int(rect.bottom() + 15), f"[{label}] Angle: {angle:.2f}°")

    def _draw_tracked_line_info(self, painter, rect_real, pts, processed_img, cfg, color_str, label, view_roi_flag, view_line_flag):
        if rect_real is None: return
        cx, cy, sx, sy = self._get_render_params()
        target_rect = QRectF(cx + rect_real.x() * sx, cy + rect_real.y() * sy, rect_real.width() * sx, rect_real.height() * sy)
        
        if view_roi_flag:
            self._draw_roi_box(painter, target_rect, color_str, label + " (Tracked)", False, dash=True)
        if cfg.get('show_prep', False) and processed_img:
            painter.drawImage(target_rect, processed_img)
            
        if pts and view_line_flag and cfg.get('show_line', False):
            A1, A2, angle = pts
            p1_screen = QPointF(cx + A1[0] * sx, cy + A1[1] * sy)
            p2_screen = QPointF(cx + A2[0] * sx, cy + A2[1] * sy)
            painter.setPen(QPen(QColor(color_str), 3, Qt.SolidLine))
            painter.drawLine(p1_screen, p2_screen)
            painter.setPen(QPen(QColor(color_str), 1))
            painter.drawText(int(target_rect.left()), int(target_rect.bottom() + 15), f"[{label}] Angle: {angle:.2f}°")

    def _draw_roi_by_id(self, painter, r_id):
        if r_id == "ALIGN":
            self._draw_roi_box(painter, self.align_roi, "#f59e0b", "Alignment Zone", self.active_roi in ["ALIGN", None])
            if self.active_roi == "ALIGN" and not self.model_roi.isEmpty():
                mcx, mcy = self.model_roi.center().x(), self.model_roi.center().y()
                painter.setPen(QPen(QColor("#ef4444"), 2)); painter.drawLine(int(mcx - 15), int(mcy), int(mcx + 15), int(mcy)); painter.drawLine(int(mcx), int(mcy - 15), int(mcx), int(mcy + 15))
        elif r_id == "MODEL": self._draw_roi_box(painter, self.model_roi, "#38bdf8", "Model", self.active_roi in ["MODEL", None])
        elif r_id == "OBJ_LINE": self._draw_roi_box(painter, self.obj_line_roi, "#0ea5e9", "Object Line", self.active_roi == "OBJ_LINE", dash=True); self._render_line_processing_live(painter, self.obj_line_roi, self.setup_panel.obj_cfg, "#0ea5e9", "Object Line")
        elif r_id == "SHD_LINE": self._draw_roi_box(painter, self.shd_line_roi, "#f97316", "Shadow Line", self.active_roi == "SHD_LINE", dash=True); self._render_line_processing_live(painter, self.shd_line_roi, self.setup_panel.shd_cfg, "#f97316", "Shadow Line")
        elif r_id == "RESULT": self._draw_roi_box(painter, self.res_roi, "#facc15", "Result Display Area", self.active_roi == "RESULT")

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing); painter.fillRect(self.rect(), QColor("#0f172a"))
        cx, cy, sx, sy = self._get_render_params(); sw, sh = 640.0 * sx, 480.0 * sy
        new_image_rect = QRectF(cx, cy, sw, sh)

        if not self.current_image_rect.isEmpty() and self.current_image_rect != new_image_rect:
            sc_x, sc_y = new_image_rect.width() / self.current_image_rect.width(), new_image_rect.height() / self.current_image_rect.height()
            def scale_roi(roi): return QRectF((roi.x() - self.current_image_rect.x()) * sc_x + new_image_rect.x(), (roi.y() - self.current_image_rect.y()) * sc_y + new_image_rect.y(), roi.width() * sc_x, roi.height() * sc_y) if not roi.isEmpty() else roi
            self.model_roi = scale_roi(self.model_roi); self.align_roi = scale_roi(self.align_roi); self.obj_line_roi = scale_roi(self.obj_line_roi); self.shd_line_roi = scale_roi(self.shd_line_roi); self.res_roi = scale_roi(self.res_roi)
            
        self.current_image_rect = new_image_rect; painter.drawImage(new_image_rect, self.image)
        
        is_aligned, status, cp, r = False, 1, QPointF(), self.test_result
        if self.mode == "TEST" and r and r['isFound']:
            status, cp = r['status'], QPointF(cx + r['x'] * sx + r['w'] * sx / 2.0, cy + r['y'] * sy + r['h'] * sy / 2.0)
            if not self.align_roi.isEmpty() and self.align_roi.contains(cp): is_aligned = True
        
        rst_cfg = self.setup_panel.rst_cfg

        # ALIGN Box Rendering
        if not self.align_roi.isEmpty() and self.mode != "TEACH" and rst_cfg.get('view_align_roi', True):
            painter.setPen(QPen(QColor("#10b981") if is_aligned else QColor("#f59e0b"), 3 if is_aligned else 2, Qt.SolidLine if is_aligned else Qt.DashLine))
            painter.setBrush(QColor(16, 185, 129, 30) if is_aligned else Qt.NoBrush); painter.drawRect(self.align_roi)
            painter.setPen(QColor("#10b981") if is_aligned else QColor("#f59e0b")); painter.drawText(self.align_roi.topLeft() + QPointF(5, -5), "Alignment Zone")

        if self.mode == "TEST" and r:
            m_color = QColor("#38bdf8") if status == 3 else (QColor("#f59e0b") if status == 2 else QColor("#ef4444"))
            
            # MODEL Box / Cross Rendering
            if rst_cfg.get('view_loc_roi', True):
                painter.setPen(QPen(m_color, 2)); painter.setBrush(Qt.NoBrush); painter.drawRect(QRectF(cx + r['x'] * sx, cy + r['y'] * sy, r['w'] * sx, r['h'] * sy))
                painter.setPen(Qt.white); painter.drawText(int(cx + r['x'] * sx), int(cy + r['y'] * sy - 5), f"MATCH: {r['score']}%")
            if rst_cfg.get('view_loc_cross', True):
                painter.setPen(QPen(m_color, 2))
                painter.drawLine(int(cp.x()-15), int(cp.y()), int(cp.x()+15), int(cp.y())); painter.drawLine(int(cp.x()), int(cp.y()-15), int(cp.x()), int(cp.y()+15))

            if r['isFound'] and self.ref_model_center:
                # LINE Tracked Rendering
                self._draw_tracked_line_info(painter, self.tracked_obj_rect_real, self.tracked_obj_pts, self.processed_obj_img, self.setup_panel.obj_cfg, "#0ea5e9", "Object Line", rst_cfg.get('view_obj_roi', True), rst_cfg.get('view_obj_line', True))
                self._draw_tracked_line_info(painter, self.tracked_shd_rect_real, self.tracked_shd_pts, self.processed_shd_img, self.setup_panel.shd_cfg, "#f97316", "Shadow Line", rst_cfg.get('view_shd_roi', True), rst_cfg.get('view_shd_line', True))

                if self.calc_IA and self.calc_IB and rst_cfg.get('view_dist_line', True):
                    screen_IA = QPointF(cx + self.calc_IA[0] * sx, cy + self.calc_IA[1] * sy)
                    screen_IB = QPointF(cx + self.calc_IB[0] * sx, cy + self.calc_IB[1] * sy)
                    painter.setPen(QPen(QColor("#facc15"), 2, Qt.SolidLine))
                    painter.drawLine(screen_IA, screen_IB)
                    painter.setBrush(QColor("#facc15"))
                    painter.drawEllipse(screen_IA, 3, 3); painter.drawEllipse(screen_IB, 3, 3)

            # RESULT Box Rendering (OK/NG)
            if rst_cfg.get('view_result', True):
                rx, ry, rw, rh = rst_cfg.get('res_x', 480), rst_cfg.get('res_y', 20), rst_cfg.get('res_w', 140), rst_cfg.get('res_h', 60)
                screen_res_rect = QRectF(cx + rx * sx, cy + ry * sy, rw * sx, rh * sy)
                
                is_dist_ok = (rst_cfg.get('dist_min', 10.0) <= self.calculated_dist <= rst_cfg.get('dist_max', 50.0))
                res_text = "OK" if is_dist_ok else "NG"
                res_color = QColor("#10b981") if is_dist_ok else QColor("#ef4444")
                
                painter.setPen(QPen(res_color, 3)); painter.setBrush(QColor(res_color.red(), res_color.green(), res_color.blue(), 40))
                painter.drawRect(screen_res_rect)
                
                font = QFont("Arial", 28, QFont.Bold)
                painter.setFont(font)
                painter.setPen(QColor(0, 0, 0, 150))
                painter.drawText(screen_res_rect.translated(2, 2), Qt.AlignCenter, res_text) # 그림자
                painter.setPen(res_color)
                painter.drawText(screen_res_rect, Qt.AlignCenter, res_text)
                painter.setFont(QFont()) # 폰트 복구

        if self.mode == "TEACH":
            roi_order = ["ALIGN", "MODEL", "OBJ_LINE", "SHD_LINE", "RESULT"]
            if self.active_roi in roi_order: roi_order.remove(self.active_roi); roi_order.append(self.active_roi) 
            for r_id in roi_order: self._draw_roi_by_id(painter, r_id)


# ==========================================
# 메인 윈도우 UI
# ==========================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.default_title = "HyVision Pro-Inspector (Board-Master Version)"
        self.setWindowTitle(self.default_title)
        self.setStyleSheet("background-color: #020617; color: #f1f5f9; font-family: 'Segoe UI', sans-serif;")
        
        self.worker = None
        self.pending_target = "" 
        self.init_ui()
        self.refresh_ports()

    def init_ui(self):
        main_widget = QWidget(); self.setCentralWidget(main_widget)
        layout = QHBoxLayout(main_widget); layout.setContentsMargins(15, 15, 15, 15)

        left_layout = QVBoxLayout()
        self.status_card = QFrame(); self.status_card.setFixedHeight(50); self.status_card.setStyleSheet("background-color: #0f172a; border-radius: 8px; border: 1px solid #1e293b;")
        status_layout = QHBoxLayout(self.status_card)
        self.led_status = StatusLED(); status_layout.addWidget(self.led_status)
        self.lbl_mode = QLabel("STANDBY"); self.lbl_mode.setStyleSheet("font-weight: bold; color: #38bdf8; font-size: 14px;"); status_layout.addWidget(self.lbl_mode)
        
        status_layout.addStretch(1)
        self.lbl_test_stats = QLabel("")
        self.lbl_test_stats.setStyleSheet("font-size: 12px; font-weight: bold; background: transparent;")
        self.lbl_test_stats.setAlignment(Qt.AlignCenter)
        status_layout.addWidget(self.lbl_test_stats)
        status_layout.addStretch(1)

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
        
        self.lbl_info = QLabel("○ OFFLINE"); self.lbl_info.setAlignment(Qt.AlignRight | Qt.AlignVCenter); self.lbl_info.setStyleSheet("color: #64748b; font-size: 12px; margin-left: 15px; margin-right: 5px;"); status_layout.addWidget(self.lbl_info)
        left_layout.addWidget(self.status_card)

        self.vision_map = VisionMap()
        self.vision_map.setup_panel.img_config_updated.connect(lambda cfg: self.worker.push_task('SET_IMG_STRUCT', cfg) if self.worker and self.worker.running else None)
        self.vision_map.stats_updated.connect(self._update_test_stats)
        self.vision_map.roi_updated.connect(self._on_roi_updated)
        
        self.vision_map.setup_panel.save_rst_requested.connect(self.save_rst_to_board)

        left_layout.addWidget(self.vision_map)
        layout.addLayout(left_layout, stretch=1)

        # --- 우측: 제어 패널 ---
        right_panel = QWidget(); right_panel.setFixedWidth(360)
        right_layout = QVBoxLayout(right_panel); right_layout.setContentsMargins(0, 0, 0, 0)
        grp_style = "QGroupBox { background-color: #0b1120; border: 1px solid #1e293b; border-radius: 8px; margin-top: 15px; padding-top: 25px; padding-bottom: 5px; } QGroupBox::title { subcontrol-origin: margin; left: 12px; top: 8px; color: #94a3b8; font-weight: bold; font-size: 11px; }"

        conn_group = QGroupBox("HARDWARE CONNECTION"); conn_group.setStyleSheet(grp_style)
        vbox_conn = QVBoxLayout()
        hbox_port = QHBoxLayout()
        self.cmb_ports = QComboBox(); self.cmb_ports.setStyleSheet("background-color: #020617; border: 1px solid #1e293b; padding: 6px; border-radius: 4px; color: #cbd5e1;")
        self.btn_refresh = QPushButton("🔄"); self.btn_refresh.setFixedSize(32, 32); self.btn_refresh.setStyleSheet("QPushButton { background-color: #334155; color: white; border-radius: 4px; font-weight: bold; } QPushButton:hover { background-color: #475569; }")
        self.btn_refresh.clicked.connect(self.refresh_ports)
        hbox_port.addWidget(self.cmb_ports, 1); hbox_port.addWidget(self.btn_refresh); vbox_conn.addLayout(hbox_port)

        lbl_fmt = QLabel("이미지 전송 포맷:"); lbl_fmt.setStyleSheet("font-size: 11px; color: #94a3b8; margin-top: 5px;"); vbox_conn.addWidget(lbl_fmt)
        self.cmb_format = QComboBox(); self.cmb_format.addItem("Compressed (JPEG)", 0); self.cmb_format.addItem("Original (Raw RGB565)", 1)
        self.cmb_format.setStyleSheet(self.cmb_ports.styleSheet()); self.cmb_format.currentIndexChanged.connect(self.on_format_changed); vbox_conn.addWidget(self.cmb_format)

        self.btn_connect = QPushButton("CONNECT DEVICE"); self.btn_connect.setCheckable(True); self.btn_connect.setFixedHeight(45)
        self.btn_connect.setStyleSheet("QPushButton { background-color: #2563eb; color: white; font-weight: bold; border-radius: 6px; border: none; font-size: 13px; } QPushButton:hover { background-color: #1d4ed8; } QPushButton:checked { background-color: #0f766e; border: 2px solid #38bdf8; color: #e0f2fe; } QPushButton:disabled { background-color: #1e293b; color: #475569; }")
        self.btn_connect.clicked.connect(self.on_connect_toggled); vbox_conn.addWidget(self.btn_connect)
        conn_group.setLayout(vbox_conn); right_layout.addWidget(conn_group)

        model_group = QGroupBox("MODEL LIBRARY"); model_group.setStyleSheet(grp_style)
        vbox_model = QVBoxLayout()
        self.list_models = QListWidget(); self.list_models.setStyleSheet("background-color: #020617; border: 1px solid #1e293b; height: 50px; border-radius: 4px; padding: 4px;"); vbox_model.addWidget(self.list_models)
        
        # 💡 [신규] 리스트 클릭 이벤트 연동 (클릭 시점에 파일들 사전 로드)
        self.list_models.currentItemChanged.connect(self._on_model_selected)

        sub_btn_style = "QPushButton { background-color: #334155; color: #e2e8f0; border-radius: 4px; padding: 6px; font-weight: bold; font-size: 11px; } QPushButton:hover { background-color: #475569; }"
        hbox_list = QHBoxLayout()
        self.btn_sync = QPushButton("🔄 Sync List"); self.btn_sync.setStyleSheet(sub_btn_style); self.btn_sync.clicked.connect(lambda: self.worker.push_task('GET_MODELS') if self.worker else None)
        self.btn_delete = QPushButton("🗑️ Delete"); self.btn_delete.setStyleSheet(sub_btn_style); self.btn_delete.clicked.connect(self.delete_selected_model)
        hbox_list.addWidget(self.btn_sync); hbox_list.addWidget(self.btn_delete); vbox_model.addLayout(hbox_list)
        
        self.txt_reg_name = QLineEdit(); self.txt_reg_name.setPlaceholderText("New Model Name (e.g. indoor)")
        self.txt_reg_name.setStyleSheet("background-color: #020617; border: 1px solid #1e293b; padding: 8px; border-radius: 4px;"); vbox_model.addWidget(self.txt_reg_name)
        
        self.btn_register = QPushButton("REGISTER MODEL"); self.btn_register.setFixedHeight(45); self.btn_register.setStyleSheet(self.btn_connect.styleSheet())
        self.btn_register.clicked.connect(self.register_model); vbox_model.addWidget(self.btn_register)
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

    def _create_action_btn(self, text, color):
        btn = QPushButton(text); btn.setFixedHeight(40) 
        btn.setStyleSheet(f"QPushButton {{ background-color: #0f172a; border-left: 5px solid {color}; color: #e2e8f0; font-weight: bold; text-align: left; padding-left: 15px; border-radius: 4px; }} QPushButton:hover {{ background-color: #1e293b; }}")
        btn.setEnabled(False); return btn

    # 💡 [신규] 리스트 선택 시 사전 로딩 트리거 (데드락 회피)
    def _on_model_selected(self, current, previous):
        if not current or not self.worker: return
        self.pending_target = current.text()
        # 클릭 즉시 기존의 ROI 및 파라미터 설정을 깨끗하게 초기화합니다.
        self.vision_map.setup_panel.reset_to_defaults()
        self.vision_map.reset_to_defaults()
        
        # 워커로 연쇄 호출을 보내 미리 준비시킵니다.
        self.worker.push_task('LOAD_META', self.pending_target)
        self.worker.push_task('LOAD_RST', self.pending_target)

    def _on_roi_updated(self, roi_type, rx, ry, rw, rh):
        if roi_type == "RESULT":
            self.vision_map.setup_panel.update_res_labels(rx, ry, rw, rh)

    def _update_test_stats(self, result, dist, is_aligned):
        if self.vision_map.mode == "TEST" and result and result.get('isFound'):
            st = result.get('status', 1)
            
            if st == 3: loc_text = "LOCK"; color = "#10b981"
            elif st == 2: loc_text = "WEAK"; color = "#facc15"
            else: loc_text = "FAIL"; color = "#ef4444"
            
            align_text = "OK" if is_aligned else "FAIL"
            align_color = "#10b981" if is_aligned else "#ef4444"
            
            html = f"<span style='color:#94a3b8;'>Location: </span><span style='color:{color};'>{loc_text}</span>"
            html += f" &nbsp;&nbsp;<span style='color:#475569;'>|</span>&nbsp;&nbsp; <span style='color:#94a3b8;'>Alignment: </span><span style='color:{align_color};'>{align_text}</span>"
            html += f" &nbsp;&nbsp;<span style='color:#475569;'>|</span>&nbsp;&nbsp; <span style='color:#94a3b8;'>STD Diff: </span><span style='color:#e2e8f0;'>{result.get('stdev', 0)}</span>"
            html += f" &nbsp;&nbsp;<span style='color:#475569;'>|</span>&nbsp;&nbsp; <span style='color:#94a3b8;'>SIG Diff: </span><span style='color:#e2e8f0;'>{result.get('diffVal', 0)}</span>"
            html += f" &nbsp;&nbsp;<span style='color:#475569;'>|</span>&nbsp;&nbsp; <span style='color:#38bdf8;'>Distance: {dist:.2f} px</span>"
            
            self.lbl_test_stats.setText(html)
        else:
            self.lbl_test_stats.setText("")

    def refresh_ports(self):
        self.cmb_ports.clear()
        for p in serial.tools.list_ports.comports(): self.cmb_ports.addItem(f"{p.device} - {p.description if p.description else 'Unknown Device'}", p.device)

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
        if connected:
            self.lbl_info.setText(f"● ONLINE - {self.cmb_ports.currentData()}")
            self.lbl_info.setStyleSheet("color: #34d399; font-size: 13px; font-weight: bold; margin-right: 5px;")
            self.on_format_changed(self.cmb_format.currentIndex())
        else:
            self.btn_connect.setChecked(False); self._reset_teach_ui(); self.list_models.clear(); self.vision_map.set_mode("STANDBY")
            self.setWindowTitle(self.default_title) 
            self.lbl_info.setText("○ OFFLINE"); self.lbl_info.setStyleSheet("color: #64748b; font-size: 12px; font-weight: normal; margin-right: 5px;")
            if state == 2: self.log_msg("장치와의 연결이 비정상적으로 종료되었습니다.", "error")
            else: self.log_msg("연결이 안전하게 해제되었습니다.", "info")

    def _update_list(self, models):
        self.list_models.clear(); self.list_models.addItems(models); self.log_msg(f"보드 모델 목록 갱신 ({len(models)}개)", "info")

    def register_model(self):
        if not self.worker: return
        name = self.txt_reg_name.text().strip()
        if not name or self.vision_map.model_roi.isEmpty(): QMessageBox.warning(self, "오류", "이름을 입력하고 화면에 ROI를 그려주세요."); return
        if not name.endswith('.mdl'): name += '.mdl'
        
        self.log_msg(f"'{name}' 좌표 및 파라미터를 보드(플래시 메모리)로 전송 중...", "process")
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
        
        json_str = json.dumps(meta_data)
        self.worker.push_task('UPLOAD_MODEL', (name, coord_data, json_str))
        
        rst_str = json.dumps(self.vision_map.setup_panel.rst_cfg)
        self.worker.push_task('UPLOAD_RST', (name, rst_str))
        
        self.txt_reg_name.clear()
        if self.vision_map.mode == "TEACH": self.toggle_teach()

    def delete_selected_model(self):
        if not self.worker: return
        item = self.list_models.currentItem()
        if not item: return
        name = item.text()
        if QMessageBox.Yes == QMessageBox.question(self, "삭제", f"'{name}' 모델을 보드에서 삭제하시겠습니까?", QMessageBox.Yes|QMessageBox.No):
            self.worker.push_task('DELETE_MODEL', name)

    def save_rst_to_board(self):
        if not self.worker: return
        item = self.list_models.currentItem()
        if not item:
            self.log_msg("저장할 대상을 모델 목록에서 선택해주세요.", "error")
            return
        target_name = item.text()
        rst_str = json.dumps(self.vision_map.setup_panel.rst_cfg)
        self.worker.push_task('UPLOAD_RST', (target_name, rst_str))

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
        self.btn_sel_obj_line.hide(); self.btn_sel_shd_line.hide(); self.sep_teach.hide()
        self.btn_res_setup.hide()
        self.lbl_test_stats.setText("") 
        self.vision_map.setup_panel.setVisible(False)

    # 💡 [버그 픽스] 체인(next_action) 제거 및 비어있는 데이터 안전 처리
    def _on_meta_loaded(self, meta_data, _):
        if not meta_data:
            self.log_msg("등록된 설정 정보(.meta)가 없어 기본값으로 동작합니다.", "system")
        else:
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
            if 'image' in meta_data: self.worker.push_task('SET_IMG_STRUCT', meta_data['image'])

    def _on_rst_loaded(self, rst_data, _):
        if not rst_data:
            self.log_msg("등록된 결과 설정(.rst)이 없어 기본값으로 동작합니다.", "system")
        else:
            self.vision_map.setup_panel.load_rst_settings(rst_data)
            self.vision_map.set_real_roi('RESULT', rst_data.get('res_x', 480), rst_data.get('res_y', 20), rst_data.get('res_w', 140), rst_data.get('res_h', 60))

    def toggle_live(self):
        if not self.worker.is_live:
            self._reset_all_buttons(); self._reset_teach_ui(); self.worker.push_task('STOP_ALL')
            self.vision_map.set_mode("LIVE"); self.lbl_mode.setText("LIVE VIEWING")
            self.btn_live.setText("1. STOP LIVE STREAM")
            self.btn_live.setStyleSheet("QPushButton { background-color: #0284c7; border-left: 5px solid #38bdf8; color: white; font-weight: bold; text-align: left; padding-left: 15px; border-radius: 4px; }")
            self.btn_img_setup.show(); self.worker.push_task('LIVE')
        else:
            self._reset_all_buttons(); self._reset_teach_ui(); self.vision_map.set_mode("STANDBY")
            self.lbl_mode.setText("STANDBY"); self.worker.push_task('STOP_ALL')

    def toggle_teach(self):
        if self.vision_map.mode != "TEACH":
            self._reset_all_buttons(); self._reset_teach_ui(); self.worker.push_task('STOP_ALL')
            
            self.vision_map.set_mode("TEACH"); self.lbl_mode.setText("TEACHING MODE")
            self.btn_teach.setText("2. EXIT TEACH MODE")
            self.btn_teach.setStyleSheet("QPushButton { background-color: #b45309; border-left: 5px solid #fbbf24; color: white; font-weight: bold; text-align: left; padding-left: 15px; border-radius: 4px; }")
            self.btn_sel_model.show(); self.btn_sel_align.show(); self.sep_teach.show()
            self.btn_sel_obj_line.show(); self.btn_sel_shd_line.show()
            
            self.worker.push_task('CAP_REF')
        else:
            self._reset_all_buttons(); self._reset_teach_ui(); self.vision_map.set_mode("STANDBY"); self.lbl_mode.setText("STANDBY")

    # 💡 [버그 픽스] 설정 연쇄 호출 삭제. 클릭 즉시 테스트 전환 고속화
    def toggle_test(self):
        if not self.worker.is_test:
            item = self.list_models.currentItem()
            if not item: return
            self.pending_target = item.text()
            
            self._reset_teach_ui(); self._reset_all_buttons(); self.worker.push_task('STOP_ALL')
            
            self.vision_map.set_mode("TEST"); self.lbl_mode.setText(f"TESTING - {self.pending_target}")
            self.btn_test.setText("3. STOP RESULT TEST")
            self.btn_test.setStyleSheet("QPushButton { background-color: #6d28d9; border-left: 5px solid #a78bfa; color: white; font-weight: bold; text-align: left; padding-left: 15px; border-radius: 4px; }")
            self.btn_res_setup.show()
            
            self.worker.push_task('TEST_MODE', self.pending_target)
        else:
            self._reset_all_buttons(); self._reset_teach_ui(); self.vision_map.set_mode("STANDBY"); self.worker.push_task('STOP_ALL')

    def toggle_start(self):
        self.log_msg("자동 검사 실행 시퀀스는 내일(직선 검출 연계) 추가될 예정입니다.", "system")

    def closeEvent(self, event):
        if self.worker: self.worker.stop()
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.showMaximized()
    sys.exit(app.exec_())