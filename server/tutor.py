"""Chat engine: hybrid retrieval (curated key facts first, then RAG), grounded answers with sources + confidence."""
import time

from . import finder, gemini_client as ai, vault

MODES = {
    "grounded": ("Answer ONLY from the source excerpts. If they do not contain the answer, say so clearly in the answer and "
                 "give a brief general hint, marked as 'outside your material'. Leave example empty."),
    "deep": ("Use the source excerpts as the base, then go deeper with your own expert knowledge to give a complete, detailed "
             "answer, even if the source only has a short answer. Leave example empty."),
    "example": ("Use about 90% material from the source excerpts and about 10% your own enhancement. Put a fresh, "
                "AI-created example (not from the source) in the example field."),
}

SYSTEM = (
    "You are a patient tutor. The student's own study material is given as numbered excerpts. "
    "Reply in the language the student writes in. Return JSON: "
    '{"answer": "markdown answer", "key_points": ["3-5 short points"], "example": "string or empty", '
    '"used_sources": [numbers of the excerpts you actually used]}.'
)


def _facts(scope, query):
    rows = vault.rows("SELECT item_id,term,fact FROM facts" + ("" if scope == "all" else " WHERE item_id=?"),
                      () if scope == "all" else (int(scope),))
    q = set(finder.tokens(query))
    scored = []
    for r in rows:
        overlap = len(q & set(finder.tokens((r["term"] or "") + " " + r["fact"])))
        if overlap >= 2 or (r["term"] and set(finder.tokens(r["term"])) & q and overlap >= 1):
            scored.append((overlap, r))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:4]]


def _label(r):
    if r.get("start") is not None:
        m, s = divmod(int(r["start"]), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    return f"page {r['page']}" if r.get("page") else "text"


def ask(scope, message, mode="grounded"):
    scope = str(scope)
    if mode not in MODES:
        mode = "grounded"
    item_id = None if scope == "all" else int(scope)
    results = finder.search(message, item_id=item_id, k=6)
    facts = _facts(scope, message)
    conf = finder.confidence(results)

    hist = vault.rows("SELECT role,content FROM chat_messages WHERE scope=? ORDER BY id DESC LIMIT 6", (scope,))[::-1]
    hist_txt = "\n".join(f"{h['role']}: {h['content'][:600]}" for h in hist)
    ex_txt = "\n\n".join(f"[{i + 1}] ({r['title']}, {_label(r)})\n{r['text']}" for i, r in enumerate(results)) or "(no excerpts found)"
    facts_txt = "\n".join(f"- {f['term']}: {f['fact']}" for f in facts) or "(none)"
    prompt = (f"{MODES[mode]}\n\nCURATED KEY FACTS (check first):\n{facts_txt}\n\nSOURCE EXCERPTS:\n{ex_txt}\n\n"
              f"RECENT CHAT:\n{hist_txt or '(start of chat)'}\n\nSTUDENT QUESTION: {message}")
    data = ai.generate_json(prompt, system=SYSTEM, temperature=0.4)
    if not isinstance(data, dict):
        data = {"answer": str(data)}
    used = [n for n in data.get("used_sources", []) if isinstance(n, int) and 1 <= n <= len(results)] or list(range(1, min(3, len(results)) + 1))
    sources = [{"n": n, "item_id": results[n - 1]["item_id"], "title": results[n - 1]["title"],
                "page": results[n - 1]["page"], "start": results[n - 1]["start"],
                "label": _label(results[n - 1]), "snippet": results[n - 1]["text"][:220]} for n in used]
    reply = {"answer": data.get("answer", ""), "key_points": data.get("key_points") or [],
             "example": data.get("example") or "", "sources": sources, "confidence": conf, "mode": mode,
             "used_facts": [f["term"] for f in facts]}
    now = time.time()
    vault.run("INSERT INTO chat_messages(scope,role,content,extra,created_at) VALUES(?,?,?,?,?)", (scope, "user", message, None, now))
    vault.run("INSERT INTO chat_messages(scope,role,content,extra,created_at) VALUES(?,?,?,?,?)",
              (scope, "assistant", reply["answer"], vault.dumps(reply), now + 0.001))
    vault.log_event("chat", item_id)
    return reply
