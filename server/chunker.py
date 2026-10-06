"""Turns raw text into searchable chunks and handles huge sources with map-reduce."""
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import gemini_client, jobs, settings, vault


def chunk_segments(segments, target=900):
    """Group consecutive transcript segments; every chunk remembers its start time."""
    chunks, buf, start = [], [], None
    for s in segments:
        if start is None:
            start = s["start"]
        buf.append(s["text"])
        if sum(len(t) for t in buf) >= target:
            chunks.append({"text": " ".join(buf), "start": start, "page": None})
            buf, start = [], None
    if buf:
        chunks.append({"text": " ".join(buf), "start": start, "page": None})
    return chunks


def chunk_pages(pages, target=1000, overlap=150):
    """Split each page into overlapping windows; every chunk remembers its page number."""
    chunks = []
    for p in pages:
        text = re.sub(r"\n{3,}", "\n\n", p["text"]).strip()
        if len(text) <= target:
            chunks.append({"text": text, "page": p["page"], "start": None})
            continue
        pos = 0
        while pos < len(text):
            end = min(len(text), pos + target)
            if end < len(text):
                cut = max(text.rfind("\n", pos, end), text.rfind(". ", pos, end), text.rfind("। ", pos, end))
                if cut > pos + target // 2:
                    end = cut + 1
            chunks.append({"text": text[pos:end].strip(), "page": p["page"], "start": None})
            if end >= len(text):
                break
            pos = max(end - overlap, pos + 1)
    return [c for c in chunks if c["text"]]


def full_text(item_id):
    segs = vault.rows("SELECT page,text FROM segments WHERE item_id=? ORDER BY idx", (item_id,))
    out, last_page = [], None
    for s in segs:
        if s["page"] and s["page"] != last_page:
            out.append(f"[Page {s['page']}]")
            last_page = s["page"]
        out.append(s["text"])
    return "\n".join(out)


def split_blocks(text, size):
    blocks, pos = [], 0
    while pos < len(text):
        end = min(len(text), pos + size)
        if end < len(text):
            cut = max(text.rfind("\n", pos, end), text.rfind(". ", pos, end))
            if cut > pos + size // 2:
                end = cut + 1
        blocks.append(text[pos:end])
        pos = end
    return blocks


CONDENSE_PROMPT = (
    "Condense this part of a lecture/document into dense study notes. Keep EVERY key concept, "
    "definition, formula, number, name and example. Remove filler and repetition. Aim for roughly 35-40% of the "
    "original length. Keep the same language as the source. Output only the notes.\n\n---\n{block}"
)

_digest_locks = {}
_guard = threading.Lock()


def _lock_for(item_id):
    with _guard:
        return _digest_locks.setdefault(item_id, threading.Lock())


def _condense(block):
    return gemini_client.generate(CONDENSE_PROMPT.format(block=block), temperature=0.2, thinking="minimal", label="condense")


def digest(item_id, on_progress=None):
    """Map-reduce: condense every block in parallel (map), join, repeat only if still too long (reduce). Cached.
    A per-item lock guarantees two requests never condense the same document twice."""
    row = vault.one("SELECT digest FROM items WHERE id=?", (item_id,))
    if row and row["digest"]:
        return row["digest"]
    lock = _lock_for(item_id)
    if lock.locked():
        jobs.report("Waiting for the long document to be prepared", 0.1)
    with lock:
        row = vault.one("SELECT digest FROM items WHERE id=?", (item_id,))
        if row and row["digest"]:  # another thread finished it while we waited
            return row["digest"]
        text = full_text(item_id)
        limit = settings.DIRECT_LIMIT_CHARS
        for level in range(4):
            blocks = split_blocks(text, settings.BLOCK_CHARS)
            parts, done = [None] * len(blocks), 0
            with ThreadPoolExecutor(max_workers=settings.PARALLEL) as pool:
                futures = {pool.submit(_condense, b): i for i, b in enumerate(blocks)}
                for f in as_completed(futures):
                    parts[futures[f]] = f.result()
                    done += 1
                    msg = f"Condensing long document ({done}/{len(blocks)})" + (" - second pass" if level else "")
                    jobs.report(msg, 0.05 + 0.3 * done / len(blocks))
                    if on_progress:
                        on_progress(done / len(blocks), msg)
            text = "\n\n".join(parts)
            if len(text) <= limit:
                break
        text = text[: int(limit * 1.5)]
        vault.run("UPDATE items SET digest=? WHERE id=?", (text, item_id))
        return text


def context_for(item_id):
    """Text that is safe to send to the AI in one request."""
    text = full_text(item_id)
    if len(text) <= settings.DIRECT_LIMIT_CHARS:
        return text
    return digest(item_id)
