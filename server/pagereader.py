"""Reads PDFs (page by page), images and text files. Scanned pages / images are read by Gemini."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import gemini_client, settings

OCR_PROMPT = (
    "Transcribe ALL text in this image exactly, keeping the reading order. "
    "If there are diagrams or tables, describe them briefly in plain text. Output only the content."
)
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def read_pdf(path, on_progress=None):
    """Extract text page by page (fast). Pages with no text layer are scanned: those are read by Gemini in parallel.
    on_progress(fraction, label) is called as work completes."""
    import fitz  # PyMuPDF, lazy import
    doc = fitz.open(str(path))
    total = len(doc)
    texts, scans = {}, []
    for i, page in enumerate(doc):
        text = (page.get_text() or "").strip()
        texts[i + 1] = text
        if len(text) < 25:
            try:
                scans.append((i + 1, page.get_pixmap(dpi=140).tobytes("png")))
            except Exception:
                pass
        if on_progress and (i % 10 == 0 or i + 1 == total):
            on_progress(0.3 * (i + 1) / total, f"Reading pages ({i + 1}/{total})")
    doc.close()

    if scans:
        def ocr(item):
            try:
                return item[0], gemini_client.read_image(item[1], "image/png", OCR_PROMPT)
            except Exception:
                return item[0], ""
        done = 0
        with ThreadPoolExecutor(max_workers=settings.PARALLEL) as pool:
            for page_no, text in pool.map(ocr, scans):
                if text.strip():
                    texts[page_no] = text.strip()
                done += 1
                if on_progress:
                    on_progress(0.3 + 0.7 * done / len(scans), f"Reading scanned pages ({done}/{len(scans)})")
    return [{"page": n, "text": t} for n, t in sorted(texts.items()) if t.strip()]


def read_image(path):
    ext = Path(path).suffix.lower()
    text = gemini_client.read_image(Path(path).read_bytes(), MIME.get(ext, "image/png"), OCR_PROMPT)
    return [{"page": 1, "text": text}] if text.strip() else []


def read_text(path):
    return [{"page": 1, "text": Path(path).read_text(encoding="utf-8", errors="ignore")}]
