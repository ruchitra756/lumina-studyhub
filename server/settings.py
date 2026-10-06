"""Central configuration. Reads values from the .env file next to launch.py."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env():
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
EMBED_DIM = int(os.getenv("EMBED_DIM", "768"))

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
WHISPER_BEAM = int(os.getenv("WHISPER_BEAM_SIZE", "1"))

# ---- video / audio transcription ----
# auto = short clips are transcribed on this computer, long ones with Gemini (minutes instead of hours).
# gemini = always cloud, local = always on this computer (private, slow on a CPU).
TRANSCRIBE_MODE = os.getenv("TRANSCRIBE_MODE", "auto").strip().lower()
LOCAL_MAX_MINUTES = float(os.getenv("LOCAL_MAX_MINUTES", "5"))     # auto mode: up to this length stays local
CLOUD_CHUNK_MINUTES = float(os.getenv("CLOUD_CHUNK_MINUTES", "8"))  # one Gemini request per ~8 min of audio
LOCAL_CHUNK_MINUTES = float(os.getenv("LOCAL_CHUNK_MINUTES", "10"))
DENOISE_AUTO_MAX_MINUTES = float(os.getenv("DENOISE_AUTO_MAX_MINUTES", "10"))  # auto: noise filter only up to this length
# faster-whisper defaults to only 4 CPU threads; use the machine's real core count instead.
WHISPER_THREADS = max(1, int(os.getenv("WHISPER_THREADS", str(min(os.cpu_count() or 4, 16)))))
ETA_MIN_SECONDS = float(os.getenv("ETA_MIN_SECONDS", "10"))  # measure speed this long before showing an ETA
STALL_MINUTES = float(os.getenv("STALL_MINUTES", "15"))  # no activity for this long = shown as "may have stopped"

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "6144"))
AUTO_FACTS = os.getenv("AUTO_FACTS", "1") == "1"

# Map-reduce: sources longer than this are condensed block by block first.
DIRECT_LIMIT_CHARS = int(os.getenv("DIRECT_LIMIT_CHARS", "150000"))
BLOCK_CHARS = int(os.getenv("BLOCK_CHARS", "30000"))

# How many Gemini requests may run at the same time (notes sections, long-document condensing, scanned-page OCR).
# Raise to 4-5 if you never see "busy" messages; lower to 2 on a strict free tier.
PARALLEL = max(1, int(os.getenv("GEMINI_PARALLEL", "3")))

WORKSPACE = ROOT / "workspace"
UPLOAD_DIR = WORKSPACE / "uploads"
CACHE_DIR = WORKSPACE / "cache"
DB_PATH = WORKSPACE / "studyhub.sqlite3"
WEB_DIR = ROOT / "web"

for _d in (WORKSPACE, UPLOAD_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)
