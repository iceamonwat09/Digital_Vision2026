"""
Tests for the burst-capture test tool (/api/snapshot/burst).

Why it exists: pressing the shutter N times by hand measures the OPERATOR's
reaction time, not whether the camera can catch a can moving past. Burst
captures N consecutive frames server-side and scores each one, so "did we catch
it and was it sharp enough" gets a number instead of an impression.

These import app (ultralytics/pyodbc), so they skip on a bare checkout and run
on the station. Camera and model are both faked — no hardware needed.
"""

import threading
import time

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")


@pytest.fixture
def appmod():
    pytest.importorskip("ultralytics")
    pytest.importorskip("pyodbc")
    import app
    return app


class FakeCam:
    """Alternates sharp and blurred frames, like an object moving through focus."""

    def __init__(self, period=3, delay=0.01, stall_after=None):
        self.i = 0
        self.delay = delay
        self.period = period
        self.stall_after = stall_after
        base = np.zeros((120, 160, 3), np.uint8)
        base[:, ::8] = 255                       # fine stripes = high detail
        self.sharp = base
        self.blur = cv2.GaussianBlur(base, (21, 21), 0)

    def read_frame(self):
        if self.stall_after is not None and self.i >= self.stall_after:
            time.sleep(0.05)
            return (False, None)                 # camera stopped delivering
        self.i += 1
        time.sleep(self.delay)
        sharp = (self.i % self.period == 0)
        return (True, (self.sharp if sharp else self.blur).copy())

    def release(self):
        pass


class FakeDetector:
    """Flags frame #ng_on as NG; everything else OK."""
    model = object()

    def __init__(self, ng_on=None):
        self.n = 0
        self.ng_on = ng_on

    def detect(self, frame, imgsz=None):
        self.n += 1
        if self.n == self.ng_on:
            return [{"class_name": "dent", "confidence": 0.8,
                     "bbox": [5, 5, 40, 40], "center": [22, 22]}]
        return []

    def draw_detections(self, frame, dets):
        return frame


@pytest.fixture
def running_viewfinder(appmod):
    """Start the real viewfinder loop against a fake camera, then tear it down."""
    def _start(cam, detector):
        appmod.detector = detector
        appmod.viewfinder_camera = cam
        appmod.viewfinder_active = True
        t = threading.Thread(target=appmod.viewfinder_loop, daemon=True)
        t.start()
        deadline = time.time() + 2
        while time.time() < deadline:            # wait for the first publish
            with appmod.vf_lock:
                if appmod.viewfinder_frame is not None:
                    break
            time.sleep(0.01)
        return t

    threads = []
    yield lambda cam, det: threads.append(_start(cam, det))
    appmod.viewfinder_active = False
    for t in threads:
        t.join(timeout=2)
    appmod.viewfinder_camera = None
    with appmod.vf_lock:
        appmod.viewfinder_frame = None
        appmod.viewfinder_jpeg = None
        appmod.viewfinder_frame_ts = 0.0


# ── _collect_burst: capture only, no inference in the loop ─────────────────

def test_collect_burst_returns_distinct_consecutive_frames(appmod, running_viewfinder):
    running_viewfinder(FakeCam(), FakeDetector())
    frames, stamps = appmod._collect_burst(8, 5.0)
    assert len(frames) == 8
    # Distinct publish timestamps prove we followed viewfinder_seq instead of
    # handing back the same buffered frame N times.
    assert len(set(stamps)) == 8
    assert all(f.shape == frames[0].shape for f in frames)
    assert stamps == sorted(stamps)


def test_collect_burst_gives_up_at_timeout_with_partial_result(appmod, running_viewfinder):
    """A camera that stalls mid-burst must return what it got, not hang."""
    running_viewfinder(FakeCam(stall_after=3), FakeDetector())
    started = time.time()
    frames, _ = appmod._collect_burst(10, 0.7)
    elapsed = time.time() - started
    assert len(frames) < 10
    assert elapsed < 2.5, "burst must honour its timeout"


def test_collect_burst_rejects_stale_frames(appmod, running_viewfinder):
    """A frozen feed leaves the last good frame in the buffer. A QC tool must
    never grade a stale image — same rule the single shutter already follows."""
    running_viewfinder(FakeCam(), FakeDetector())
    appmod.viewfinder_active = False
    time.sleep(0.05)
    with appmod.vf_lock:                          # age the published frame out
        appmod.viewfinder_frame_ts = time.monotonic() - 60.0
    frames, _ = appmod._collect_burst(5, 0.4)
    assert frames == []


# ── the endpoint ───────────────────────────────────────────────────────────

def test_burst_endpoint_scores_every_frame(appmod, running_viewfinder):
    import config
    running_viewfinder(FakeCam(period=3), FakeDetector(ng_on=2))
    client = appmod.app.test_client()

    r = client.post("/api/snapshot/burst", json={"imgsz": 480})
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "ok"
    assert data["captured"] == config.BURST_COUNT
    assert len(data["shots"]) == data["captured"]

    for i, s in enumerate(data["shots"]):
        assert s["index"] == i
        assert s["verdict"] in ("ok", "ng")
        assert s["sharpness"] >= 0
        assert s["image"].startswith("data:image/jpeg;base64,")
    assert data["shots"][0]["t_ms"] == 0.0
    assert data["ng_count"] == 1

    # The sharp frames must actually outscore the blurred ones — this is the
    # whole point of the tool, so assert the separation is not marginal.
    sharps = sorted(s["sharpness"] for s in data["shots"])
    assert sharps[-1] > sharps[0] * 5
    best = max(data["shots"], key=lambda s: s["sharpness"])
    assert data["sharpest_index"] == best["index"]


def test_burst_count_is_clamped(appmod, running_viewfinder):
    import config
    running_viewfinder(FakeCam(), FakeDetector())
    client = appmod.app.test_client()
    r = client.post("/api/snapshot/burst", json={"count": 9999})
    assert r.get_json()["requested"] == config.BURST_MAX_COUNT


def test_burst_does_not_touch_counters(appmod, running_viewfinder):
    """Diagnostic tool: it must never inflate the production defect counts."""
    running_viewfinder(FakeCam(), FakeDetector(ng_on=1))
    before = dict(appmod.detection_stats)
    appmod.app.test_client().post("/api/snapshot/burst", json={})
    assert dict(appmod.detection_stats) == before


def test_burst_requires_an_open_viewfinder(appmod):
    appmod.viewfinder_active = False
    appmod.detector = FakeDetector()
    r = appmod.app.test_client().post("/api/snapshot/burst", json={})
    assert r.status_code == 409


def test_burst_requires_a_model(appmod):
    appmod.viewfinder_active = True
    appmod.detector = None
    try:
        r = appmod.app.test_client().post("/api/snapshot/burst", json={})
        assert r.status_code == 400
    finally:
        appmod.viewfinder_active = False
