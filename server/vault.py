"""SQLite storage. Every function opens its own short-lived connection (thread-safe)."""
import json
import sqlite3
import time
from contextlib import contextmanager

from . import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  kind TEXT NOT NULL,
  filename TEXT,
  status TEXT NOT NULL DEFAULT 'queued',
  stage TEXT DEFAULT '',
  progress REAL DEFAULT 0,
  error TEXT,
  language TEXT,
  duration REAL,
  digest TEXT,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS segments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  start REAL, end REAL, page INTEGER,
  text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_seg_item ON segments(item_id, idx);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  text TEXT NOT NULL,
  page INTEGER, start REAL,
  embedding BLOB
);
CREATE INDEX IF NOT EXISTS ix_chunk_item ON chunks(item_id);
CREATE TABLE IF NOT EXISTS outputs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  options TEXT,
  content TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cards(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  output_id INTEGER,
  front TEXT NOT NULL, back TEXT NOT NULL,
  ease REAL DEFAULT 2.5, interval INTEGER DEFAULT 0, reps INTEGER DEFAULT 0,
  due TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS quiz_attempts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  mode TEXT, difficulty TEXT, total INTEGER, correct INTEGER,
  weak TEXT, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL, item_id INTEGER, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS facts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  term TEXT, fact TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  extra TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chat_scope ON chat_messages(scope, id);
CREATE TABLE IF NOT EXISTS chunk_plan(
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  start REAL NOT NULL, end REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  engine TEXT, error TEXT,
  PRIMARY KEY(item_id, idx)
);
CREATE TABLE IF NOT EXISTS job_info(
  item_id INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
  engine TEXT, mode_pref TEXT, denoise_pref TEXT, language TEXT, lang_detected TEXT,
  denoise_used INTEGER DEFAULT 0,
  media_seconds REAL DEFAULT 0, done_seconds REAL DEFAULT 0, done_at_start REAL DEFAULT 0,
  chunks_total INTEGER DEFAULT 0, chunks_done INTEGER DEFAULT 0,
  t_start REAL, updated REAL
);
"""


@contextmanager
def conn():
    c = sqlite3.connect(settings.DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init():
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        # A restart kills background jobs, so unfinished items are marked failed.
        c.execute(
            "UPDATE items SET status='error', error='Processing was interrupted (the app was closed or restarted). Press Resume: finished parts of a video are kept.' "
            "WHERE status IN ('queued','processing')"
        )


def rows(sql, args=()):
    with conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(sql, args=()):
    r = rows(sql, args)
    return r[0] if r else None


def run(sql, args=()):
    with conn() as c:
        return c.execute(sql, args).lastrowid


def many(sql, seq):
    with conn() as c:
        c.executemany(sql, seq)


def log_event(kind, item_id=None):
    run("INSERT INTO events(kind,item_id,created_at) VALUES(?,?,?)", (kind, item_id, time.time()))


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False)


def update_item(item_id, **fields):
    """Update an item. Progress only ever moves forward, so the bar never jumps backwards."""
    sets, args = [], []
    for k, v in fields.items():
        sets.append("progress=MAX(COALESCE(progress,0), ?)" if k == "progress" else f"{k}=?")
        args.append(v)
    run(f"UPDATE items SET {', '.join(sets)} WHERE id=?", (*args, item_id))


def update_job(item_id, **fields):
    """Upsert the video-job record. Every call is also a heartbeat (updated = now)."""
    run("INSERT OR IGNORE INTO job_info(item_id) VALUES(?)", (item_id,))
    fields["updated"] = time.time()
    run(f"UPDATE job_info SET {', '.join(k + '=?' for k in fields)} WHERE item_id=?", (*fields.values(), item_id))
