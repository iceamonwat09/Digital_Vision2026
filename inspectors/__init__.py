"""
Label Paper inspection pipeline.

Modules:
  master_loader     — load SKU master (PDF text + spec.json)
  master_renderer   — render PDF master to high-DPI RGB (with on-disk cache)
  master_ocr        — OCR the rendered master for symmetric comparison (Phase 1)
  calibration       — gray-world / white-patch AWB
  registration      — ORB homography + ECC affine alignment (Phase 3)
  deltae_map        — per-pixel ΔE2000 + defect clustering
  overlay           — heatmap PNG overlay generator
  block_match       — spatial + textual OCR block matching (Phase 2)
  text_compare      — field-aware exact / Levenshtein / regex matcher
  color_compare     — named-brand CIE2000 ΔE check + spatial sampling (Phase 5)
  vertex_client     — Vertex Document AI + Gemini wrappers (stubbed)
  visual_diff       — Gemini master↔captured diff with block-diff context (Phase 4)
  label_pipeline    — end-to-end orchestrator (Phase 1–5)
"""
