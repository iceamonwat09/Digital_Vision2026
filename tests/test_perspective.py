"""
Tests for inspectors/perspective.py — 4-point perspective crop and
dual-resolution (OCR 4096 / pixel 2048) image preparation — plus the
label_pipeline plumbing that consumes the high-res OCR variant.
"""

import cv2
import numpy as np
import pytest

from inspectors import perspective as P
from inspectors.label_pipeline import _image_dims


# ── Helpers ──────────────────────────────────────────────────────────────────

# Quadrant colors (BGR) used to verify orientation survives the warp:
#   TL=red  TR=green  BL=blue  BR=yellow
_TL = (0, 0, 255)
_TR = (0, 255, 0)
_BL = (255, 0, 0)
_BR = (0, 255, 255)


def _quadrant_image(w=800, h=600, x0=100, y0=100, x1=500, y1=300):
    """White canvas with a 'label' at (x0,y0)-(x1,y1) split into 4 colored quadrants."""
    img = np.full((h, w, 3), 255, np.uint8)
    mx, my = (x0 + x1) // 2, (y0 + y1) // 2
    img[y0:my, x0:mx] = _TL
    img[y0:my, mx:x1] = _TR
    img[my:y1, x0:mx] = _BL
    img[my:y1, mx:x1] = _BR
    return img


def _assert_quadrants(warped, atol=40):
    """Mean color of each warped quadrant must match the expected corner color."""
    h, w = warped.shape[:2]
    mx, my = w // 2, h // 2
    # Sample the inner half of each quadrant to avoid interpolation at seams
    def inner(ys, ye, xs, xe):
        dy, dx = (ye - ys) // 4, (xe - xs) // 4
        return warped[ys + dy:ye - dy, xs + dx:xe - dx].reshape(-1, 3).mean(axis=0)

    for region, expected in [
        (inner(0, my, 0, mx), _TL),
        (inner(0, my, mx, w), _TR),
        (inner(my, h, 0, mx), _BL),
        (inner(my, h, mx, w), _BR),
    ]:
        assert np.allclose(region, expected, atol=atol), \
            f"quadrant mean {region} != expected {expected}"


def _jpeg(img):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    return bytes(buf)


# ── parse_corners ────────────────────────────────────────────────────────────

class TestParseCorners:
    def test_none_and_empty_return_none(self):
        assert P.parse_corners(None) is None
        assert P.parse_corners("") is None
        assert P.parse_corners("   ") is None

    def test_valid_corners(self):
        pts = P.parse_corners("[[0,0],[100,0],[100,50],[0,50]]")
        assert pts == [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError):
            P.parse_corners("not json")

    def test_wrong_count_raises(self):
        with pytest.raises(ValueError):
            P.parse_corners("[[0,0],[1,1],[2,2]]")

    def test_non_numeric_raises(self):
        with pytest.raises(ValueError):
            P.parse_corners('[[0,0],[1,1],[2,2],["a","b"]]')

    def test_non_pair_raises(self):
        with pytest.raises(ValueError):
            P.parse_corners("[[0,0],[1,1],[2,2],[3]]")


# ── quad_output_size ─────────────────────────────────────────────────────────

class TestQuadOutputSize:
    def test_axis_aligned_rect(self):
        corners = [(100, 100), (500, 100), (500, 300), (100, 300)]
        w, h = P.quad_output_size(corners)
        assert (w, h) == (400, 200)

    def test_caps_long_edge(self):
        corners = [(0, 0), (8000, 0), (8000, 4000), (0, 4000)]
        w, h = P.quad_output_size(corners, max_long_edge=4096)
        assert max(w, h) == 4096
        assert abs(w / h - 2.0) < 0.01  # aspect preserved

    def test_degenerate_quad_raises(self):
        corners = [(0, 0), (10, 0), (10, 10), (0, 10)]
        with pytest.raises(ValueError):
            P.quad_output_size(corners)


# ── warp_quad ────────────────────────────────────────────────────────────────

class TestWarpQuad:
    def test_axis_aligned_recovery(self):
        img = _quadrant_image()
        corners = [(100, 100), (500, 100), (500, 300), (100, 300)]
        warped = P.warp_quad(img, corners)
        assert warped.shape[:2] == (200, 400)
        _assert_quadrants(warped)

    def test_rotated_photo_corner_order_encodes_rotation(self):
        """Photo rotated 90° + corners reordered accordingly → upright output."""
        img = _quadrant_image()
        rotated = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)  # 600x800 → 800w? (h,w)=(800,600)
        h_orig = img.shape[0]

        def rot_pt(x, y):
            # original (x, y) → rotated-CW (h_orig - 1 - y, x)
            return (h_orig - 1.0 - y, x)

        # Same physical corners, still ordered TL,TR,BR,BL of the UPRIGHT label
        corners = [rot_pt(100, 100), rot_pt(500, 100),
                   rot_pt(500, 300), rot_pt(100, 300)]
        warped = P.warp_quad(rotated, corners)
        assert warped.shape[:2] == (200, 400)
        _assert_quadrants(warped)

    def test_out_of_bounds_corners_are_clamped(self):
        img = _quadrant_image()
        corners = [(-50, -50), (900, -50), (900, 700), (-50, 700)]
        warped = P.warp_quad(img, corners)
        assert warped.shape[0] > 0 and warped.shape[1] > 0


# ── downscale_long_edge ──────────────────────────────────────────────────────

class TestDownscale:
    def test_never_upscales(self):
        img = np.zeros((100, 200, 3), np.uint8)
        out = P.downscale_long_edge(img, 4096)
        assert out.shape == (100, 200, 3)

    def test_downscales_preserving_aspect(self):
        img = np.zeros((1500, 3000, 3), np.uint8)
        out = P.downscale_long_edge(img, 2048)
        assert max(out.shape[:2]) == 2048
        assert abs(out.shape[1] / out.shape[0] - 2.0) < 0.01


# ── prepare_inspection_images (dual resolution) ──────────────────────────────

class TestPrepareInspectionImages:
    def test_no_corners_legacy_path(self):
        img = np.random.randint(0, 255, (3000, 5000, 3), np.uint8)
        ocr_b, px_b = P.prepare_inspection_images(_jpeg(img))
        ocr = cv2.imdecode(np.frombuffer(ocr_b, np.uint8), cv2.IMREAD_COLOR)
        px = cv2.imdecode(np.frombuffer(px_b, np.uint8), cv2.IMREAD_COLOR)
        assert max(ocr.shape[:2]) == P.OCR_MAX_EDGE
        assert max(px.shape[:2]) == P.PIXEL_MAX_EDGE
        # Same aspect ratio between the two variants
        assert abs(ocr.shape[1] / ocr.shape[0] - px.shape[1] / px.shape[0]) < 0.01

    def test_with_corners_warps_then_dual_res(self):
        img = _quadrant_image(w=4000, h=3000, x0=500, y0=500, x1=3500, y1=2000)
        corners = [(500, 500), (3500, 500), (3500, 2000), (500, 2000)]
        ocr_b, px_b = P.prepare_inspection_images(_jpeg(img), corners)
        ocr = cv2.imdecode(np.frombuffer(ocr_b, np.uint8), cv2.IMREAD_COLOR)
        px = cv2.imdecode(np.frombuffer(px_b, np.uint8), cv2.IMREAD_COLOR)
        assert ocr.shape[:2] == (1500, 3000)        # quad size (≤ 4096, no scale)
        assert max(px.shape[:2]) == P.PIXEL_MAX_EDGE
        _assert_quadrants(ocr)
        _assert_quadrants(px)

    def test_small_image_not_upscaled(self):
        img = np.zeros((400, 600, 3), np.uint8)
        ocr_b, px_b = P.prepare_inspection_images(_jpeg(img))
        ocr = cv2.imdecode(np.frombuffer(ocr_b, np.uint8), cv2.IMREAD_COLOR)
        px = cv2.imdecode(np.frombuffer(px_b, np.uint8), cv2.IMREAD_COLOR)
        assert ocr.shape[:2] == (400, 600)
        assert px.shape[:2] == (400, 600)

    def test_undecodable_bytes_raise(self):
        with pytest.raises(ValueError):
            P.prepare_inspection_images(b"this is not an image")

    def test_degenerate_quad_raises(self):
        img = np.zeros((600, 800, 3), np.uint8)
        corners = [(0, 0), (5, 0), (5, 5), (0, 5)]
        with pytest.raises(ValueError):
            P.prepare_inspection_images(_jpeg(img), corners)


# ── label_pipeline._image_dims (block bbox normalisation source) ────────────

class TestImageDims:
    def test_jpeg_dims(self):
        img = np.zeros((300, 700, 3), np.uint8)
        assert _image_dims(_jpeg(img)) == (700, 300)

    def test_png_dims(self):
        img = np.zeros((123, 456, 3), np.uint8)
        ok, buf = cv2.imencode(".png", img)
        assert ok
        assert _image_dims(bytes(buf)) == (456, 123)

    def test_garbage_returns_fallback(self):
        assert _image_dims(b"garbage") == (1, 1)
