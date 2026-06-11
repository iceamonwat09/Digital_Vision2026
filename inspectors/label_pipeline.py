"""
End-to-end inspection orchestrator:

    cropped_image_bytes + master  →  InspectionReport

Stages (revised with Phase 1-5 improvements):
    1. OCR captured image  (N8N → Gemini; stub fallback)
    1b. OCR master PDF     (same engine → symmetric comparison; cached)
    2. Block matching      (spatial + textual; master_blocks ↔ captured_blocks)
    3. Field-aware text compare  (exact / Levenshtein / regex, block-guided)
    4. Alignment           (ORB homography → ECC affine → resize fallback)
    5. Color compare       (CIE2000 ΔE; spatially sampled from aligned image)
    6. Pixel inspection    (ΔE2000 map + defect clustering; pre-aligned)
    7. Gemini visual diff  (master vs captured, grounded with block-diff context)
    8. Gemini context check (ambiguous fields only; still stubbed)
    9. Combine to PASS / WARN / FAIL
"""

from __future__ import annotations

import base64
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np

import config

from . import (
    barcode as barcode_decoder,
    block_match,
    calibration,
    deltae_map,
    master_ocr,
    master_renderer,
    overlay,
    registration,
    vertex_client,
    visual_diff as visual_diff_client,
)
from .color_compare import compare_colors, extract_brand_colors
from .master_loader import Master
from .text_compare import compare_all, overall_text_verdict
from .text_diff import line_diff

logger = logging.getLogger(__name__)


# ── Dataclasses ──────────────────────────────────────────────────────────────

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
    master_png_b64: str = ""        # downscaled master render (for UI compare)
    aligned_png_b64: str = ""       # downscaled aligned capture (for UI compare)
    align_info: dict = field(default_factory=dict)
    verdict: str = "PASS"           # "PASS" | "WARN" | "FAIL" | "SKIPPED"
    note: str = ""
    mask_info: dict = field(default_factory=dict)   # edge/glare/ignored %


@dataclass
class InspectionReport:
    sku_code: str
    verdict: str                        # "PASS" | "WARN" | "FAIL"
    field_results: List[dict] = field(default_factory=list)
    color_results: List[dict] = field(default_factory=list)
    ocr_text: str = ""
    master_text: str = ""               # OCR of master (Phase 1) or PDF text layer
    text_line_diff: List[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    ocr_engine: str = ""
    ocr_error: str = ""
    gemini: dict = field(default_factory=dict)
    visual_diff: dict = field(default_factory=dict)
    stub_mode: bool = False
    pixel_inspection: dict = field(default_factory=dict)
    barcodes: List[dict] = field(default_factory=list)  # decoded 1-D/2-D symbols

    def to_dict(self) -> dict:
        return asdict(self)


# ── Helpers ──────────────────────────────────────────────────────────────────

_SEVERITY_RANK = {"critical": 3, "warning": 2, "minor": 1, "ok": 0, "": 0}

_PREVIEW_MAX_EDGE = 1000     # downscale master/aligned previews for the UI


def _rgb_preview_b64(rgb: Optional[np.ndarray]) -> str:
    """Downscaled JPEG-base64 of an RGB image for side-by-side UI display."""
    if rgb is None:
        return ""
    try:
        h, w = rgb.shape[:2]
        long_edge = max(h, w)
        if long_edge > _PREVIEW_MAX_EDGE:
            scale = _PREVIEW_MAX_EDGE / long_edge
            rgb = cv2.resize(rgb, (int(w * scale), int(h * scale)),
                             interpolation=cv2.INTER_AREA)
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return base64.b64encode(buf).decode("ascii") if ok else ""
    except Exception:
        return ""


def _visual_diff_verdict(vd: dict) -> str:
    """PASS/WARN/FAIL from Gemini differences.  SKIPPED on error or stub."""
    if not vd or vd.get("stub"):
        return "SKIPPED"
    if vd.get("error"):
        return "SKIPPED"
    diffs = vd.get("differences") or []
    if not diffs:
        return "PASS"
    worst = max((_SEVERITY_RANK.get(d.get("severity", ""), 0) for d in diffs),
                default=0)
    if worst >= 3:
        return "FAIL"
    if worst >= 2:
        return "WARN"
    return "WARN"


def _build_summary(field_results, color_results, px_report,
                   visual_diff_payload: dict) -> dict:
    fld_total = len(field_results)
    fld_fail  = [r for r in field_results if not r.passed]
    fld_crit  = [r for r in fld_fail if r.severity == "critical"]

    col_total = len(color_results)
    col_fail  = [c for c in color_results if not c.passed]

    px_defects = px_report.defects or []
    px_area    = sum(int(d.get("area_px", 0)) for d in px_defects)

    vd_diffs   = (visual_diff_payload or {}).get("differences") or []
    vd_crit    = sum(1 for d in vd_diffs if d.get("severity") == "critical")
    vd_enabled = bool(visual_diff_payload) and not visual_diff_payload.get("stub")

    return {
        "fields": {
            "total":    fld_total,
            "failed":   len(fld_fail),
            "critical": len(fld_crit),
            "passed":   fld_total - len(fld_fail),
        },
        "colors": {
            "total":  col_total,
            "failed": len(col_fail),
            "passed": col_total - len(col_fail),
        },
        "pixels": {
            "enabled":      px_report.enabled,
            "verdict":      px_report.verdict,
            "defect_count": len(px_defects),
            "defect_area":  px_area,
            "pass_rate":    px_report.pass_rate,
            "peak":         px_report.de_peak,
        },
        "visual_diff": {
            "enabled":  vd_enabled,
            "count":    len(vd_diffs),
            "critical": vd_crit,
            "engine":   (visual_diff_payload or {}).get("engine", ""),
        },
    }


def _decode_image_to_rgb(image_bytes: bytes) -> Optional[np.ndarray]:
    if not image_bytes:
        return None
    arr = np.frombuffer(image_bytes, np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        return None
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def _image_dims(image_bytes: bytes) -> Tuple[int, int]:
    """
    (width, height) of an encoded image.  Header-only read via Pillow so the
    high-res OCR variant is never fully decoded just for its dimensions;
    falls back to an OpenCV decode when Pillow is unavailable.
    """
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(image_bytes)) as im:
            return int(im.width), int(im.height)
    except Exception:
        arr = cv2.imdecode(np.frombuffer(image_bytes, np.uint8),
                           cv2.IMREAD_GRAYSCALE)
        if arr is None:
            return 1, 1
        return int(arr.shape[1]), int(arr.shape[0])


def _pixel_verdict(stats: dict, defects: List[dict], cfg=None) -> str:
    """
    Reduce per-pixel stats to PASS/WARN/FAIL.

    Tolerates JPEG noise and sub-pixel alignment artefacts at
    high-contrast edges.  A real print defect is LARGE + HIGH ΔE.

    Thresholds come from the SKU's ``pixel_inspection`` spec (``cfg``) so
    each material can be tuned; falls back to the dataclass defaults.
    """
    from .master_loader import PixelInspectionConfig
    if cfg is None:
        cfg = PixelInspectionConfig()

    pass_rate = stats.get("pass_rate", 100.0)
    peak = stats.get("peak", 0.0)
    tol  = stats.get("tolerance", cfg.delta_e_tolerance)

    severe = [d for d in defects
              if d.get("severity") == "critical"
              and d.get("area_px", 0) >= cfg.critical_area_px]
    big    = [d for d in defects if d.get("area_px", 0) >= cfg.big_area_px]

    if (severe or big or pass_rate < cfg.fail_pass_rate
            or peak > tol * cfg.peak_fail_mult):
        return "FAIL"
    if (defects or pass_rate < cfg.warn_pass_rate
            or peak > tol * cfg.peak_warn_mult):
        return "WARN"
    return "PASS"


# ── Parallel-I/O safe wrappers (never raise inside a worker thread) ──────────

def _safe_master_rgb(pdf_path: str) -> Optional[np.ndarray]:
    try:
        return master_renderer.get_master_image(pdf_path)
    except Exception as e:
        logger.warning("master render (RGB) failed: %s", e)
        return None


def _safe_master_jpeg(pdf_path: str) -> Optional[bytes]:
    try:
        return master_renderer.render_master_to_jpeg_bytes(pdf_path)
    except Exception as e:
        logger.warning("master render (JPEG) failed: %s", e)
        return None


def _align_to_master(
    master_rgb: Optional[np.ndarray],
    captured_rgb: Optional[np.ndarray],
) -> Tuple[Optional[np.ndarray], dict]:
    """Calibrate + align a captured image to an already-rendered master."""
    if master_rgb is None or captured_rgb is None:
        return None, {}
    calibrated = calibration.calibrate(captured_rgb, master=master_rgb)
    return registration.align(master_rgb, calibrated)


# ── Pixel inspection stage ───────────────────────────────────────────────────

def pixel_inspect(
    master: Master,
    captured_rgb: Optional[np.ndarray],
    master_rgb: Optional[np.ndarray] = None,
    aligned_rgb: Optional[np.ndarray] = None,
    align_info: Optional[dict] = None,
) -> PixelInspectionReport:
    """
    Compute ΔE2000 pixel map and cluster defects.

    When ``master_rgb`` and ``aligned_rgb`` are provided (pre-computed by
    ``_render_and_align``), the render/align step is skipped to avoid
    redundant work.
    """
    cfg = master.pixel_inspection
    skipped = PixelInspectionReport(enabled=False, tolerance=cfg.delta_e_tolerance,
                                    verdict="SKIPPED")

    if not cfg.enabled:
        return PixelInspectionReport(**{**skipped.__dict__,
                                       "note": "pixel_inspection.enabled = false ใน spec.json"})
    if captured_rgb is None:
        return PixelInspectionReport(**{**skipped.__dict__,
                                       "note": "ภาพที่ส่งมาไม่สามารถ decode เป็น RGB ได้"})
    if not master.pdf_path and master_rgb is None:
        return PixelInspectionReport(**{**skipped.__dict__,
                                       "note": "SKU นี้ไม่มี master.pdf — ข้าม pixel inspection"})

    # Use pre-computed master_rgb if provided, otherwise render
    if master_rgb is None:
        try:
            master_rgb = master_renderer.get_master_image(master.pdf_path)
        except Exception as e:
            return PixelInspectionReport(**{**skipped.__dict__,
                                           "note": f"render PDF ไม่สำเร็จ: {e}"})

    # Use pre-computed aligned_rgb if provided, otherwise align
    if aligned_rgb is None:
        calibrated = calibration.calibrate(captured_rgb, master=master_rgb)
        aligned_rgb, align_info = registration.align(master_rgb, calibrated)

    align_info = align_info or {}

    de = deltae_map.compute_delta_e(master_rgb, aligned_rgb)

    # Suppress registration-halo (edges) + specular glare before scoring.
    ignore_mask, mask_info = deltae_map.build_ignore_mask(
        master_rgb, aligned_rgb,
        ignore_edges=cfg.ignore_edges,
        edge_dilate_px=cfg.edge_dilate_px,
        ignore_glare=cfg.ignore_glare,
        glare_v_thresh=cfg.glare_v_thresh,
        glare_s_thresh=cfg.glare_s_thresh,
    )

    defects, _mask = deltae_map.cluster_defects(
        de, master_rgb, aligned_rgb,
        tolerance=cfg.delta_e_tolerance,
        min_area_px=cfg.min_defect_area_px,
        ignore_mask=ignore_mask,
    )
    stats = deltae_map.map_stats(de, cfg.delta_e_tolerance, ignore_mask=ignore_mask)
    heatmap_b64 = overlay.make_heatmap_overlay(
        aligned_rgb, de, cfg.delta_e_tolerance, defects
    ) or ""
    verdict = _pixel_verdict(stats, defects, cfg=cfg)

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
        master_png_b64=_rgb_preview_b64(master_rgb),
        aligned_png_b64=_rgb_preview_b64(aligned_rgb),
        align_info=align_info,
        verdict=verdict,
        mask_info=mask_info,
    )


# ── Visual diff stage ────────────────────────────────────────────────────────

def visual_inspect(
    master: Master,
    captured_jpeg_bytes: bytes,
    master_jpeg_bytes: Optional[bytes] = None,
    block_diff: Optional[dict] = None,
) -> dict:
    """
    Ask Gemini (via N8N) to enumerate differences between master and captured.

    ``master_jpeg_bytes`` — pre-rendered master JPEG (avoids a second render
    when the caller already rendered for pixel inspection).
    ``block_diff`` — compact diff_summary from block_match; sent as context
    so the N8N/Gemini prompt can confirm OCR findings and focus on visuals.
    """
    stub = lambda msg: {
        "differences": [], "summary": "", "stub": True,
        "engine": "visual_diff", "error": msg,
    }

    if not getattr(config, "VISUAL_DIFF_ENABLED", False):
        return stub("VISUAL_DIFF_ENABLED=false")
    if not master.pdf_path:
        return stub("SKU has no master.pdf")
    if not captured_jpeg_bytes:
        return stub("empty captured image")

    if master_jpeg_bytes is None:
        try:
            master_jpeg_bytes = master_renderer.render_master_to_jpeg_bytes(
                master.pdf_path)
        except Exception as e:
            return stub(f"failed to render master: {e}")

    return visual_diff_client.compare_images(
        master_bytes=master_jpeg_bytes,
        captured_bytes=captured_jpeg_bytes,
        sku_code=master.sku_code,
        block_diff=block_diff,
    )


# ── Main orchestrator ────────────────────────────────────────────────────────

def inspect(
    master: Master,
    cropped_image_bytes: bytes,
    found_color_hexes: Optional[List[str]] = None,
    ocr_image_bytes: Optional[bytes] = None,
) -> InspectionReport:
    """
    Full inspection pipeline.  All phases run in sequence; failures in any
    single stage are isolated so the overall report is always returned.

    ``ocr_image_bytes`` — optional higher-resolution variant of the same
    crop (identical content/aspect, long edge up to 4096).  When provided
    it feeds only the OCR stage, so small label text stays legible while
    alignment / ΔE / visual diff keep the bounded ``cropped_image_bytes``.
    """
    captured_rgb = _decode_image_to_rgb(cropped_image_bytes)
    ocr_bytes = ocr_image_bytes or cropped_image_bytes
    vd_enabled = bool(master.pdf_path
                      and getattr(config, "VISUAL_DIFF_ENABLED", False))

    # ────────────────────────────────────────────────────────────────────────
    # Parallel I/O phase: these calls are independent, so the two network OCR
    # round-trips (captured + master), the barcode decode, and the master
    # render(s) all run concurrently instead of one-after-another. On a cold
    # SKU this removes a full OCR round-trip from the wall-clock time.
    # ────────────────────────────────────────────────────────────────────────
    with ThreadPoolExecutor(max_workers=5) as pool:
        f_cap_ocr  = pool.submit(vertex_client.ocr_image, ocr_bytes)
        f_barcode  = pool.submit(barcode_decoder.decode_barcodes, ocr_bytes)
        f_mas_ocr  = (pool.submit(master_ocr.get_master_ocr, master.pdf_path)
                      if master.pdf_path else None)
        f_mas_rgb  = (pool.submit(_safe_master_rgb, master.pdf_path)
                      if master.pdf_path else None)
        f_mas_jpeg = (pool.submit(_safe_master_jpeg, master.pdf_path)
                      if vd_enabled else None)

        ocr               = f_cap_ocr.result()
        decoded_barcodes  = f_barcode.result()
        master_ocr_result = f_mas_ocr.result() if f_mas_ocr else {}
        master_rgb        = f_mas_rgb.result() if f_mas_rgb else None
        master_jpeg_bytes = f_mas_jpeg.result() if f_mas_jpeg else None

    # Stage 1a: captured OCR results
    ocr_text       = ocr.get("text", "")
    captured_blocks: List[dict] = ocr.get("blocks") or []
    # Block bboxes live in the coordinate space of the image the OCR engine
    # actually saw — use that image's dimensions for bbox normalisation.
    if ocr_image_bytes:
        captured_w, captured_h = _image_dims(ocr_image_bytes)
    elif captured_rgb is not None:
        captured_h, captured_w = int(captured_rgb.shape[0]), int(captured_rgb.shape[1])
    else:
        captured_h, captured_w = 1, 1

    # Stage 1b: master OCR results (Phase 1 — symmetric OCR)
    master_blocks: List[dict] = []
    master_ocr_text: str = master.raw_text or ""  # fallback: PDF text layer
    if master_ocr_result and not master_ocr_result.get("stub"):
        _mo_text = master_ocr_result.get("text", "")
        if _mo_text:
            master_ocr_text = _mo_text
        master_blocks = master_ocr_result.get("blocks") or []

    # ────────────────────────────────────────────────────────────────────────
    # Stage 4 / Phase 3: align captured to the pre-rendered master (CPU-local)
    # ────────────────────────────────────────────────────────────────────────
    master_h, master_w = (1, 1)
    aligned_rgb, align_info = _align_to_master(master_rgb, captured_rgb)
    if master_rgb is not None:
        master_h, master_w = int(master_rgb.shape[0]), int(master_rgb.shape[1])

    # Auto white-balance the aligned capture against the master's white
    # regions (location-free white-patch) — sharpens colour ΔE under
    # non-neutral lighting. Feeds the ΔE + brand-colour stages below.
    _px_cfg = master.pixel_inspection
    if (aligned_rgb is not None and master_rgb is not None
            and getattr(_px_cfg, "auto_white_balance", True)):
        aligned_rgb, wb_info = calibration.auto_white_balance_from_master(
            aligned_rgb, master_rgb,
            white_thresh=getattr(_px_cfg, "white_ref_thresh", 235))
        if wb_info:
            align_info = {**align_info, "white_balance": wb_info}

    # ────────────────────────────────────────────────────────────────────────
    # Phase 2: Spatial block matching
    # ────────────────────────────────────────────────────────────────────────
    block_diff_result: dict = {}
    block_diff_summary: Optional[dict] = None
    if master_blocks and captured_blocks:
        block_diff_result = block_match.match_blocks(
            master_blocks, captured_blocks,
            master_img_w=master_w, master_img_h=master_h,
            captured_img_w=captured_w, captured_img_h=captured_h,
        )
        block_diff_summary = block_match.diff_summary(block_diff_result)
        logger.info("Block match: %d matched, %d missing, %d extra",
                    len(block_diff_result.get("matched", [])),
                    len(block_diff_result.get("missing", [])),
                    len(block_diff_result.get("extra",   [])))

    # ────────────────────────────────────────────────────────────────────────
    # Phase 4: kick off the Gemini visual diff (network) in the background so
    # it overlaps the local CPU work below (align/ΔE/colour/text). It is
    # grounded with block_diff_summary, which is now available.
    # ────────────────────────────────────────────────────────────────────────
    vd_pool = ThreadPoolExecutor(max_workers=1)
    f_visual = vd_pool.submit(
        visual_inspect, master, cropped_image_bytes,
        master_jpeg_bytes, block_diff_summary)

    try:
        # ── Phase 5: Color sampling from aligned image ───────────────────
        if master_rgb is not None and aligned_rgb is not None and master.colors:
            found_color_hexes = extract_brand_colors(
                master_rgb, aligned_rgb, master.colors)

        # ── 2. Named brand color compare ─────────────────────────────────
        color_results = (
            compare_colors(master.colors, found_color_hexes)
            if found_color_hexes else []
        )

        # ── 3. Field-aware text compare (block-guided + symmetric OCR) ────
        field_results = compare_all(
            master.fields, ocr_text,
            master_ocr_text=master_ocr_text,   # symmetric: master_found per field
            master_blocks=master_blocks,
            captured_blocks=captured_blocks,
            master_img_w=master_w,
            master_img_h=master_h,
            captured_img_w=captured_w,
            captured_img_h=captured_h,
            decoded_barcodes=decoded_barcodes,
        )

        # ── 6. Pixel inspection (pre-computed alignment) ─────────────────
        px_report = pixel_inspect(
            master, captured_rgb,
            master_rgb=master_rgb,
            aligned_rgb=aligned_rgb,
            align_info=align_info,
        )
    finally:
        # Always collect the visual-diff result (and shut the pool down).
        vd_payload = f_visual.result()
        vd_pool.shutdown(wait=True)

    vd_verdict = _visual_diff_verdict(vd_payload)

    # ── 8. Gemini context check (ambiguous fields; still stubbed) ─────────
    ambiguous = [r.name for r in field_results if r.severity in ("minor", "warning")]
    gemini = (
        {"verdict": "not_needed"}
        if not ambiguous
        else vertex_client.gemini_context_check(
            master_ocr_text, ocr_text, ambiguous)
    )

    # ── 9. Combined verdict ───────────────────────────────────────────────
    stub_ocr    = bool(ocr.get("stub", False))
    text_verdict = "SKIPPED" if stub_ocr else overall_text_verdict(field_results)
    color_verdict = (
        "SKIPPED" if not color_results
        else ("FAIL" if any(not c.passed for c in color_results) else "PASS")
    )

    candidates = {text_verdict, px_report.verdict, color_verdict, vd_verdict}
    real = {v for v in candidates if v != "SKIPPED"}

    if "FAIL" in real:
        verdict = "FAIL"
    elif "WARN" in real:
        verdict = "WARN"
    elif not real:
        verdict = "WARN"   # everything skipped → can't certify
    else:
        verdict = "PASS"

    summary = _build_summary(field_results, color_results, px_report, vd_payload)
    summary["verdict"]        = verdict
    summary["text_verdict"]   = text_verdict
    summary["color_verdict"]  = color_verdict
    summary["pixel_verdict"]  = px_report.verdict
    summary["visual_verdict"] = vd_verdict

    return InspectionReport(
        sku_code=master.sku_code,
        verdict=verdict,
        field_results=[r.__dict__ for r in field_results],
        color_results=[c.__dict__ for c in color_results],
        ocr_text=ocr_text,
        master_text=master_ocr_text,           # Phase 1: OCR-derived master text
        text_line_diff=line_diff(master_ocr_text, ocr_text),
        summary=summary,
        ocr_engine=str(ocr.get("engine", "")),
        ocr_error=str(ocr.get("error", "")),
        gemini=gemini,
        visual_diff=vd_payload,
        stub_mode=stub_ocr,
        pixel_inspection=asdict(px_report),
        barcodes=decoded_barcodes,
    )
