"""PyMuPDF helpers.

COPIED subset from ANN-RPT-ANALYZER\\WEB-APP\\ingest\\pdf_utils.py (open_pdf,
PdfError, clean_text) and ingest\\ocr_transcribe.py (is_scanned, the page
renderer) on 2026-09-29 (plan §2). PyMuPDF is imported as `pymupdf`, not
`fitz`: a conflicting `fitz` package is installed on this machine.
"""

import threading
import unicodedata

import pymupdf

SCANNED_TEXT_SHARE = 0.5          # fewer text pages than this share -> treat as scanned
SYNTHETIC_MARK = "SYNTHETIC TEST DATA"

pdf_lock = threading.Lock()       # PyMuPDF is not thread-safe


class PdfError(Exception):
    """A PDF that can't be read (missing, encrypted, corrupt)."""


def open_pdf(path):
    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # pymupdf raises several exception types
        raise PdfError(f"Could not open PDF: {exc}") from exc
    if doc.needs_pass:
        doc.close()
        raise PdfError("This PDF is password-protected and can't be read.")
    if doc.page_count == 0:
        doc.close()
        raise PdfError("This PDF has no pages.")
    return doc


def clean_text(text):
    return unicodedata.normalize("NFKC", text)


def is_scanned(doc):
    text_pages = sum(1 for page in doc if len(page.get_text().strip()) > 20)
    return text_pages < max(1, int(doc.page_count * SCANNED_TEXT_SHARE))


def is_synthetic(doc):
    meta = doc.metadata or {}
    return SYNTHETIC_MARK in (meta.get("keywords") or "") or SYNTHETIC_MARK in (meta.get("subject") or "")


def render_page(doc, pno, target, dpi, gray=True):
    """Save page pno (1-based) as PNG."""
    target.parent.mkdir(parents=True, exist_ok=True)
    cs = pymupdf.csGRAY if gray else pymupdf.csRGB
    doc[pno - 1].get_pixmap(dpi=dpi, colorspace=cs).save(str(target))
    return target
