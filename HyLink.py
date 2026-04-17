"""
HyLink.py - 통합 통신 워커 스레드 (v2.0)
물리 시리얼 / 가상 큐(VVM) 연결을 단일 인터페이스로 추상화.
UI 코드는 연결 대상이 물리 장치인지 VVM인지 알 필요 없음.
"""
import time
import struct
import queue

try:
    import serial
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QImage

from HyProtocol import HyProtocol


class HyLink(QThread):
    """
    물리 시리얼 / 가상 큐 통신을 단일 인터페이스로 추상화.
    sig_frame   → VisionCanvas 에 이미지 + Burst 전달
    sig_log     → InspectorApp 로그 패널
    sig_connected → 연결 상태 (0=해제, 1=연결, 2=비정상 단절)
    """

    sig_log       = pyqtSignal(str, str)          # (text, level)
    sig_frame     = pyqtSignal(QImage, list)      # (QImage, burst_results: list[dict])
    sig_connected = pyqtSignal(int)               # 0/1/2

    # Heartbeat
    PING_INTERVAL   = 3.0    # 초
    PING_TIMEOUT    = 1.0    # 초
    PING_MAX_MISS   = 3      # 연속 실패 한계

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode     = "idle"       # "idle" | "serial" | "virtual"
        self._port     = None         # serial.Serial
        self._vm       = None         # VirtualMachine
        self._cmd_q    = queue.Queue()
        self.running   = False

        self._ping_miss   = 0
        self._last_ping_t = 0.0
        self._waiting_pong = False
        self._tx_id = 0

    # ─────────────────────────────────────────────────────────────────────────
    # 연결 / 해제
    # ─────────────────────────────────────────────────────────────────────────

    def connect_serial(self, port_name: str, baudrate: int = 115200):
        """물리 시리얼 연결 시작."""
        self._mode     = "serial"
        self._port_name = port_name
        self._baudrate  = baudrate
        self._vm        = None
        self.start()

    def connect_virtual(self, vm):
        """VVM 큐 연결 시작."""
        self._mode = "virtual"
        self._vm   = vm
        self._port = None
        if not vm.isRunning():
            vm.start()
        self.start()

    def stop(self):
        self.running = False
        self.quit()
        self.wait(3000)
        if self._port and self._port.is_open:
            try:
                self._port.close()
            except Exception:
                pass
        self._port = None

    # ─────────────────────────────────────────────────────────────────────────
    # 명령 전송 (UI → HyLink → Device/VVM)
    # ─────────────────────────────────────────────────────────────────────────

    def send_command(self, cmd_id: int, target_id: int = 0,
                     target_type: int = 0, params=(0, 0, 0, 0),
                     fparam: float = 0.0, payload: bytes = b''):
        """32B Command 패킷을 큐에 적재 (v3.0)."""
        p0, p1, p2, p3 = (list(params) + [0, 0, 0, 0])[:4]
        raw = HyProtocol.pack_command(
            cmd_id,
            target_id=target_id,
            target_type=target_type,
            p0=int(p0), p1=int(p1), p2=int(p2), p3=int(p3),
            fparam=fparam,
            payload_len=len(payload),
        )
        self._cmd_q.put(raw + payload)

    # ─────────────────────────────────────────────────────────────────────────
    # 스레드 진입점
    # ─────────────────────────────────────────────────────────────────────────

    def run(self):
        self.running = True
        if self._mode == "serial":
            self._run_serial()
        elif self._mode == "virtual":
            self._run_virtual()

    # ─────────────────────────────────────────────────────────────────────────
    # 시리얼 루프
    # ─────────────────────────────────────────────────────────────────────────

    def _run_serial(self):
        if not HAS_SERIAL:
            self.sig_log.emit("pyserial 미설치 — 시리얼 연결 불가", "error")
            self.sig_connected.emit(0)
            return
        try:
            self._port = serial.Serial(
                self._port_name, self._baudrate, timeout=1.0)
            self._port.dtr = True
            self._port.rts = True
            time.sleep(0.5)
            self._port.reset_input_buffer()
            self.sig_log.emit(f"포트 연결 성공: {self._port_name}", "success")
            self.sig_connected.emit(1)
            self._last_ping_t = time.time()

            while self.running:
                if not self._port.is_open:
                    break
                self._flush_cmd_queue_serial()
                self._check_heartbeat_serial()

                if self._port.in_waiting > 0:
                    b1 = self._port.read(1)
                    if b1 == b'\x55':
                        b2 = self._read_fixed(1, 0.1)
                        if b2 == b'\xaa':
                            self._receive_burst_serial()
                else:
                    time.sleep(0.002)

        except Exception as e:
            self.sig_log.emit(f"시리얼 오류: {e}", "error")
            self.sig_connected.emit(2)
        finally:
            if self._port and self._port.is_open:
                self._port.close()
            self.sig_connected.emit(0)

    def _flush_cmd_queue_serial(self):
        try:
            while True:
                raw = self._cmd_q.get_nowait()
                self._port.write(raw)
                self._port.flush()
        except queue.Empty:
            pass

    def _check_heartbeat_serial(self):
        now = time.time()
        if now - self._last_ping_t < self.PING_INTERVAL:
            return
        self._last_ping_t = now
        ping = HyProtocol.pack_command(HyProtocol.CMD_PING)
        self._port.write(ping)
        self._port.flush()

    def _read_fixed(self, size: int, timeout: float) -> bytes:
        data = b''
        t0   = time.time()
        while len(data) < size and (time.time() - t0 < timeout) and self.running:
            chunk = self._port.read(size - len(data))
            if chunk:
                data += chunk
            else:
                time.sleep(0.001)
        return data

    def _receive_burst_serial(self):
        try:
            count_data = self._read_fixed(2, 0.2)
            if len(count_data) != 2:
                return
            n = struct.unpack('<H', count_data)[0]

            results = []
            for _ in range(n):
                raw = self._read_fixed(HyProtocol.RST_SIZE, 0.5)
                if len(raw) == HyProtocol.RST_SIZE:
                    parsed = HyProtocol.unpack_result(raw)
                    if parsed:
                        results.append(parsed)

            size_data = self._read_fixed(4, 0.2)
            if len(size_data) != 4:
                return
            img_size = struct.unpack('<I', size_data)[0]

            qimg = QImage()
            if 0 < img_size < 4_000_000:
                img_data = self._read_fixed(img_size, 2.0)
                if len(img_data) == img_size:
                    qimg = QImage.fromData(img_data, "JPG")

            self.sig_frame.emit(qimg, results)

        except Exception as e:
            self.sig_log.emit(f"Burst 파싱 오류: {e}", "error")

    # ─────────────────────────────────────────────────────────────────────────
    # 가상(VVM) 루프
    # ─────────────────────────────────────────────────────────────────────────

    def _run_virtual(self):
        self.sig_log.emit("VVM 연결됨", "success")
        self.sig_connected.emit(1)
        self._last_ping_t  = time.time()
        self._waiting_pong = False
        self._ping_miss    = 0

        while self.running:
            # 명령 → VVM cmd_queue 로 전달
            try:
                while True:
                    raw = self._cmd_q.get_nowait()
                    self._vm.cmd_queue.put_nowait(raw)
            except queue.Empty:
                pass

            # VVM rst_queue 에서 Burst 수신
            try:
                burst_bytes = self._vm.rst_queue.get(timeout=0.05)
                self._process_burst_bytes(burst_bytes)
            except queue.Empty:
                pass

            # P2-16 Heartbeat (VVM 모드)
            self._check_heartbeat_virtual()

        self.sig_connected.emit(0)

    def _check_heartbeat_virtual(self):
        """VVM 모드 Heartbeat: PING_INTERVAL 마다 CMD_PING 전송,
        PING_TIMEOUT 내 PONG 미수신이 PING_MAX_MISS 회 연속이면 sig_connected(2)."""
        now = time.time()
        if self._waiting_pong:
            if now - self._last_ping_t > self.PING_TIMEOUT:
                self._ping_miss += 1
                self._waiting_pong = False
                if self._ping_miss >= self.PING_MAX_MISS:
                    self.sig_log.emit(
                        f"VVM PING {self.PING_MAX_MISS}회 무응답 — 연결 이상", "warn")
                    self.sig_connected.emit(2)
                    self._ping_miss = 0
        else:
            if now - self._last_ping_t >= self.PING_INTERVAL:
                ping = HyProtocol.pack_command(HyProtocol.CMD_PING)
                try:
                    self._vm.cmd_queue.put_nowait(ping)
                except queue.Full:
                    pass
                self._last_ping_t  = now
                self._waiting_pong = True

    def _process_burst_bytes(self, burst_bytes: bytes):
        """VVM 에서 받은 Burst bytes → sig_frame 시그널 발행."""
        parsed = HyProtocol.unpack_burst(burst_bytes)
        if parsed is None:
            return
        results, img_bytes = parsed
        # P2-16: PONG(tool_id=0, rst_done=EXEC_DONE) 감지 → ping_miss 리셋
        for r in results:
            if r.get('tool_id') == 0 and r.get('rst_done') == HyProtocol.EXEC_DONE:
                self._waiting_pong = False
                self._ping_miss    = 0
                break

        qimg = QImage()
        if img_bytes:
            if HAS_CV2:
                arr = np.frombuffer(img_bytes, np.uint8)
                bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if bgr is not None:
                    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                    h, w, ch = rgb.shape
                    qimg = QImage(rgb.data, w, h, ch * w,
                                  QImage.Format_RGB888).copy()
            else:
                qimg = QImage.fromData(img_bytes, "JPG")

        self.sig_frame.emit(qimg, results)
