"""
Load a SKU master from ``data/label_paper/skus/<sku>/``.

Expected files inside each SKU directory:
    master.pdf   — the artwork PDF used as ground truth (text layer)
    spec.json    — declarative field + color spec

spec.json schema:
{
  "sku_code": "SNK-CHK-060",
  "display_name": "Snack ไก่ย่าง 60g",
  "fields": [
    {"name":"barcode","expected":"8851234567890","tolerance":0,
     "method":"exact","critical":true},
    {"name":"product_name","expected":"ไก่ย่าง","tolerance":2,
     "method":"levenshtein","critical":false}
  ],
  "colors": [
    {"name":"brand_red","hex":"#E53935","delta_e_tolerance":8.0}
  ]
}
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class FieldSpec:
    name: str
    expected: str
    tolerance: int = 0
    method: str = "exact"          # "exact" | "levenshtein" | "regex"
    critical: bool = True
    anchor: str = ""               # label text that precedes the value in OCR output
    value_regex: str = ""          # regex to extract value from/near the anchor line
    normalize: str = ""            # "digits" | "lower" | "nospace" | ""


@dataclass
class MasterColor:
    name: str
    hex: str
    delta_e_tolerance: float = 10.0


@dataclass
class PixelInspectionConfig:
    enabled: bool = True
    delta_e_tolerance: float = 6.0
    min_defect_area_px: int = 80

    # ── False-positive suppression (per-material, overridable in spec.json) ──
    # Sub-pixel misalignment at high-contrast text edges and specular glare on
    # glossy/UV-coated labels both create ΔE that is NOT a print defect. These
    # masks exclude those pixels from the ΔE statistics and defect clustering.
    ignore_edges: bool = True
    edge_dilate_px: int = 3          # how far around a master edge to ignore
    ignore_glare: bool = True
    glare_v_thresh: int = 245        # HSV V ≥ this  → candidate highlight
    glare_s_thresh: int = 35         # HSV S ≤ this  → washed-out (specular)

    # ── Verdict thresholds (were hard-coded in label_pipeline._pixel_verdict) ──
    # A real print defect is LARGE + HIGH ΔE; these tune how strict that is.
    fail_pass_rate: float = 88.0     # pass_rate below this → FAIL
    warn_pass_rate: float = 97.0     # pass_rate below this → WARN
    peak_fail_mult: float = 6.0      # peak ΔE > tol*this → FAIL
    peak_warn_mult: float = 2.0      # peak ΔE > tol*this → WARN
    critical_area_px: int = 500      # 'critical'-severity defect this big → FAIL
    big_area_px: int = 2000          # any defect this big → FAIL


@dataclass
class Master:
    sku_code: str
    display_name: str
    pdf_path: Optional[str]
    raw_text: str
    fields: List[FieldSpec] = field(default_factory=list)
    colors: List[MasterColor] = field(default_factory=list)
    pixel_inspection: PixelInspectionConfig = field(default_factory=PixelInspectionConfig)


def load_master(sku_dir: str) -> Master:
    spec_path = os.path.join(sku_dir, "spec.json")
    pdf_path = os.path.join(sku_dir, "master.pdf")

    if not os.path.isfile(spec_path):
        raise FileNotFoundError(f"spec.json not found in {sku_dir}")

    with open(spec_path, "r", encoding="utf-8") as f:
        spec = json.load(f)

    raw_text = _extract_pdf_text(pdf_path) if os.path.isfile(pdf_path) else ""

    # Tolerate extra documentation keys in spec.json (e.g. ``cmyk``, ``note``)
    # by filtering each row down to the dataclass-known field names.
    _field_keys = {"name", "expected", "tolerance", "method", "critical",
                   "anchor", "value_regex", "normalize"}
    _color_keys = {"name", "hex", "delta_e_tolerance"}
    fields = [FieldSpec(**{k: v for k, v in fd.items() if k in _field_keys})
              for fd in spec.get("fields", [])]
    colors = [MasterColor(**{k: v for k, v in c.items() if k in _color_keys})
              for c in spec.get("colors", [])]

    px_spec = spec.get("pixel_inspection") or {}
    _px_defaults = PixelInspectionConfig()
    pixel_cfg = PixelInspectionConfig(
        enabled=bool(px_spec.get("enabled", True)),
        delta_e_tolerance=float(px_spec.get("delta_e_tolerance", 6.0)),
        min_defect_area_px=int(px_spec.get("min_defect_area_px", 80)),
        ignore_edges=bool(px_spec.get("ignore_edges", _px_defaults.ignore_edges)),
        edge_dilate_px=int(px_spec.get("edge_dilate_px", _px_defaults.edge_dilate_px)),
        ignore_glare=bool(px_spec.get("ignore_glare", _px_defaults.ignore_glare)),
        glare_v_thresh=int(px_spec.get("glare_v_thresh", _px_defaults.glare_v_thresh)),
        glare_s_thresh=int(px_spec.get("glare_s_thresh", _px_defaults.glare_s_thresh)),
        fail_pass_rate=float(px_spec.get("fail_pass_rate", _px_defaults.fail_pass_rate)),
        warn_pass_rate=float(px_spec.get("warn_pass_rate", _px_defaults.warn_pass_rate)),
        peak_fail_mult=float(px_spec.get("peak_fail_mult", _px_defaults.peak_fail_mult)),
        peak_warn_mult=float(px_spec.get("peak_warn_mult", _px_defaults.peak_warn_mult)),
        critical_area_px=int(px_spec.get("critical_area_px", _px_defaults.critical_area_px)),
        big_area_px=int(px_spec.get("big_area_px", _px_defaults.big_area_px)),
    )

    return Master(
        sku_code=spec["sku_code"],
        display_name=spec.get("display_name", spec["sku_code"]),
        pdf_path=pdf_path if os.path.isfile(pdf_path) else None,
        raw_text=raw_text,
        fields=fields,
        colors=colors,
        pixel_inspection=pixel_cfg,
    )


def list_skus(skus_root: str) -> List[Dict[str, object]]:
    """Return ``[{sku_code, display_name, has_master_pdf}, ...]``."""
    if not os.path.isdir(skus_root):
        return []
    out: List[Dict[str, object]] = []
    for name in sorted(os.listdir(skus_root)):
        sku_dir = os.path.join(skus_root, name)
        spec_path = os.path.join(sku_dir, "spec.json")
        if not os.path.isfile(spec_path):
            continue
        try:
            with open(spec_path, "r", encoding="utf-8") as f:
                spec = json.load(f)
            out.append({
                "sku_code": spec["sku_code"],
                "display_name": spec.get("display_name", spec["sku_code"]),
                "has_master_pdf": os.path.isfile(os.path.join(sku_dir, "master.pdf")),
            })
        except Exception:
            continue
    return out


def _extract_pdf_text(pdf_path: str) -> str:
    """Extract text via PyMuPDF. Returns "" when the lib is not installed."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return ""
    doc = fitz.open(pdf_path)
    try:
        parts = [page.get_text() for page in doc]
    finally:
        doc.close()
    return "\n".join(parts)
