import time
import cv2
import glob
import os
import struct
import queue
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QImage
from HyVisionTools import VisionTaskRunner, HyLine, HyLocator, HyLinePatMat

class VirtualMachine(QThread):
    """
    OpenMV 장비의 물리적 행위를 PC 메모리 상에서 100% 동일하게 흉내 내는 가상 머신입니다.
    가상의 통신 큐(Queue)를 통해 32B 명령을 받고, 64B 결과 Burst를 내보냅니다.
    """
    
    def __init__(self, cmd_queue, res_queue, image_source, interval_ms=100):
        """
        :param image_source: str (폴더 경로) 또는 int (카메라 인덱스, 예: 0)
        """
        super().__init__()
        self.cmd_queue = cmd_queue
        self.res_queue = res_queue
        self.image_source = image_source
        self.interval_ms = interval_ms
        
        self.running = False
        self.mode = 0x00 # 0x00: STANDBY, 0x01: LIVE, 0x02: TEACH_SNAP, 0x03: TEST, 0x04: RUN
        
        self.task_runner = VisionTaskRunner()
        
        # 💡 [핵심 기능] 이중 이미지 소싱 (Dual Image Sourcing) 변수
        self.is_camera = isinstance(image_source, int) or (isinstance(image_source, str) and image_source.isdigit())
        self.cap = None
        self.image_files = []
        self.file_idx = 0
        
    def _init_source(self):
        if self.is_camera:
            cam_idx = int(self.image_source)
            self.cap = cv2.VideoCapture(cam_idx)
            # 기본 해상도 640x480 보장
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            print(f"[VVM] 실제 웹캠 모드 시작 (Index: {cam_idx})")
        else:
            self.image_files = []
            for ext in ('*.jpg', '*.jpeg', '*.png', '*.bmp'):
                self.image_files.extend(glob.glob(os.path.join(self.image_source, ext)))
            self.image_files.sort()
            if not self.image_files:
                print(f"[VVM] 경고: '{self.image_source}' 폴더에 이미지 파일이 없습니다.")
            else:
                print(f"[VVM] 로컬 폴더 모드 시작 (총 {len(self.image_files)}장 발견)")
            
    def _get_next_frame(self):
        if self.is_camera:
            if self.cap and self.cap.isOpened():
                ret, frame = self.cap.read()
                return frame if ret else None
            return None
        else:
            if not self.image_files: return None
            img_path = self.image_files[self.file_idx]
            frame = cv2.imread(img_path)
            self.file_idx = (self.file_idx + 1) % len(self.image_files)
            return frame
        
    def _cv2_to_qimage(self, cv_img):
        if cv_img is None: return QImage()
        h, w = cv_img.shape[:2]
        if len(cv_img.shape) == 3:
            rgb_img = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
            return QImage(rgb_img.data, w, h, w * 3, QImage.Format_RGB888).copy()
        else:
            return QImage(cv_img.data, w, h, w, QImage.Format_Grayscale8).copy()

    def run(self):
        self.running = True
        self._init_source()
        
        img_id = 0
        cycle_id = 0
        tx_count = 0
        
        print("[VVM] 가상 비전 엔진 파이프라인 구동 시작")
        
        while self.running:
            start_t = time.time()
            
            # 1. 32바이트 Command 수신 및 파싱 (Virtual RX)
            try:
                while True:
                    cmd_data = self.cmd_queue.get_nowait()
                    self._process_command(cmd_data)
            except queue.Empty:
                pass
                
            # 2. State Machine (4가지 모드 구현)
            if self.mode in (0x01, 0x02, 0x03, 0x04):
                frame = self._get_next_frame()
                if frame is not None:
                    img_id += 1
                    
                    if self.mode == 0x01: # LIVE (쌩얼 영상만 전송)
                        self._send_burst_to_queue([], frame, cycle_id, img_id)
                        
                    elif self.mode == 0x02: # TEACH_SNAP (1장 캡처 후 대기)
                        self._send_burst_to_queue([], frame, cycle_id, img_id)
                        self.mode = 0x00 # STANDBY 복귀
                        
                    elif self.mode == 0x03: # TEST (연산 결과 + 영상 전송)
                        cycle_id += 1
                        tx_count += 1
                        packets = self.task_runner.run_all(frame, tx_count, cycle_id, img_id)
                        self._send_burst_to_queue(packets, frame, cycle_id, img_id)
                        
                    elif self.mode == 0x04: # RUN (영상 없이 결과만 초고속 전송)
                        cycle_id += 1
                        tx_count += 1
                        packets = self.task_runner.run_all(frame, tx_count, cycle_id, img_id)
                        self._send_burst_to_queue(packets, None, cycle_id, img_id)
            
            # 카메라 캡처 지연을 방지하기 위해, 파일 소싱 시에만 interval 제어
            if not self.is_camera:
                elapsed_ms = (time.time() - start_t) * 1000
                time.sleep(max(0, (self.interval_ms - elapsed_ms) / 1000.0))
            else:
                time.sleep(0.005) # 웹캠은 무한 루프 과부하 방지용 미세 딜레이만

        # 정리 작업
        if self.cap: self.cap.release()

    def _process_command(self, cmd_data):
        if len(cmd_data) < 32: return
        
        # 통합 제어 프로토콜 파싱: <H B B 4i I 8x
        header = struct.unpack('<HBBiiiiI8x', cmd_data[:32])
        sync, cmd_id, target_id = header[0], header[1], header[2]
        p1, p2, p3, p4 = header[3], header[4], header[5], header[6]
        payload_len = header[7]
        
        if sync != 0xBB66: return
        
        # [상태 제어 명령]
        if cmd_id == 0x00: self.mode = 0x00
        elif cmd_id == 0x01: self.mode = 0x01; self.interval_ms = 1000.0 / (p1 if p1 > 0 else 30)
        elif cmd_id == 0x02: self.mode = 0x02
        elif cmd_id == 0x03: self.mode = 0x03
        elif cmd_id == 0x04: self.mode = 0x04
        
        # [동적 프로비저닝 명령] (장치에 툴 심기)
        elif cmd_id == 0x10: 
            tool_type = p1
            seq_id = p2
            # 좌표 압축 해제 (Param2: 상위 16비트 X, 하위 16비트 Y)
            x = (p3 >> 16) & 0xFFFF; y = p3 & 0xFFFF
            w = (p4 >> 16) & 0xFFFF; h = p4 & 0xFFFF
            
            if tool_type == 1: # HyLine
                self.task_runner.add_tool(HyLine(target_id, seq_id, (x,y,w,h)))
            elif tool_type == 2: # HyPatMat (TODO: Phase 1 P1-10 구현 후 연결)
                pass
            elif tool_type == 3: # HyLocator (임시 더미 매핑)
                dummy_target = HyLine(0, 0, (0,0,0,0))
                self.task_runner.add_tool(HyLocator(target_id, seq_id, dummy_target, (x,y,w,h), (-15, 15)))
                
        elif cmd_id == 0x19: # CLEAR_TOOLS
            self.task_runner.tools.clear()
            
    def _send_burst_to_queue(self, packets, cv_img, cycle_id, img_id):
        # 버스트 헤더 생성: 0xAA55 (2B) + Packet Count (2B) = 4 Bytes
        burst_data = bytearray()
        burst_data.extend(struct.pack('<HH', 0xAA55, len(packets)))
        
        for p in packets:
            burst_data.extend(p) # 64 Bytes 패킷들 이어붙이기
            
        qimg = self._cv2_to_qimage(cv_img)
        
        # 가상 RX 큐에 삽입 (OpenMVWorker가 이 큐를 읽어갑니다)
        self.res_queue.put((bytes(burst_data), qimg, cycle_id))
        
    def stop(self):
        self.running = False
        self.wait()