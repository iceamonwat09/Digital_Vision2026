"""
Artwork Proof Check (ตรวจ Artwork ก่อนสั่งพิมพ์).

A self-contained mode that inspects printing-master artwork PDFs for
spelling mistakes, inconsistent repeated panels, wrong numbers/weights
and bad barcode check digits — WITHOUT inventing or suggesting words.

Design rules (agreed with the user):
  * Completely isolated from the existing Can Dent / Label / Label Paper
    modes. Nothing in ``inspectors/`` or ``modes/`` is modified; the only
    integration points are a guarded blueprint registration in ``app.py``
    and one nav link in ``base.html``.
  * OCR goes through the same N8N webhook (``inspectors.vertex_client``
    dispatcher) used by Label Paper — imported read-only.
  * Every finding is a *flag for human review*, never an auto-correction.
"""
