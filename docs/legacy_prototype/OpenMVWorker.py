import time
import struct
import queue
import json
import serial
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QImage

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
                    break
        except Exception as e:
            self.log_signal.emit(f"연결 오류: {e}", "error")
            self.connected_signal.emit(2)
        finally:
            self.running = False
            if self.serial_port and self.serial_port.is_open:
                try: self.serial_port.close()
                except: pass
            self.connected_signal.emit(0)

    def _read_fixed_size(self, size, timeout=0.5):
        data = b''
        start_t = time.time()
        while len(data) < size and (time.time() - start_t < timeout) and self.running:
            needed = size - len(data)
            chunk = self.serial_port.read(needed)
            if chunk:
                data += chunk
            else:
                time.sleep(0.01)
        return data if len(data) == size else b''

    def _wait_for_response(self, timeout=3.0):
        start_t = time.time()
        while time.time() - start_t < timeout and self.running:
            if self.serial_port.in_waiting:
                res = self.serial_port.read_all()
                if res.endswith(b'OK') or res.endswith(b'ER'):
                    time.sleep(0.01)
                    if self.serial_port.in_waiting > 0:
                        continue
                    return res[-2:]
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
            self.serial_port.flush()
            self.is_live = False
            self.is_test = False
            self._wait_for_response(2.0)
            
        elif cmd == 'SET_IMG_STRUCT':
            cfg = data
            payload = struct.pack('<iiiiiiiii', cfg['exp_auto'], cfg['exp_val'], cfg['gain_auto'], cfg['gain_val'], 
                                  cfg['contrast'], cfg['brightness'], cfg['vflip'], cfg['hmirror'], cfg['quality'])
            self.serial_port.write(b'i' + payload)
            self._wait_for_response(2.0)
            
        elif cmd == 'GET_INFO':
            self.serial_port.write(b'I')
            len_data = self._read_fixed_size(4)
            if len(len_data) == 4:
                jl = struct.unpack('<I', len_data)[0]
                if 0 < jl < 10000:
                    raw_info = self._read_fixed_size(jl)
                    try:
                        self.info_signal.emit(json.loads(raw_info.decode('utf-8')))
                    except: pass

        elif cmd == 'GET_MODELS':
            self.serial_port.write(b'm')
            len_data = self._read_fixed_size(4)
            if len(len_data) == 4:
                sl = struct.unpack('<I', len_data)[0]
                if sl > 0:
                    raw_csv = self._read_fixed_size(sl)
                    self.models_signal.emit(raw_csv.decode('utf-8').split(','))
                else:
                    self.models_signal.emit([])

        elif cmd == 'LOAD_META':
            target_name = data
            time.sleep(0.1)
            self.serial_port.read_all() 
            self.serial_port.write(b'j' + struct.pack('<I', len(target_name)) + target_name.encode('utf-8'))
            
            ld = self._read_fixed_size(4)
            meta_data = {}
            if len(ld) == 4:
                jl = struct.unpack('<I', ld)[0]
                if 0 < jl < 50000:
                    raw_data = self._read_fixed_size(jl)
                    try:
                        meta_data = json.loads(raw_data.decode('utf-8'))
                    except: pass
            self.meta_signal.emit(meta_data, "")

        elif cmd == 'LOAD_RST':
            target_name = data
            time.sleep(0.1)
            while True:
                if not self.serial_port.read_all(): 
                    break
            self.serial_port.write(b'R' + struct.pack('<I', len(target_name)) + target_name.encode('utf-8'))
            
            ld = self._read_fixed_size(4)
            rst_data = {}
            if len(ld) == 4:
                jl = struct.unpack('<I', ld)[0]
                if 0 < jl < 50000:
                    raw_data = self._read_fixed_size(jl)
                    try:
                        rst_data = json.loads(raw_data.decode('utf-8'))
                    except: pass
            self.rst_signal.emit(rst_data, "")

        elif cmd == 'TEST_MODE':
            name = data
            time.sleep(0.05)
            self.serial_port.read_all() 
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
            time.sleep(0.1)
            self.serial_port.read_all() 
            payload = b'u' + struct.pack('<I', len(name)) + name.encode('utf-8') + coord_data + struct.pack('<I', len(json_str)) + json_str.encode('utf-8')
            self.serial_port.write(payload)
            if self._wait_for_response(5.0) == b'OK':
                self.log_signal.emit(f"'{name}' 모델 저장 완료.", "success")
                self.push_task('GET_MODELS')
            else:
                self.log_signal.emit("모델 업로드 에러", "error")

        elif cmd == 'UPLOAD_META':
            name, json_str = data
            while True:
                if not self.serial_port.read_all(): 
                    break
                time.sleep(0.01)
            payload = b'M' + struct.pack('<I', len(name)) + name.encode('utf-8') + struct.pack('<I', len(json_str)) + json_str.encode('utf-8')
            self.serial_port.write(payload)
            if self._wait_for_response(3.0) == b'OK':
                time.sleep(0.5)
                self.log_signal.emit(f"'{name}' vision setup(.meta) 저장 완료.", "success")
            else:
                self.log_signal.emit("vision setup(.meta) 저장 실패", "error")

        elif cmd == 'UPLOAD_IMGSETUP':
            name, json_str = data
            while True:
                if not self.serial_port.read_all(): 
                    break
                time.sleep(0.01)
            payload = b'M' + struct.pack('<I', len(name)) + name.encode('utf-8') + struct.pack('<I', len(json_str)) + json_str.encode('utf-8')
            self.serial_port.write(payload)
            if self._wait_for_response(3.0) == b'OK':
                time.sleep(0.5)
                self.log_signal.emit(f"'{name}' image setup(.meta) 저장 완료.", "success")
            else:
                self.log_signal.emit("image setup(.meta) 저장 실패", "error")

        elif cmd == 'UPLOAD_RST':
            name, json_str = data
            time.sleep(0.1)
            self.serial_port.read_all() 
            payload = b'W' + struct.pack('<I', len(name)) + name.encode('utf-8') + struct.pack('<I', len(json_str)) + json_str.encode('utf-8')
            self.serial_port.write(payload)
            if self._wait_for_response(2.0) == b'OK':
                self.log_signal.emit(f"'{name}' Result(.rst) 저장 완료.", "success")
            else:
                self.log_signal.emit("Result(.rst) 저장 실패", "error")

        elif cmd == 'DELETE_MODEL':
            name = data
            self.serial_port.write(b'd' + struct.pack('<I', len(name)) + name.encode('utf-8'))
            self._wait_for_response()
            self.push_task('GET_MODELS')

    def _wait_sync(self):
        st = time.time()
        while time.time() - st < 2.0 and self.running:
            if self.serial_port.read(1) == b'\x55':
                if self.serial_port.read(1) == b'\xAA': 
                    return True
        return False

    def _read_live_frame(self):
        if self._wait_sync(): 
            self._read_image_payload(is_jpeg=(self.img_format == 0))

    def _read_test_frame(self):
        if self._wait_sync():
            hd = self._read_fixed_size(8)
            if len(hd) == 8:
                _, _, sz = struct.unpack('<HHI', hd)
                rd = self._read_fixed_size(41)
                if len(rd) == 41:
                    isF, st, sc, x, y, w, h, ang, std, sig, pt = struct.unpack('<Biiiiiifiii', rd)
                    self._read_image_payload(sz, is_jpeg=(self.img_format==0), 
                                             result={'isFound':isF, 'status':st, 'score':sc, 'x':x, 'y':y, 'w':w, 'h':h, 'stdev':std, 'diffVal':sig, 'ang':ang, 'procTime':pt})

    def _read_image_payload(self, sz=None, is_jpeg=True, result=None):
        if sz is None:
            ld = self._read_fixed_size(4)
            sz = struct.unpack('<I', ld)[0] if len(ld)==4 else 0
        if 0 < sz < 2000000:
            dat = self._read_fixed_size(sz, timeout=1.0)
                
            if len(dat) == sz:
                img = QImage.fromData(dat, "JPG") if is_jpeg else QImage(dat, 640, 480, QImage.Format_RGB16).copy()
                if img and not img.isNull(): 
                    self.frame_signal.emit(img, result)

    def stop(self): 
        self.running = False
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.write(b'x') 
                self.serial_port.flush()
                self.serial_port.close()     
            except:
                pass
        self.wait()