"""
Tests for auto white-balance (calibration.auto_white_balance_from_master),
the location-free white-patch used to sharpen colour ΔE.
"""

import numpy as np

from inspectors import calibration


def _master_with_white(h=100, w=100, white_rows=20):
    m = np.full((h, w, 3), 128, np.uint8)
    m[:white_rows, :] = 255
    return m


class TestAutoWhiteBalance:
    def test_neutralizes_color_cast(self):
        master = _master_with_white()
        cap = np.clip(master.astype(np.float32) * np.array([0.8, 0.9, 1.2]),
                      0, 255).astype(np.uint8)
        bal, info = calibration.auto_white_balance_from_master(cap, master)
        assert info and info["applied"]
        strip = bal[:20, :].reshape(-1, 3).mean(axis=0)
        assert abs(strip[0] - strip[2]) < 15        # R and B re-balanced

    def test_noop_without_enough_white(self):
        m = np.full((50, 50, 3), 100, np.uint8)     # no near-white pixels
        out, info = calibration.auto_white_balance_from_master(m.copy(), m)
        assert info is None
        assert np.array_equal(out, m)

    def test_shape_mismatch_safe(self):
        a = np.zeros((10, 10, 3), np.uint8)
        b = np.zeros((20, 20, 3), np.uint8)
        out, info = calibration.auto_white_balance_from_master(a, b)
        assert info is None
        assert out is a

    def test_gain_is_clamped(self):
        master = _master_with_white()
        # Capture nearly black in the white region → huge raw gain, must clamp
        cap = master.copy()
        cap[:20, :] = 5
        bal, info = calibration.auto_white_balance_from_master(
            cap, master, max_gain=1.8)
        assert info["applied"]
        assert all(g <= 1.8 + 1e-6 for g in info["gain"])

    def test_already_white_minimal_change(self):
        master = _master_with_white()
        bal, info = calibration.auto_white_balance_from_master(master.copy(), master)
        # White region already ~255 → gain near 1, image barely changes
        assert info["applied"]
        assert np.allclose(info["gain"], [1.0, 1.0, 1.0], atol=0.05)
