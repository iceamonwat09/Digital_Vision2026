"""
Brand vocabulary store — approved words and phrases per brand.

File per brand: ``data/artwork_check/vocab/<brand>.json``

    {"brand": "Hidden Bay",
     "words":   ["SKIPJACK", "ROTAR", ...],
     "phrases": ["¡Para mejor calidad!", ...]}

Words extend the dictionary layer (so brand terms are not flagged);
phrases are exact approved strings — near-misses on the artwork are
reported as PHRASE_FAIL. Entries come from artwork that humans already
approved, so matching against them is verification, not invention.
"""

from __future__ import annotations

import json
import os
import re
from typing import List

from . import config


def _path(brand: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9ก-๛_ -]", "", brand).strip()
    if not safe:
        raise ValueError("invalid brand name")
    return os.path.join(config.VOCAB_DIR, f"{safe}.json")


def list_brands() -> List[str]:
    return sorted(fn[:-5] for fn in os.listdir(config.VOCAB_DIR)
                  if fn.endswith(".json"))


def load(brand: str) -> dict:
    p = _path(brand)
    if not os.path.exists(p):
        return {"brand": brand, "words": [], "phrases": []}
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    return {
        "brand": brand,
        "words": [str(w) for w in data.get("words", []) if str(w).strip()],
        "phrases": [str(p) for p in data.get("phrases", [])
                    if str(p).strip()],
    }


def save(brand: str, words: List[str], phrases: List[str]) -> dict:
    data = {
        "brand": brand,
        "words": sorted({str(w).strip() for w in words if str(w).strip()}),
        "phrases": sorted({str(p).strip() for p in phrases
                           if str(p).strip()}),
    }
    with open(_path(brand), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data
