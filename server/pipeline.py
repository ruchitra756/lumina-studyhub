"""Background processing for one uploaded file: clean -> read/transcribe -> chunk -> index -> key facts."""
import threading
import traceback
from pathlib import Path

from . import chunker, finder, gemini_client, mediaflow, pagereader, recipes, settings, vault

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
TEXT_EXT = {".txt", ".md"}
ALLOWED = AUDIO_EXT | VIDEO_EXT | IMAGE_EXT | TEXT_EXT | {".pdf"}

_heavy = threading.Semaphore(1)  # only one transcription at a time (memory friendly)


def kind_of(ext):
    ext = ext.lower()
    if ext in AUDIO_EXT:
        return "audio"
    if ext in VIDEO_EXT:
        return "video"
    if ext in IMAGE_EXT:
        return "image"
    if ext == ".pdf":
        return "pdf"
    return "text"


_set = vault.update_item  # progress only ever moves forward


def source_path(item_id):
    item = vault.one("SELECT filename FROM items WHERE id=?", (item_id,))
    for f in settings.UPLOAD_DIR.glob(f"{item_id}.*"):
        return f
    return None


ACTIVE = set()
_active_lock = threading.Lock()


def is_active(item_id):
    return item_id in ACTIVE


def start(item_id, language=None, mode=None, denoise=None):
    """Start (or resume) processing in a background thread. Returns False if it is already running."""
    with _active_lock:
        if item_id in ACTIVE:
            return False
        ACTIVE.add(item_id)
    # flip to "processing" NOW, so the page never shows a stale "error" for a moment after Resume is pressed
    vault.update_item(item_id, status="processing", error=None, stage="Starting")
    threading.Thread(target=run, args=(item_id, language, mode, denoise), daemon=True).start()
    return True


def run(item_id, language=None, mode=None, denoise=None):
    with _active_lock:
        ACTIVE.add(item_id)
    try:
        _run(item_id, language, mode, denoise)
    finally:
        with _active_lock:
            ACTIVE.discard(item_id)


def _run(item_id, language=None, mode=None, denoise=None):
    try:
        item = vault.one("SELECT * FROM items WHERE id=?", (item_id,))
        path = source_path(item_id)
        if not path:
            raise RuntimeError("The uploaded file is missing on disk.")
        vault.run("UPDATE items SET progress=0 WHERE id=?", (item_id,))
        _set(item_id, status="processing", error=None, progress=0.02, stage="Starting")
        if item["kind"] not in ("audio", "video"):
            vault.run("DELETE FROM segments WHERE item_id=?", (item_id,))  # media keeps finished chunks so Resume works
        vault.run("DELETE FROM chunks WHERE item_id=?", (item_id,))
        vault.run("UPDATE items SET digest=NULL WHERE id=?", (item_id,))

        kind = item["kind"]
        if kind in ("audio", "video"):
            prefs = vault.one("SELECT mode_pref, denoise_pref, language FROM job_info WHERE item_id=?", (item_id,)) or {}
            chunks = mediaflow.run(item_id, path, language or prefs.get("language") or "auto",
                                   mode or prefs.get("mode_pref") or "auto", denoise or prefs.get("denoise_pref") or "auto")
        else:
            chunks = _do_document(item_id, path, kind)
        if not chunks:
            raise RuntimeError("No readable text was found in this file.")

        media = kind in ("audio", "video")
        i_lo, i_hi, d_lo, d_hi = (0.88, 0.93, 0.93, 0.98) if media else (0.6, 0.8, 0.8, 0.98)
        _set(item_id, stage="Building search index", progress=i_lo)
        indexed = finder.index_item(item_id, chunks, lambda p: _set(item_id, progress=i_lo + (i_hi - i_lo) * p))
        note = None if indexed else ("AI search is unavailable for this item, so keyword search is used instead. "
                                     "Check GEMINI_EMBED_MODEL in .env and upload the file again to fix it.")

        # Long documents are condensed ONCE here, with visible progress, so the first Notes click does not pay for it.
        if len(chunker.full_text(item_id)) > settings.DIRECT_LIMIT_CHARS:
            try:
                _set(item_id, stage="Condensing long document", progress=d_lo)
                chunker.digest(item_id, lambda f, msg: _set(item_id, stage=msg, progress=d_lo + (d_hi - d_lo) * f))
            except gemini_client.LLMError:
                pass  # not fatal: it is retried automatically on the first generation

        _set(item_id, status="ready", stage="Ready", progress=1.0, error=note)
        vault.log_event("upload", item_id)

        if settings.AUTO_FACTS:
            try:
                facts = recipes.keyfacts(chunker.context_for(item_id))
                vault.run("DELETE FROM facts WHERE item_id=?", (item_id,))
                vault.many("INSERT INTO facts(item_id,term,fact) VALUES(?,?,?)",
                           [(item_id, f.get("term", ""), f["fact"]) for f in facts])
            except Exception:
                pass  # key facts are a bonus; the item is already usable
    except mediaflow.Cancelled:
        return
    except Exception as exc:
        if not vault.one("SELECT id FROM items WHERE id=?", (item_id,)):
            return  # the item was deleted while it was being processed: nothing to report
        traceback.print_exc()
        msg = str(exc) if isinstance(exc, (RuntimeError, gemini_client.LLMError)) else f"{type(exc).__name__}: {exc}"
        _set(item_id, status="error", error=msg[:600], stage="Failed")


def _do_document(item_id, path, kind):
    _set(item_id, stage="Reading pages", progress=0.1)
    if kind == "pdf":
        pages = pagereader.read_pdf(path, lambda f, label: _set(item_id, stage=label, progress=0.1 + 0.5 * f))
    elif kind == "image":
        pages = pagereader.read_image(path)
    else:
        pages = pagereader.read_text(path)
    vault.many(
        "INSERT INTO segments(item_id,idx,start,end,page,text) VALUES(?,?,?,?,?,?)",
        [(item_id, i, None, None, p["page"], p["text"]) for i, p in enumerate(pages)],
    )
    return chunker.chunk_pages(pages)
