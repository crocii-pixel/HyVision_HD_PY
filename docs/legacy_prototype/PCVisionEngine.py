import math
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter, QImage, QColor

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
            
            # 💡 (0, 0)을 가장 먼저 불투명도 1.0으로 그려서 1픽셀 밀림(Shift) 방지
            offsets = [(0, 0), (-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
            step = 1
            for dx, dy in offsets:
                p.setOpacity(1.0 / step)
                p.drawImage(dx, dy, res)
                step += 1
            p.end()
            res = temp
        return res.convertToFormat(orig_fmt)

    # 💡 고속 감마 & 대비 통합 보정 (LUT를 활용해 1회의 연산으로 두 필터 동시 적용)
    @staticmethod
    def apply_gamma_contrast(qimg, gamma, contrast):
        if gamma == 1.0 and contrast == 1.0: return qimg
        
        # 0~255 값을 미리 계산한 256바이트 변환 표(Look-Up Table) 생성
        lut_array = bytearray(256)
        for i in range(256):
            # 1. 감마(Gamma) 먼저 적용
            val = (math.pow(i / 255.0, gamma) * 255.0) if gamma != 1.0 else float(i)
            # 2. 이어서 대비(Contrast) 적용
            if contrast != 1.0:
                val = (val - 128.0) * contrast + 128.0
            
            lut_array[i] = max(0, min(255, int(val)))
            
        lut = bytes(lut_array)
        
        fmt = qimg.format()
        if fmt not in (QImage.Format_Grayscale8, QImage.Format_RGB888):
            qimg = qimg.convertToFormat(QImage.Format_RGB888)
            fmt = QImage.Format_RGB888
            
        w, h = qimg.width(), qimg.height()
        bpl = qimg.bytesPerLine()
        
        res_img = QImage(w, h, fmt)
        
        src_ptr = qimg.constBits()
        src_ptr.setsize(bpl * h)
        res_ptr = res_img.bits()
        res_ptr.setsize(bpl * h)
        
        # 파이썬 내장 C-수준 바이트 치환 연산 (극도로 빠름)
        res_ptr[:] = bytes(src_ptr).translate(lut)
        
        return res_img

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

    # 💡 외곽 필터 안정화를 위해 패딩(padding) 개념 도입
    @staticmethod
    def apply_pre_processing(image, cfg, roi=None, padding=2):
        if roi is None:
            processed = image.copy()
        else:
            x, y, w, h = roi
            pad_x = x - padding
            pad_y = y - padding
            pad_w = w + (padding * 2)
            pad_h = h + (padding * 2)
            processed = image.copy(pad_x, pad_y, pad_w, pad_h)

        if cfg.get('grayscale', True): 
            processed = processed.convertToFormat(QImage.Format_Grayscale8)
        else: 
            processed = processed.convertToFormat(QImage.Format_RGB888)
            
        blur_level = cfg.get('blur', 1)
        if blur_level > 0:
            processed = PCVisionEngine.apply_blur(processed, blur_level)
            
        # 💡 블러링 후 감마 & 대비 보정을 통합 적용하여 선명한 엣지 획득
        gamma_val = cfg.get('gamma', 1.0)
        contrast_val = cfg.get('contrast', 1.0)
        if gamma_val != 1.0 or contrast_val != 1.0:
            processed = PCVisionEngine.apply_gamma_contrast(processed, gamma_val, contrast_val)
            
        if cfg.get('morph', True):
            kernel_idx = cfg.get('kernel', 0)
            processed = PCVisionEngine.apply_morphology(processed, kernel_idx)
            
        if cfg.get('invert', False):
            processed.invertPixels()

        if roi is not None:
            processed = processed.copy(padding, padding, w, h)

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
        
        # 💡 피크 위치 결정을 위해 밴드 탐색 전에 scan_dir과 peak_mode를 불러옴
        scan_dir = cfg.get('scan_dir', 0)
        peak_mode = cfg.get('peak_mode', 0)
        
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
                    if in_band:
                        e1, e2 = st, i - 1
                        # 💡 scan_dir에 따라 마주치는 Start/End 엣지가 역전됨을 반영
                        if peak_mode == 1: py = e2 if scan_dir == 1 else e1
                        elif peak_mode == 2: py = e1 if scan_dir == 1 else e2
                        else: py = (e1 + e2) // 2
                        peaks.append(py)
                        in_band = False
            if in_band:
                e1, e2 = st, len(profile) - 1
                if peak_mode == 1: py = e2 if scan_dir == 1 else e1
                elif peak_mode == 2: py = e1 if scan_dir == 1 else e2
                else: py = (e1 + e2) // 2
                peaks.append(py)
            return peaks

        mid_idx = NUM_SPLITS // 2
        seeds = get_peaks(profiles[mid_idx], thresholds[mid_idx])
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