/* Item workspace: source, notes, flashcards, quiz (player), mind map, audio, revision, plan, exam, concept map, chat. */
const TABS = [['source', 'Source'], ['notes', 'Notes'], ['flashcards', 'Flashcards'], ['quiz', 'Quiz'], ['mindmap', 'Mind map'],
  ['audio', 'Audio recap'], ['revision', 'Revision sheet'], ['plan', '7-day plan'], ['exam', 'Exam mode'], ['concepts', 'Concept map'], ['chat', 'Ask AI']];

const LANG_SEL = `<label class="field">Language<select name="lang"><option>English</option><option>Hindi</option><option>Hinglish</option><option>Same as source</option></select></label>`;
const sel = (name, label, opts) => `<label class="field">${label}<select name="${name}">${opts.map(([v, t]) => `<option value="${v}">${t}</option>`).join('')}</select></label>`;
const OPTS = {
  notes: sel('style', 'Style', [['detailed', 'Detailed'], ['bullet', 'Bullet points'], ['exam', 'Exam focused']]) +
    `<div class="stack" style="gap:6px"><b class="small">Include</b><div class="checks">${[['definitions', 'Definitions'], ['examples', 'Examples'], ['questions', 'Important questions'], ['mnemonics', 'Mnemonics']].map(([v, l]) => `<label><input type="checkbox" name="include" value="${v}">${l}</label>`).join('')}</div></div>` + LANG_SEL,
  flashcards: sel('count', 'Number of cards', [[10, '10'], [20, '20'], [30, '30']]) + LANG_SEL,
  quiz: sel('difficulty', 'Difficulty', [['easy', 'Easy'], ['medium', 'Medium'], ['hard', 'Hard']]) +
    sel('qtype', 'Question type', [['mixed', 'Mixed'], ['mcq', 'Multiple choice'], ['tf', 'True / False'], ['short', 'Short answer']]) +
    sel('count', 'Questions', [[5, '5'], [10, '10'], [15, '15'], [20, '20']]) + LANG_SEL,
  mindmap: LANG_SEL, audio: LANG_SEL, revision: LANG_SEL, concepts: LANG_SEL, exam: LANG_SEL,
  plan: sel('hours', 'Study hours per day', [[1, '1 hour'], [2, '2 hours'], [3, '3 hours'], [4, '4 hours']]) + LANG_SEL,
};
const GEN_NOTE = {
  notes: 'Detailed notes go beyond your source and explain every topic in depth.',
  flashcards: 'Cards also join your spaced-repetition review queue.',
  quiz: 'One question at a time, with explanations and memory tips.',
  exam: 'Expected questions, 10 MCQs, viva questions and a last-minute sheet.',
  concepts: 'Shows what you must understand before each topic.',
};

const whenFonts = fn => (document.fonts && document.fonts.ready ? document.fonts.ready.then(fn) : fn());

function readOpts(form) {
  const o = {};
  new FormData(form).forEach((v, k) => { if (k === 'include') (o.include ||= []).push(v); else o[k] = (v === '' || isNaN(v)) ? v : +v; });
  return o;
}
function download(name, text, type = 'text/plain') {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

route(/^\/item\/(\d+)\/(\w+)$/, async (id, tab) => {
  const it = await api('/items/' + id);
  const head = `<div class="stack"><div class="row spread"><div><a href="#/library" class="small">← Library</a><h1 style="margin:4px 0 0">${esc(it.title)}</h1></div>
    <button class="btn ghost sm" id="del">Delete</button></div>`;
  if (it.status !== 'ready') {
    const draw = n => {
      const j = n.job, eta = j && fmtEta(j.eta_seconds), isMedia = n.kind === 'video' || n.kind === 'audio';
      const stalled = n.status === 'processing' && j && j.stalled_seconds > 15 * 60 && !n.running;
      const stats = j && j.media_seconds ? `<div class="proc-stats">
          <div><b>${Math.round(n.progress * 100)}%</b><span>overall</span></div>
          <div><b>${j.chunks_total ? `${j.chunks_done} / ${j.chunks_total}` : '–'}</b><span>parts transcribed</span></div>
          <div><b>${fmtTime(j.done_seconds)} / ${fmtTime(j.media_seconds)}</b><span>of the recording</span></div>
          <div><b>${eta ? eta : (n.status === 'processing' ? 'measuring…' : '–')}</b><span>left (measured speed)</span></div>
          <div><b>${j.elapsed_seconds ? fmtElapsed(j.elapsed_seconds) : '–'}</b><span>transcribing for</span></div>
          <div><b>${j.engine === 'gemini' ? 'Gemini' : j.engine === 'local' ? 'This computer' : '–'}</b><span>transcription${j.denoise_used ? ' + noise filter' : ''}</span></div></div>` : '';
      $('#proc').innerHTML = `<div class="row spread"><b>${esc(n.stage)}</b><span class="chip ${n.status === 'error' ? 'bad' : 'sun'}">${n.status === 'error' ? 'stopped' : n.status}</span></div>
        <div class="bar"><i style="width:${Math.max(1, n.progress * 100)}%"></i></div>${stats}
        ${n.status === 'error' ? `<div class="callout bad">${esc(n.error)}</div>` :
          stalled ? '<div class="callout warn">No activity for more than 15 minutes. The app may have been closed or crashed. Press Resume: finished parts are kept.</div>' :
          '<div class="callout">You do not need to keep this page open. Processing continues in the background as long as the app window (terminal) stays open and the laptop stays awake. Find this item in Library later; you will also get a notice when it is done.</div>'}
        <div class="row">${n.status === 'error' || stalled ? `<button class="btn" data-r="">Resume</button>${isMedia ? (j && j.engine === 'local' ? '<button class="btn ghost" data-r="gemini">Resume with Gemini (fast)</button>' : '<button class="btn ghost" data-r="local">Resume on this computer (slow, private)</button>') : ''}` : ''}</div>`;
      $$('#proc [data-r]').forEach(b => b.onclick = async () => {
        try { await api(`/items/${n.id}/retry` + (b.dataset.r ? '?mode=' + b.dataset.r : ''), { method: 'POST' }); pokeWatcher(); navigate(); }
        catch (e) { toast(e.message, true); }
      });
    };
    view().innerHTML = head + `<div class="card proc-card" id="proc"></div></div>`;
    wireDelete(id, it.status !== 'error' ? 'Cancel and delete this upload? Work done so far is lost.' : null);
    draw(it);
    if (it.status !== 'error') pollTimer = setInterval(async () => {
      const n = await api('/items/' + id);
      if (n.status === 'ready') navigate(); else draw(n);
    }, 2000);
    return;
  }
  view().innerHTML = head + `<nav class="tabs">${TABS.map(([k, l]) => `<a href="#/item/${id}/${k}" class="${k === tab ? 'on' : ''}">${l}</a>`).join('')}</nav><div id="tab"></div></div>`;
  wireDelete(id);
  const el = $('#tab');
  if (tab === 'source') return sourceTab(it, el);
  if (tab === 'chat') { await chatPanel(el, String(it.id)); return; }
  if (OPTS[tab]) return genTab(it, tab, el);
  el.innerHTML = '<div class="card empty">Unknown tab</div>';
});

function wireDelete(id, msg) {
  $('#del').onclick = async () => {
    if (!confirm(msg || 'Delete this material and everything generated from it?')) return;
    await api('/items/' + id, { method: 'DELETE' }); toast('Deleted'); go('/library'); refreshDue();
  };
}

/* ---------- source tab ---------- */
function sourceTab(it, el) {
  const q = query(); const t = parseFloat(q.get('t')); const page = q.get('page');
  let player = '';
  if (it.kind === 'video') player = `<video id="player" controls preload="metadata" src="/media/${it.id}"></video>`;
  else if (it.kind === 'audio') player = `<audio id="player" controls preload="metadata" src="/media/${it.id}"></audio>`;
  else if (it.kind === 'pdf') player = `<iframe src="/media/${it.id}${page ? '#page=' + page : ''}" style="width:100%;height:620px;border:1px solid var(--line);border-radius:14px"></iframe>`;
  else if (it.kind === 'image') player = `<img src="/media/${it.id}" alt="" style="max-width:100%;border-radius:14px">`;
  const isMedia = it.kind === 'video' || it.kind === 'audio';
  el.innerHTML = `<div class="stack">
    <div class="row"><span class="chip teal">${it.kind}</span>${it.language ? `<span class="chip violet">Language: ${esc(it.language)}</span>` : ''}${it.duration ? `<span class="chip">${fmtTime(it.duration)}</span>` : ''}</div>
    ${it.error ? `<div class="card flat" style="background:var(--sun-soft)">${esc(it.error)}</div>` : ''}
    ${player}
    <details class="segs" id="segs"><summary>${isMedia ? 'Time-stamped transcript' : 'Extracted text by page'}</summary><div class="list" id="seglist"><p class="muted" style="padding:12px">Loading…</p></div></details>
    ${it.facts.length ? `<details class="segs"><summary>Key facts the tutor checks first (${it.facts.length})</summary><div class="list" style="padding:6px 18px 14px">${it.facts.map(f => `<p><b>${esc(f.term)}</b>: ${esc(f.fact)}</p>`).join('')}</div></details>` : ''}
    <div class="row no-print"><button class="btn ghost sm" id="cp">Copy full text</button></div></div>`;
  const p = $('#player');
  if (p && !isNaN(t)) { p.addEventListener('loadedmetadata', () => { p.currentTime = t; }, { once: true }); if (p.readyState > 0) p.currentTime = t; }
  let segs = null;
  const load = async () => segs || (segs = await api(`/items/${it.id}/segments`));
  $('#segs').addEventListener('toggle', async e => {
    if (!e.target.open) return;
    const s = await load();
    $('#seglist').innerHTML = s.map(x => isMedia
      ? `<div class="seg" data-t="${x.start}"><time>${fmtTime(x.start)}</time><span>${esc(x.text)}</span></div>`
      : `<div class="seg"><time>p.${x.page}</time><span>${esc(x.text.slice(0, 1200))}</span></div>`).join('');
    $$('#seglist .seg[data-t]').forEach(r => r.onclick = () => { if (p) { p.currentTime = +r.dataset.t; p.play(); } });
  });
  $('#cp').onclick = async () => { const s = await load(); await navigator.clipboard.writeText(s.map(x => x.text).join('\n')); toast('Copied'); };
}

/* ---------- generate tabs ---------- */
async function genTab(it, kind, el) {
  const saved = it.outputs.filter(o => o.kind === kind);
  const wanted = +query().get('o') || (saved[0] && saved[0].id);
  el.innerHTML = `<div class="genbox"><form class="card stack" id="gf">
      <h3 style="margin:0">${KIND_LABEL[kind]}</h3>${GEN_NOTE[kind] ? `<p class="muted small" style="margin:0">${GEN_NOTE[kind]}</p>` : ''}
      ${OPTS[kind]}<button class="btn" type="submit">${saved.length ? 'Generate a new one' : 'Generate'}</button>
      ${saved.length > 1 ? `<label class="field">Saved versions<select id="ver">${saved.map(o => `<option value="${o.id}" ${o.id === wanted ? 'selected' : ''}>${ago(o.created_at)}</option>`).join('')}</select></label>` : ''}
    </form><div id="out"></div></div>`;
  const out = $('#out');
  $('#gf').onsubmit = async e => {
    e.preventDefault();
    let o;
    try { o = await runGeneration(it.id, kind, readOpts(e.target), `Creating your ${KIND_LABEL[kind].toLowerCase()}`); }
    catch { return; }   // runGeneration already showed the error
    if (kind === 'flashcards') refreshDue();
    go(`/item/${it.id}/${kind}?o=${o.id}`);
  };
  const ver = $('#ver'); if (ver) ver.onchange = () => go(`/item/${it.id}/${kind}?o=${ver.value}`);
  if (!wanted) { out.innerHTML = '<div class="card empty"><h3>Nothing here yet</h3><p>Choose options and press Generate.</p></div>'; return; }
  out.innerHTML = '<div class="empty"><div class="spinner" style="margin:auto"></div></div>';
  const o = await api('/outputs/' + wanted);
  RENDER[kind](o, out, it);
}

const RENDER = {
  notes(o, el) {
    el.innerHTML = `<div class="card"><div class="row spread no-print"><span class="chip teal">${esc(o.content.style)} notes</span>
      <div class="row"><button class="btn ghost sm" id="cp">Copy</button><button class="btn ghost sm" id="dl">Download .md</button></div></div>
      <div class="prose">${md(o.content.markdown)}</div></div>`;
    $('#cp').onclick = async () => { await navigator.clipboard.writeText(o.content.markdown); toast('Copied'); };
    $('#dl').onclick = () => download('notes.md', o.content.markdown);
  },

  flashcards(o, el, it) {
    const cards = o.content.cards; let i = 0, on = false;
    const draw = () => {
      const c = cards[i];
      el.innerHTML = `<div class="card stack"><div class="row spread"><span class="chip violet">${i + 1} / ${cards.length}</span>
        <a class="btn sun sm" href="#/review?item=${it.id}">Study with spaced repetition</a></div>
        <div class="flip ${on ? 'on' : ''}" id="fl" tabindex="0"><div class="inner"><div class="face front"><div class="ft">${esc(c.front)}</div></div><div class="face back"><div class="ft">${esc(c.back)}</div></div></div></div>
        <div class="row" style="justify-content:center"><button class="btn ghost" id="pv">Previous</button><button class="btn ghost" id="fp">Flip</button><button class="btn" id="nx">Next</button></div>
        <p class="muted small" style="text-align:center;margin:0">Keys: space flips, arrows move</p></div>`;
      const flip = () => { on = !on; $('#fl').classList.toggle('on', on); };
      $('#fl').onclick = flip; $('#fp').onclick = flip;
      $('#pv').onclick = () => { i = (i - 1 + cards.length) % cards.length; on = false; draw(); };
      $('#nx').onclick = () => { i = (i + 1) % cards.length; on = false; draw(); };
    };
    draw();
    document.onkeydown = e => {
      if (!$('#fl') || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;
      if (e.key === ' ') { e.preventDefault(); $('#fp').click(); }
      if (e.key === 'ArrowRight') $('#nx').click(); if (e.key === 'ArrowLeft') $('#pv').click();
    };
  },

  quiz(o, el, it) {
    const qs = o.content.questions;
    el.innerHTML = `<div class="card stack"><h3 style="margin:0">Quiz ready</h3>
      <div class="row"><span class="chip violet">${qs.length} questions</span><span class="chip sun">${esc(o.content.difficulty)}</span><span class="chip">${esc(o.content.qtype)}</span></div>
      <button class="btn" id="go">Start quiz</button></div>`;
    $('#go').onclick = () => runQuiz(el, qs, { itemId: it.id, difficulty: o.content.difficulty, mode: 'quiz', retake: () => RENDER.quiz(o, el, it) });
  },

  mindmap(o, el) {
    el.innerHTML = `<div class="stack"><div class="row no-print"><button class="btn ghost sm" id="dl">Download SVG</button><span class="muted small">Scroll to explore. Branch colours group related ideas.</span></div>
      <div class="mapbox"></div></div>`;
    whenFonts(() => { $('.mapbox', el).innerHTML = renderMindMap(o.content); });
    $('#dl').onclick = () => download('mindmap.svg', $('#mm-svg').outerHTML, 'image/svg+xml');
  },

  audio(o, el) {
    const text = o.content.script || '';
    const sents = text.match(/[^.!?।]+[.!?।]?\s*/g) || [text];
    const chunks = []; let cur = '';
    sents.forEach(s => { if ((cur + s).length > 200 && cur) { chunks.push(cur); cur = s; } else cur += s; });
    if (cur) chunks.push(cur);
    const deva = /[\u0900-\u097F]/.test(text);
    let i = 0, playing = false;
    el.innerHTML = `<div class="card stack"><h3 style="margin:0">${esc(o.content.title || 'Audio recap')}</h3>
      <div class="row no-print"><button class="btn" id="pl">Play</button><button class="btn ghost" id="st">Stop</button>
        <label class="row small">Speed <select id="sp" style="width:auto"><option>0.8</option><option selected>1</option><option>1.2</option><option>1.5</option></select></label></div>
      <p class="muted small" style="margin:0">Spoken by your browser's built-in voice. ${deva ? 'For Hindi, install a Hindi voice in your system settings if you hear nothing.' : ''}</p>
      <div class="prose">${chunks.map((c, k) => `<span class="sent" id="s${k}">${esc(c)}</span>`).join(' ')}</div></div>`;
    const mark = k => { $$('.sent').forEach(s => s.classList.remove('now')); const n = $('#s' + k); if (n) { n.classList.add('now'); n.scrollIntoView({ block: 'nearest' }); } };
    const speak = k => {
      if (k >= chunks.length) { playing = false; i = 0; $('#pl').textContent = 'Play'; mark(-1); return; }
      i = k; mark(k);
      const u = new SpeechSynthesisUtterance(chunks[k]);
      u.lang = deva ? 'hi-IN' : 'en-IN';
      const v = speechSynthesis.getVoices().find(v => v.lang.toLowerCase().startsWith(deva ? 'hi' : 'en'));
      if (v) u.voice = v;
      u.rate = +$('#sp').value;
      u.onend = () => { if (playing) speak(k + 1); };
      speechSynthesis.speak(u);
    };
    $('#pl').onclick = () => {
      if (playing) { playing = false; speechSynthesis.cancel(); $('#pl').textContent = 'Resume'; }
      else { playing = true; $('#pl').textContent = 'Pause'; speechSynthesis.cancel(); speak(i); }
    };
    $('#st').onclick = () => { playing = false; speechSynthesis.cancel(); i = 0; mark(-1); $('#pl').textContent = 'Play'; };
  },

  revision(o, el) {
    const c = o.content;
    const inner = `<h2>${esc(c.title || 'Revision sheet')}</h2><div class="cols">
      ${(c.sections || []).map(s => `<div class="sec"><h4>${esc(s.heading)}</h4><ul>${(s.points || []).map(p => `<li>${esc(p)}</li>`).join('')}</ul></div>`).join('')}
      ${(c.formulas || []).length ? `<div class="sec"><h4>Formulas and definitions</h4>${c.formulas.map(f => `<div class="formula">${esc(f)}</div>`).join('')}</div>` : ''}</div>
      ${(c.remember || []).length ? `<div class="remember"><b>Must remember</b><ul>${c.remember.map(r => `<li>${esc(r)}</li>`).join('')}</ul></div>` : ''}`;
    el.innerHTML = `<div class="stack"><div class="row no-print"><button class="btn" id="pr">Print or save as PDF</button><button class="btn ghost" id="dl">Download as web page</button></div><div class="sheet">${inner}</div></div>`;
    $('#pr').onclick = () => window.print();
    $('#dl').onclick = () => download('revision-sheet.html', `<!doctype html><meta charset="utf-8"><title>${esc(c.title)}</title><style>body{font-family:system-ui,sans-serif;max-width:900px;margin:20px auto;color:#14324A}h2{background:#14324A;color:#fff;padding:10px 16px;border-radius:8px}.cols{columns:2 280px;column-gap:20px}.sec{break-inside:avoid;margin-bottom:12px;border-left:4px solid #1E6F70;padding-left:10px}h4{margin:0 0 4px;color:#1E6F70}ul{margin:0;padding-left:1.1em;font-size:.9rem}.formula{background:#FFF4D1;padding:8px 12px;border-radius:8px;margin:6px 0;font-weight:600;font-size:.9rem}.remember{background:#EFE8FD;border-radius:8px;padding:10px 14px}</style>${inner}`, 'text/html');
  },

  plan(o, el) {
    const key = 'plan-' + o.id; let done = {};
    try { done = JSON.parse(localStorage.getItem(key) || '{}'); } catch {}
    el.innerHTML = `<div class="stack"><h3 style="margin:0">${esc(o.content.title || '7-day study plan')}</h3>${(o.content.days || []).map(d => `
      <div class="card day"><div class="dnum">Day ${d.day}</div><div><b>${esc(d.focus)}</b>
        ${(d.tasks || []).map((t, k) => `<label class="task"><input type="checkbox" data-k="${d.day}-${k}" ${done[d.day + '-' + k] ? 'checked' : ''}><span>${esc(t.task)} <span class="muted small">· ${t.minutes} min</span></span></label>`).join('')}
        ${d.tip ? `<div class="chip sun" style="margin-top:6px">${esc(d.tip)}</div>` : ''}</div></div>`).join('')}</div>`;
    $$('.task input').forEach(c => c.onchange = () => { done[c.dataset.k] = c.checked; try { localStorage.setItem(key, JSON.stringify(done)); } catch {} });
  },

  exam(o, el, it) {
    const c = o.content; let sub = 'q';
    const acc = list => list.map(x => `<details class="card flat"><summary><b>${esc(x.question)}</b></summary><div class="prose" style="margin-top:8px">${md(x.answer)}</div></details>`).join('');
    const draw = () => {
      const body = sub === 'q' ? acc(c.expected_questions || []) : sub === 'v' ? acc(c.viva || [])
        : sub === 'l' ? `<div class="card"><ul>${(c.last_minute || []).map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>`
        : `<div class="card stack"><p>Ten multiple-choice questions on a 15-minute timer. Answers are explained as you go.</p><button class="btn violet" id="ex">Start exam mode</button></div>`;
      el.innerHTML = `<div class="stack"><div class="row"><button class="btn sun" id="start">Start exam mode</button></div>
        <div class="checks">${[['q', 'Expected questions'], ['m', '10 MCQs'], ['v', 'Viva questions'], ['l', 'Last-minute sheet']].map(([k, l]) => `<label style="cursor:pointer"><input type="radio" name="sub" value="${k}" ${sub === k ? 'checked' : ''} hidden>${l}</label>`).join('')}</div>${body}</div>`;
      $$('.checks label').forEach(l => l.onclick = () => { sub = $('input', l).value; draw(); });
      const start = () => runQuiz(el, c.mcqs || [], { itemId: it.id, difficulty: 'exam', mode: 'exam', timer: 900, retake: () => RENDER.exam(o, el, it) });
      $('#start').onclick = start; const ex = $('#ex'); if (ex) ex.onclick = start;
    };
    draw();
  },

  concepts(o, el) {
    el.innerHTML = `<div class="stack"><p class="muted" style="margin:0">Arrows point from a prerequisite to what depends on it. Click a concept to highlight everything you must learn first. Double-click to reset.</p>
      <div class="mapbox" id="cgbox"></div></div>`;
    whenFonts(() => { $('#cgbox').innerHTML = renderConceptGraph(o.content); wireConceptGraph($('#cgbox')); });
  },
};

/* ---------- Duolingo-style quiz player (also used by exam mode) ---------- */
function runQuiz(el, questions, meta) {
  const qs = questions.map(q => normaliseQ(q)); let idx = 0, score = 0, sel = null, checked = false; const weak = [];
  let left = meta.timer || 0;
  if (left) pollTimer = setInterval(() => { left--; const t = $('#timer'); if (t) t.textContent = fmtTime(left); if (left <= 0) { clearPoll(); finish(); } }, 1000);
  function draw() {
    if (idx >= qs.length) return finish();
    const q = qs[idx]; checked = false; sel = null;
    el.innerHTML = `<div class="qwrap"><div class="qtop"><button class="btn ghost sm" id="quit" aria-label="Quit">✕</button>
        <div class="bar"><i style="width:${idx / qs.length * 100}%"></i></div>
        <span class="chip ${meta.difficulty === 'hard' || meta.mode === 'exam' ? 'bad' : meta.difficulty === 'easy' ? 'good' : 'sun'}">${esc(meta.difficulty)}</span>
        ${left ? `<span class="chip violet" id="timer">${fmtTime(left)}</span>` : ''}</div>
      <div><span class="muted small">Question ${idx + 1} of ${qs.length}${q.topic ? ' · ' + esc(q.topic) : ''}</span><div class="qprompt">${esc(q.question)}</div></div>
      <div id="answer">${q.type === 'short' ? '<textarea id="sa" rows="4" placeholder="Type your answer"></textarea>'
        : q.options.map((o, k) => `<button class="opt" data-k="${k}">${esc(o)}</button>`).join('')}</div>
      <div id="feedback"></div><div class="row" style="justify-content:flex-end"><button class="btn" id="check" ${q.type === 'short' ? '' : 'disabled'}>Check</button></div></div>`;
    $('#quit').onclick = () => { if (confirm('Leave this quiz?')) { clearPoll(); meta.retake(); } };
    $$('.opt').forEach(b => b.onclick = () => { if (checked) return; sel = +b.dataset.k; $$('.opt').forEach(x => x.classList.toggle('sel', x === b)); $('#check').disabled = false; });
    $('#check').onclick = onCheck;
  }
  async function onCheck() {
    const q = qs[idx];
    if (!checked) {
      let ok, note = '';
      if (q.type === 'short') {
        const ans = $('#sa').value.trim(); if (!ans) return toast('Type an answer first');
        const g = await withBusy('Checking your answer…', () => api('/quiz/grade', { method: 'POST', body: { question: q.question, model_answer: q.model_answer, user_answer: ans } }));
        ok = !!g.correct; note = g.feedback || '';
      } else ok = sel === q.answer_index;
      checked = true; if (ok) score++; else weak.push(q.topic || 'General');
      $$('.opt').forEach((b, k) => { b.disabled = true; if (k === q.answer_index) b.classList.add('right'); else if (k === sel) b.classList.add('wrong'); });
      $('#feedback').innerHTML = `<div class="reveal ${ok ? 'right' : 'wrong'} stack" style="gap:6px"><b>${ok ? 'Correct!' : 'Not quite'}</b>
        ${note ? `<div>${esc(note)}</div>` : ''}<div><b>Answer:</b> ${esc(q.model_answer)}</div>${q.explanation ? `<div>${esc(q.explanation)}</div>` : ''}
        <div id="tip"></div>${ok ? '' : '<button class="btn ghost sm" id="tipbtn" style="align-self:flex-start">Get a memory tip</button>'}</div>`;
      const tb = $('#tipbtn'); if (tb) tb.onclick = async () => { tb.disabled = true; try { const r = await api('/memory_tip', { method: 'POST', body: { question: q.question, answer: q.model_answer } }); $('#tip').innerHTML = `<div class="chip sun" style="white-space:normal">Tip: ${esc(r.tip)}</div>`; tb.remove(); } catch (e) { toast(e.message, true); tb.disabled = false; } };
      $('#check').textContent = idx + 1 >= qs.length ? 'See results' : 'Continue';
    } else { idx++; draw(); }
  }
  async function finish() {
    clearPoll();
    const total = qs.length, pct = Math.round(score / total * 100);
    const uniqWeak = [...new Set(weak)];
    try { await api('/quiz/attempt', { method: 'POST', body: { item_id: meta.itemId, mode: meta.mode, difficulty: meta.difficulty, total, correct: score, weak: uniqWeak } }); } catch {}
    el.innerHTML = `<div class="qwrap" style="align-items:center;text-align:center"><div class="big-score">${pct}%</div>
      <h2>${score} of ${total} correct</h2>
      <p class="muted">${pct >= 80 ? 'Excellent work.' : pct >= 50 ? 'Good effort. A little more revision will lock it in.' : 'Keep going. Review the weak topics below and try again.'}</p>
      ${uniqWeak.length ? `<div class="card flat" style="text-align:left;width:100%"><b>Weak topics</b><div class="checks" style="margin-top:8px">${uniqWeak.map(w => `<span class="chip bad">${esc(w)}</span>`).join('')}</div></div>` : ''}
      <div class="row"><button class="btn" id="again">Retry same questions</button><button class="btn ghost" id="newq">Back to setup</button></div></div>`;
    $('#again').onclick = () => runQuiz(el, shuffle(questions), meta);
    $('#newq').onclick = () => meta.retake();
  }
  draw();
}
const shuffle = a => [...a].sort(() => Math.random() - 0.5);
function normaliseQ(q) {
  const type = ['mcq', 'tf', 'short'].includes(q.type) ? q.type : (q.options && q.options.length ? 'mcq' : 'short');
  let options = type === 'tf' ? ['True', 'False'] : (q.options || []);
  let ai = Number.isInteger(q.answer_index) ? q.answer_index : -1;
  if (type !== 'short' && (ai < 0 || ai >= options.length)) ai = Math.max(0, options.findIndex(o => o === q.model_answer));
  return { ...q, type, options, answer_index: ai, model_answer: q.model_answer || (options[ai] ?? '') };
}

/* ---------- chat panel (single item or all material) ---------- */
async function chatPanel(el, scope) {
  let mode = 'grounded';
  const MODES = [['grounded', 'From my material', 'Answers only from what you uploaded'], ['deep', 'Go deeper', 'Material plus the AI’s own knowledge'], ['example', 'Explain with an example', '90% your material, plus a new example']];
  el.innerHTML = `<div class="chatbox"><div class="modes">${MODES.map(([k, t, d]) => `<button class="mode ${k === mode ? 'on' : ''}" data-m="${k}"><b>${t}</b><span>${d}</span></button>`).join('')}</div>
    <div class="msgs" id="msgs"></div>
    <form class="composer" id="cf"><input id="ci" placeholder="Ask a question about your material" autocomplete="off"><button class="btn" type="submit">Send</button></form>
    <div class="row"><button class="btn ghost sm" id="clr">Clear chat</button></div></div>`;
  const msgs = $('#msgs');
  $$('.mode', el).forEach(b => b.onclick = () => { mode = b.dataset.m; $$('.mode', el).forEach(x => x.classList.toggle('on', x === b)); });
  const addUser = t => { msgs.insertAdjacentHTML('beforeend', `<div class="bubble user">${esc(t)}</div>`); msgs.scrollTop = msgs.scrollHeight; };
  const addAns = r => {
    const conf = r.confidence || { level: 'Low', pct: 0 };
    const cites = (r.sources || []).map(s => `<button class="cite" data-i="${s.item_id}" data-t="${s.start ?? ''}" data-p="${s.page ?? ''}" title="${esc(s.snippet)}">${s.start != null ? '▶ ' : '📄 '}${scope === 'all' ? esc(s.title.slice(0, 18)) + ' · ' : ''}${esc(s.label)}</button>`).join('');
    msgs.insertAdjacentHTML('beforeend', `<div class="ans"><div class="acard t"><h5>Answer</h5><div class="prose">${md(r.answer)}</div></div>
      ${(r.key_points || []).length ? `<div class="acard p"><h5>Key points</h5><ul style="margin:0;padding-left:1.1em">${r.key_points.map(k => `<li>${esc(k)}</li>`).join('')}</ul></div>` : ''}
      ${r.example ? `<div class="acard y"><h5>Example (created by AI)</h5><div class="prose">${md(r.example)}</div></div>` : ''}
      <div class="row"><span class="chip ${conf.level === 'High' ? 'good' : conf.level === 'Medium' ? 'sun' : 'bad'}">Match with your material: ${conf.level} (${conf.pct}%)</span></div>
      ${cites ? `<div class="cites">${cites}</div>` : ''}</div>`);
    msgs.scrollTop = msgs.scrollHeight;
    $$('.cite', msgs).forEach(b => b.onclick = () => {
      if (b.dataset.t !== '') go(`/item/${b.dataset.i}/source?t=${b.dataset.t}`);
      else go(`/item/${b.dataset.i}/source?page=${b.dataset.p}`);
    });
  };
  try {
    const hist = await api('/chat/' + scope);
    hist.forEach(m => m.role === 'user' ? addUser(m.content) : (m.reply ? addAns(m.reply) : null));
    if (!hist.length) msgs.innerHTML = '<div class="muted" style="margin:auto;text-align:center">Ask anything. Answers show their sources and how well they match your material.</div>';
  } catch {}
  $('#cf').onsubmit = async e => {
    e.preventDefault();
    const inp = $('#ci'), text = inp.value.trim(); if (!text) return;
    if (msgs.querySelector('.muted')) msgs.innerHTML = '';
    inp.value = ''; addUser(text);
    msgs.insertAdjacentHTML('beforeend', '<div class="bubble" id="typing" style="background:#fff;border:1px solid var(--line)">Thinking…</div>'); msgs.scrollTop = msgs.scrollHeight;
    try { addAns(await api('/chat/' + scope, { method: 'POST', body: { message: text, mode } })); }
    catch (err) { toast(err.message, true); }
    $('#typing')?.remove();
  };
  $('#clr').onclick = async () => { if (confirm('Clear this chat?')) { await api('/chat/' + scope, { method: 'DELETE' }); chatPanel(el, scope); } };
}
