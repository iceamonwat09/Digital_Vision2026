"""
Tests for the ΔE false-positive suppression added in the label_paper
accuracy pass: edge masking, specular-glare masking, ignore-mask-aware
statistics/clustering, and spec-driven pixel verdict thresholds.
"""

import numpy as np
import pytest

from inspectors import deltae_map
from inspectors.label_pipeline import _pixel_verdict
from inspectors.master_loader import PixelInspectionConfig


def _text_like_master(h=200, w=400):
    """White canvas with black bars → lots of high-contrast edges."""
    img = np.full((h, w, 3), 255, np.uint8)
    for x in range(20, w - 20, 40):
        img[30:h - 30, x:x + 8] = 0
    return img


class TestEdgeMask:
    def test_detects_edges_on_text(self):
        m = _text_like_master()
        em = deltae_map.edge_mask(m, dilate_px=2)
        assert em.dtype == bool
        assert em.shape == m.shape[:2]
        assert em.sum() > 0

    def test_blank_image_has_no_edges(self):
        blank = np.full((100, 100, 3), 128, np.uint8)
        assert deltae_map.edge_mask(blank, dilate_px=2).sum() == 0

    def test_dilate_grows_mask(self):
        m = _text_like_master()
        small = deltae_map.edge_mask(m, dilate_px=1).sum()
        big = deltae_map.edge_mask(m, dilate_px=5).sum()
        assert big > small


class TestSpecularMask:
    def test_flags_blown_highlights(self):
        img = np.full((50, 50, 3), 100, np.uint8)   # mid grey, not glare
        img[10:20, 10:20] = 255                       # white blown patch
        gm = deltae_map.specular_mask(img)
        assert gm[15, 15]            # white patch flagged
        assert not gm[40, 40]        # mid grey not flagged

    def test_saturated_color_not_flagged(self):
        # Pure bright red is high V but also high S → not specular
        img = np.zeros((20, 20, 3), np.uint8)
        img[..., 0] = 255            # RGB red
        gm = deltae_map.specular_mask(img)
        assert gm.sum() == 0


class TestBuildIgnoreMask:
    def test_combines_and_reports_pct(self):
        m = _text_like_master()
        aligned = m.copy()
        aligned[0:10, 0:10] = 255    # add a glare corner
        ignore, stats = deltae_map.build_ignore_mask(m, aligned)
        assert ignore.dtype == bool
        assert 0 <= stats["ignored_pct"] <= 100
        assert stats["edge_pct"] > 0
        assert stats["ignored_pct"] >= stats["edge_pct"]  # union ≥ any part

    def test_toggles_off(self):
        m = _text_like_master()
        ignore, stats = deltae_map.build_ignore_mask(
            m, m, ignore_edges=False, ignore_glare=False)
        assert ignore.sum() == 0
        assert stats["ignored_pct"] == 0.0


class TestMapStatsIgnoreMask:
    def test_ignored_pixels_excluded(self):
        de = np.zeros((10, 10), np.float32)
        de[0, :] = 100.0                       # one bad row
        ignore = np.zeros((10, 10), bool)
        ignore[0, :] = True                    # ignore exactly that row

        full = deltae_map.map_stats(de, 6.0)
        masked = deltae_map.map_stats(de, 6.0, ignore_mask=ignore)
        assert full["fail_pixels"] == 10
        assert masked["fail_pixels"] == 0
        assert masked["pass_rate"] == 100.0
        assert masked["peak"] == 0.0

    def test_all_masked_falls_back(self):
        de = np.full((5, 5), 3.0, np.float32)
        ignore = np.ones((5, 5), bool)
        s = deltae_map.map_stats(de, 6.0, ignore_mask=ignore)
        assert s["total_pixels"] == 25          # fell back to full map


class TestClusterDefectsIgnoreMask:
    def test_defect_in_ignored_region_dropped(self):
        de = np.zeros((100, 100), np.float32)
        de[40:60, 40:60] = 50.0                 # one big high-ΔE blob
        master = np.full((100, 100, 3), 200, np.uint8)
        captured = master.copy()

        without = deltae_map.cluster_defects(de, master, captured,
                                             tolerance=6.0, min_area_px=50)[0]
        assert len(without) == 1

        ignore = np.zeros((100, 100), bool)
        ignore[40:60, 40:60] = True
        with_mask = deltae_map.cluster_defects(de, master, captured,
                                               tolerance=6.0, min_area_px=50,
                                               ignore_mask=ignore)[0]
        assert len(with_mask) == 0


class TestPixelVerdictConfig:
    def _stats(self, pass_rate, peak, tol=6.0):
        return {"pass_rate": pass_rate, "peak": peak, "tolerance": tol}

    def test_defaults_fail_on_low_pass_rate(self):
        assert _pixel_verdict(self._stats(80.0, 5.0), []) == "FAIL"

    def test_defaults_pass_clean(self):
        assert _pixel_verdict(self._stats(99.5, 5.0), []) == "PASS"

    def test_spec_loosened_thresholds(self):
        cfg = PixelInspectionConfig(fail_pass_rate=50.0, warn_pass_rate=60.0,
                                    peak_warn_mult=10.0, peak_fail_mult=20.0)
        # 80% would FAIL under defaults but PASS with the loosened spec
        assert _pixel_verdict(self._stats(80.0, 5.0), [], cfg=cfg) == "PASS"

    def test_spec_tightened_area(self):
        cfg = PixelInspectionConfig(critical_area_px=10, big_area_px=20)
        defects = [{"severity": "critical", "area_px": 15}]
        assert _pixel_verdict(self._stats(99.9, 1.0), defects, cfg=cfg) == "FAIL"
