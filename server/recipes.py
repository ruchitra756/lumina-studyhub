"""Study-material generators. Each one builds a prompt, calls Gemini and returns plain data."""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import chunker, finder, jobs, settings, gemini_client as ai

LANGS = {
    "English": "Write in clear English.",
    "Hindi": "Write in Hindi (Devanagari script). Keep technical terms in English in brackets where helpful.",
    "Hinglish": "Write in simple Hinglish (Hindi in Roman script mixed with English technical terms).",
    "Same as source": "Write in the same language as the source material.",
}

BASE_SYSTEM = (
    "You are an expert teacher and study-material designer helping a college student prepare for exams. "
    "Be accurate. The source material may be a lecture transcript (possibly with recognition errors), "
    "notes, questions or a textbook. Correct obvious transcription mistakes silently. {lang}"
)


def _system(opts):
    return BASE_SYSTEM.format(lang=LANGS.get(opts.get("lang", "English"), LANGS["English"]))


def _wrap(ctx, task):
    return f"{task}\n\n=== SOURCE MATERIAL START ===\n{ctx}\n=== SOURCE MATERIAL END ==="


# ---------- notes ----------
STYLE = {
    "bullet": "Write concise bullet-point notes grouped under clear headings. Max two lines per bullet.",
    "exam": ("Write EXAM-ORIENTED notes: headings per topic, the points that score marks, formulas, "
             "comparison tables (markdown) for differences, and mark the most exam-relevant lines with 'Exam tip:'. "
             "End with probable exam questions and a short answer outline for each."),
    "detailed": (
        "Write VERY DETAILED, textbook-quality, long-form study notes (at least 2000 words; more for long sources). "
        "For EVERY topic: define it, explain why it matters, explain how it works step by step, give background, "
        "real-world applications and common mistakes. Use the source as the backbone but go well BEYOND it with your "
        "own expert knowledge. If the source only gives a one-line or short answer to something, expand it into a "
        "complete, well-explained answer of a full paragraph or more. Never just restate the source."
    ),
}
TOGGLES = {
    "definitions": "Add a 'Key definitions' section under each major topic.",
    "examples": "Add clear worked examples for every important concept.",
    "questions": "End with 'Important questions' (8-15) with short model answers.",
    "mnemonics": "Add a 'Memory tricks' section with mnemonics, acronyms or analogies for the hardest points.",
}


DIAGRAM_RULE = (
    "If a diagram genuinely helps, draw it as a simple text diagram inside a ``` code block: plain ASCII only "
    "(+ - | > v ^ *), at most 60 characters wide, no emojis, no box-drawing characters."
)

SECTION_STYLE = {
    "detailed": (
        "Write this section in VERY DETAILED, textbook-quality depth (roughly 500-900 words, more if the topic is rich). "
        "Define each idea, explain why it matters, explain how it works step by step, give background, real-world "
        "applications and common mistakes. Use the source as the backbone but go well BEYOND it with your own expert "
        "knowledge. If the source only gives a one-line or short answer to something, expand it into a complete, "
        "well-explained answer of a full paragraph or more. Never just restate the source."),
    "exam": (
        "Write this section as EXAM-ORIENTED notes: the points that score marks, formulas, comparison tables (markdown) for "
        "differences, and 'Exam tip:' lines on the most exam-relevant points. End with 1-3 probable exam questions and a "
        "short answer outline for each."),
}


def _notes_single(ctx, o, style, extra):
    """One request. Used for bullet notes, and as the safety net if section planning fails."""
    task = f"{STYLE.get(style, STYLE['detailed'])} {extra} Use markdown (## headings, bullets, **bold** key terms, tables where useful). {DIAGRAM_RULE}"
    text = ai.generate(_wrap(ctx, task), system=_system(o), temperature=0.5,
                       max_tokens=32000 if style == "detailed" else 12000, thinking="low", label="notes")
    return {"markdown": text, "style": style}


def _plan_sections(ctx, o):
    size = "4-6" if len(ctx) < 12000 else "6-9" if len(ctx) < 50000 else "8-12"
    task = (f"Plan the sections of a complete set of study notes for this material: {size} sections, each a distinct topic, "
            "ordered logically (fundamentals first), together covering everything important. "
            'Return JSON: {"title": "title of the notes", "sections": [{"heading": "...", "focus": "one sentence on what this section covers"}]}')
    data = ai.generate_json(_wrap(ctx, task), system=_system(o), thinking="low", label="notes-plan")
    secs = [x for x in (data.get("sections") or []) if isinstance(x, dict) and x.get("heading")]
    if not secs:
        raise ai.LLMError("empty plan")
    return data.get("title") or "Study notes", secs


def _section_context(ctx, item_id, sec):
    """Small material: the whole text. Large material: just the passages relevant to this section (cheaper, sharper)."""
    if len(ctx) <= 40000 or not item_id:
        return ctx
    hits = finder.search(f"{sec['heading']}. {sec.get('focus', '')}", item_id=item_id, k=16)
    if not hits:
        return ctx[:40000]
    hits.sort(key=lambda h: (h["page"] or 0, h["start"] or 0))
    return "\n\n".join((f"[Page {h['page']}] " if h["page"] else "") + h["text"] for h in hits)


def _write_section(ctx, o, style, sec, others, extra, item_id):
    if sec.get("kind") == "questions":
        task = ("Write the final section of the notes: 'Important questions'. 8-15 questions that are likely in an exam, each followed "
                "by a short model answer. Cover the whole material. Start with '## Important questions'. Use markdown.")
        source = ctx if len(ctx) <= 40000 else ctx[:40000]
    else:
        task = (f"You are writing ONE section of a larger set of study notes. Section: \"{sec['heading']}\". Focus: {sec.get('focus', '')}. "
                f"The other sections are: {others}. Do not repeat their content, and do not write an introduction or conclusion for the "
                f"whole notes. {SECTION_STYLE.get(style, SECTION_STYLE['detailed'])} {extra} "
                f"Start with the heading '## {sec['heading']}'. Use markdown (bullets, **bold** key terms, tables where useful). {DIAGRAM_RULE}")
        source = _section_context(ctx, item_id, sec)
    return ai.generate(_wrap(source, task), system=_system(o), temperature=0.5, max_tokens=8192,
                       thinking="low", label=f"notes-section")


def notes(ctx, o):
    style = o.get("style", "detailed")
    extra = " ".join(TOGGLES[t] for t in o.get("include", []) if t in TOGGLES and t != "questions")
    if style == "bullet":
        jobs.report("Writing your notes", 0.4)
        return _notes_single(ctx, o, style, " ".join(TOGGLES[t] for t in o.get("include", []) if t in TOGGLES))
    t0 = time.time()
    try:
        jobs.report("Planning the sections", 0.25)
        title, sections = _plan_sections(ctx, o)
    except ai.LLMError as exc:
        if isinstance(exc, (ai.BusyError, ai.ModelMissing)):
            raise
        jobs.report("Writing your notes", 0.4)
        return _notes_single(ctx, o, style, " ".join(TOGGLES[t] for t in o.get("include", []) if t in TOGGLES))
    if "questions" in o.get("include", []):
        sections.append({"heading": "Important questions", "kind": "questions"})
    heads = "; ".join(s["heading"] for s in sections)
    item_id = o.get("_item_id")
    t_plan = time.time() - t0
    parts, done = [None] * len(sections), 0
    jobs.report(f"Writing sections (0/{len(sections)})", 0.35)
    with ThreadPoolExecutor(max_workers=settings.PARALLEL) as pool:
        futs = {pool.submit(_write_section, ctx, o, style, s, heads, extra, item_id): i for i, s in enumerate(sections)}
        for f in as_completed(futs):
            parts[futs[f]] = f.result().strip()
            done += 1
            jobs.report(f"Writing sections ({done}/{len(sections)})", 0.35 + 0.6 * done / len(sections))
    body = []
    for s_, p in zip(sections, parts):
        body.append(p if p.lstrip().startswith("#") else f"## {s_['heading']}\n\n{p}")
    print(f"[notes] {len(sections)} sections, plan {t_plan:.1f}s, total {time.time() - t0:.1f}s", flush=True)
    return {"markdown": f"# {title}\n\n" + "\n\n".join(body), "style": style}


# ---------- flashcards ----------
def flashcards(ctx, o):
    n = int(o.get("count", 20))
    task = (f"Create {n} high-quality flashcards covering the most important ideas. Each card: a clear question or term "
            "on the front and a short, precise answer on the back. Return JSON: "
            '[{"front": "...", "back": "..."}]')
    data = ai.generate_json(_wrap(ctx, task), system=_system(o))
    return {"cards": [c for c in data if isinstance(c, dict) and c.get("front") and c.get("back")]}


# ---------- quiz ----------
QUIZ_SHAPE = (
    'Return JSON: [{"type": "mcq"|"tf"|"short", "question": "...", "options": ["A","B","C","D"] '
    '(4 options for mcq, ["True","False"] for tf, [] for short), "answer_index": 0 (index of the correct option; -1 for short), '
    '"model_answer": "the correct answer in one sentence", "explanation": "why it is correct", "topic": "2-4 word topic"}]'
)


def quiz(ctx, o):
    n = int(o.get("count", 10))
    diff = o.get("difficulty", "medium")
    qtype = o.get("qtype", "mixed")
    kinds = {"mcq": "multiple-choice (mcq) only", "tf": "true/false (tf) only", "short": "short-answer (short) only",
             "mixed": "a mix of mcq, tf and short"}.get(qtype, "a mix")
    task = (f"Create {n} {diff}-difficulty quiz questions: {kinds}. Test understanding, not memorisation of wording. "
            f"Spread questions across the whole material. {QUIZ_SHAPE}")
    data = ai.generate_json(_wrap(ctx, task), system=_system(o))
    return {"questions": [q for q in data if isinstance(q, dict) and q.get("question")], "difficulty": diff, "qtype": qtype}


# ---------- mind map ----------
def mindmap(ctx, o):
    depth = 2 if len(ctx) < 8000 else 3 if len(ctx) < 40000 else 4
    task = (f"Create a mind map as a tree with {depth} levels below the root. Root = the main subject. "
            "4-8 main branches; deeper levels get more specific. Titles must be short (max 6 words). Give every node a fitting "
            'emoji as "icon". Return JSON: {"title": "...", "icon": "...", "children": [{"title": "...", "icon": "...", "children": [...]}]}')
    return ai.generate_json(_wrap(ctx, task), system=_system(o))


# ---------- audio overview ----------
def audio(ctx, o):
    words = 450 if len(ctx) < 20000 else 700 if len(ctx) < 80000 else 900
    task = (f"Write a spoken audio recap of about {words} words, like a friendly teacher explaining the key ideas before an exam. "
            "Natural spoken sentences, no markdown, no bullet points, no headings. "
            'Return JSON: {"title": "...", "script": "..."}')
    return ai.generate_json(_wrap(ctx, task), system=_system(o))


# ---------- revision sheet ----------
def revision(ctx, o):
    task = ("Create a ONE-PAGE cheat sheet. Max 8 sections, each with 3-6 very short points. Add the key formulas/definitions and a "
            '"remember" list of the 6-8 most important things. Return JSON: {"title": "...", "sections": [{"heading": "...", '
            '"points": ["..."]}], "formulas": ["..."], "remember": ["..."]}')
    return ai.generate_json(_wrap(ctx, task), system=_system(o))


# ---------- 7-day plan ----------
def plan(ctx, o):
    hours = o.get("hours", 2)
    task = (f"Create a 7-day study plan for this material for a student with about {hours} hours per day. Day 1 = learn core ideas, "
            "middle days = practise and deepen, last day = full revision and self-test. Be specific to the topics in the material. "
            'Return JSON: {"title": "...", "days": [{"day": 1, "focus": "...", "tasks": [{"task": "...", "minutes": 30}], "tip": "..."}]}')
    return ai.generate_json(_wrap(ctx, task), system=_system(o))


# ---------- exam mode ----------
def exam(ctx, o):
    mcq_shape = ('{"type": "mcq", "question": "...", "options": ["A","B","C","D"], "answer_index": 0, '
                 '"model_answer": "...", "explanation": "...", "topic": "2-4 word topic"}')
    task = ("Create an exam-prep bundle. Return JSON with exactly these keys: "
            '"expected_questions": [{"question": "...", "answer": "a complete exam answer"}] (10 items), '
            f'"mcqs": [{mcq_shape}] (10 items), '
            '"viva": [{"question": "...", "answer": "short spoken answer"}] (10 items), '
            '"last_minute": ["one-line fact to read just before the exam"] (15 items).')
    data = ai.generate_json(_wrap(ctx, task), system=_system(o))
    for q in data.get("mcqs", []):
        q["type"] = "mcq"
    return data


# ---------- concept dependency graph ----------
def concepts(ctx, o):
    task = ("Build a concept dependency graph: which concepts must be understood BEFORE others. 8-18 nodes, short labels (max 4 words). "
            'An edge {"from": "a", "to": "b"} means "learn a before b". No cycles. '
            'Return JSON: {"nodes": [{"id": "a", "label": "..."}], "edges": [{"from": "a", "to": "b"}]}')
    return ai.generate_json(_wrap(ctx, task), system=_system(o))


# ---------- key facts (curated knowledge layer) ----------
def keyfacts(ctx, o=None):
    task = ("Extract the 15-40 most important standalone facts, definitions and formulas from this material. Each must make sense "
            'on its own. Return JSON: [{"term": "short term", "fact": "one or two precise sentences"}]')
    data = ai.generate_json(_wrap(ctx, task), system=_system(o or {}))
    return [f for f in data if isinstance(f, dict) and f.get("fact")]


KIND_NAMES = {"notes": "notes", "flashcards": "flashcards", "quiz": "quiz", "mindmap": "mind map", "audio": "audio recap",
              "revision": "revision sheet", "plan": "study plan", "exam": "exam bundle", "concepts": "concept map"}
KINDS = {"notes": notes, "flashcards": flashcards, "quiz": quiz, "mindmap": mindmap, "audio": audio,
         "revision": revision, "plan": plan, "exam": exam, "concepts": concepts}


def generate(item_id, kind, opts):
    if kind not in KINDS:
        raise ValueError(f"Unknown kind: {kind}")
    jobs.report("Reading your material", 0.05)
    ctx = chunker.context_for(item_id)  # long documents are condensed once and cached
    if not ctx.strip():
        raise ValueError("This item has no text yet.")
    jobs.report("Preparing study material", 0.38)
    o = {**(opts or {}), "_item_id": item_id}
    jobs.report(f"Generating {KIND_NAMES.get(kind, kind)}", 0.45)
    result = KINDS[kind](ctx, o)
    jobs.report("Finalizing", 0.97)
    return result


# ---------- small helpers ----------
def memory_tip(question, answer, lang="English"):
    prompt = (f"Question: {question}\nAnswer: {answer}\n\nGive ONE short, vivid memory trick (mnemonic, acronym, rhyme or visual "
              "analogy) to remember this answer. Max 2 sentences. " + LANGS.get(lang, ""))
    return ai.generate(prompt, temperature=0.8, max_tokens=300)


def grade_short(question, model_answer, user_answer):
    prompt = (f"Question: {question}\nModel answer: {model_answer}\nStudent answer: {user_answer}\n\n"
              'Judge if the student answer is essentially correct (meaning matters, not wording). Return JSON: '
              '{"correct": true|false, "feedback": "one short encouraging sentence"}')
    return ai.generate_json(prompt, temperature=0.1)
