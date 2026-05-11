"""
Label Paper inspection pipeline.

Modules:
  master_loader  — load SKU master (PDF text + spec.json)
  text_compare   — field-aware exact / Levenshtein / regex matcher
  color_compare  — Delta E color check (stub-grade in Phase 1)
  vertex_client  — Vertex Document AI + Gemini wrappers (stubbed in Phase 1)
  label_pipeline — orchestrates the full inspection flow
"""
