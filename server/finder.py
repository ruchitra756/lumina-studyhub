"""RAG retrieval: embeddings + keyword hybrid search, with page/timestamp on every hit."""
import re

import numpy as np

from . import gemini_client, vault

STOP = set("a an the is are was were of to in on for and or with what why how when which who this that it as by be at from do does did you me my i about explain tell".split())


def tokens(text):
    parts = re.split(r"[\s,.;:!?()\[\]{}\"'“”।|/\\\-_]+", text.lower())
    return [p for p in parts if p and p not in STOP and len(p) > 1]


def index_item(item_id, chunks, on_progress=None):
    """Store chunks, then add embeddings. If embedding fails the chunks still work via keyword search."""
    vault.run("DELETE FROM chunks WHERE item_id=?", (item_id,))
    vault.many(
        "INSERT INTO chunks(item_id,idx,text,page,start) VALUES(?,?,?,?,?)",
        [(item_id, i, c["text"], c.get("page"), c.get("start")) for i, c in enumerate(chunks)],
    )
    rows = vault.rows("SELECT id,text FROM chunks WHERE item_id=? ORDER BY idx", (item_id,))
    try:
        for i in range(0, len(rows), 64):
            batch = rows[i:i + 64]
            vecs = gemini_client.embed([r["text"] for r in batch])
            data = []
            for r, v in zip(batch, vecs):
                a = np.asarray(v, dtype=np.float32)
                a = a / (np.linalg.norm(a) or 1.0)
                data.append((a.tobytes(), r["id"]))
            vault.many("UPDATE chunks SET embedding=? WHERE id=?", data)
            if on_progress:
                on_progress(min(1.0, (i + 64) / len(rows)))
        return True
    except Exception as exc:
        print(f"[index] embeddings failed for item {item_id}, keyword search will be used: {exc}", flush=True)
        return False


def search(query, item_id=None, k=6):
    sql = ("SELECT c.id,c.item_id,c.text,c.page,c.start,c.embedding,i.title FROM chunks c "
           "JOIN items i ON i.id=c.item_id WHERE i.status='ready'")
    args = ()
    if item_id:
        sql += " AND c.item_id=?"
        args = (item_id,)
    rows = vault.rows(sql, args)
    if not rows:
        return []
    q_tokens = set(tokens(query))
    kw = np.array([len(q_tokens & set(tokens(r["text"]))) / max(1, len(q_tokens)) for r in rows])

    cos = np.zeros(len(rows))
    have = np.array([r["embedding"] is not None for r in rows])
    if have.any():
        try:
            qv = np.asarray(gemini_client.embed([query], task="RETRIEVAL_QUERY")[0], dtype=np.float32)
            qv = qv / (np.linalg.norm(qv) or 1.0)
            mat = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) if r["embedding"] else np.zeros_like(qv)
                             for r in rows])
            if mat.shape[1] == qv.shape[0]:
                cos = mat @ qv
            else:
                have[:] = False
        except Exception:
            have[:] = False
    score = np.where(have, 0.8 * cos + 0.2 * kw, 0.8 * kw)
    order = np.argsort(-score)[:k]
    results = []
    for i in order:
        r = rows[int(i)]
        results.append({
            "chunk_id": r["id"], "item_id": r["item_id"], "title": r["title"], "text": r["text"],
            "page": r["page"], "start": r["start"], "score": float(score[i]),
            "cos": float(cos[i]) if have[i] else None, "kw": float(kw[i]),
        })
    return results


def confidence(results):
    """Turn retrieval scores into a simple High / Medium / Low label."""
    if not results:
        return {"level": "Low", "pct": 0}
    top = results[0]
    if top["cos"] is not None:
        pct = (top["cos"] - 0.45) / 0.35 * 100
    else:
        pct = top["kw"] * 100
    pct = int(max(0, min(100, pct)))
    return {"level": "High" if pct >= 70 else "Medium" if pct >= 40 else "Low", "pct": pct}
