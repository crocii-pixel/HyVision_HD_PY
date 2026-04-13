import sensor, image, time, gc, ustruct, os, json
import sys, select, micropython
import machine

if "h7 plus" in machine:
    from pyb import USB_VCP
elif "rt1062" in machine:
    class USB_VCP:
        def __init__(self):
            self.poller = select.poll()
            self.poller.register(sys.stdin, select.POLLIN)

        def setinterrupt(self, val):
            micropython.kbd_intr(val)

        def any(self):
            return len(self.poller.poll(0)) > 0

        def read(self, size=1):
            return sys.stdin.buffer.read(size)

        def recv(self, size, timeout=1000):
            start = time.ticks_ms()
            buf = b''
            while len(buf) < size:
                if self.any():
                    chunk = sys.stdin.buffer.read(size - len(buf))
                    if chunk:
                        buf += chunk
                if time.ticks_diff(time.ticks_ms(), start) > timeout:
                    break
            return buf

        def send(self, data, timeout=1000):
            return sys.stdout.buffer.write(data)

# ==============================================================================
# [1. 환경 설정]
# ==============================================================================
SEND_TIMEOUT = 500
READ_TIMEOUT = 200

sensor.reset()
sensor.set_pixformat(sensor.RGB565)
sensor.set_framesize(sensor.VGA)

sensor.set_auto_exposure(True, exposure_us=200000)
sensor.set_auto_gain(False, gain_db=10)
sensor.set_contrast(0)
sensor.set_brightness(0)
sensor.set_vflip(False)
sensor.set_hmirror(False)

sensor.skip_frames(time=1000)
gc.collect()

usb = USB_VCP()
usb.setinterrupt(-1)

try: os.mkdir('Reg')
except: pass
try: os.mkdir('Reg/Model')
except: pass
SAVE_DIR = "Reg/Model"

PACKET_FORMAT = "<HHHIBiiiiiifiii"
img_format = 0
jpeg_quality = 50

current_img_cfg = {'exp_auto': 1, 'exp_val': 200000, 'gain_auto': 0, 'gain_val': 10, 'contrast': 0, 'brightness': 0, 'vflip': 0, 'hmirror': 0, 'quality': 50}

def write_log(msg):
    try:
        with open('Reg/debug.txt', 'a') as f: f.write(msg + '\n')
    except: pass

# ==============================================================================
# [2. HyPatMat Core Engine (Optimized & Cleaned)]
# ==============================================================================
class SmartLockSystem:
    def __init__(self, config):
        self.cfg = config
        self.roi_w, self.roi_h = self.cfg['roi_w'], self.cfg['roi_h']
        self.margin = self.cfg['search_margin']
        self.step_l0 = self.cfg.get('step_l0', 1)
        self.en_L1, self.en_L2 = self.cfg['enable_L1'], self.cfg['enable_L2']

        self.early_exit = self.cfg.get('early_exit', False)

        self.en_mean, self.st_mean, self.ls_mean = self._parse_th(self.cfg.get('check_mean', (0,0)))
        self.en_std,  self.st_std,  self.ls_std  = self._parse_th(self.cfg.get('check_std', (0,0)))
        self.en_iqr,  self.st_iqr,  self.ls_iqr  = self._parse_th(self.cfg.get('check_iqr', (0,0)))

        self.en_sig,  self.st_sig,  self.ls_sig  = self._parse_th(self.cfg.get('check_sig', (0,0)))
        self.bins = self.cfg.get('sig_bins', 4)

        self.tgt_mean = 0; self.tgt_stdev = 0; self.tgt_iqr = 0
        self.tgt_x_prof = [0.0] * self.bins
        self.tgt_y_prof = [0.0] * self.bins

        self.fb_l0 = sensor.alloc_extra_fb(80, 60, sensor.GRAYSCALE)
        self.fb_l1 = sensor.alloc_extra_fb(160, 120, sensor.GRAYSCALE) if self.en_L1 else None
        self.fb_l2 = sensor.alloc_extra_fb(320, 240, sensor.GRAYSCALE) if self.en_L2 else None

        self.fb_patch = sensor.alloc_extra_fb(self.roi_w + 2, self.roi_h + 2, sensor.GRAYSCALE)

        self.templates = {}
        self.is_trained = False

    def cleanup(self):
        if self.is_trained:
            if self.fb_patch: sensor.dealloc_extra_fb()
            if self.fb_l2: sensor.dealloc_extra_fb()
            if self.fb_l1: sensor.dealloc_extra_fb()
            if self.fb_l0: sensor.dealloc_extra_fb()
            self.is_trained = False
            self.fb_patch = None; self.fb_l2 = None; self.fb_l1 = None; self.fb_l0 = None
            self.templates.clear()

    def _parse_th(self, val):
        if isinstance(val, (tuple, list)) and len(val) == 2:
            st, ls = val[0], val[1]
            if st > 0 and ls > 0 and st < ls: return True, st, ls
        return False, 0, 0

    def _get_iqr(self, stats):
        try: return stats.iqr()
        except: return 0

    def _get_diff(self, tgt_prof, curr_val, tgt_mean, curr_mean):
        return abs((tgt_prof - tgt_mean) - (curr_val - curr_mean))

    def load_templates(self, t0, t1, t2, t3):
        self.templates[0] = t0
        if self.en_L1: self.templates[1] = t1
        if self.en_L2: self.templates[2] = t2
        self.templates[3] = t3

        stats = t3.get_statistics()
        self.tgt_mean = stats.mean()
        self.tgt_stdev = stats.stdev()
        self.tgt_iqr = self._get_iqr(stats)

        if self.en_sig:
            step_w, step_h = max(1, self.roi_w // self.bins), max(1, self.roi_h // self.bins)
            for i in range(min(self.bins, self.roi_w)):
                self.tgt_x_prof[i] = t3.get_statistics(roi=(i*step_w, 0, step_w, self.roi_h)).mean()
            for i in range(min(self.bins, self.roi_h)):
                self.tgt_y_prof[i] = t3.get_statistics(roi=(0, i*step_h, self.roi_w, step_h)).mean()

        self.is_trained = True

    def learn(self, template_img):
        def _make_t(scale):
            tw, th = int(self.roi_w*scale), int(self.roi_h*scale)
            t = sensor.alloc_extra_fb(tw, th, sensor.GRAYSCALE)
            t.draw_image(template_img, 0, 0, x_scale=scale, y_scale=scale)
            return t
        self.templates[0] = _make_t(0.125)
        if self.en_L1: self.templates[1] = _make_t(0.25)
        if self.en_L2: self.templates[2] = _make_t(0.5)
        self.templates[3] = template_img

        stats = template_img.get_statistics()
        self.tgt_mean = stats.mean()
        self.tgt_stdev = stats.stdev()
        self.tgt_iqr = self._get_iqr(stats)

        if self.en_sig:
            step_w, step_h = max(1, self.roi_w // self.bins), max(1, self.roi_h // self.bins)
            for i in range(min(self.bins, self.roi_w)):
                self.tgt_x_prof[i] = template_img.get_statistics(roi=(i*step_w, 0, step_w, self.roi_h)).mean()
            for i in range(min(self.bins, self.roi_h)):
                self.tgt_y_prof[i] = template_img.get_statistics(roi=(0, i*step_h, self.roi_w, step_h)).mean()

        self.is_trained = True

    def scan(self, img):
        if not self.is_trained: return False, (0,0,0,0), 0, 1, 0, 0, 0, 0

        self.fb_l0.draw_image(img, 0, 0, x_scale=0.125, y_scale=0.125)
        r0 = self.fb_l0.find_template(self.templates[0], self.cfg['th_scan'], step=self.step_l0, search=image.SEARCH_EX)
        if not r0: return False, (0,0,0,0), 0, 1, 0, 0, 0, 0

        gx, gy, gw, gh = r0[:4]
        cx, cy = gx+(gw//2), gy+(gh//2)

        if self.en_L1:
            sw, sh = self.templates[1].width()+self.margin, self.templates[1].height()+self.margin
            sx, sy = max(0, cx*2-(sw//2)), max(0, cy*2-(sh//2))
            self.fb_l1.draw_image(img, 0, 0, x_scale=0.25, y_scale=0.25)
            r1 = self.fb_l1.find_template(self.templates[1], self.cfg['th_find'], roi=(sx,sy,sw,sh), step=1)
            if r1: cx, cy = r1[0]+(r1[2]//2), r1[1]+(r1[3]//2)
            else: return False, (0,0,0,0), 0, 1, 0, 0, 0, 0

        if self.en_L2:
            sw, sh = self.templates[2].width()+self.margin, self.templates[2].height()+self.margin
            sx, sy = max(0, cx*2-(sw//2)), max(0, cy*2-(sh//2))
            self.fb_l2.draw_image(img, 0, 0, x_scale=0.5, y_scale=0.5)
            r2 = self.fb_l2.find_template(self.templates[2], self.cfg['th_find'], roi=(sx,sy,sw,sh), step=1)
            if r2: cx, cy = r2[0]+(r2[2]//2), r2[1]+(r2[3]//2)
            else: return False, (0,0,0,0), 0, 1, 0, 0, 0, 0

        fx, fy = cx*2, cy*2
        sw, sh = self.fb_patch.width(), self.fb_patch.height()
        sx, sy = max(0, min(fx-sw//2, img.width()-sw)), max(0, min(fy-sh//2, img.height()-sh))

        self.fb_patch.draw_image(img, 0, 0, roi=(sx, sy, sw, sh))

        r3 = self.fb_patch.find_template(self.templates[3], self.cfg['th_find'], step=1)
        if not r3:
            return False, (0,0,0,0), 0, 1, 0, 0, 0, 0

        final_x, final_y = sx + r3[0], sy + r3[1]
        vga_roi = (final_x, final_y, self.roi_w, self.roi_h)

        d_mean, d_std, d_iqr, d_sig = 0, 0, 0, 0
        curr_stats = None
        global_status = 3

        local_rx, local_ry = r3[0], r3[1]

        if self.en_mean or self.en_std or self.en_iqr or self.en_sig:
            curr_stats = self.fb_patch.get_statistics(roi=(local_rx, local_ry, self.roi_w, self.roi_h))

        if curr_stats:
            if self.en_mean:
                d_mean = abs(self.tgt_mean - curr_stats.mean())
                global_status = min(global_status, 1 if d_mean >= self.ls_mean else (2 if d_mean >= self.st_mean else 3))
                if self.early_exit and global_status == 1: return True, vga_roi, 0, global_status, int(d_mean), int(d_std), int(d_iqr), int(d_sig)
            if self.en_std:
                d_std = abs(self.tgt_stdev - curr_stats.stdev())
                global_status = min(global_status, 1 if d_std >= self.ls_std else (2 if d_std >= self.st_std else 3))
                if self.early_exit and global_status == 1: return True, vga_roi, 0, global_status, int(d_mean), int(d_std), int(d_iqr), int(d_sig)
            if self.en_iqr:
                d_iqr = abs(self.tgt_iqr - self._get_iqr(curr_stats))
                global_status = min(global_status, 1 if d_iqr >= self.ls_iqr else (2 if d_iqr >= self.st_iqr else 3))
                if self.early_exit and global_status == 1: return True, vga_roi, 0, global_status, int(d_mean), int(d_std), int(d_iqr), int(d_sig)

        if self.en_sig and curr_stats:
            step_w, step_h = max(1, self.roi_w // self.bins), max(1, self.roi_h // self.bins)
            diff_sum, count = 0, 0
            for i in range(min(self.bins, self.roi_w)):
                v = self.fb_patch.get_statistics(roi=(local_rx + i*step_w, local_ry, step_w, self.roi_h)).mean()
                diff_sum += self._get_diff(self.tgt_x_prof[i], v, self.tgt_mean, curr_stats.mean())
                count += 1
            for i in range(min(self.bins, self.roi_h)):
                v = self.fb_patch.get_statistics(roi=(local_rx, local_ry + i*step_h, self.roi_w, step_h)).mean()
                diff_sum += self._get_diff(self.tgt_y_prof[i], v, self.tgt_mean, curr_stats.mean())
                count += 1
            d_sig = diff_sum / (count if count else 1)

        return True, vga_roi, 0, global_status, int(d_mean), int(d_std), int(d_iqr), int(d_sig)

# ==============================================================================
# [3. 통신 및 메인 루프 (Board-Master Protocol)]
# ==============================================================================
def list_models():
    try:
        files = [f for f in os.listdir(SAVE_DIR) if f.endswith('.mdl')]
        file_list_str = ",".join(files)
    except: file_list_str = ""
    encoded = file_list_str.encode('utf-8')
    usb.send(ustruct.pack('<I', len(encoded)), timeout=SEND_TIMEOUT)
    if len(encoded) > 0: usb.send(encoded, timeout=SEND_TIMEOUT)

mode = 'STANDBY'
smart_lock = None
ref_fb = None

tool_spMat = {
    'roi_w': 100, 'roi_h': 100, 'search_margin': 3, 'step_l0': 1,
    'enable_L1': True, 'enable_L2': True, 'th_scan': 0.10, 'th_find': 0.50,
    'check_mean': (0, 0), 'check_std': (4, 12), 'check_iqr': (0, 0), 'check_sig': (4, 7),
    'early_exit': False, 'sig_bins': 4
}

while True:
    if usb.any():
        cmd = usb.read(1)

        if cmd == b'I':
            info = {"fw": "HyVision_H7Plus FW v1.1-StableStatus", "img_cfg": current_img_cfg}
            json_str = json.dumps(info).encode('utf-8')
            usb.send(ustruct.pack('<I', len(json_str)), timeout=SEND_TIMEOUT)
            usb.send(json_str, timeout=SEND_TIMEOUT)

        elif cmd == b'f':
            try:
                img_format = ustruct.unpack('<B', usb.recv(1, timeout=READ_TIMEOUT))[0]
                usb.send(b'OK', timeout=SEND_TIMEOUT)
            except: usb.send(b'ER', timeout=SEND_TIMEOUT)

        elif cmd == b'i':
            try:
                payload = usb.recv(36, timeout=READ_TIMEOUT)
                if len(payload) == 36:
                    exp_a, exp_v, gain_a, gain_v, cont, brt, vflip, hmir, qual = ustruct.unpack('<iiiiiiiii', payload)
                    jpeg_quality = qual
                    if current_img_cfg['exp_auto'] != exp_a or (exp_a == 0 and current_img_cfg['exp_val'] != exp_v):
                        if exp_a == 1: sensor.set_auto_exposure(True)
                        else: sensor.set_auto_exposure(False, exposure_us=exp_v)
                        current_img_cfg['exp_auto'], current_img_cfg['exp_val'] = exp_a, exp_v
                    if current_img_cfg['gain_auto'] != gain_a or (gain_a == 0 and current_img_cfg['gain_val'] != gain_v):
                        if gain_a == 1: sensor.set_auto_gain(True)
                        else: sensor.set_auto_gain(False, gain_db=gain_v)
                        current_img_cfg['gain_auto'], current_img_cfg['gain_val'] = gain_a, gain_v
                    if current_img_cfg['contrast'] != cont:
                        sensor.set_contrast(cont); current_img_cfg['contrast'] = cont
                    if current_img_cfg['brightness'] != brt:
                        sensor.set_brightness(brt); current_img_cfg['brightness'] = brt
                    if current_img_cfg['vflip'] != vflip:
                        sensor.set_vflip(vflip == 1); current_img_cfg['vflip'] = vflip
                    if current_img_cfg['hmirror'] != hmir:
                        sensor.set_hmirror(hmir == 1); current_img_cfg['hmirror'] = hmir
                    current_img_cfg.update({'contrast':cont,'brightness':brt,'vflip':vflip,'hmirror':hmir, 'quality':qual})
                    usb.send(b'OK', timeout=SEND_TIMEOUT)
                else: usb.send(b'ER', timeout=SEND_TIMEOUT)
            except Exception as e: usb.send(b'ER', timeout=SEND_TIMEOUT)

        elif cmd == b'l':
            mode = 'LIVE'
            if ref_fb is not None: sensor.dealloc_extra_fb(); ref_fb = None

        elif cmd == b'c':
            mode = 'STANDBY'
            try:
                if ref_fb is not None: sensor.dealloc_extra_fb(); ref_fb = None
                gc.collect(); sensor.skip_frames(2)
                img = sensor.snapshot()
                ref_fb = sensor.alloc_extra_fb(img.width(), img.height(), sensor.GRAYSCALE)
                if not ref_fb: raise Exception("FB Alloc Fail")
                ref_fb.draw_image(img, 0, 0)
                cimg = img.compress(quality=jpeg_quality)
                usb.send(b'\x55\xAA' + ustruct.pack('<I', cimg.size()), timeout=SEND_TIMEOUT); usb.send(cimg.bytearray(), timeout=SEND_TIMEOUT)
            except Exception as e: write_log("CAP_ERR: " + str(e))

        elif cmd == b'x':
            mode = 'STANDBY'
            if smart_lock: smart_lock.cleanup(); smart_lock = None
            if ref_fb is not None: sensor.dealloc_extra_fb(); ref_fb = None
            gc.collect()
            usb.send(b'OK', timeout=SEND_TIMEOUT)

        elif cmd == b'm':
            mode = 'STANDBY'; list_models()

        elif cmd == b'd':
            mode = 'STANDBY'
            try:
                name_len = ustruct.unpack('<I', usb.recv(4, timeout=READ_TIMEOUT))[0]
                name = usb.recv(name_len, timeout=READ_TIMEOUT).decode('utf-8')
                try: os.remove(SAVE_DIR + '/' + name)
                except: pass
                try: os.remove(SAVE_DIR + '/' + name + '.meta')
                except: pass
                try: os.remove(SAVE_DIR + '/' + name + '.rst')
                except: pass
                os.sync(); usb.send(b'OK', timeout=SEND_TIMEOUT)
            except Exception: usb.send(b'ER', timeout=SEND_TIMEOUT)

        elif cmd == b'u':
            mode = 'STANDBY'
            try:
                name_len = ustruct.unpack('<I', usb.recv(4, timeout=READ_TIMEOUT))[0]
                name = usb.recv(name_len, timeout=READ_TIMEOUT).decode('utf-8')
                coord_data = usb.recv(16, timeout=READ_TIMEOUT)
                mx, my, mw, mh, ax, ay, aw, ah = ustruct.unpack('<HHHHHHHH', coord_data)
                json_len_bytes = usb.recv(4, timeout=READ_TIMEOUT)
                if len(json_len_bytes) != 4: raise Exception("Invalid JSON Length bytes")
                json_len = ustruct.unpack('<I', json_len_bytes)[0]
                json_str = b''
                t_start = time.ticks_ms()
                while len(json_str) < json_len and time.ticks_diff(time.ticks_ms(), t_start) < 3000:
                    chunk = usb.recv(json_len - len(json_str), timeout=READ_TIMEOUT)
                    if chunk: json_str += chunk
                if len(json_str) != json_len: raise Exception("Incomplete JSON data")

                if ref_fb is not None:
                    # 💡 [핵심 최적화: 파일 깨짐 방지] 임시 대형 버퍼 할당 방식을 제거하고 음수 좌표 크롭으로 교체
                    try:
                        scales = [(0.125, 80, 60), (0.25, 160, 120), (0.5, 320, 240), (1.0, 640, 480)]
                        with open(SAVE_DIR + '/' + name, 'wb') as f:
                            f.write(ustruct.pack("<2sB", b'MS', 4))
                            for scale, sw, sh in scales:
                                tw, th = max(1, int(mw * scale)), max(1, int(mh * scale))
                                if scale == 1.0:
                                    t_fb = sensor.alloc_extra_fb(tw, th, sensor.GRAYSCALE)
                                    if not t_fb: raise Exception("OOM L3")
                                    t_fb.draw_image(ref_fb, 0, 0, roi=(mx, my, mw, mh))
                                    data = t_fb.bytearray()
                                    f.write(ustruct.pack("<HHI", tw, th, len(data)))
                                    f.write(data)
                                    sensor.dealloc_extra_fb()
                                else:
                                    # 임시 전체 버퍼(full_scaled) 없이 음수 좌표 드로잉으로 다이렉트 크롭 추출! (OOM 메모리 에러 완전 차단)
                                    smx, smy = int(mx * scale), int(my * scale)
                                    tw = min(tw, sw - smx)
                                    th = min(th, sh - smy)

                                    t_fb = sensor.alloc_extra_fb(tw, th, sensor.GRAYSCALE)
                                    if not t_fb: raise Exception("OOM Scaling")
                                    t_fb.draw_image(ref_fb, -smx, -smy, x_scale=scale, y_scale=scale)

                                    data = t_fb.bytearray()
                                    f.write(ustruct.pack("<HHI", tw, th, len(data)))
                                    f.write(data)
                                    sensor.dealloc_extra_fb()
                    except Exception as e:
                        # 💡 [안전장치] 만약 오류 발생 시 반쪽짜리 파일(깨진 파일)을 삭제하여 추후 TEST 모드 크래시 방지
                        try: os.remove(SAVE_DIR + '/' + name)
                        except: pass
                        raise e

                with open(SAVE_DIR + '/' + name + '.meta', 'wb') as f:
                    f.write(json_str)
                if ref_fb is not None: sensor.dealloc_extra_fb(); ref_fb = None
                os.sync();
                usb.send(b'OK', timeout=SEND_TIMEOUT)
            except Exception as e: write_log("UPLOAD_ERR: " + str(e)); usb.send(b'ER', timeout=SEND_TIMEOUT)

        elif cmd == b'j':
            mode = 'STANDBY'
            try:
                name_len = ustruct.unpack('<I', usb.recv(4, timeout=READ_TIMEOUT))[0]
                name = usb.recv(name_len, timeout=READ_TIMEOUT).decode('utf-8')
                with open(SAVE_DIR + '/' + name + '.meta', 'rb') as f: json_data = f.read()
                usb.send(ustruct.pack('<I', len(json_data)), timeout=SEND_TIMEOUT)
                usb.send(json_data, timeout=SEND_TIMEOUT)
            except Exception as e: usb.send(ustruct.pack('<I', 0), timeout=SEND_TIMEOUT)

        elif cmd == b'M':
            mode = 'STANDBY'
            try:
                name_len = ustruct.unpack('<I', usb.recv(4, timeout=READ_TIMEOUT))[0]
                name = usb.recv(name_len, timeout=READ_TIMEOUT).decode('utf-8')
                json_len = ustruct.unpack('<I', usb.recv(4, timeout=READ_TIMEOUT))[0]
                json_str = b''
                t_start = time.ticks_ms()
                while len(json_str) < json_len and time.ticks_diff(time.ticks_ms(), t_start) < 2000:
                    chunk = usb.recv(json_len - len(json_str), timeout=READ_TIMEOUT)
                    if chunk: json_str += chunk
                with open(SAVE_DIR + '/' + name + '.meta', 'wb') as f:
                    f.write(json_str)
                os.sync()
                usb.send(b'OK', timeout=SEND_TIMEOUT)
            except:
                usb.send(b'ER', timeout=SEND_TIMEOUT)

        elif cmd == b'W':
            mode = 'STANDBY'
            try:
                name_len = ustruct.unpack('<I', usb.recv(4, timeout=READ_TIMEOUT))[0]
                name = usb.recv(name_len, timeout=READ_TIMEOUT).decode('utf-8')
                json_len = ustruct.unpack('<I', usb.recv(4, timeout=READ_TIMEOUT))[0]
                json_str = b''
                t_start = time.ticks_ms()
                while len(json_str) < json_len and time.ticks_diff(time.ticks_ms(), t_start) < 2000:
                    chunk = usb.recv(json_len - len(json_str), timeout=READ_TIMEOUT)
                    if chunk: json_str += chunk
                with open(SAVE_DIR + '/' + name + '.rst', 'wb') as f: f.write(json_str)
                os.sync();
                usb.send(b'OK', timeout=SEND_TIMEOUT)
            except: usb.send(b'ER', timeout=SEND_TIMEOUT)

        elif cmd == b'R':
            mode = 'STANDBY'
            try:
                name_len = ustruct.unpack('<I', usb.recv(4, timeout=READ_TIMEOUT))[0]
                name = usb.recv(name_len, timeout=READ_TIMEOUT).decode('utf-8')
                try:
                    with open(SAVE_DIR + '/' + name + '.rst', 'rb') as f: json_data = f.read()
                    usb.send(ustruct.pack('<I', len(json_data)), timeout=SEND_TIMEOUT)
                    usb.send(json_data, timeout=SEND_TIMEOUT)
                except: usb.send(ustruct.pack('<I', 0), timeout=SEND_TIMEOUT)
            except: usb.send(ustruct.pack('<I', 0), timeout=SEND_TIMEOUT)

        elif cmd == b't':
            err_code = b'ER'
            try:
                if ref_fb is not None: sensor.dealloc_extra_fb(); ref_fb = None
                if smart_lock: smart_lock.cleanup(); smart_lock = None
                gc.collect()
                name_len = ustruct.unpack('<I', usb.recv(4, timeout=READ_TIMEOUT))[0]
                target_name = usb.recv(name_len, timeout=READ_TIMEOUT).decode('utf-8')

                try:
                    with open(SAVE_DIR + '/' + target_name + '.rst', 'rb') as f:
                        rst_json = json.loads(f.read())
                        std_w = rst_json.get('std_weak', 4)
                        std_f = rst_json.get('std_fail', 12)

                        sig_w = rst_json.get('sig_weak', 4)
                        sig_f = rst_json.get('sig_fail', 7)

                        tool_spMat['check_std'] = (std_w, std_f)
                        tool_spMat['check_sig'] = (sig_w, sig_f)
                except Exception as e:
                    tool_spMat['check_std'] = (4, 12)
                    tool_spMat['check_sig'] = (4, 7)

                with open(SAVE_DIR + '/' + target_name, 'rb') as f:
                    header = f.read(3)
                    if header == b'MS\x04':
                        loaded_templates = {}
                        for i in range(4):
                            bh = f.read(8)
                            tw, th, tsize = ustruct.unpack("<HHI", bh)
                            raw_pixels = f.read(tsize)
                            loaded_templates[i] = image.Image(tw, th, sensor.GRAYSCALE, buffer=raw_pixels)

                        tool_spMat['roi_w'] = loaded_templates[3].width()
                        tool_spMat['roi_h'] = loaded_templates[3].height()
                        smart_lock = SmartLockSystem(tool_spMat)
                        smart_lock.load_templates(loaded_templates[0], loaded_templates[1], loaded_templates[2], loaded_templates[3])
                    else:
                        f.seek(0)
                        header = f.read(16)
                        magic, w, h, ax, ay, aw, ah, pad = ustruct.unpack("<2sHHHHHHH", header)
                        if magic != b'MD': raise Exception("Invalid Magic")
                        raw_pixels = f.read()
                        template_img = image.Image(w, h, sensor.GRAYSCALE, buffer=raw_pixels)
                        tool_spMat['roi_w'] = w; tool_spMat['roi_h'] = h

                        smart_lock = SmartLockSystem(tool_spMat)
                        smart_lock.learn(template_img)

                mode = 'TEST'; usb.send(b'OK', timeout=SEND_TIMEOUT)
            except Exception as e:
                if smart_lock: smart_lock.cleanup(); smart_lock = None
                gc.collect(); mode = 'STANDBY'; usb.send(err_code, timeout=SEND_TIMEOUT)

    if mode == 'LIVE':
        try:
            img = sensor.snapshot()
            if img_format == 0: img_data = img.compress(quality=jpeg_quality)
            else: img_data = img
            usb.send(b'\x55\xAA' + ustruct.pack('<I', img_data.size()), timeout=SEND_TIMEOUT); usb.send(img_data.bytearray(), timeout=SEND_TIMEOUT)
        except: mode = 'STANDBY'

    elif mode == 'TEST' and smart_lock:
        try:
            t_start = time.ticks_ms()
            img = sensor.snapshot()
            found, rect, ang, status, d_mean, d_std, d_iqr, d_sig = smart_lock.scan(img)
            procTime = time.ticks_diff(time.ticks_ms(), t_start)

            score = 100 if status == 3 else (70 if status == 2 else 0)

            x, y, w, h = rect if found else (0,0,0,0)
            isFound = 1 if found else 0
            gc.collect()
            if img_format == 0: img_to_send = img.compress(quality=jpeg_quality)
            else: img_to_send = img
            img_size = img_to_send.size()
            packet = ustruct.pack(PACKET_FORMAT, 0xAA55, 1, 1, img_size, isFound, status, score, x, y, w, h, float(ang), d_std, d_sig, procTime)
            usb.send(packet, timeout=SEND_TIMEOUT); usb.send(img_to_send.bytearray(), timeout=SEND_TIMEOUT)
        except Exception as e: mode = 'STANDBY'