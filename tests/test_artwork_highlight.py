"""Unit tests for the defect-card red-box highlighter.

The pure-python location logic (which OCR line/token a word sits on, and
OCR-block bbox normalization) runs with no numpy/cv2. The projection
profile CV path is exercised separately and skipped when numpy is absent.
"""

import pytest

from artwork_check import highlight as hl


# ── locate_token: which line / token ──────────────────────────────────

NUTRITION = (
    "Guaranteed Analysis\n"
    "Cude Protein 8.0% Min\n"
    "Cude Fat 0.2% Min\n"
    "Ash 2.0% Max\n"
    "Phosphours 0.040% Min\n"
)


def test_locate_single_word_line_and_token():
    loc = hl.locate_token("Cude", NUTRITION)
    assert loc is not None
    assert loc["line"] == 1          # first "Cude" occurrence
    assert loc["token"] == 0         # first token on that line
    assert loc["multi"] is False
    assert loc["n_lines"] == 5       # blank trailing line dropped


def test_locate_word_not_first_token():
    loc = hl.locate_token("Phosphours", NUTRITION)
    assert loc["line"] == 4
    assert loc["token"] == 0
    assert loc["n_tokens"] == 3      # Phosphours 0.040% Min


def test_locate_word_inside_middle_of_line():
    loc = hl.locate_token("Protein", NUTRITION)
    assert loc["line"] == 1
    assert loc["token"] == 1


def test_locate_missing_word_returns_none():
    assert hl.locate_token("Sunflower", NUTRITION) is None


def test_locate_empty_inputs():
    assert hl.locate_token("", NUTRITION) is None
    assert hl.locate_token("Cude", "") is None


def test_locate_multi_word_phrase_boxes_line():
    loc = hl.locate_token("Cude Fat", NUTRITION)
    assert loc is not None
    assert loc["multi"] is True
    assert loc["line"] == 2


def test_locate_is_punctuation_tolerant():
    txt = "NET WT\n5 OZ (142 g)\n"
    loc = hl.locate_token("Cude:", "Cude: Protein\n")  # trailing colon
    assert loc["token"] == 0
    # number-style found spanning tokens → multi/line box
    loc2 = hl.locate_token("142 g", txt)
    assert loc2 is not None and loc2["multi"] is True
    assert loc2["line"] == 1


# ── _infer_scale: per-zone coordinate-convention detection ────────────

def test_infer_scale_0_1():
    blocks = [{"text": "A", "bbox": [0.1, 0.2, 0.3, 0.1]},
              {"text": "B", "bbox": [0.5, 0.6, 0.2, 0.1]}]
    assert hl._infer_scale(blocks, ocr_wh=[3000, 2000]) == (1.0, 1.0)


def test_infer_scale_0_1000():
    # some block reaches ~900 (≤1050) and the OCR crop is big → 0..1000
    blocks = [{"text": "A", "bbox": [10, 20, 30, 10]},
              {"text": "B", "bbox": [800, 850, 100, 40]}]
    assert hl._infer_scale(blocks, ocr_wh=[3000, 2000]) == (1000.0, 1000.0)


def test_infer_scale_pixels():
    # a block coordinate exceeds 1050 → must be raw pixels of the crop
    blocks = [{"text": "A", "bbox": [100, 100, 50, 30]},
              {"text": "B", "bbox": [1800, 900, 120, 40]}]
    assert hl._infer_scale(blocks, ocr_wh=[2000, 1000]) == (2000.0, 1000.0)


def test_infer_scale_none_without_ocr_wh():
    blocks = [{"text": "B", "bbox": [800, 850, 100, 40]}]
    assert hl._infer_scale(blocks, ocr_wh=None) is None


# ── _norm_block_bbox: fraction → display pixels via a given scale ──────

def test_bbox_normalized_0_1():
    box = hl._norm_block_bbox([0.1, 0.2, 0.3, 0.4], 1000, 500, (1.0, 1.0))
    assert box == (100, 100, 400, 300)


def test_bbox_0_1000_scale():
    box = hl._norm_block_bbox([100, 200, 300, 400], 800, 800,
                              (1000.0, 1000.0))
    assert box == (80, 160, 320, 480)


def test_bbox_pixel_scale():
    # pixels of a 2000×1000 OCR crop → onto a 1000×500 display crop
    # x: 500..900 /2000 → 0.25..0.45 → 250..450 ; y: 250..450 /1000 →
    # 0.25..0.45 → 125..225
    box = hl._norm_block_bbox([500, 250, 400, 200], 1000, 500,
                              (2000.0, 1000.0))
    assert box == (250, 125, 450, 225)


def test_bbox_none_scale_rejected():
    assert hl._norm_block_bbox([0.1, 0.2, 0.3, 0.4], 1000, 500, None) is None


def test_bbox_rejects_whole_crop():
    # a box covering essentially the entire crop carries no localization
    assert hl._norm_block_bbox([0.0, 0.0, 1.0, 1.0], 500, 500,
                               (1.0, 1.0)) is None


def test_bbox_rejects_degenerate():
    assert hl._norm_block_bbox([0.1, 0.1, 0, 0.2], 500, 500,
                               (1.0, 1.0)) is None
    assert hl._norm_block_bbox([1, 2, 3], 500, 500, (1.0, 1.0)) is None
    assert hl._norm_block_bbox(None, 500, 500, (1.0, 1.0)) is None


def test_bbox_clamps_rounding_overflow():
    # a hair past 1.0 (rounding) is accepted and clamped to the edge
    box = hl._norm_block_bbox([0.9, 0.9, 0.11, 0.11], 100, 100, (1.0, 1.0))
    assert box is not None and box[2] == 100 and box[3] == 100


def test_bbox_rejects_far_out_of_frame():
    # fractions well past 1.0 (wrong convention guess) → None
    assert hl._norm_block_bbox([0.9, 0.9, 0.5, 0.5], 100, 100,
                               (1.0, 1.0)) is None


# ── _block_box: match a block by text then use its bbox ────────────────

def test_block_box_matches_word():
    blocks = [
        {"text": "Guaranteed Analysis", "bbox": [0.0, 0.0, 0.5, 0.1]},
        {"text": "Cude", "bbox": [0.1, 0.2, 0.1, 0.05]},
    ]
    box = hl._block_box("Cude", blocks, 1000, 1000, ocr_wh=[3000, 2000])
    assert box == (100, 200, 200, 250)


def test_block_box_prefers_tightest_match():
    blocks = [
        {"text": "Cude Protein Line", "bbox": [0.0, 0.2, 0.9, 0.05]},
        {"text": "Cude", "bbox": [0.1, 0.2, 0.08, 0.05]},
    ]
    box = hl._block_box("Cude", blocks, 1000, 1000, ocr_wh=[3000, 2000])
    assert box[0] == 100          # picked the tight "Cude" block


def test_block_box_pixels_via_ocr_wh():
    blocks = [{"text": "Cude", "bbox": [200, 400, 100, 60]},
              {"text": "Edge", "bbox": [1900, 950, 40, 20]}]  # forces pixels
    box = hl._block_box("Cude", blocks, 1000, 500, ocr_wh=[2000, 1000])
    assert box == (100, 200, 150, 230)


def test_block_box_none_when_no_bbox():
    blocks = [{"text": "Cude", "bbox": None}]
    assert hl._block_box("Cude", blocks, 1000, 1000, ocr_wh=[3000, 2000]) is None
    assert hl._block_box("Cude", [], 1000, 1000, ocr_wh=[3000, 2000]) is None


# ── CV path (needs numpy) ─────────────────────────────────────────────

np = pytest.importorskip("numpy")


def _text_row_crop():
    """White 120×400 crop with two dark word-blobs on one row so the CV
    path can find row + word bands deterministically."""
    img = np.full((120, 400, 3), 255, np.uint8)
    img[40:80, 20:120] = 0     # word 0
    img[40:80, 200:300] = 0    # word 1
    return img


def test_cv_locates_second_word():
    crop = _text_row_crop()
    loc = {"line": 0, "n_lines": 1, "token": 1, "n_tokens": 2,
           "multi": False}
    box = hl._cv_box(crop, loc)
    assert box is not None
    x0, y0, x1, y1 = box
    # box should sit over the SECOND blob (x ~200..300), not the first
    assert 180 <= x0 <= 210
    assert 290 <= x1 <= 320


def test_cv_multi_boxes_full_row_width():
    crop = _text_row_crop()
    loc = {"line": 0, "n_lines": 1, "token": 0, "n_tokens": 2,
           "multi": True}
    box = hl._cv_box(crop, loc)
    assert box is not None
    x0, _, x1, _ = box
    assert x0 == 0 and x1 == 400      # whole-line band


def test_annotate_returns_same_shape_and_never_raises():
    crop = _text_row_crop()
    out = hl.annotate(crop, "WORDB", "WORDA WORDB\n")
    assert out.shape == crop.shape
    # unlocatable word → unchanged original (identity), never an exception
    out2 = hl.annotate(crop, "NOPE", "WORDA WORDB\n")
    assert out2 is crop
