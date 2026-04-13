import os
import time
import struct
import queue
import serial
import glob
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QImage
from HyProtocol import HyProtocol

class OpenMVWorker(QThread):
    log_signal = pyqtSignal(str, str)
    frame_signal = pyqtSignal(QImage, int)
    burst_results_signal = pyqtSignal(list, int)
    connected_signal = pyqtSignal(int)

    def __init__(self, port_name=""):
        super().__init__()
        self.port_name = port_name
        self.serial_port = None
        self.running = False
        
        self.is_virtual = False
        self.virtual_folder = ""
        self.update_interval_ms = 100
        
        self.cmd_queue = queue.Queue()
        self.current_tx_id = 0
        self.tx_callbacks = {}

    def start_virtual_camera(self, folder_path, interval_ms=100):
        self.port_name = "VIRTUAL"
        self.is_virtual = True
        self.virtual_folder = folder_path
        self.update_interval_ms = interval_ms
        self.start()

    def send_command(self, cmd_struct, callback=None):
        self.current_tx_id += 1
        if callback:
            self.tx_callbacks[self.current_tx_id] = callback
        self.cmd_queue.put((self.current_tx_id, cmd_struct))
        return self.current_tx_id

    def stop(self):
        self.running = False
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.close()
            except: pass
        self.wait()

    def run(self):
        self.running = True
        if self.is_virtual:
            self._run_virtual_loop()
        else:
            self._run_serial_loop()

    def _run_virtual_loop(self):
        self.log_signal.emit(f"가상 카메라 모드 시작: {self.virtual_folder}", "system")
        self.connected_signal.emit(1)
        image_files = []
        for ext in ('*.jpg', '*.jpeg', '*.png', '*.bmp'):
            image_files.extend(glob.glob(os.path.join(self.virtual_folder, ext)))
        image_files.sort()
        
        if not image_files:
            self.log_signal.emit("폴더에 이미지가 없습니다.", "error")
            self.connected_signal.emit(0)
            return

        img_id = 0; file_idx = 0
        while self.running:
            start_t = time.time()
            img_path = image_files[file_idx]
            qimg = QImage(img_path)
            
            if not qimg.isNull():
                img_id += 1
                self.frame_signal.emit(qimg, img_id)
            file_idx = (file_idx + 1) % len(image_files)
            
            elapsed_ms = (time.time() - start_t) * 1000
            time.sleep(max(0, (self.update_interval_ms - elapsed_ms) / 1000.0))
        self.connected_signal.emit(0)

    def _run_serial_loop(self):
        try:
            self.serial_port = serial.Serial(self.port_name, baudrate=115200, timeout=1.0)
            self.serial_port.dtr = True
            self.serial_port.rts = True
            time.sleep(0.5)
            # 버퍼 비우기 (연결 직후 발생한 가비지 데이터 제거)
            self.serial_port.reset_input_buffer()
            self.log_signal.emit(f"포트 연결 성공: {self.port_name}", "success")
            self.connected_signal.emit(1)

            while self.running:
                if not self.serial_port.is_open: break
                
                # 1. 큐에 쌓인 명령 전송 처리
                try:
                    tx_id, cmd_data = self.cmd_queue.get_nowait()
                    self.serial_port.write(cmd_data)
                    self.serial_port.flush()
                except queue.Empty:
                    pass

                # 2. 장치로부터 Burst 데이터 수신 대기 (동기화 헤더 0xAA55 탐색)
                if self.serial_port.in_waiting > 0:
                    b1 = self.serial_port.read(1)
                    if b1 == b'\x55':
                        b2 = self._read_fixed_size(1, timeout=0.1)
                        if b2 == b'\xaa':
                            self._receive_burst_payload()
                else:
                    time.sleep(0.002)
                    
        except Exception as e:
            self.log_signal.emit(f"통신 에러: {e}", "error")
            self.connected_signal.emit(2)
        finally:
            self.stop()
            self.connected_signal.emit(0)

    def _read_fixed_size(self, size, timeout=1.0):
        data = b''
        start_t = time.time()
        while len(data) < size and (time.time() - start_t < timeout) and self.running:
            chunk = self.serial_port.read(size - len(data))
            if chunk: data += chunk
            else: time.sleep(0.001)
        return data

    def _receive_burst_payload(self):
        try:
            # 1. 툴 결과 배열 크기 수신
            count_data = self._read_fixed_size(2, timeout=0.2)
            if len(count_data) != 2: return
            result_count = struct.unpack('<H', count_data)[0]
            
            # 2. 64-Byte 구조체 배열 수신
            parsed_results = []
            cycle_id = 0
            for _ in range(result_count):
                raw_64 = self._read_fixed_size(HyProtocol.PACKET_SIZE, timeout=0.5)
                if len(raw_64) == HyProtocol.PACKET_SIZE:
                    parsed = HyProtocol.unpack_result(raw_64)
                    if parsed:
                        parsed_results.append(parsed)
                        cycle_id = parsed['cycleID']
                        tx_id = parsed['txID']
                        if tx_id in self.tx_callbacks:
                            self.tx_callbacks[tx_id](parsed)
                            del self.tx_callbacks[tx_id]

            # 3. 이미지 사이즈 및 데이터 수신
            size_data = self._read_fixed_size(4, timeout=0.2)
            if len(size_data) == 4:
                img_size = struct.unpack('<I', size_data)[0]
                if 0 < img_size < 2000000:
                    img_data = self._read_fixed_size(img_size, timeout=1.0)
                    if len(img_data) == img_size:
                        qimg = QImage.fromData(img_data, "JPG")
                        if not qimg.isNull():
                            if parsed_results:
                                self.burst_results_signal.emit(parsed_results, cycle_id)
                            img_id_val = parsed_results[0]['imgID'] if parsed_results else 0
                            self.frame_signal.emit(qimg, img_id_val)
                            
        except Exception as e:
            self.log_signal.emit(f"Burst 파싱 에러: {e}", "error")