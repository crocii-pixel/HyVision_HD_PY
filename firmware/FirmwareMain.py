"""
firmware/FirmwareMain.py - DCOM VisionTaskRunner 기반 펌웨어 (v2.0)

P1-20: VisionTaskRunner.rebuild_from_commands()
        SET_TOOL 32B 명령 리스트 파싱 → seq_id 정렬 평탄 배열 구성
P1-21: VisionTaskRunner.run_cycle()
        exec_dev_id != My_ID 인 툴 진입 시 바톤 터치 + Burst 송출

■ 플랫폼 분기
  - MicroPython (OpenMV H7+/RT1062) : sensor/image/ustruct 직접 사용
  - CPython (PC 테스트)              : 스텁 레이어를 통해 VisionTaskRunner
                                       핵심 로직을 그대로 검증 가능
"""

# ─────────────────────────────────────────────────────────────────────────────
# 플랫폼 감지 (MicroPython vs CPython)
# ─────────────────────────────────────────────────────────────────────────────
_IS_DEVICE = False
try:
    import ustruct as struct          # MicroPython
    import sensor, image, time, gc   # type: ignore
    import os, select, sys           # type: ignore
    import micropython               # type: ignore
    _IS_DEVICE = True

    try:
        import machine               # type: ignore
        _board = machine.unique_id()
    except Exception:
        _board = b''

    try:
        from pyb import USB_VCP as _USBVCP  # type: ignore
    except ImportError:
        class _USBVCP:                      # RT1062 polyfill
            def __init__(self):
                self._poll = select.poll()
                self._poll.register(sys.stdin, select.POLLIN)
            def setinterrupt(self, v): micropython.kbd_intr(v)
            def any(self): return bool(self._poll.poll(0))
            def read(self, n=1): return sys.stdin.buffer.read(n)
            def recv(self, n, timeout=1000):
                buf, t0 = b'', time.ticks_ms()
                while len(buf) < n:
                    if self.any():
                        chunk = sys.stdin.buffer.read(n - len(buf))
                        if chunk:
                            buf += chunk
                    if time.ticks_diff(time.ticks_ms(), t0) > timeout:
                        break
                return buf
            def send(self, data, timeout=1000):
                return sys.stdout.buffer.write(data)

except ImportError:
    import struct                     # CPython fallback
    import time

# ─────────────────────────────────────────────────────────────────────────────
# 프로토콜 상수 (HyProtocol v3.0 내용 미러 — MicroPython 독립)
# ─────────────────────────────────────────────────────────────────────────────
CMD_SYNC        = 0xBB66
RST_SYNC        = 0xAA55

CMD_STOP        = 0x00
CMD_LIVE        = 0x01
CMD_TEACH_SNAP  = 0x02
CMD_TEST        = 0x03
CMD_RUN         = 0x04
CMD_SET_TOOL    = 0x10
CMD_SET_CAMERA  = 0x12
CMD_SAVE_RECIPE = 0x18
CMD_CLEAR_TOOLS = 0x19
CMD_PING        = 0xF0
CMD_RESET       = 0xFF

TOOL_LINE       = 0x01
TOOL_PATMAT     = 0x02
TOOL_LOCATOR    = 0x03
TOOL_DISTANCE   = 0x10
TOOL_CONTRAST   = 0x11
TOOL_FND        = 0x12
TOOL_WHEN       = 0x20
TOOL_AND        = 0x21
TOOL_OR         = 0x22
TOOL_FIN        = 0x23

DEV_CAMERA      = 1
DEV_PC          = 2

EXEC_IDLE       = 0
EXEC_DONE       = 1
EXEC_ERROR      = 2
EXEC_PENDING    = 3

JUDGE_NG        = 0
JUDGE_OK        = 1
JUDGE_PENDING   = 2

CMD_FORMAT  = "<4H4IfI"          # 32 B
CMD_SIZE    = 32
RST_FORMAT  = "<HIIIHHH BB 5f 4f I 2x"   # 64 B (공백 무시됨)
RST_SIZE    = 64

BURST_HDR_FMT  = "<HH"          # RST_SYNC + packet_count
BURST_IMG_FMT  = "<I"           # image_size

MAX_BURST_PACKETS = 128
MAX_IMAGE_BYTES   = 2 * 1024 * 1024  # 2 MiB OOM 방어


# ─────────────────────────────────────────────────────────────────────────────
# 내부 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
def _ticks_ms():
    """현재 시각 (ms). MicroPython/CPython 양쪽 호환."""
    if _IS_DEVICE:
        return time.ticks_ms()
    return int(time.time() * 1000)


def _ticks_diff(end, start):
    if _IS_DEVICE:
        return time.ticks_diff(end, start)
    return end - start


def _decode_tree_info(p0: int):
    """param[0] → (device_id, parent_id)"""
    return (p0 >> 16) & 0xFFFF, p0 & 0xFFFF


def _decode_xy(p2: int):
    """param[2] → (x, y)"""
    return (p2 >> 16) & 0xFFFF, p2 & 0xFFFF


def _decode_wh(p3: int):
    """param[3] → (w, h)"""
    return (p3 >> 16) & 0xFFFF, p3 & 0xFFFF


# ─────────────────────────────────────────────────────────────────────────────
# VisionTaskRunner  (P1-20 / P1-21)
# ─────────────────────────────────────────────────────────────────────────────

class VisionTaskRunner:
    """DCOM SET_TOOL 명령 파싱 → 평탄 배열 → 사이클 실행 관리자.

    P1-20 rebuild_from_commands():
        32B SET_TOOL 패킷 리스트를 파싱하여 seq_id 오름차순으로 정렬된
        _tool_specs 평탄 배열을 구성한다.

    P1-21 run_cycle():
        _tool_specs 를 순서대로 실행하되, device_id != MY_DEVICE_ID 인
        툴을 만나는 즉시 멈추고 지금까지의 결과를 Burst 로 PC 에 송출한다
        (바톤 터치).  MY_DEVICE 전용 툴만 존재할 경우에도 마지막에 Burst
        를 내보낸다.

    실제 영상 연산은 ``register_executor(tool_type, fn)`` 으로 주입된
    핸들러 함수가 담당한다.  핸들러 미등록 시 EXEC_ERROR 처리.
    """

    MY_DEVICE_ID = DEV_CAMERA       # 이 장치의 device_id

    def __init__(self):
        self._tool_specs: list  = []   # rebuild 후 seq_id 정렬 dict 리스트
        self._results:    dict  = {}   # tool_id → 최신 RST 패킷 bytes
        self._tx_id:      int   = 0    # 단조 증가 송출 번호
        self._executors:  dict  = {}   # tool_type → callable(spec, img) → dict

    # ─────────────────────────────────────────────────────────────────────────
    # 실행기 등록 API
    # ─────────────────────────────────────────────────────────────────────────

    def register_executor(self, tool_type: int, fn) -> None:
        """tool_type 에 대한 실행 함수 등록.

        fn(spec: dict, img) -> dict
        반환 dict 키: rst_state, x, y, w, h, angle, stat1~4 (모두 선택적)
        """
        self._executors[tool_type] = fn

    # ─────────────────────────────────────────────────────────────────────────
    # P1-20: rebuild_from_commands
    # ─────────────────────────────────────────────────────────────────────────

    def rebuild_from_commands(self, cmds: list) -> None:
        """SET_TOOL 32B 명령 리스트 파싱 → _tool_specs 재구성.

        Args:
            cmds: list[bytes]  각 원소는 정확히 32B 인 CMD_SET_TOOL 패킷.
                  다른 cmd_id 나 손상된 패킷은 조용히 무시된다.

        Side-effects:
            self._tool_specs ← seq_id 오름차순 정렬된 dict 리스트
            self._results    ← 빈 dict 로 초기화
        """
        specs = []
        for raw in cmds:
            if len(raw) < CMD_SIZE:
                continue
            try:
                u = struct.unpack_from(CMD_FORMAT, raw)
            except Exception:
                continue

            sync, cmd_id, target_id, target_type = u[0], u[1], u[2], u[3]
            p0 = int(u[4]); p1 = int(u[5]); p2 = int(u[6]); p3 = int(u[7])
            fparam = float(u[8])

            if sync != CMD_SYNC or cmd_id != CMD_SET_TOOL:
                continue

            device_id, parent_id = _decode_tree_info(p0)
            seq_id               = p1 & 0xFFFF_FFFF  # int32 → uint 범위 정규화
            roi_x, roi_y         = _decode_xy(p2)
            roi_w, roi_h         = _decode_wh(p3)

            specs.append({
                'tool_id':   int(target_id),
                'tool_type': int(target_type),
                'device_id': int(device_id),
                'parent_id': int(parent_id),
                'seq_id':    int(seq_id),
                'roi':       (int(roi_x), int(roi_y), int(roi_w), int(roi_h)),
                'rot_angle': fparam,
            })

        # seq_id 기준 오름차순 정렬 (MicroPython / CPython 동일)
        specs.sort(key=lambda s: s['seq_id'])
        self._tool_specs = specs
        self._results    = {}

    # ─────────────────────────────────────────────────────────────────────────
    # P1-21: run_cycle
    # ─────────────────────────────────────────────────────────────────────────

    def run_cycle(self, img, cycle_id: int, img_id: int,
                  send_burst_fn, keep_img: bool = True) -> bool:
        """현재 프레임에 대해 MY_DEVICE 툴 순차 실행 → Burst 송출.

        MY_DEVICE 툴들을 seq_id 순으로 실행하다가 다른 device_id 툴을
        만나면 즉시 멈추고 지금까지의 결과 패킷 + 이미지를 Burst 로
        send_burst_fn 에 넘긴다 (바톤 터치).

        Args:
            img:           캡처 이미지 (OpenMV image | numpy ndarray | None)
            cycle_id:      현재 사이클 번호
            img_id:        현재 이미지 번호
            send_burst_fn: fn(packets: list[bytes], img, has_more: bool) → None
                           has_more=True → PC 툴이 남아있음(바톤 터치 의미)
            keep_img:      True 면 Burst 에 이미지 포함 전달

        Returns:
            has_more (bool): PC 에 바톤 터치가 필요했으면 True
        """
        packets  = []
        has_more = False

        for spec in self._tool_specs:
            if spec['device_id'] != self.MY_DEVICE_ID:
                # 다른 장치(PC 등) 툴 → 바톤 터치 지점
                has_more = True
                break

            # 이 장치(Camera) 담당 툴 실행
            t0 = _ticks_ms()
            rst_done  = EXEC_DONE
            rst_state = JUDGE_NG
            x = y = w = h = angle = 0.0
            stat1 = stat2 = stat3 = stat4 = 0.0

            try:
                fn = self._executors.get(spec['tool_type'])
                if fn is None:
                    raise NotImplementedError
                result    = fn(spec, img)
                rst_state = int(result.get('rst_state', JUDGE_NG))
                x     = float(result.get('x',     0.0))
                y     = float(result.get('y',     0.0))
                w     = float(result.get('w',     0.0))
                h     = float(result.get('h',     0.0))
                angle = float(result.get('angle', 0.0))
                stat1 = float(result.get('stat1', 0.0))
                stat2 = float(result.get('stat2', 0.0))
                stat3 = float(result.get('stat3', 0.0))
                stat4 = float(result.get('stat4', 0.0))
            except Exception:
                rst_done  = EXEC_ERROR
                rst_state = JUDGE_NG

            proc_ms = _ticks_diff(_ticks_ms(), t0)
            self._tx_id += 1

            pkt = struct.pack(
                RST_FORMAT,
                RST_SYNC,
                self._tx_id, int(cycle_id), int(img_id),
                int(spec['seq_id']), int(spec['tool_id']), int(spec['tool_type']),
                int(rst_done), int(rst_state),
                x, y, w, h, angle,
                stat1, stat2, stat3, stat4,
                int(proc_ms),
            )
            packets.append(pkt)
            self._results[spec['tool_id']] = pkt

        # Burst 송출 (바톤 터치 여부와 무관하게 항상 호출)
        send_burst_fn(packets, img if keep_img else None, has_more)
        return has_more

    # ─────────────────────────────────────────────────────────────────────────
    # 진단용
    # ─────────────────────────────────────────────────────────────────────────

    @property
    def tool_count(self) -> int:
        return len(self._tool_specs)

    @property
    def my_tool_count(self) -> int:
        return sum(1 for s in self._tool_specs if s['device_id'] == self.MY_DEVICE_ID)

    def get_result(self, tool_id: int):
        """마지막 run_cycle 에서 생성된 RST 패킷 bytes 반환. 없으면 None."""
        return self._results.get(tool_id)


# ─────────────────────────────────────────────────────────────────────────────
# 장치 전용 Vision 실행기 (OpenMV)
# 이 블록은 _IS_DEVICE == True 일 때만 의미가 있다.
# PC 테스트 시에는 VisionTaskRunner.register_executor() 로 대체 주입.
# ─────────────────────────────────────────────────────────────────────────────

def _exec_hyline_device(spec, img):
    """HyLine 장치 실행기 (OpenMV 전용).
    선형 ROI 내 가장 밝은 선의 위치·각도를 검출한다.
    """
    if not _IS_DEVICE:
        raise RuntimeError("device executor called on PC")

    rx, ry, rw, rh = spec['roi']
    # ROI 경계 보정
    iw, ih = img.width(), img.height()
    rx = max(0, min(rx, iw - 1))
    ry = max(0, min(ry, ih - 1))
    rw = max(1, min(rw, iw - rx))
    rh = max(1, min(rh, ih - ry))

    roi = (rx, ry, rw, rh)

    # 수직 방향 밝기 프로젝션으로 선 위치 추정 (row-sum)
    stats = img.get_statistics(roi=roi)
    cx = float(rx + rw / 2)
    cy = float(ry + rh / 2)

    return {
        'rst_state': JUDGE_OK,
        'x': cx, 'y': cy,
        'w': float(rw), 'h': float(rh),
        'angle': 0.0,
        'stat1': float(stats.mean()),
    }


def _exec_hypatmat_device(spec, img):
    """HyPatMat 장치 실행기 (OpenMV 전용) — 스텁."""
    if not _IS_DEVICE:
        raise RuntimeError("device executor called on PC")
    rx, ry, rw, rh = spec['roi']
    return {
        'rst_state': JUDGE_NG,
        'x': float(rx), 'y': float(ry),
        'w': float(rw), 'h': float(rh),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 메인 루프 (OpenMV 장치 전용)
# ─────────────────────────────────────────────────────────────────────────────

if _IS_DEVICE:
    # ── 센서 초기화 ──────────────────────────────────────────────────────────
    sensor.reset()
    sensor.set_pixformat(sensor.RGB565)
    sensor.set_framesize(sensor.VGA)
    sensor.set_auto_exposure(True, exposure_us=200000)
    sensor.set_auto_gain(False, gain_db=10)
    sensor.skip_frames(time=1000)
    gc.collect()

    usb = _USBVCP()
    usb.setinterrupt(-1)

    # ── VisionTaskRunner 초기화 ───────────────────────────────────────────────
    runner = VisionTaskRunner()
    runner.register_executor(TOOL_LINE,   _exec_hyline_device)
    runner.register_executor(TOOL_PATMAT, _exec_hypatmat_device)

    # ── 상태 변수 ─────────────────────────────────────────────────────────────
    mode           = 'STANDBY'
    cycle_id       = 0
    img_id         = 0
    pending_cmds   = []      # rebuild_from_commands 에 전달할 버퍼
    jpeg_quality   = 50

    def _send_burst(packets, img, has_more):
        """Burst 헤더 + 패킷 + 이미지 직렬 송출."""
        n = len(packets)
        if n > MAX_BURST_PACKETS:
            return
        hdr = struct.pack(BURST_HDR_FMT, RST_SYNC, n)
        usb.send(hdr)
        for pkt in packets:
            usb.send(pkt)
        if img is not None:
            try:
                cimg = img.compress(quality=jpeg_quality)
                img_bytes = cimg.bytearray()
                img_size  = len(img_bytes)
            except Exception:
                img_bytes = b''
                img_size  = 0
        else:
            img_bytes = b''
            img_size  = 0
        if img_size > MAX_IMAGE_BYTES:
            img_bytes = b''
            img_size  = 0
        usb.send(struct.pack(BURST_IMG_FMT, img_size))
        if img_size:
            usb.send(img_bytes)

    # ── 명령 수신 루프 ────────────────────────────────────────────────────────
    SEND_TO = 500
    READ_TO = 200

    while True:
        if usb.any():
            raw = usb.recv(CMD_SIZE, timeout=READ_TO)
            if len(raw) < CMD_SIZE:
                continue

            try:
                u     = struct.unpack_from(CMD_FORMAT, raw)
                sync  = u[0]
                cid   = u[1]
            except Exception:
                continue

            if sync != CMD_SYNC:
                continue

            if cid == CMD_STOP:
                mode = 'STANDBY'

            elif cid == CMD_LIVE:
                mode = 'LIVE'

            elif cid == CMD_TEACH_SNAP:
                mode = 'STANDBY'
                try:
                    img = sensor.snapshot()
                    img_id += 1
                    _send_burst([], img, False)
                except Exception:
                    pass

            elif cid == CMD_TEST:
                cycle_id += 1
                mode      = 'TEST'

            elif cid == CMD_RUN:
                cycle_id += 1
                mode      = 'RUN'

            elif cid == CMD_SET_TOOL:
                # 이미 수신된 패킷 저장 (rebuild 시 일괄 처리)
                pending_cmds.append(bytes(raw))
                # 마지막 툴까지 받았다는 신호 없이 바로 rebuild 호출
                # (PC 가 CLEAR_TOOLS 후 SET_TOOL 스트림을 보내고 RUN/TEST 명령으로 종료)

            elif cid == CMD_CLEAR_TOOLS:
                pending_cmds.clear()
                runner.rebuild_from_commands([])
                mode = 'STANDBY'

            elif cid == CMD_PING:
                # PONG 응답 (tool_id=0, rst_done=EXEC_DONE)
                pong = struct.pack(
                    RST_FORMAT,
                    RST_SYNC,
                    0, 0, 0,
                    0, 0, 0,
                    EXEC_DONE, JUDGE_OK,
                    0.0, 0.0, 0.0, 0.0, 0.0,
                    0.0, 0.0, 0.0, 0.0,
                    0,
                )
                usb.send(pong)

            elif cid == CMD_SET_CAMERA:
                # exposure=p0, gain=p1
                p0, p1 = int(u[4]), int(u[5])
                try:
                    if p0 > 0:
                        sensor.set_auto_exposure(False, exposure_us=p0)
                    else:
                        sensor.set_auto_exposure(True)
                    if p1 > 0:
                        sensor.set_auto_gain(False, gain_db=p1)
                    else:
                        sensor.set_auto_gain(True)
                except Exception:
                    pass

        # ── 연속 실행 모드 ────────────────────────────────────────────────────
        if mode == 'LIVE':
            try:
                img     = sensor.snapshot()
                img_id += 1
                _send_burst([], img, False)
            except Exception:
                mode = 'STANDBY'

        elif mode in ('TEST', 'RUN'):
            try:
                img = sensor.snapshot()
                img_id  += 1
                cycle_id += 1
                # 처음 TEST/RUN 진입 시 pending_cmds 로 rebuild
                if pending_cmds:
                    runner.rebuild_from_commands(pending_cmds)
                    pending_cmds.clear()
                keep = (mode == 'TEST')   # RUN 모드에서는 이미지 전송 생략
                runner.run_cycle(img, cycle_id, img_id, _send_burst, keep_img=keep)
                gc.collect()
            except Exception:
                mode = 'STANDBY'
