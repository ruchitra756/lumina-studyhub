"""Audio side: probing, fast audio extraction with REAL progress, chunk planning, and local Whisper.
Heavy libraries (faster-whisper) load lazily."""
import json
import re
import subprocess
import threading
import wave
from pathlib import Path

import numpy as np

from . import settings

RATE = 16000
_model = None
_model_lock = threading.Lock()

NO_FFMPEG = ("ffmpeg is not installed. Install it with: winget install Gyan.FFmpeg  "
             "then close and reopen the terminal and restart the app.")


def _run(cmd, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, **kw)
    except FileNotFoundError:
        raise RuntimeError(NO_FFMPEG)


def probe(path):
    """Duration in seconds. Raises RuntimeError with a plain message if unreadable or has no audio."""
    r = _run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_type:format=duration",
              "-of", "json", str(path)], text=True)
    if r.returncode != 0:
        raise RuntimeError("This file could not be read. It may be corrupted or in an unsupported format. "
                           + (r.stderr or "")[-200:].strip())
    info = json.loads(r.stdout or "{}")
    if not info.get("streams"):
        raise RuntimeError("This file has no audio track, so there is nothing to transcribe.")
    duration = float((info.get("format") or {}).get("duration") or 0)
    if duration <= 0:
        raise RuntimeError("Could not work out how long this file is. It may be corrupted.")
    return duration


def extract_audio(src, dst, denoise, duration, on_progress=None):
    """Video/audio -> 16 kHz mono WAV. Light filter by default; the heavy noise filter only when asked for.
    (loudnorm was removed: it cost ~80% of the time and speech models do not need it.)"""
    dst = Path(dst)
    part = dst.with_name(dst.name + ".part")
    chains = []
    if denoise:
        chains.append("highpass=f=80,lowpass=f=8000,afftdn=nf=-25")
    chains.append("highpass=f=80")  # also the fallback if this ffmpeg build lacks afftdn
    last = ""
    for af in chains:
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(RATE), "-af", af,
               "-c:a", "pcm_s16le", "-f", "wav", "-progress", "pipe:1", "-nostats", str(part)]
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except FileNotFoundError:
            raise RuntimeError(NO_FFMPEG)
        errors = []
        for line in p.stdout:
            line = line.strip()
            m = re.match(r"out_time_(?:us|ms)=(\d+)", line)  # ffmpeg reports microseconds in both
            if m:
                if on_progress and duration:
                    on_progress(min(1.0, int(m.group(1)) / 1e6 / duration))
            elif line and "=" not in line.split(" ")[0]:
                errors.append(line)
        p.wait()
        if p.returncode == 0 and part.exists():
            part.replace(dst)
            return
        last = " ".join(errors[-3:])
        part.unlink(missing_ok=True)
    raise RuntimeError("Could not extract the audio from this file. " + last[:300])


def valid_wav(path, duration):
    try:
        with wave.open(str(path), "rb") as w:
            return abs(w.getnframes() / w.getframerate() - duration) < 5
    except Exception:
        return False


def read_slice(path, start, end):
    """Samples [start, end) seconds as float32 in [-1, 1] (what Whisper wants)."""
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        w.setpos(max(0, int(start * rate)))
        raw = w.readframes(max(0, int((end - start) * rate)))
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def encode_flac(path, start, end):
    """One chunk as FLAC bytes (Gemini accepts audio/flac; ~5-8 MB for 8 minutes of speech).
    Written to a temp file, not a pipe, so the FLAC header carries the full length."""
    import os
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".flac", dir=str(settings.CACHE_DIR))
    os.close(fd)
    try:
        r = _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(path),
                  "-c:a", "flac", tmp], text=True)
        data = Path(tmp).read_bytes()
        if r.returncode != 0 or not data:
            raise RuntimeError("Could not prepare an audio chunk: " + (r.stderr or "")[-200:])
        return data
    finally:
        Path(tmp).unlink(missing_ok=True)


def _quietest(path, lo, hi):
    """Time of the quietest 0.25 s stretch in [lo, hi]: cutting there avoids splitting a word."""
    x = read_slice(path, lo, hi)
    n = int(0.25 * RATE)
    frames = len(x) // n
    if frames < 3:
        return (lo + hi) / 2
    rms = np.sqrt((x[:frames * n].reshape(frames, n) ** 2).mean(axis=1))
    smooth = np.convolve(rms, np.ones(2) / 2, mode="same")
    return lo + (int(np.argmin(smooth)) + 0.5) * 0.25


def plan_chunks(path, duration, target_sec, search=20.0):
    """Split into ~target_sec pieces, cutting at quiet moments. Deterministic, so a resumed job gets the same plan."""
    if duration <= target_sec * 1.25:
        return [(0.0, duration)]
    cuts, t = [0.0], target_sec
    while t < duration - target_sec * 0.4:
        lo = max(cuts[-1] + target_sec * 0.5, t - search)
        hi = min(duration - 5, t + search)
        cuts.append(_quietest(path, lo, hi) if hi > lo + 1 else t)
        t = cuts[-1] + target_sec
    cuts.append(duration)
    return list(zip(cuts[:-1], cuts[1:]))


# ---------------- local Whisper ----------------
def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            try:
                from faster_whisper import WhisperModel  # lazy import: keeps app start fast
                device, compute = "cpu", "int8"
                try:
                    import ctranslate2
                    if ctranslate2.get_cuda_device_count() > 0:
                        device, compute = "cuda", "float16"
                except Exception:
                    pass
                kw = {"cpu_threads": settings.WHISPER_THREADS} if device == "cpu" else {}
                _model = WhisperModel(settings.WHISPER_MODEL, device=device, compute_type=compute, **kw)
            except Exception as exc:
                raise RuntimeError(f"Could not load the speech model '{settings.WHISPER_MODEL}'. The first run needs "
                                   f"internet to download it (a few hundred MB). Details: {exc}")
        return _model


def model_loaded():
    return _model is not None


def transcribe_array(audio, language=None, offset=0.0, on_progress=None):
    """Transcribe one chunk (float32 array). Times are shifted by `offset` so they are absolute in the video."""
    model = _get_model()
    lang = None if language in (None, "", "auto") else language
    seg_iter, info = model.transcribe(
        audio, language=lang, beam_size=settings.WHISPER_BEAM,
        vad_filter=True,                    # skips silence = faster
        condition_on_previous_text=False,   # avoids repetition loops on long audio
    )
    segments = []
    for s in seg_iter:
        text = s.text.strip()
        if text:
            segments.append({"start": offset + float(s.start), "end": offset + float(s.end), "text": text})
        if on_progress:
            on_progress(float(s.end))
    return segments, info.language
