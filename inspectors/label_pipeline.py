"""
End-to-end inspection orchestrator:

    cropped_image_bytes + master  →  InspectionReport

Stages:
    1. OCR  (Vertex Document AI — stubbed in Phase 1)
    2. Color compare  (named-brand colors, ΔE2000 of mean RGB)
    3. Field-aware text compare  (exact / Levenshtein / regex)
    4. Pixel inspection           (ΔE2000 map + defect clustering)
    5. Gemini context check       (only for ambiguous fields, stubbed)
    6. Reduce to PASS / WARN / FAIL
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import List, Optional

import cv2
import numpy as np

from . import calibration, deltae_map, master_renderer, overlay, registration, vertex_client
from .color_compare import compare_colors
from .master_loader import Master
from .text_compare import compare_all, overall_text_verdict


@dataclass
class PixelInspectionReport:
    enabled: bool
    tolerance: float
    de_mean: float = 0.0
    de_peak: float = 0.0
    de_p95: float = 0.0
    de_p99: float = 0.0
    pass_rate: float = 100.0
    fail_pixels: int = 0
    total_pixels: int = 0
    defects: List[dict] = field(default_factory=list)
    heatmap_png_b64: str = ""
    align_info: dict = field(default_factory=dict)
    verdict: str = "PASS"           # "PASS" | "WARN" | "FAIL" | "SKIPPED"
    note: str = ""


@dataclass
class InspectionReport:
    sku_code: str
    verdict: str                       # "PASS" | "WARN" | "FAIL"
    field_results: List[dict] = field(default_factory=list)
    color_results: List[dict] = field(default_factory=list)
    ocr_text: str = ""
    gemini: dict = field(default_factory=dict)
    stub_mode: bool = False
    pixel_inspection: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Helpers ─────────────────────────────────────────────────────────────

def _decode_image_to_rgb(image_bytes: bytes) -> Optional[np.ndarray]:
    if not image_bytes:
        return None
    arr = np.frombuffer(image_bytes, np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        return None
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def _pixel_verdict(stats: dict, defects: List[dict]) -> str:
    """
    Reduce per-pixel stats to PASS/WARN/FAIL.

    The thresholds tolerate JPEG compression noise and sub-pixel
    alignment artifacts at high-contrast edges (white text on solid
    color is the typical worst case). A real print defect shows up as
    a LARGE region with a HIGH peak — those bump us to FAIL. Lots of
    tiny edge-noise defects yield WARN, not FAIL.
    """
    pass_rate = stats.get("pass_rate", 100.0)
    peak = stats.get("peak", 0.0)
    tol = stats.get("tolerance", 6.0)

    # A "severe" defect is large enough to be visible and far past tolerance.
    severe = [
        d for d in defects
        if d.get("severity") == "critical" and d.get("area_px", 0) >= 500
    ]
    big = [d for d in defects if d.get("area_px", 0) >= 2000]

    if severe or big or pass_rate < 88.0 or peak > tol * 6.0:
        return "FAIL"
    if defects or pass_rate < 97.0 or peak > tol * 2.0:
        return "WARN"
    return "PASS"


# ── Pixel inspection stage ──────────────────────────────────────────────

def pixel_inspect(master: Master,
                  captured_rgb: Optional[np.ndarray]) -> PixelInspectionReport:
    cfg = master.pixel_inspection
    if not cfg.enabled:
        return PixelInspectionReport(enabled=False, tolerance=cfg.delta_e_tolerance,
                                     verdict="SKIPPED",
                                     note="pixel_inspection.enabled = false ใน spec.json")
    if captured_rgb is None:
        return PixelInspectionReport(enabled=False, tolerance=cfg.delta_e_tolerance,
                                     verdict="SKIPPED",
                                     note="ภาพที่ส่งมาไม่สามารถ decode เป็น RGB ได้")
    if not master.pdf_path:
        return PixelInspectionReport(enabled=False, tolerance=cfg.delta_e_tolerance,
                                     verdict="SKIPPED",
                                     note="SKU นี้ไม่มี master.pdf — ข้าม pixel inspection")

    try:
        master_rgb = master_renderer.get_master_image(master.pdf_path)
    except Exception as e:
        return PixelInspectionReport(enabled=False, tolerance=cfg.delta_e_tolerance,
                                     verdict="SKIPPED",
                                     note=f"render PDF ไม่สำเร็จ: {e}")

    # Calibrate using the master as color reference. This avoids the
    # neutral-gray-scene assumption of plain gray-world AWB, which would
    # destroy the brand-color of a label that is intentionally one-colored
    # (e.g., the AQUA navy-blue tuna can).
    calibrated = calibration.calibrate(captured_rgb, master=master_rgb)
    aligned, align_info = registration.align(master_rgb, calibrated)
    de = deltae_map.compute_delta_e(master_rgb, aligned)

    defects, _mask = deltae_map.cluster_defects(
        de, master_rgb, aligned,
        tolerance=cfg.delta_e_tolerance,
        min_area_px=cfg.min_defect_area_px,
    )
    stats = deltae_map.map_stats(de, cfg.delta_e_tolerance)
    heatmap_b64 = overlay.make_heatmap_overlay(
        aligned, de, cfg.delta_e_tolerance, defects
    ) or ""

    verdict = _pixel_verdict(stats, defects)

    return PixelInspectionReport(
        enabled=True,
        tolerance=cfg.delta_e_tolerance,
        de_mean=stats["mean"],
        de_peak=stats["peak"],
        de_p95=stats["p95"],
        de_p99=stats["p99"],
        pass_rate=stats["pass_rate"],
        fail_pixels=stats["fail_pixels"],
        total_pixels=stats["total_pixels"],
        defects=defects,
        heatmap_png_b64=heatmap_b64,
        align_info=align_info,
        verdict=verdict,
    )


# ── Main orchestrator ───────────────────────────────────────────────────

def inspect(master: Master,
            cropped_image_bytes: bytes,
            found_color_hexes: Optional[List[str]] = None) -> InspectionReport:
    captured_rgb = _decode_image_to_rgb(cropped_image_bytes)

    # 1. OCR
    ocr = vertex_client.ocr_image(cropped_image_bytes)
    ocr_text = ocr.get("text", "")

    # 2. Named brand color compare (whitelist)
    # Only meaningful when the caller has supplied sampled hex values
    # (e.g., from a future K-Means dominant-color stage). For now leave
    # the list empty rather than compare against a black placeholder.
    color_results = (
        compare_colors(master.colors, found_color_hexes)
        if found_color_hexes else []
    )

    # 3. Field-aware text compare
    field_results = compare_all(master.fields, ocr_text)

    # 4. Pixel inspection (ΔE map)
    px_report = pixel_inspect(master, captured_rgb)

    # 5. Gemini context check
    ambiguous = [r.name for r in field_results if r.severity in ("minor", "warning")]
    gemini = (
        {"verdict": "not_needed"}
        if not ambiguous
        else vertex_client.gemini_context_check(master.raw_text, ocr_text, ambiguous)
    )

    # 6. Combined verdict.
    # When OCR is stubbed every text field "fails" against the stub string,
    # so we ignore the text verdict in stub mode and let the pixel + color
    # checks decide.
    stub = bool(ocr.get("stub", False))
    text_verdict = "SKIPPED" if stub else overall_text_verdict(field_results)
    if not color_results:
        color_verdict = "SKIPPED"
    else:
        color_verdict = "FAIL" if any(not c.passed for c in color_results) else "PASS"

    candidates = {text_verdict, px_report.verdict, color_verdict}
    real = {v for v in candidates if v != "SKIPPED"}

    if "FAIL" in real:
        verdict = "FAIL"
    elif "WARN" in real:
        verdict = "WARN"
    elif not real:
        # Everything was skipped → can't certify.
        verdict = "WARN"
    else:
        verdict = "PASS"

    return InspectionReport(
        sku_code=master.sku_code,
        verdict=verdict,
        field_results=[r.__dict__ for r in field_results],
        color_results=[c.__dict__ for c in color_results],
        ocr_text=ocr_text,
        gemini=gemini,
        stub_mode=bool(ocr.get("stub", False)),
        pixel_inspection=asdict(px_report),
    )
