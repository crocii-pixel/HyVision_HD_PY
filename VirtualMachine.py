"""
VirtualMachine.py - 가상 비전 머신 (VVM) 스레드 (v2.0)
물리 장치를 100% 동일한 프로토콜(32B/64B)로 에뮬레이션.
Command 큐 수신 → 내부 연산 → Result Burst 큐 송출.
"""
import os
import time
import glob
import queue
import struct
import threading

import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    from PyQt5.QtCore import QThread, pyqtSignal
    from PyQt5.QtGui import QImage
    HAS_QT = True
except ImportError:
    HAS_QT = False

from HyProtocol import HyProtocol
from RecipeTree import RecipeTree
from HyVisionTools import HyLogicTool, HyLocator, create_tool


# =============================================================================
# ImageProvider — 이미지 소스 추상 인터페이스
# =============================================================================

class ImageProvider:
    """이미지 소스 추상 인터페이스."""

    def get_frame(self):
        """(ndarray | None, img_id) 반환."""
        raise NotImplementedError

    def close(self):
        pass

    @property
    def is_available(self) -> bool:
        return True


class FolderProvider(ImageProvider):
    """
    모드 B: 로컬 폴더 이미지 순환 로드.
    오프라인 배치 테스트 / 개발 전용.
    """

    EXTS = ('*.jpg', '*.jpeg', '*.png', '*.bmp', '*.tiff')

    def __init__(self, folder_path: str):
        self._folder = folder_path
        self._files  = []
        self._idx    = 0
        self._img_id = 0
        self._reload()

    def _reload(self):
        files = []
        for ext in self.EXTS:
            files.extend(glob.glob(os.path.join(self._folder, ext)))
        self._files = sorted(files)
        self._idx   = 0

    def get_frame(self):
        if not self._files:
            return None, 0
        path = self._files[self._idx % len(self._files)]
        self._idx = (self._idx + 1) % len(self._files)
        self._img_id += 1

        if HAS_CV2:
            img = cv2.imread(path)
        else:
            try:
                from PIL import Image
                import numpy as np
                img = np.array(Image.open(path).convert('RGB'))
                img = img[:, :, ::-1]   # RGB → BGR
            except Exception:
                return None, self._img_id

        if img is None:
            return None, self._img_id
        return img, self._img_id

    @property
    def is_available(self) -> bool:
        return len(self._files) > 0

    def __repr__(self):
        return f"<FolderProvider '{self._folder}' files={len(self._files)}>"


class LiveCameraProvider(ImageProvider):
    """
    모드 A: USB 웹캠 / OpenMV 실시간 스트리밍.
    """

    def __init__(self, camera_index: int = 0):
        self._idx    = camera_index
        self._cap    = None
        self._img_id = 0
        self._open()

    def _open(self):
        if not HAS_CV2:
            return
        self._cap = cv2.VideoCapture(self._idx)
        if not self._cap.isOpened():
            self._cap = None

    def get_frame(self):
        if self._cap is None or not self._cap.isOpened():
            return None, 0
        ret, frame = self._cap.read()
        if not ret or frame is None:
            return None, 0
        self._img_id += 1
        return frame, self._img_id

    @property
    def is_available(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    def close(self):
        if self._cap:
            self._cap.release()
            self._cap = None

    def __repr__(self):
        return f"<LiveCameraProvider idx={self._idx} open={self.is_available}>"


# =============================================================================
# VirtualMachine — VVM 스레드
# =============================================================================

_BaseThread = QThread if HAS_QT else threading.Thread


class VirtualMachine(_BaseThread):
    """
    물리 장치를 100% 동일한 프로토콜로 에뮬레이션하는 백그라운드 스레드.
    UI 코드는 물리/가상 연결 구분 없이 HyLink 를 통해 동일하게 통신.
    """

    if HAS_QT:
        sig_log = pyqtSignal(str, str)

    MODE_STANDBY    = "STANDBY"
    MODE_LIVE       = "LIVE"
    MODE_TEACH_SNAP = "TEACH_SNAP"
    MODE_TEST       = "TEST"
    MODE_RUN        = "RUN"

    # JPEG 압축 품질 (0-100)
    JPEG_QUALITY = 60

    def __init__(self, image_provider: ImageProvider = None):
        if HAS_QT:
            super().__init__()
        else:
            super().__init__(daemon=True)

        self.cmd_queue = queue.Queue()   # HyLink 가 32B 패킷을 넣음
        self.rst_queue = queue.Queue(maxsize=3)   # HyLink 가 꺼내는 Burst bytes

        self.image_provider = image_provider
        self.recipe_tree    = RecipeTree()

        self.mode    = self.MODE_STANDBY
        self.running = False

        # 내부 카운터
        self._cycle_id = 0
        self._tx_id    = 0
        self._fps_limit = 30.0   # LIVE 모드 FPS 상한

    # ─────────────────────────────────────────────────────────────────────────
    # 스레드 진입점
    # ─────────────────────────────────────────────────────────────────────────

    def run(self):
        self.running = True
        self._log(f"VVM 시작 [{self.image_provider}]", "system")
        interval = 1.0 / max(1.0, self._fps_limit)

        while self.running:
            loop_start = time.perf_counter()

            # 명령 처리 (모든 큐 대기 명령 소화)
            self._process_commands()

            if self.mode == self.MODE_STANDBY:
                time.sleep(0.05)

            elif self.mode == self.MODE_LIVE:
                self._emit_live_frame()
                elapsed = time.perf_counter() - loop_start
                time.sleep(max(0.0, interval - elapsed))

            elif self.mode == self.MODE_TEACH_SNAP:
                self._emit_live_frame(snap=True)
                self.mode = self.MODE_STANDBY

            elif self.mode in (self.MODE_TEST, self.MODE_RUN):
                self._run_vision_cycle()
                elapsed = time.perf_counter() - loop_start
                time.sleep(max(0.0, interval - elapsed))

        self._log("VVM 종료", "info")

    def stop(self):
        self.running = False
        if HAS_QT:
            self.quit()
            self.wait(3000)
        else:
            self.join(3.0)

    # ─────────────────────────────────────────────────────────────────────────
    # 명령 처리
    # ─────────────────────────────────────────────────────────────────────────

    def _process_commands(self):
        while True:
            try:
                raw = self.cmd_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_command(raw)

    def _handle_command(self, raw: bytes):
        parsed = HyProtocol.unpack_command(raw)
        if parsed is None:
            return

        cmd    = parsed['cmd_id']
        target = parsed['target_id']
        params = parsed['params']

        if cmd == HyProtocol.CMD_STOP:
            self.mode = self.MODE_STANDBY

        elif cmd == HyProtocol.CMD_LIVE:
            fps = params[0] if params[0] > 0 else 30
            self._fps_limit = float(fps)
            self.mode = self.MODE_LIVE

        elif cmd == HyProtocol.CMD_TEACH_SNAP:
            self.mode = self.MODE_TEACH_SNAP

        elif cmd == HyProtocol.CMD_TEST:
            fps = params[0] if params[0] > 0 else 15
            self._fps_limit = float(fps)
            self.mode = self.MODE_TEST

        elif cmd == HyProtocol.CMD_RUN:
            self._fps_limit = 30.0
            self.mode = self.MODE_RUN

        elif cmd == HyProtocol.CMD_SET_TOOL:
            self._handle_set_tool(target, parsed['target_type'],
                                  params, parsed.get('fparam', 0.0))

        elif cmd == HyProtocol.CMD_CLEAR_TOOLS:
            self.recipe_tree.clear()
            self._log("레시피 초기화 완료", "info")

        elif cmd == HyProtocol.CMD_SAVE_RECIPE:
            self._log("레시피 저장(Flash 에뮬): 미구현", "warn")

        elif cmd == HyProtocol.CMD_PING:
            pong = HyProtocol.pack_burst([HyProtocol.make_pong(self._next_tx(), 0)])
            self._push_result(pong)

    def _handle_set_tool(self, tool_id: int, tool_type: int,
                         params: list, fparam: float = 0.0):
        """SET_TOOL 명령으로 내부 RecipeTree 갱신 (v3.0)."""
        device_id, parent_id = HyProtocol.decode_tree_info(params[0])
        # params[1] is now unused (was tool_type in v2; tool_type comes from target_type header)
        rx        = (params[2] >> 16) & 0xFFFF
        ry        =  params[2]        & 0xFFFF
        rw        = (params[3] >> 16) & 0xFFFF
        rh        =  params[3]        & 0xFFFF

        existing = self.recipe_tree.get_tool(tool_id)
        if existing:
            existing.search_roi = (rx, ry, rw, rh)
            existing.device_id  = device_id
            existing.rot_angle  = float(fparam)
            return

        try:
            tool = create_tool(tool_type, tool_id)
            tool.search_roi = (rx, ry, rw, rh)
            tool.device_id  = device_id
            tool.rot_angle  = float(fparam)
            self.recipe_tree.add_tool(tool, parent_id)
        except Exception as e:
            self._log(f"SET_TOOL 오류 (id={tool_id}): {e}", "error")

    # ─────────────────────────────────────────────────────────────────────────
    # 프레임 처리
    # ─────────────────────────────────────────────────────────────────────────

    def _emit_live_frame(self, snap: bool = False):
        """LIVE / TEACH_SNAP: 비전 연산 없이 이미지만 전송."""
        if self.image_provider is None:
            return
        img, img_id = self.image_provider.get_frame()
        if img is None:
            return

        img_bytes = self._encode_jpeg(img)

        # LIVE: 빈 Burst (결과 없음) + 이미지
        burst = HyProtocol.pack_burst([], img_bytes)
        self._push_result(burst)

    def _run_vision_cycle(self):
        """TEST / RUN: 비전 연산 ON. Device ID=1 툴 실행 후 Burst 전송."""
        if self.image_provider is None:
            return
        img, img_id = self.image_provider.get_frame()
        if img is None:
            return

        self._cycle_id = (self._cycle_id % 65535) + 1
        cycle_id = self._cycle_id
        tx_start = self._next_tx()

        # VVM 내부 device_id=1 툴 실행
        packets = []
        tx_counter = [tx_start]

        def _exec_tool(tool):
            from HyVisionTools import HyTool, is_physical_tool
            if tool.device_id == HyProtocol.DEV_CAMERA and is_physical_tool(tool):
                tool.execute(img, img_id, cycle_id)
                packets.append(tool.to_packet(tx_counter[0], cycle_id, img_id))
                tx_counter[0] = (tx_counter[0] % 65535) + 1
                self._tx_id = tx_counter[0]
            if isinstance(tool, HyLogicTool):
                for child in tool.children:
                    _exec_tool(child)

        # HyLocator 는 개별 실행 (트리 밖에 있을 수 있음)
        if self.recipe_tree.anchor:
            anchor = self.recipe_tree.anchor
            anchor.execute(img, img_id, cycle_id)
            packets.append(anchor.to_packet(tx_counter[0], cycle_id, img_id))
            tx_counter[0] = (tx_counter[0] % 65535) + 1

        for root in self.recipe_tree.root_nodes:
            _exec_tool(root)

        # RUN 모드: 이미지 생략
        img_bytes = b'' if self.mode == self.MODE_RUN else self._encode_jpeg(img)
        burst = HyProtocol.pack_burst(packets, img_bytes)
        self._push_result(burst)

    # ─────────────────────────────────────────────────────────────────────────
    # 내부 헬퍼
    # ─────────────────────────────────────────────────────────────────────────

    def _encode_jpeg(self, img: np.ndarray) -> bytes:
        if not HAS_CV2:
            return b''
        ok, buf = cv2.imencode('.jpg', img,
                               [cv2.IMWRITE_JPEG_QUALITY, self.JPEG_QUALITY])
        return bytes(buf) if ok else b''

    def _push_result(self, burst_bytes: bytes):
        """rst_queue 가 가득 찬 경우 가장 오래된 항목 폐기 (Back-pressure)."""
        if self.rst_queue.full():
            try:
                self.rst_queue.get_nowait()
            except queue.Empty:
                pass
        try:
            self.rst_queue.put_nowait(burst_bytes)
        except queue.Full:
            pass

    def _next_tx(self) -> int:
        self._tx_id = (self._tx_id % 65535) + 1
        return self._tx_id

    def _log(self, msg: str, level: str = "info"):
        if HAS_QT and hasattr(self, 'sig_log'):
            try:
                self.sig_log.emit(f"[VVM] {msg}", level)
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # 편의 메서드 (UI 에서 직접 호출용)
    # ─────────────────────────────────────────────────────────────────────────

    def set_image_provider(self, provider: ImageProvider):
        self.image_provider = provider

    def get_recipe_tree(self) -> RecipeTree:
        return self.recipe_tree
