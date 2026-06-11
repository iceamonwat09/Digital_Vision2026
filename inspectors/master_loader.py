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
    pixel_cfg = PixelInspectionConfig(
        enabled=bool(px_spec.get("enabled", True)),
        delta_e_tolerance=float(px_spec.get("delta_e_tolerance", 6.0)),
        min_defect_area_px=int(px_spec.get("min_defect_area_px", 80)),
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
