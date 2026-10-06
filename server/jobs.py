"""Tiny in-memory progress tracker for long generation jobs (the browser polls it)."""
import threading
import time
import uuid

_jobs = {}
_lock = threading.Lock()
_local = threading.local()


def create():
    jid = uuid.uuid4().hex[:10]
    now = time.time()
    with _lock:
        _jobs[jid] = {"status": "running", "stage": "Starting", "pct": 0.02, "result": None, "error": None, "t": now}
        for k in [k for k, v in _jobs.items() if now - v["t"] > 3600]:
            _jobs.pop(k, None)
    return jid


def bind(jid):
    """Call at the start of a worker thread so report() knows which job it belongs to."""
    _local.jid = jid


def report(stage, pct=None):
    """Safe to call anywhere: does nothing when no job is bound to this thread."""
    jid = getattr(_local, "jid", None)
    if not jid:
        return
    with _lock:
        j = _jobs.get(jid)
        if j:
            j["stage"] = stage
            if pct is not None:
                j["pct"] = max(j["pct"], min(0.99, pct))  # progress never goes backwards


def finish(jid, result):
    with _lock:
        if jid in _jobs:
            _jobs[jid].update(status="done", stage="Done", pct=1.0, result=result)


def fail(jid, message):
    with _lock:
        if jid in _jobs:
            _jobs[jid].update(status="error", error=message)


def get(jid):
    with _lock:
        j = _jobs.get(jid)
        if not j:
            return None
        return {**j, "elapsed": time.time() - j["t"]}
