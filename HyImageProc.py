"""
HyImageProc.py - 이미지 전처리 엔진 (v2.0)
블러·감마·모폴로지 유틸리티. numpy 배열(grayscale / BGR) 입출력.
Qt 비의존 — PC 전용 ([PC]).

PCVisionEngine.py 로부터 리팩터링. 알고리즘은 동일하되 QImage 의존 제거.
"""
import math
import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


class HyImageProc:
    """
    이미지 전처리 파이프라인 유틸리티.
    모든 메서드는 numpy.ndarray (uint8) 을 받아 numpy.ndarray 를 반환.
    """

    # ─────────────────────────────────────────────────────────────────────────
    # 블러 (Box blur 다중 패스)
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def apply_blur(img: np.ndarray, passes: int) -> np.ndarray:
        """Box blur 를 `passes` 회 반복 적용. passes=0 이면 원본 반환."""
        if passes <= 0:
            return img
        result = img.copy()
        if HAS_CV2:
            for _ in range(passes):
                result = cv2.blur(result, (3, 3))
        else:
            # cv2 없을 때: 간단한 3×3 균일 가중치 수동 합산
            for _ in range(passes):
                result = HyImageProc._box_blur_numpy(result)
        return result

    @staticmethod
    def _box_blur_numpy(img: np.ndarray) -> np.ndarray:
        """cv2 없이 구현한 3×3 Box blur (테스트 환경용)."""
        is_gray = img.ndim == 2
        if is_gray:
            img = img[:, :, np.newaxis]
        h, w, c = img.shape
        out = np.zeros_like(img)
        for ch in range(c):
            src = img[:, :, ch].astype(np.float32)
            out[1:-1, 1:-1, ch] = (
                src[:-2, :-2] + src[:-2, 1:-1] + src[:-2, 2:] +
                src[1:-1, :-2] + src[1:-1, 1:-1] + src[1:-1, 2:] +
                src[2:, :-2] + src[2:, 1:-1] + src[2:, 2:]
            ) / 9.0
        out = out.astype(np.uint8)
        return out[:, :, 0] if is_gray else out

    # ─────────────────────────────────────────────────────────────────────────
    # 감마 + 대비 (LUT 기반 통합 보정)
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def apply_gamma_contrast(img: np.ndarray,
                              gamma: float = 1.0,
                              contrast: float = 1.0) -> np.ndarray:
        """
        256-entry LUT 로 감마 보정 후 대비 보정을 1회 패스로 통합 적용.
        gamma < 1  → 밝게  /  gamma > 1 → 어둡게
        contrast > 1 → 대비 강화
        """
        if gamma == 1.0 and contrast == 1.0:
            return img
        lut = np.zeros(256, dtype=np.uint8)
        for i in range(256):
            val = (i / 255.0 ** gamma) * 255.0 if gamma != 1.0 else float(i)
            if contrast != 1.0:
                val = (val - 128.0) * contrast + 128.0
            lut[i] = max(0, min(255, int(val)))
        return lut[img]

    # ─────────────────────────────────────────────────────────────────────────
    # 모폴로지 (커스텀 3×3 컨볼루션 커널 세트)
    # ─────────────────────────────────────────────────────────────────────────

    KERNELS = [
        [0,  0, 0,  0,  1,  0,  0,  0, 0 ],   # 0: identity
        [-1,-1,-1, -1,  8, -1, -1, -1,-1 ],   # 1: edge detect
        [1,  1, 1,  1, -8,  1,  1,  1, 1 ],   # 2: inverse edge
        [-1,-1,-1, -1,  9, -1, -1, -1,-1 ],   # 3: sharpen
        [-1,-1,-1, -1, 10, -1, -1, -1,-1 ],   # 4: strong sharpen
        [0,  1, 0,  1, -4,  1,  0,  1, 0 ],   # 5: Laplacian
        [1,  1, 1,  1, -8,  1,  1,  1, 1 ],   # 6: LoG approx
        [-1,-1,-1,  0,  6,  0, -1, -1,-1 ],   # 7: h-edge
        [-1, 0,-1, -1,  6, -1, -1,  0,-1 ],   # 8: v-edge
        [0, -1,-1, -1,  6, -1, -1, -1, 0 ],   # 9: diag-edge-a
        [-1,-1, 0, -1,  6, -1,  0, -1,-1 ],   # 10: diag-edge-b
    ]

    @staticmethod
    def apply_morphology(img: np.ndarray, kernel_idx: int) -> np.ndarray:
        """
        KERNELS[kernel_idx] 의 3×3 컨볼루션 적용.
        kernel_idx=0 (identity) 이면 원본 반환.
        """
        if kernel_idx == 0:
            return img
        kernels = HyImageProc.KERNELS
        if not (0 <= kernel_idx < len(kernels)):
            return img
        k = kernels[kernel_idx]

        if HAS_CV2:
            kernel_np = np.array(k, dtype=np.float32).reshape(3, 3)
            return np.clip(cv2.filter2D(img.astype(np.float32),
                                        -1, kernel_np), 0, 255).astype(np.uint8)
        else:
            return HyImageProc._convolve3x3_numpy(img, k)

    @staticmethod
    def _convolve3x3_numpy(img: np.ndarray, kernel: list) -> np.ndarray:
        """cv2 없이 구현한 3×3 컨볼루션 (테스트 환경용)."""
        is_gray = img.ndim == 2
        if is_gray:
            img = img[:, :, np.newaxis]
        h, w, c = img.shape
        k = np.array(kernel, dtype=np.float32).reshape(3, 3)
        out = np.zeros_like(img, dtype=np.float32)
        for dy in range(3):
            for dx in range(3):
                out[1:-1, 1:-1] += img[dy:h-2+dy, dx:w-2+dx].astype(np.float32) * k[dy, dx]
        out = np.clip(out, 0, 255).astype(np.uint8)
        return out[:, :, 0] if is_gray else out

    # ─────────────────────────────────────────────────────────────────────────
    # 통합 전처리 파이프라인
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def apply_preprocessing(img: np.ndarray, cfg: dict,
                             roi: tuple | None = None,
                             padding: int = 2) -> np.ndarray:
        """
        전처리 파이프라인 (ROI 크롭 → 회색조 → 블러 → 감마/대비 → 모폴로지 → 반전).

        cfg 키:
          grayscale  bool  = True   회색조 변환
          blur       int   = 1      블러 패스 수
          gamma      float = 1.0    감마 보정
          contrast   float = 1.0    대비 보정
          morph      bool  = True   모폴로지 적용
          kernel     int   = 0      KERNELS 인덱스
          invert     bool  = False  픽셀 반전

        roi: (x, y, w, h) 이미지 좌표 또는 None (전체 이미지)
        padding: ROI 주변 추가 픽셀 수 (경계 안정화)
        """
        if roi is not None:
            x, y, w, h = [int(v) for v in roi]
            ih, iw = img.shape[:2]
            px1 = max(0, x - padding)
            py1 = max(0, y - padding)
            px2 = min(iw, x + w + padding)
            py2 = min(ih, y + h + padding)
            processed = img[py1:py2, px1:px2].copy()
        else:
            processed = img.copy()
            x = y = padding = 0

        # 회색조 변환
        if cfg.get('grayscale', True):
            if processed.ndim == 3:
                if HAS_CV2:
                    processed = cv2.cvtColor(processed, cv2.COLOR_BGR2GRAY)
                else:
                    processed = processed.mean(axis=2).astype(np.uint8)
        else:
            if processed.ndim == 2:
                if HAS_CV2:
                    processed = cv2.cvtColor(processed, cv2.COLOR_GRAY2BGR)

        # 블러
        blur_level = cfg.get('blur', 1)
        if blur_level > 0:
            processed = HyImageProc.apply_blur(processed, blur_level)

        # 감마 + 대비
        gamma_val    = cfg.get('gamma', 1.0)
        contrast_val = cfg.get('contrast', 1.0)
        if gamma_val != 1.0 or contrast_val != 1.0:
            processed = HyImageProc.apply_gamma_contrast(
                processed, gamma_val, contrast_val)

        # 모폴로지
        if cfg.get('morph', True):
            kernel_idx = cfg.get('kernel', 0)
            if kernel_idx > 0:
                processed = HyImageProc.apply_morphology(processed, kernel_idx)

        # 반전
        if cfg.get('invert', False):
            processed = 255 - processed

        # 패딩 제거 (ROI 모드)
        if roi is not None and padding > 0:
            crop_x = min(padding, processed.shape[1])
            crop_y = min(padding, processed.shape[0])
            processed = processed[crop_y:crop_y + h, crop_x:crop_x + w]

        return processed
