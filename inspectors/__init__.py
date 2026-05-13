"""
Label Paper inspection pipeline.

Modules:
  master_loader     — load SKU master (PDF text + spec.json)
  master_renderer   — render PDF master to high-DPI RGB (with on-disk cache)
  calibration       — gray-world / white-patch AWB
  registration      — resize + ECC alignment of captured photo to master
  deltae_map        — per-pixel ΔE2000 + defect clustering
  overlay           — heatmap PNG overlay generator
  text_compare      — field-aware exact / Levenshtein / regex matcher
  color_compare     — named-brand color ΔE check
  vertex_client     — Vertex Document AI + Gemini wrappers (stubbed)
  label_pipeline    — orchestrates the full inspection flow
"""
