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
    # profile path explicitly (tesseract off) so the test is deterministic
    out = hl.annotate(crop, "WORDB", "WORDA WORDB\n",
                      use_tesseract=False, use_profile=True)
    assert out.shape == crop.shape
    # unlocatable word → unchanged original (identity), never an exception
    out2 = hl.annotate(crop, "NOPE", "WORDA WORDB\n",
                       use_tesseract=False, use_profile=True)
    assert out2 is crop


def test_locate_profile_off_by_default_needs_no_box():
    # with tesseract off and profile off (defaults), the profile path is
    # not used → no box from the CV strategy even if the word is in text
    crop = _text_row_crop()
    box = hl.locate(crop, "WORDB", "WORDA WORDB\n",
                    use_tesseract=False, use_profile=False)
    assert box is None


# ── Tesseract path (needs the binary) ─────────────────────────────────

def _tess_or_skip():
    if not hl._tesseract_available():
        pytest.skip("tesseract binary/pytesseract not installed")


def _pil_text_crop():
    """Render real anti-aliased text so tesseract can read it."""
    from PIL import Image, ImageDraw, ImageFont
    import cv2
    for fp in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
               "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
        try:
            font = ImageFont.truetype(fp, 34)
            break
        except OSError:
            font = None
    if font is None:
        pytest.skip("no truetype font available")
    img = Image.new("RGB", (520, 90), (255, 255, 255))
    dr = ImageDraw.Draw(img)
    dr.text((20, 25), "Protein", font=font, fill=(0, 0, 0))
    x2 = 20 + dr.textlength("Protein ", font=font)
    lt, tt, rt, bt = font.getbbox("Phosphours")
    true_box = (int(x2 + lt), int(25 + tt), int(x2 + rt), int(25 + bt))
    dr.text((x2, 25), "Phosphours", font=font, fill=(0, 0, 0))
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR), true_box


def test_tesseract_locates_word():
    _tess_or_skip()
    crop, tb = _pil_text_crop()
    box = hl._tess_box(crop, "Phosphours")
    assert box is not None
    # overlaps the true word box (IoU > 0.3)
    ix0, iy0 = max(box[0], tb[0]), max(box[1], tb[1])
    ix1, iy1 = min(box[2], tb[2]), min(box[3], tb[3])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    ua = ((box[2]-box[0])*(box[3]-box[1]) + (tb[2]-tb[0])*(tb[3]-tb[1])
          - inter)
    assert inter / ua > 0.3


def test_tesseract_missing_word_returns_none():
    _tess_or_skip()
    crop, _ = _pil_text_crop()
    assert hl._tess_box(crop, "Zzzqqq") is None


# ── tesseract executable auto-detect ──────────────────────────────────

def test_find_tesseract_env_override_wins(tmp_path, monkeypatch):
    fake = tmp_path / "tesseract.exe"
    fake.write_text("")
    monkeypatch.setenv("ARTWORK_TESSERACT_CMD", str(fake))
    assert hl._find_tesseract_cmd() == str(fake)


def test_find_tesseract_bogus_override_ignored(monkeypatch):
    # a non-existent override path must be ignored, not returned
    monkeypatch.setenv("ARTWORK_TESSERACT_CMD",
                       "/no/such/tesseract-binary-xyz")
    got = hl._find_tesseract_cmd()
    assert got != "/no/such/tesseract-binary-xyz"


def test_find_tesseract_uses_path_when_present(monkeypatch):
    import shutil as _sh
    monkeypatch.delenv("ARTWORK_TESSERACT_CMD", raising=False)
    which = _sh.which("tesseract")
    if not which:
        pytest.skip("tesseract not on PATH in this environment")
    assert hl._find_tesseract_cmd() == which


# ── language string resolution (the eng+ara+tha safety net) ───────────

def _installed_langs():
    _tess_or_skip()
    import pytesseract
    return set(pytesseract.get_languages(config="") or [])


def test_resolve_langs_drops_uninstalled():
    avail = _installed_langs()
    # a bogus language must never survive → can't make tesseract raise
    got = hl._resolve_langs("eng+zzznotalang").split("+")
    assert "zzznotalang" not in got
    assert got and all(g in avail for g in got)
    if "eng" in avail:
        assert "eng" in got


def test_resolve_langs_all_unknown_falls_back_to_installed():
    avail = _installed_langs()
    got = hl._resolve_langs("zzz111+qqq222").split("+")
    # never empty, never a bogus code → always something tesseract can load
    assert got and all(g in avail for g in got)


def test_resolve_langs_keeps_installed_combo():
    avail = _installed_langs()
    if not {"eng", "ara"} <= avail:
        pytest.skip("needs eng+ara installed")
    got = set(hl._resolve_langs("eng+ara").split("+"))
    assert got == {"eng", "ara"}


# ── matching guard: no wrong box on short / CJK words ─────────────────

def test_match_exact_and_substring():
    words = [("WATER", "W"), ("SALT", "S"), ("SUNFLOWEROIL", "O")]
    assert hl._best_word_match(words, "WATER") == "W"
    assert hl._best_word_match(words, "SUNFLOWER") == "O"   # substring


def test_match_fuzzy_only_long_ascii():
    # a long ascii typo still fuzzy-matches (the defect we want to catch)
    assert hl._best_word_match([("SHREDDED", "x")], "SHREDED") == "x"
    # a SHORT ascii word does NOT fuzzy (len < 5) → no wrong box
    assert hl._best_word_match([("FISH", "x")], "DISH") is None


def test_match_no_fuzzy_for_cjk():
    # 灰分 vs 水分 differ by one glyph but are DIFFERENT words → must NOT
    # match (this is the real 'wrong box' bug the guard prevents)
    assert hl._best_word_match([("水分", "moist")], "灰分") is None
    # exact CJK still matches
    assert hl._best_word_match([("水分", "moist")], "水分") == "moist"


# ── PDF-text layer: fraction-box helpers ──────────────────────────────

def test_rotate_frac_box_all_angles():
    b = (0.1, 0.2, 0.3, 0.4)
    assert hl.rotate_frac_box(b, 0) == b
    assert hl.rotate_frac_box(b, 90) == pytest.approx((0.6, 0.1, 0.8, 0.3))
    assert hl.rotate_frac_box(b, 180) == pytest.approx((0.7, 0.6, 0.9, 0.8))
    assert hl.rotate_frac_box(b, 270) == pytest.approx((0.2, 0.7, 0.4, 0.9))


def test_frac_to_px_scales_and_guards():
    assert hl.frac_to_px((0.1, 0.2, 0.3, 0.5), 1000, 400) == (100, 80, 300, 200)
    # basically the whole crop → rejected (no localization value)
    assert hl.frac_to_px((0.0, 0.0, 1.0, 1.0), 500, 500) is None
    # degenerate → rejected
    assert hl.frac_to_px((0.5, 0.5, 0.5005, 0.5005), 500, 500) is None


def test_match_word_box_uses_fraction_payload():
    words = [("COMPOSITION:", (0.1, 0.2, 0.3, 0.24)),
             ("Chicken", (0.1, 0.3, 0.2, 0.34))]
    fb = hl.match_word_box(words, "Chicken")
    assert fb == (0.1, 0.3, 0.2, 0.34)
    assert hl.match_word_box(words, "Salmon") is None


# ── all-occurrences matching (a typo repeats across table rows) ───────

def test_all_matches_returns_every_occurrence():
    words = [("CUDE", "a"), ("PROTEIN", "p"), ("CUDE", "b"), ("CUDE", "c")]
    assert hl._all_word_matches(words, "CUDE") == ["a", "b", "c"]
    assert hl._best_word_match(words, "CUDE") == "a"   # single-box API


def test_all_matches_one_tier_only():
    # a literal hit exists → merely-similar words must NOT be added
    words = [("SHREDDED", "exact"), ("SHREDED", "typo")]
    assert hl._all_word_matches(words, "SHREDDED") == ["exact"]


def test_all_matches_fuzzy_tier_when_no_literal():
    words = [("SHREDED", "typo"), ("WATER", "w")]
    assert hl._all_word_matches(words, "SHREDDED") == ["typo"]


def test_match_word_boxes_all_fractions():
    words = [("Cude", (0.1, 0.1, 0.2, 0.14)), ("Fat", (0.3, 0.1, 0.4, 0.14)),
             ("Cude", (0.1, 0.2, 0.2, 0.24))]
    got = hl.match_word_boxes(words, "Cude")
    assert got == [(0.1, 0.1, 0.2, 0.14), (0.1, 0.2, 0.2, 0.24)]


def test_dedupe_overlapping_boxes():
    a = (10, 10, 50, 30)
    near = (11, 11, 51, 31)        # same word, tight key + containing line
    far = (10, 100, 50, 120)
    assert hl._dedupe_boxes([a, near, far]) == [a, far]


def test_dedupe_drops_nested_box():
    tight = (10, 10, 50, 30)
    wrapping = (5, 8, 90, 34)      # phrase window that contains the tight one
    assert hl._dedupe_boxes([tight, wrapping]) == [tight]


# ── multi-word target must stay on ONE line (no scattered boxes) ──────
# A MISMATCH defect reports a whole LINE as `found`. Matching its words
# independently used to box every row repeating any of them.

def _row(words, y, x0=0, wdt=40, hgt=20):
    """[(key, box)] for one text row at height y."""
    return [(w, (x0 + i * (wdt + 5), y, x0 + i * (wdt + 5) + wdt, y + hgt))
            for i, w in enumerate(words)]


def test_phrase_matches_only_same_line_run():
    words = (_row(["TOTAL", "FAT"], 10)
             + _row(["SATURATED", "FAT"], 60)
             + _row(["TOTAL", "CARBOHYDRATE"], 110))
    got = hl._match_boxes(words, "Total fat")
    assert len(got) == 1
    assert got[0][1] == 10          # the y of the first row only


def test_phrase_does_not_box_rows_sharing_one_word():
    # "FAT" appears on 2 rows and "TOTAL" on 2 rows — a word-by-word match
    # would light up 4 boxes; the phrase must yield exactly its own row.
    words = (_row(["TOTAL", "FAT"], 10)
             + _row(["SATURATED", "FAT"], 60)
             + _row(["TOTAL", "CARBOHYDRATE"], 110))
    assert len(hl._match_boxes(words, "Total carbohydrate")) == 1
    assert hl._match_boxes(words, "Total carbohydrate")[0][1] == 110


def test_phrase_box_spans_all_its_words():
    words = _row(["TOTAL", "FAT"], 10)      # boxes 0-40 and 45-85
    got = hl._match_boxes(words, "Total fat")
    assert got[0][0] == 0 and got[0][2] == 85


def test_phrase_tolerates_one_dropped_letter():
    # real case: Arabic كربوهيدرات was OCR'd as كربوهيدات (one letter lost)
    words = _row(["كربوهيدات", "كلية"], 10) + _row(["دهون", "كلية"], 60)
    got = hl._match_boxes(words, "كربوهيدرات كلية")
    assert len(got) == 1 and got[0][1] == 10


def test_phrase_rejects_a_different_line():
    words = _row(["SUNFLOWER", "OIL"], 10)
    assert hl._match_boxes(words, "OLIVE OIL EXTRA VIRGIN") == []


def test_short_and_numeric_targets_need_exact_match():
    # "17" must NOT match the Calories value "170" (different row!) and
    # "24" must not match "240" — substring matching on short/numeric
    # targets points the reviewer at the wrong cell.
    words = _row(["170"], 10) + _row(["17"], 60) + _row(["240"], 110)
    got = hl._match_boxes(words, "17%")
    assert len(got) == 1 and got[0][1] == 60
    assert hl._match_boxes(words, "24%") == []


def test_long_word_keeps_substring_match():
    words = _row(["CUDE"], 10) + _row(["SUNFLOWEROIL"], 60)
    assert len(hl._match_boxes(words, "Cude:")) == 1
    assert len(hl._match_boxes(words, "Sunflower")) == 1


def test_single_word_still_matches_every_row():
    words = (_row(["CUDE", "PROTEIN"], 10)
             + _row(["CUDE", "FAT"], 60)
             + _row(["ASH"], 110))
    got = hl._match_boxes(words, "Cude")
    assert len(got) == 2
    assert [b[1] for b in got] == [10, 60]


def test_backend_bbox_is_not_trusted_over_tesseract():
    """A vision-LLM bbox is an estimate. When it points at the wrong place
    the local measurement must win — this is the bug that made real boxes
    land a row off on the station while every test (which passed
    blocks=[]) looked fine."""
    _tess_or_skip()
    crop, tb = _pil_text_crop()          # "Protein Phosphours" rendered
    H, W = crop.shape[:2]
    # backend claims the word is in the far bottom-left corner
    wrong = [{"text": "Phosphours", "bbox": [0.02, 0.80, 0.10, 0.15],
              "conf": 0.9}]
    got = hl.locate_all(crop, "Phosphours", "", blocks=wrong,
                        ocr_wh=[W, H], use_tesseract=True)
    assert got, "should still produce a box (via tesseract)"
    # the box must sit on the real word, not where the backend claimed
    cx = (got[0][0] + got[0][2]) / 2
    assert tb[0] - 20 <= cx <= tb[2] + 20


def test_merge_words_dedupes_same_hit():
    a = [("SODIUM", (10, 10, 60, 30))]
    b = [("SODIUM", (11, 11, 61, 31)), ("PROTEIN", (10, 90, 70, 110))]
    got = hl._merge_words(a, b)
    assert len(got) == 2                      # the duplicate SODIUM dropped
    assert ("PROTEIN", (10, 90, 70, 110)) in got


def test_row_refine_needs_ocr_text_and_anchor():
    crop = np.full((200, 300, 3), 255, np.uint8)
    # no ocr_text → cannot know which row the word is on
    assert hl._row_refine(crop, "24%", "", "eng", [("SODIUM", (5, 5, 50, 20))]) == []
    # ocr_text without the target → nothing to anchor on
    assert hl._row_refine(crop, "24%", "Protein 26 g", "eng",
                          [("SODIUM", (5, 5, 50, 20))]) == []


def test_row_refine_rejects_ambiguous_anchor():
    # the anchor word appears twice → it cannot pin a single row, so the
    # refine must decline rather than guess
    crop = np.full((200, 300, 3), 255, np.uint8)
    words = [("TOTAL", (5, 5, 50, 20)), ("TOTAL", (5, 100, 50, 115))]
    assert hl._row_refine(crop, "24%", "Total fat 24%", "eng", words) == []


def test_backend_bbox_box_on_blank_area_is_dropped():
    """An LLM-estimated box that lands on empty space must never be drawn:
    nothing there can prove it holds the word."""
    _tess_or_skip()
    crop = np.full((120, 300, 3), 255, np.uint8)
    box = [(20, 20, 90, 50)]
    assert hl._verify_boxes(crop, box, "Sodium", "eng",
                            require_positive=True) == []


def test_measured_box_kept_for_non_latin_target():
    """The re-read of a tight non-Latin crop is not trustworthy enough to
    overrule a measured box (a correct Arabic box once re-read as
    'Yoda كلية'), so such boxes are kept."""
    _tess_or_skip()
    crop = np.full((120, 300, 3), 255, np.uint8)
    box = [(20, 20, 90, 50)]
    assert hl._verify_boxes(crop, box, "صوديوم", "eng+ara") == box


def test_backend_bbox_used_when_tesseract_unavailable():
    # without tesseract the backend box is all we have — it must still work
    crop = np.full((400, 400, 3), 255, np.uint8)
    blocks = [{"text": "Phosphours", "bbox": [0.1, 0.1, 0.2, 0.05]}]
    got = hl.locate_all(crop, "Phosphours", "", blocks=blocks,
                        ocr_wh=[400, 400], use_tesseract=False)
    assert len(got) == 1


def test_locate_all_respects_max_boxes():
    blocks = [{"text": "Cude", "bbox": [0.1, 0.1, 0.1, 0.05]},
              {"text": "Cude", "bbox": [0.1, 0.3, 0.1, 0.05]},
              {"text": "Cude", "bbox": [0.1, 0.5, 0.1, 0.05]}]
    crop = np.full((400, 400, 3), 255, np.uint8)
    allb = hl.locate_all(crop, "Cude", "", blocks=blocks, ocr_wh=[400, 400],
                         use_tesseract=False)
    assert len(allb) == 3
    capped = hl.locate_all(crop, "Cude", "", blocks=blocks,
                           ocr_wh=[400, 400], use_tesseract=False,
                           max_boxes=2)
    assert len(capped) == 2
    # single-box API still returns exactly one
    assert hl.locate(crop, "Cude", "", blocks=blocks, ocr_wh=[400, 400],
                     use_tesseract=False) == allb[0]


def test_draw_boxes_marks_all_and_keeps_shape():
    crop = np.full((200, 300, 3), 255, np.uint8)
    boxes = [(10, 10, 60, 40), (10, 100, 60, 130)]
    out = hl.draw_boxes(crop, boxes)
    assert out.shape == crop.shape
    # both regions changed (red drawn), a third untouched region did not
    assert not np.array_equal(out[10:40, 10:60], crop[10:40, 10:60])
    assert not np.array_equal(out[100:130, 10:60], crop[100:130, 10:60])
    assert np.array_equal(out[160:190, 200:290], crop[160:190, 200:290])


def test_annotate_default_is_single_box():
    # default max_boxes=1 keeps the previous one-box behavior
    blocks = [{"text": "Cude", "bbox": [0.1, 0.1, 0.1, 0.05]},
              {"text": "Cude", "bbox": [0.1, 0.5, 0.1, 0.05]}]
    crop = np.full((400, 400, 3), 255, np.uint8)
    one = hl.annotate(crop, "Cude", "", blocks=blocks, ocr_wh=[400, 400],
                      use_tesseract=False)
    many = hl.annotate(crop, "Cude", "", blocks=blocks, ocr_wh=[400, 400],
                       use_tesseract=False, max_boxes=6)
    assert one.shape == crop.shape and many.shape == crop.shape
    # the second occurrence is only marked when several boxes are allowed
    y0, y1, x0, x1 = 200, 220, 40, 80
    assert np.array_equal(one[y0:y1, x0:x1], crop[y0:y1, x0:x1])
    assert not np.array_equal(many[y0:y1, x0:x1], crop[y0:y1, x0:x1])


# ── zone_words: real PDF text-layer extraction ────────────────────────

def test_zone_words_on_synthetic_pdf(tmp_path):
    fitz = pytest.importorskip("fitz")
    from artwork_check.pdf_ingest import ArtworkDocument
    p = tmp_path / "mini.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((40, 60), "Cholesterol Sodium", fontsize=18)
    doc.save(str(p)); doc.close()

    art = ArtworkDocument(str(p))
    assert art.is_pdf
    words = art.zone_words([0.0, 0.0, 1.0, 1.0])   # whole page
    texts = [t for t, _ in words]
    assert "Cholesterol" in texts and "Sodium" in texts
    # a word box must be a 0..1 fraction inside the zone
    for _, (fx0, fy0, fx1, fy1) in words:
        assert 0.0 <= fx0 < fx1 <= 1.05 and 0.0 <= fy0 < fy1 <= 1.05


def test_zone_words_empty_for_image(tmp_path):
    import numpy as np
    import cv2
    from artwork_check.pdf_ingest import ArtworkDocument
    ip = tmp_path / "img.png"
    cv2.imwrite(str(ip), np.full((80, 200, 3), 255, np.uint8))
    art = ArtworkDocument(str(ip))
    assert art.is_pdf is False
    assert art.zone_words([0.0, 0.0, 1.0, 1.0]) == []
