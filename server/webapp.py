"""FastAPI app: REST API + the web UI."""
import json
import mimetypes
import shutil
import threading
import time
import traceback
from pathlib import Path

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import chunker, gemini_client, insights, jobs, mediaflow, pipeline, recipes, settings, spaced, tutor, vault

vault.init()
app = FastAPI(title="Lumina StudyHub")
api = APIRouter(prefix="/api")


def _item_or_404(item_id):
    item = vault.one("SELECT * FROM items WHERE id=?", (item_id,))
    if not item:
        raise HTTPException(404, "Item not found")
    return item


def _public(item):
    item = dict(item)
    item.pop("digest", None)
    return item


def _job_view(item_id):
    """Real numbers for the UI: parts done, media seconds done, a measured ETA, and whether activity has stopped."""
    j = vault.one("SELECT * FROM job_info WHERE item_id=?", (item_id,))
    if not j:
        return None
    now = time.time()
    total, done = j["media_seconds"] or 0, j["done_seconds"] or 0
    elapsed = (now - j["t_start"]) if j["t_start"] else 0
    session = done - (j["done_at_start"] or 0)
    eta = None
    if total and session > 0 and elapsed > settings.ETA_MIN_SECONDS and done < total:
        eta = (total - done) / (session / elapsed)   # measured speed, not a guess
    return {"engine": j["engine"], "chunks_done": j["chunks_done"], "chunks_total": j["chunks_total"],
            "done_seconds": done, "media_seconds": total, "elapsed_seconds": elapsed, "eta_seconds": eta,
            "stalled_seconds": now - (j["updated"] or now), "denoise_used": bool(j["denoise_used"])}


def _with_job(item):
    if item["status"] in ("queued", "processing") or item["kind"] in ("audio", "video"):
        item["job"] = _job_view(item["id"])
        item["running"] = pipeline.is_active(item["id"])
    return item


@api.get("/health")
def health():
    ffmpeg = shutil.which("ffmpeg") is not None
    key = bool(settings.GEMINI_API_KEY) and not settings.GEMINI_API_KEY.startswith("paste_")
    return {"ok": ffmpeg and key, "ffmpeg": ffmpeg, "api_key": key, "model": settings.GEMINI_MODEL,
            "whisper": settings.WHISPER_MODEL}


# ---------- items ----------
@api.post("/upload")
async def upload(file: UploadFile = File(...), language: str = Form("auto"), title: str = Form(""),
                 transcribe: str = Form("auto"), denoise: str = Form("auto")):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in pipeline.ALLOWED:
        raise HTTPException(400, f"Unsupported file type '{ext}'. Use video, audio, PDF, image or text files.")
    name = (title.strip() or Path(file.filename).stem)[:120]
    item_id = vault.run("INSERT INTO items(title,kind,filename,status,stage,created_at) VALUES(?,?,?,?,?,?)",
                        (name, pipeline.kind_of(ext), file.filename, "queued", "Uploading", time.time()))
    dest = settings.UPLOAD_DIR / f"{item_id}{ext}"
    size, limit = 0, settings.MAX_UPLOAD_MB * 1024 * 1024
    with open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                f.close()
                dest.unlink(missing_ok=True)
                vault.run("DELETE FROM items WHERE id=?", (item_id,))
                raise HTTPException(413, f"File is larger than {settings.MAX_UPLOAD_MB} MB.")
            f.write(chunk)
    transcribe = transcribe if transcribe in ("auto", "gemini", "local") else "auto"
    denoise = denoise if denoise in ("auto", "on", "off") else "auto"
    pipeline.start(item_id, language, transcribe, denoise)
    return _with_job(_public(_item_or_404(item_id)))


@api.get("/items")
def list_items(q: str = "", kind: str = ""):
    sql, args = "SELECT * FROM items WHERE 1=1", []
    if q:
        sql += " AND title LIKE ?"
        args.append(f"%{q}%")
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    sql += " ORDER BY id DESC"
    items = [_with_job(_public(i)) for i in vault.rows(sql, tuple(args))]
    counts = {r["item_id"]: r["n"] for r in vault.rows("SELECT item_id, COUNT(*) n FROM outputs GROUP BY item_id")}
    for i in items:
        i["output_count"] = counts.get(i["id"], 0)
    return items


@api.get("/active")
def active_items():
    """Everything currently processing, so the app can show progress anywhere and tell you when it finishes."""
    out = []
    for i in vault.rows("SELECT id,title,kind,status,stage,progress,error FROM items WHERE status IN ('queued','processing') ORDER BY id"):
        i["job"] = _job_view(i["id"])
        out.append(i)
    return out


@api.get("/items/{item_id}")
def get_item(item_id: int):
    item = _with_job(_public(_item_or_404(item_id)))
    item["outputs"] = [
        {"id": o["id"], "kind": o["kind"], "created_at": o["created_at"], "options": json.loads(o["options"] or "{}")}
        for o in vault.rows("SELECT id,kind,options,created_at FROM outputs WHERE item_id=? ORDER BY id DESC", (item_id,))]
    item["facts"] = vault.rows("SELECT term,fact FROM facts WHERE item_id=?", (item_id,))
    return item


@api.get("/items/{item_id}/segments")
def get_segments(item_id: int):
    _item_or_404(item_id)
    return vault.rows("SELECT idx,start,end,page,text FROM segments WHERE item_id=? ORDER BY idx", (item_id,))


@api.delete("/items/{item_id}")
def delete_item(item_id: int):
    _item_or_404(item_id)
    mediaflow.cancel(item_id)   # stops a running transcription at the next step
    vault.run("DELETE FROM items WHERE id=?", (item_id,))
    for f in settings.UPLOAD_DIR.glob(f"{item_id}.*"):
        f.unlink(missing_ok=True)
    (settings.CACHE_DIR / f"{item_id}.wav").unlink(missing_ok=True)
    return {"ok": True}


@api.post("/items/{item_id}/retry")
def retry(item_id: int, language: str = "", mode: str = "", denoise: str = ""):
    """Retry / Resume. For videos, finished parts are kept and only the missing ones are redone.
    mode=gemini or mode=local switches the transcription route for the remaining parts."""
    _item_or_404(item_id)
    mode = mode if mode in ("gemini", "local") else None
    if not pipeline.start(item_id, language or None, mode, denoise or None):
        raise HTTPException(409, "This item is still being processed in the background.")
    return {"ok": True}


@app.get("/media/{item_id}")
def media(item_id: int):
    path = pipeline.source_path(item_id)
    if not path:
        raise HTTPException(404, "File not found")
    return FileResponse(path, media_type=mimetypes.guess_type(str(path))[0] or "application/octet-stream")


# ---------- generation ----------
class GenBody(BaseModel):
    kind: str
    options: dict = {}


def _do_generate(item_id, body):
    """Generate + save. Raises LLMError / ValueError; callers turn those into HTTP errors or job errors."""
    content = recipes.generate(item_id, body.kind, body.options)
    now = time.time()
    oid = vault.run("INSERT INTO outputs(item_id,kind,options,content,created_at) VALUES(?,?,?,?,?)",
                    (item_id, body.kind, vault.dumps(body.options), vault.dumps(content), now))
    if body.kind == "flashcards":
        spaced.add_cards(item_id, oid, content.get("cards", []))
    vault.log_event(f"generate:{body.kind}", item_id)
    return {"id": oid, "kind": body.kind, "content": content, "options": body.options, "created_at": now}


def _ready_item_or_409(item_id):
    item = _item_or_404(item_id)
    if item["status"] != "ready":
        raise HTTPException(409, "This item is still processing.")
    return item


@api.post("/items/{item_id}/generate")
def generate(item_id: int, body: GenBody):
    _ready_item_or_409(item_id)
    try:
        return _do_generate(item_id, body)
    except gemini_client.LLMError as exc:
        raise HTTPException(502, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def _gen_worker(jid, item_id, body):
    jobs.bind(jid)
    try:
        out = _do_generate(item_id, body)
        jobs.finish(jid, {"id": out["id"], "kind": out["kind"]})
    except (gemini_client.LLMError, ValueError) as exc:
        jobs.fail(jid, str(exc))
    except Exception as exc:  # never leave the browser waiting forever
        traceback.print_exc()
        jobs.fail(jid, f"Unexpected error: {type(exc).__name__}: {exc}")


@api.post("/items/{item_id}/generate_async")
def generate_async(item_id: int, body: GenBody):
    """Starts generation in the background and returns at once; the browser polls /jobs/{id} for live progress."""
    _ready_item_or_409(item_id)
    jid = jobs.create()
    threading.Thread(target=_gen_worker, args=(jid, item_id, body), daemon=True).start()
    return {"job_id": jid}


@api.get("/jobs/{jid}")
def job_status(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "This job was lost (the app was restarted). Please try again.")
    return {"status": j["status"], "stage": j["stage"], "pct": j["pct"], "error": j["error"],
            "result": j["result"], "elapsed": j["elapsed"]}


@api.get("/outputs/{oid}")
def get_output(oid: int):
    o = vault.one("SELECT * FROM outputs WHERE id=?", (oid,))
    if not o:
        raise HTTPException(404, "Output not found")
    return {"id": o["id"], "item_id": o["item_id"], "kind": o["kind"], "created_at": o["created_at"],
            "options": json.loads(o["options"] or "{}"), "content": json.loads(o["content"])}


@api.delete("/outputs/{oid}")
def delete_output(oid: int):
    vault.run("DELETE FROM cards WHERE output_id=?", (oid,))
    vault.run("DELETE FROM outputs WHERE id=?", (oid,))
    return {"ok": True}


# ---------- quiz helpers ----------
class AttemptBody(BaseModel):
    item_id: int
    mode: str = "quiz"
    difficulty: str = "medium"
    total: int
    correct: int
    weak: list[str] = []


@api.post("/quiz/attempt")
def quiz_attempt(b: AttemptBody):
    vault.run("INSERT INTO quiz_attempts(item_id,mode,difficulty,total,correct,weak,created_at) VALUES(?,?,?,?,?,?,?)",
              (b.item_id, b.mode, b.difficulty, b.total, b.correct, vault.dumps(b.weak), time.time()))
    vault.log_event("quiz", b.item_id)
    return {"ok": True}


class GradeBody(BaseModel):
    question: str
    model_answer: str
    user_answer: str


@api.post("/quiz/grade")
def quiz_grade(b: GradeBody):
    try:
        return recipes.grade_short(b.question, b.model_answer, b.user_answer)
    except gemini_client.LLMError as exc:
        raise HTTPException(502, str(exc))


class TipBody(BaseModel):
    question: str
    answer: str


@api.post("/memory_tip")
def memory_tip(b: TipBody):
    try:
        return {"tip": recipes.memory_tip(b.question, b.answer)}
    except gemini_client.LLMError as exc:
        raise HTTPException(502, str(exc))


# ---------- spaced repetition ----------
@api.get("/review/due")
def review_due(item_id: int = 0):
    cards = spaced.due_cards(item_id or None)
    total = vault.one("SELECT COUNT(*) n FROM cards")["n"]
    return {"cards": cards, "total_cards": total}


class ReviewBody(BaseModel):
    quality: int


@api.post("/cards/{card_id}/review")
def card_review(card_id: int, b: ReviewBody):
    r = spaced.review(card_id, max(0, min(5, b.quality)))
    if not r:
        raise HTTPException(404, "Card not found")
    return r


# ---------- chat ----------
class ChatBody(BaseModel):
    message: str
    mode: str = "grounded"


@api.post("/chat/{scope}")
def chat(scope: str, b: ChatBody):
    if scope != "all":
        _item_or_404(int(scope))
    try:
        return tutor.ask(scope, b.message, b.mode)
    except gemini_client.LLMError as exc:
        raise HTTPException(502, str(exc))


@api.get("/chat/{scope}")
def chat_history(scope: str):
    out = []
    for m in vault.rows("SELECT role,content,extra,created_at FROM chat_messages WHERE scope=? ORDER BY id", (scope,)):
        out.append({"role": m["role"], "content": m["content"], "created_at": m["created_at"],
                    "reply": json.loads(m["extra"]) if m["extra"] else None})
    return out


@api.delete("/chat/{scope}")
def chat_clear(scope: str):
    vault.run("DELETE FROM chat_messages WHERE scope=?", (scope,))
    return {"ok": True}


# ---------- history + progress ----------
@api.get("/history")
def history(q: str = "", kind: str = "", item_id: int = 0):
    sql = ("SELECT o.id,o.kind,o.created_at,o.item_id,o.options,i.title,substr(o.content,1,4000) AS preview "
           "FROM outputs o JOIN items i ON i.id=o.item_id WHERE 1=1")
    args = []
    if kind:
        sql += " AND o.kind=?"
        args.append(kind)
    if item_id:
        sql += " AND o.item_id=?"
        args.append(item_id)
    if q:
        sql += " AND (i.title LIKE ? OR o.content LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY o.id DESC LIMIT 300"
    rows = vault.rows(sql, tuple(args))
    for r in rows:
        r["options"] = json.loads(r["options"] or "{}")
        r["preview"] = r["preview"][:160]
    return rows


@api.get("/progress")
def progress():
    return insights.progress()


app.include_router(api)
app.mount("/", StaticFiles(directory=str(settings.WEB_DIR), html=True), name="web")
