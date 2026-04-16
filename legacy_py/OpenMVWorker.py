import os
import time
import struct
import queue
import serial
import glob
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QImage
from HyProtocol import HyProtocol
from VirtualVisionEngine import VirtualVisionEngine # 💡 [추가] 가상 비전 엔진 임포트

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
        self.virtual_source = ""
        self.update_interval_ms = 100
        
        self.cmd_queue = queue.Queue()
        self.current_tx_id = 0
        self.tx_callbacks = {}

    def start_virtual_camera(self, image_source, interval_ms=100):
        """
        기존의 단순 파일 뷰어를 넘어 VirtualVisionEngine(VVM)을 기동합니다.
        image_source: 폴더 경로(str) 또는 웹캠 인덱스(int)
        """
        self.port_name = "VIRTUAL"
        self.is_virtual = True
        self.virtual_source = image_source
        self.update_interval_ms = interval_ms
        self.start()

    def send_command(self, cmd_data, callback=None):
        """
        💡 [핵심] UI에서 보낸 과거 1바이트 명령을 32B 통합 규격(Struct)으로 자동 변환 (어댑터 패턴)
        """
        if isinstance(cmd_data, bytes) and len(cmd_data) == 1:
            cmd_byte = cmd_data
            cmd_id = 0x00 # STOP
            
            if cmd_byte == b'l': cmd_id = 0x01   # LIVE
            elif cmd_byte == b'c': cmd_id = 0x02 # TEACH_SNAP
            elif cmd_byte == b't': cmd_id = 0x03 # TEST
            elif cmd_byte == b'x': cmd_id = 0x00 # STOP
            
            # 통합 제어 프로토콜 규격: <H B B 4i I 8x (32 Bytes)
            cmd_struct = struct.pack('<HBBiiiiI8x', 0xBB66, cmd_id, 0, 0, 0, 0, 0, 0)
        else:
            # 이미 32바이트 구조체로 포맷팅되어 넘어온 경우
            cmd_struct = cmd_data

        self.current_tx_id += 1
        if callback:
            self.tx_callbacks[self.current_tx_id] = callback
            
        self.cmd_queue.put(cmd_struct)
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
            self._run_virtual_engine_loop()
        else:
            self._run_serial_loop()

    # ==============================================================================
    # [가상 환경 모드] VVM을 띄우고 메모리 큐(Queue)로 통신하는 루프
    # ==============================================================================
    def _run_virtual_engine_loop(self):
        self.log_signal.emit(f"가상 비전 머신(VVM) 모드 시작: {self.virtual_source}", "system")
        self.connected_signal.emit(1)
        
        # VVM 인스턴스 생성 및 스레드 분리 실행
        vvm_res_queue = queue.Queue()
        vvm = VirtualVisionEngine(self.cmd_queue, vvm_res_queue, self.virtual_source, self.update_interval_ms)
        vvm.start()
        
        while self.running:
            try:
                # VVM에서 64B 결과 버스트와 QImage 수신 (실제 시리얼처럼 블로킹 대기)
                burst_bytes, qimg, cycle_id = vvm_res_queue.get(timeout=0.05)
                
                # Burst 헤더(0xAA55) 파싱 및 64바이트 쪼개기
                if len(burst_bytes) >= 4:
                    sync, count = struct.unpack('<HH', burst_bytes[:4])
                    if sync == 0xAA55:
                        parsed_results = []
                        offset = 4
                        for _ in range(count):
                            if offset + 64 <= len(burst_bytes):
                                packet = burst_bytes[offset:offset+64]
                                parsed = HyProtocol.unpack_result(packet)
                                if parsed:
                                    parsed_results.append(parsed)
                                offset += 64
                                
                        if parsed_results:
                            self.burst_results_signal.emit(parsed_results, cycle_id)
                            
                        img_id = parsed_results[0]['imgID'] if parsed_results else 0
                        self.frame_signal.emit(qimg, img_id)
                    
            except queue.Empty:
                pass
            except Exception as e:
                self.log_signal.emit(f"VVM 큐 읽기 에러: {e}", "error")
                
        # 워커 종료 시 가상 엔진도 함께 종료
        vvm.stop()
        self.connected_signal.emit(0)

    # ==============================================================================
    # [실 환경 모드] 시리얼 통신으로 장치(OpenMV)와 통신하는 루프
    # ==============================================================================
    def _run_serial_loop(self):
        try:
            self.serial_port = serial.Serial(self.port_name, baudrate=115200, timeout=1.0)
            self.serial_port.dtr = True
            self.serial_port.rts = True
            time.sleep(0.5)
            self.serial_port.reset_input_buffer()
            self.log_signal.emit(f"포트 연결 성공: {self.port_name}", "success")
            self.connected_signal.emit(1)

            while self.running:
                if not self.serial_port.is_open: break
                
                # 1. 큐에 쌓인 32B 명령 전송
                try:
                    cmd_data = self.cmd_queue.get_nowait()
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