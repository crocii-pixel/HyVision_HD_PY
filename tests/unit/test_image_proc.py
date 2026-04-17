"""
U-16  HyImageProc.apply_blur — 출력 shape 동일, 값 변화 발생
U-17  HyImageProc.apply_gamma_contrast — LUT 단조성 / 통과(gamma=1, contrast=1)
U-18  HyImageProc.apply_morphology — kernel_idx=0 → 원본 반환, 그 외 변환 적용
U-19  HyImageProc.apply_preprocessing — 전체 파이프라인 (ROI 크롭 포함)
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pytest
import numpy as np
from HyImageProc import HyImageProc


# ─────────────────────────────────────────────────────────────────────────────
# 픽스처
# ─────────────────────────────────────────────────────────────────────────────

def _gray(h=64, w=64) -> np.ndarray:
    """그라데이션 회색조 이미지."""
    img = np.tile(np.linspace(0, 255, w, dtype=np.uint8), (h, 1))
    return img


def _bgr(h=64, w=64) -> np.ndarray:
    """단색 BGR 이미지."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :, 1] = 128   # G 채널만
    return img


# ─────────────────────────────────────────────────────────────────────────────
# U-16  apply_blur
# ─────────────────────────────────────────────────────────────────────────────

class TestApplyBlur:
    def test_zero_passes_returns_same_shape(self):
        img = _gray()
        out = HyImageProc.apply_blur(img, passes=0)
        assert out.shape == img.shape

    def test_nonzero_passes_changes_values(self):
        """선명한 그라데이션에 블러 적용 시 변화 발생."""
        img = _gray()
        out = HyImageProc.apply_blur(img, passes=2)
        assert out.shape == img.shape
        # 블러 후에는 최솟값과 최댓값 범위가 좁아짐
        assert int(out.min()) > int(img.min()) or int(out.max()) < int(img.max())

    def test_output_dtype_uint8(self):
        out = HyImageProc.apply_blur(_gray(), passes=1)
        assert out.dtype == np.uint8

    def test_blur_bgr_preserves_channels(self):
        img = _bgr()
        out = HyImageProc.apply_blur(img, passes=1)
        assert out.shape == img.shape


# ─────────────────────────────────────────────────────────────────────────────
# U-17  apply_gamma_contrast
# ─────────────────────────────────────────────────────────────────────────────

class TestApplyGammaContrast:
    def test_identity_returns_same(self):
        img = _gray()
        out = HyImageProc.apply_gamma_contrast(img, gamma=1.0, contrast=1.0)
        np.testing.assert_array_equal(out, img)

    def test_lut_monotone_gamma_darken(self):
        """gamma > 1 → 중간값 어두워짐 (LUT 단조 감소 경향)."""
        img = np.arange(256, dtype=np.uint8).reshape(1, 256)
        out = HyImageProc.apply_gamma_contrast(img, gamma=2.0, contrast=1.0)
        # 값 128 부근이 원본보다 어두워야 함
        assert int(out[0, 128]) < 128

    def test_contrast_boost_expands_range(self):
        """contrast > 1 → 중간 영역 값이 125보다 작거나 130보다 크게 분산."""
        img = np.full((1, 256), 128, dtype=np.uint8)
        img[0, :128] = 100
        img[0, 128:] = 156
        out = HyImageProc.apply_gamma_contrast(img, gamma=1.0, contrast=2.0)
        # 100 → 대비 강화 후 더 어두워져야 함
        assert int(out[0, 0]) < 100

    def test_output_dtype_uint8(self):
        out = HyImageProc.apply_gamma_contrast(_gray(), gamma=1.5, contrast=1.2)
        assert out.dtype == np.uint8


# ─────────────────────────────────────────────────────────────────────────────
# U-18  apply_morphology
# ─────────────────────────────────────────────────────────────────────────────

class TestApplyMorphology:
    def test_kernel0_identity_returns_same(self):
        img = _gray()
        out = HyImageProc.apply_morphology(img, kernel_idx=0)
        np.testing.assert_array_equal(out, img)

    def test_kernel_out_of_range_returns_same(self):
        img = _gray()
        out = HyImageProc.apply_morphology(img, kernel_idx=999)
        np.testing.assert_array_equal(out, img)

    def test_edge_kernel_detects_gradient(self):
        """kernel_idx=1 (edge) 적용 시 평탄한 영역은 0, 엣지는 양수."""
        # 왼쪽 절반 0, 오른쪽 절반 255 — 중앙에 날카로운 엣지
        img = np.zeros((64, 64), dtype=np.uint8)
        img[:, 32:] = 255
        out = HyImageProc.apply_morphology(img, kernel_idx=1)
        assert out.shape == img.shape
        # 엣지 열(31~33) 에서 값이 0보다 큰 픽셀이 있어야 함
        assert out[32, 31:34].max() > 0

    def test_output_dtype_uint8(self):
        out = HyImageProc.apply_morphology(_gray(), kernel_idx=3)
        assert out.dtype == np.uint8


# ─────────────────────────────────────────────────────────────────────────────
# U-19  apply_preprocessing
# ─────────────────────────────────────────────────────────────────────────────

class TestApplyPreprocessing:
    def test_no_roi_returns_same_size(self):
        img = _bgr()
        out = HyImageProc.apply_preprocessing(img, cfg={'grayscale': True})
        assert out.shape[:2] == img.shape[:2]

    def test_grayscale_conversion(self):
        """BGR 입력 + grayscale=True → 출력은 2D."""
        img = _bgr()
        out = HyImageProc.apply_preprocessing(img, cfg={'grayscale': True})
        assert out.ndim == 2

    def test_roi_crop_output_size(self):
        """ROI (10,10,20,20) 지정 시 출력 크기 = (20, 20)."""
        img = _gray(64, 64)
        out = HyImageProc.apply_preprocessing(
            img, cfg={'grayscale': True, 'blur': 0}, roi=(10, 10, 20, 20))
        assert out.shape == (20, 20), f"기대 (20,20), got {out.shape}"

    def test_invert_flag(self):
        """invert=True → 255 - img. 회색조 유지로 단일 채널 결과."""
        img = np.full((10, 10), 100, dtype=np.uint8)
        out = HyImageProc.apply_preprocessing(
            img, cfg={'grayscale': True, 'blur': 0, 'morph': False, 'invert': True})
        assert int(out[5, 5]) == 155

    def test_pipeline_does_not_crash_on_edge_cases(self):
        """1×1 이미지에서 크래시 없음."""
        img = np.array([[128]], dtype=np.uint8)
        out = HyImageProc.apply_preprocessing(img, cfg={})
        assert out is not None
