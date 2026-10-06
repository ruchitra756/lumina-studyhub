"""Long video/audio -> transcript, as a chunked, resumable, cached job.

  probe -> extract audio ONCE (fast) -> split at quiet moments -> transcribe chunk by chunk
  (Gemini: several chunks at once / local Whisper: one at a time) -> every finished chunk is saved immediately.

Because each chunk is saved, progress is real, an ETA can be measured, a crash or restart loses at most one chunk,
and 'Resume' continues where it stopped. The transcript lives in the database, so no study feature ever re-transcribes."""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import chunker, ears, gemini_client, settings, vault

HEAVY = threading.Semaphore(1)       # only one CPU-heavy job (extraction / local Whisper) at a time
CANCELLED = set()
PROGRESS_FROM, PROGRESS_TO = 0.12, 0.88   # share of the bar that transcription owns (extraction owns 0.02-0.12)


class Cancelled(Exception):
    pass


def cancel(item_id):
    CANCELLED.add(item_id)


def _check(item_id):
    if item_id in CANCELLED or not vault.one("SELECT id FROM items WHERE id=?", (item_id,)):
        raise Cancelled()


def choose_engine(mode, duration):
    mode = (mode or settings.TRANSCRIBE_MODE or "auto").lower()
    if mode in ("local", "gemini"):
        return mode
    return "local" if duration <= settings.LOCAL_MAX_MINUTES * 60 else "gemini"


def _set_progress(item_id, done_seconds, total, stage):
    frac = min(1.0, done_seconds / total) if total else 0
    vault.update_item(item_id, stage=stage, progress=PROGRESS_FROM + (PROGRESS_TO - PROGRESS_FROM) * frac)
    vault.update_job(item_id, done_seconds=done_seconds)


# ---------------- Gemini route ----------------
GEMINI_PROMPT = (
    "Transcribe this lecture audio accurately and completely. Write exactly what is said, in the language it is spoken: "
    "Hindi in Devanagari script, English in Latin script, mixed speech as spoken. Never translate and never summarize. "
    "Leave out silence and filler sounds. Fix obvious word-recognition mistakes only when the correct word is clear. "
    "Return a JSON array covering the whole recording in order, one object per 5-25 seconds of speech: "
    '[{"start": "MM:SS", "text": "..."}]. "start" is the time in THIS recording where that text begins. '
    "If there is no speech, return []."
)
TS = re.compile(r"^(?:(\d+):)?(\d{1,3}):(\d{2})(?:\.\d+)?$")


def _seconds(v):
    if isinstance(v, (int, float)):
        return float(v)
    m = TS.match(str(v).strip())
    if not m:
        return None
    return int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))


def to_segments(data, length, offset):
    """Model JSON -> [{start,end,text}] with ABSOLUTE times. Bad/missing timestamps fall back to even spacing by text length."""
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [])
    items = []
    for d in data if isinstance(data, list) else []:
        if isinstance(d, dict):
            text = str(d.get("text") or d.get("transcript") or "").strip()
            if text:
                items.append((_seconds(d.get("start", d.get("time", d.get("timestamp")))), text))
        elif isinstance(d, str) and d.strip():
            items.append((None, d.strip()))
    if not items:
        return []
    starts = [s for s, _ in items]
    ok = all(s is not None for s in starts) and all(b >= a - 1 for a, b in zip(starts, starts[1:])) and max(starts) <= length + 5
    if not ok:
        total = sum(len(t) for _, t in items) or 1
        acc, starts = 0, []
        for _, t in items:
            starts.append(length * acc / total)
            acc += len(t)
    segs, prev = [], None
    for i, ((_, text), st) in enumerate(zip(items, starts)):
        st = max(0.0, min(float(st), length))
        en = float(starts[i + 1]) if i + 1 < len(starts) else length
        en = max(st, min(en, length))
        if prev is not None and text == prev[0]:
            prev[1] += 1
            if prev[1] > 2:
                continue  # a repetition loop: keep two copies at most
        else:
            prev = [text, 1]
        segs.append({"start": offset + st, "end": offset + en, "text": text})
    return segs


def _gemini_chunk(item_id, wav, c, language):
    _check(item_id)
    audio = ears.encode_flac(wav, c["start"], c["end"])
    if len(audio) > 14 * 1024 * 1024:
        raise RuntimeError("An audio part is too large to send (over 14 MB). Lower CLOUD_CHUNK_MINUTES in .env.")
    hint = {"hi": " The speaker mainly speaks Hindi.", "en": " The speaker mainly speaks English."}.get(language, "")
    last = None
    for _ in range(2):
        try:
            data = gemini_client.parse_json(gemini_client.transcribe_audio(audio, "audio/flac", GEMINI_PROMPT + hint))
            return to_segments(data, c["end"] - c["start"], c["start"])
        except gemini_client.LLMError as exc:
            if isinstance(exc, (gemini_client.BusyError, gemini_client.ModelMissing)) or "not valid JSON" not in str(exc):
                raise
            last = exc
    raise gemini_client.LLMError("The AI returned an unreadable transcript for one part. " + str(last))


def guess_language(text):
    sample = text[:4000]
    letters = [ch for ch in sample if ch.isalpha()]
    if not letters:
        return None
    dev = sum("\u0900" <= ch <= "\u097F" for ch in letters)
    return "hi" if dev / len(letters) > 0.3 else "en"


# ---------------- shared bookkeeping ----------------
def _save_chunk(item_id, c, segs, engine):
    base = c["idx"] * 100000
    vault.run("DELETE FROM segments WHERE item_id=? AND idx>=? AND idx<?", (item_id, base, base + 100000))
    vault.many("INSERT INTO segments(item_id,idx,start,end,page,text) VALUES(?,?,?,?,?,?)",
               [(item_id, base + k, s["start"], s["end"], None, s["text"]) for k, s in enumerate(segs)])
    vault.run("UPDATE chunk_plan SET status='done', engine=?, error=NULL WHERE item_id=? AND idx=?", (engine, item_id, c["idx"]))


def _plan(item_id, wav, duration, engine):
    rows = vault.rows("SELECT idx,start,end,status FROM chunk_plan WHERE item_id=? ORDER BY idx", (item_id,))
    if rows:
        return rows
    minutes = settings.CLOUD_CHUNK_MINUTES if engine == "gemini" else settings.LOCAL_CHUNK_MINUTES
    spans = ears.plan_chunks(wav, duration, minutes * 60)
    vault.many("INSERT INTO chunk_plan(item_id,idx,start,end,status) VALUES(?,?,?,?, 'pending')",
               [(item_id, i, s, e) for i, (s, e) in enumerate(spans)])
    return vault.rows("SELECT idx,start,end,status FROM chunk_plan WHERE item_id=? ORDER BY idx", (item_id,))


# ---------------- main entry ----------------
def run(item_id, path, language="auto", mode=None, denoise="auto"):
    """Returns transcript segments as dicts. Raises RuntimeError with a plain-English message on failure."""
    CANCELLED.discard(item_id)
    language = language or "auto"
    vault.update_job(item_id, mode_pref=mode or "auto", denoise_pref=denoise or "auto", language=language)

    vault.update_item(item_id, stage="Checking the file", progress=0.02)
    duration = ears.probe(path)
    engine = choose_engine(mode, duration)
    use_denoise = denoise == "on" or (denoise in (None, "", "auto") and duration <= settings.DENOISE_AUTO_MAX_MINUTES * 60)
    vault.update_item(item_id, duration=duration)
    vault.update_job(item_id, engine=engine, media_seconds=duration, denoise_used=int(use_denoise))

    wav = settings.CACHE_DIR / f"{item_id}.wav"
    if not ears.valid_wav(wav, duration):
        label = "Extracting audio" + (" with noise reduction" if use_denoise else "")
        with HEAVY:
            _check(item_id)
            ears.extract_audio(path, wav, use_denoise, duration, lambda f: (
                vault.update_item(item_id, stage=f"{label} ({int(f * 100)}%)", progress=0.02 + 0.10 * f),
                vault.update_job(item_id)))
    _check(item_id)

    plan = _plan(item_id, wav, duration, engine)
    pending = [c for c in plan if c["status"] != "done"]
    done_seconds = sum(c["end"] - c["start"] for c in plan if c["status"] == "done")
    vault.update_job(item_id, chunks_total=len(plan), chunks_done=len(plan) - len(pending),
                     done_seconds=done_seconds, done_at_start=done_seconds, t_start=time.time())
    total = len(plan)

    if engine == "gemini":
        errors = _run_gemini(item_id, wav, pending, language, total, done_seconds, duration)
    else:
        errors = _run_local(item_id, wav, pending, language, total, done_seconds, duration)

    if errors:
        done = vault.one("SELECT COUNT(*) n FROM chunk_plan WHERE item_id=? AND status='done'", (item_id,))["n"]
        first = next(iter(errors.values()))
        raise RuntimeError(f"{len(errors)} of {total} parts could not be transcribed: {first} "
                           f"Your {done} finished parts are saved. Press Resume to continue from where it stopped.")

    rows = vault.rows("SELECT start,end,text FROM segments WHERE item_id=? ORDER BY idx", (item_id,))
    info = vault.one("SELECT lang_detected FROM job_info WHERE item_id=?", (item_id,)) or {}
    lang = info.get("lang_detected") or guess_language(" ".join(r["text"] for r in rows)) or language
    vault.update_item(item_id, language=lang, duration=duration)
    try:
        wav.unlink()
    except OSError:
        pass
    return chunker.chunk_segments(rows) if rows else []


def _run_gemini(item_id, wav, pending, language, total, done_seconds, duration):
    errors, finished = {}, [done_seconds, total - len(pending)]
    stage = lambda: f"Transcribing with Gemini ({finished[1]}/{total} parts done)"
    _set_progress(item_id, done_seconds, duration, stage())
    with ThreadPoolExecutor(max_workers=settings.PARALLEL) as pool:
        futs = {pool.submit(_gemini_chunk, item_id, wav, c, language): c for c in pending}
        for f in as_completed(futs):
            c = futs[f]
            try:
                segs = f.result()
            except Cancelled:
                raise
            except Exception as exc:
                msg = str(exc)
                errors[c["idx"]] = msg
                vault.run("UPDATE chunk_plan SET status='failed', error=? WHERE item_id=? AND idx=?", (msg[:300], item_id, c["idx"]))
                continue
            _save_chunk(item_id, c, segs, "gemini")
            finished[0] += c["end"] - c["start"]; finished[1] += 1
            vault.update_job(item_id, chunks_done=finished[1])
            _set_progress(item_id, finished[0], duration, stage())
    return errors


def _run_local(item_id, wav, pending, language, total, done_seconds, duration):
    errors, done_parts = {}, total - len(pending)
    with HEAVY:
        if not ears.model_loaded():
            vault.update_item(item_id, stage="Loading the speech model (the first run downloads it)")
        ears._get_model()
        for c in pending:
            _check(item_id)
            base = done_seconds
            stage = f"Transcribing on this computer (part {done_parts + 1}/{total})"
            _set_progress(item_id, base, duration, stage)
            try:
                audio = ears.read_slice(wav, c["start"], c["end"])
                segs, lang = ears.transcribe_array(audio, language, c["start"],
                                                   lambda t: _set_progress(item_id, base + t, duration, stage))
            except Cancelled:
                raise
            except Exception as exc:
                errors[c["idx"]] = str(exc)
                vault.run("UPDATE chunk_plan SET status='failed', error=? WHERE item_id=? AND idx=?", (str(exc)[:300], item_id, c["idx"]))
                break  # local failures (missing model, out of memory) will repeat; stop and let the user decide
            _save_chunk(item_id, c, segs, "local")
            done_seconds += c["end"] - c["start"]; done_parts += 1
            vault.update_job(item_id, chunks_done=done_parts, lang_detected=lang)
            _set_progress(item_id, done_seconds, duration, stage)
    return errors
