/* Main pages: home, add material, library, history, progress, review cards, ask-everything. */
const KIND_ICON = { video: 'VID', audio: 'AUD', pdf: 'PDF', image: 'IMG', text: 'TXT' };

function itemCard(it) {
  const busy = it.status === 'queued' || it.status === 'processing';
  const eta = busy && it.job ? fmtEta(it.job.eta_seconds) : null;
  const state = it.status === 'ready' ? '' :
    it.status === 'error' ? '<span class="chip bad">Needs attention</span>' : `<span class="chip sun">${Math.round(it.progress * 100)}%</span>`;
  return `<a class="card item-card" href="#/item/${it.id}/source">
    <div class="row spread"><span class="kind-badge ${it.kind}">${KIND_ICON[it.kind] || 'FILE'}</span>${state}</div>
    <h3 style="margin:0">${esc(it.title)}</h3>
    ${busy ? `<div class="mini-bar"><i style="width:${Math.max(2, it.progress * 100)}%"></i></div>
      <div class="muted small">${esc(it.stage || 'Processing')}${eta ? ' · ' + eta + ' left' : ''}</div>` :
      `<div class="muted small">${ago(it.created_at)}${it.duration ? ' · ' + fmtTime(it.duration) : ''}${it.output_count ? ' · ' + it.output_count + ' generated' : ''}</div>`}</a>`;
}

/* ---------- home ---------- */
route(/^\/$/, async () => {
  view().innerHTML = '<div class="empty"><div class="spinner" style="margin:auto"></div></div>';
  const [p, items, due] = await Promise.all([api('/progress'), api('/items'), api('/review/due')]);
  const recent = items.slice(0, 6);
  view().innerHTML = `
  <div class="stack">
    <section class="hero">
      <div>
        <h1>Turn any lecture, PDF or photo into a study kit</h1>
        <p>Upload once. Get notes, flashcards, quizzes, mind maps and an AI tutor that answers from your own material.</p>
        <div class="row"><a class="btn sun" href="#/upload">Add material</a>
          ${due.cards.length ? `<a class="btn ghost" href="#/review">Review ${due.cards.length} due card${due.cards.length > 1 ? 's' : ''}</a>` : ''}</div>
      </div>
      <div class="stat-row">
        <div class="stat"><b>${p.items}</b><span>materials</span></div>
        <div class="stat"><b>${p.due}</b><span>cards due today</span></div>
        <div class="stat"><b>${p.streak}</b><span>day streak</span></div>
        <div class="stat"><b>${p.quizzes ? p.quiz_avg + '%' : '–'}</b><span>quiz average</span></div>
      </div>
    </section>
    <section>
      <div class="row spread"><h2>Recent material</h2><a href="#/library">See all</a></div>
      ${recent.length ? `<div class="grid g3">${recent.map(itemCard).join('')}</div>` :
        `<div class="card empty"><h3>Nothing here yet</h3><p>Add your first lecture video, audio, PDF or photo of notes.</p><a class="btn" href="#/upload">Add material</a></div>`}
    </section>
    <section class="grid g2">
      <div class="dark-card"><h3>Activity, last 14 days</h3>${barChart(p.daily, { color: '#F5B301' })}</div>
      <div class="card"><h3>Weak areas</h3>${weakList(p.weak_areas)}</div>
    </section>
  </div>`;
});

function weakList(w) {
  if (!w.length) return '<p class="muted">Take a quiz and the topics you miss will show up here.</p>';
  const m = Math.max(...w.map(x => x.misses));
  return w.map(x => `<div class="bar-row"><span>${esc(x.topic)}</span><div class="bar"><i style="width:${x.misses / m * 100}%;background:var(--bad)"></i></div><b>${x.misses}</b></div>`).join('');
}

/* ---------- upload ---------- */
route(/^\/upload$/, async () => {
  view().innerHTML = `<div class="stack">
    <h1>Add material</h1>
    <div class="card stack">
      <div class="drop" id="drop" tabindex="0"><h3>Drop files here or click to choose</h3>
        <p class="muted">Video, audio, PDF, photo of notes, or a text file. Hindi and English both work. Large files are fine.</p>
        <input id="file" type="file" multiple hidden accept="video/*,audio/*,.pdf,image/*,.txt,.md"></div>
      <div class="opts-row">
        <label class="field">Video / audio transcription
          <select id="tmode">
            <option value="auto">Auto (recommended)</option>
            <option value="gemini">Fast: Gemini cloud</option>
            <option value="local">Private: on this computer (slow)</option></select>
          <span class="muted small" style="font-weight:400">Auto: clips up to 5 minutes run on this computer, longer lectures use Gemini, which takes minutes instead of hours. Gemini uses your API quota.</span></label>
        <label class="field">Background noise reduction
          <select id="dn"><option value="auto">Auto (only for short clips)</option><option value="on">Always (slower)</option><option value="off">Never</option></select>
          <span class="muted small" style="font-weight:400">Long lectures skip it: it adds a lot of time and speech recognition copes with normal classroom noise.</span></label>
        <label class="field">Spoken language
          <select id="lang"><option value="auto">Auto-detect</option><option value="en">English</option><option value="hi">Hindi</option></select>
          <span class="muted small" style="font-weight:400">Only change this if auto-detect gets it wrong (for example mixed Hindi + English).</span></label>
      </div>
    </div>
    <div id="queue" class="stack"></div></div>`;
  const drop = $('#drop'), file = $('#file');
  drop.onclick = () => file.click();
  drop.onkeydown = e => { if (e.key === 'Enter') file.click(); };
  drop.ondragover = e => { e.preventDefault(); drop.classList.add('over'); };
  drop.ondragleave = () => drop.classList.remove('over');
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove('over'); send([...e.dataTransfer.files]); };
  file.onchange = () => send([...file.files]);
  const q = $('#queue');
  function send(files) {
    files.forEach(f => {
      const row = document.createElement('div');
      row.className = 'card flat stack';
      row.innerHTML = `<div class="row spread"><b>${esc(f.name)}</b><span class="chip sun st">Uploading 0%</span></div>
        <div class="bar"><i style="width:0%"></i></div><div class="small muted msg"></div>`;
      q.prepend(row);
      const fd = new FormData(); fd.append('file', f); fd.append('language', $('#lang').value);
      fd.append('transcribe', $('#tmode').value); fd.append('denoise', $('#dn').value);
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/upload');
      xhr.upload.onprogress = e => {
        if (!e.lengthComputable) return;
        const p = Math.round(e.loaded / e.total * 100);
        $('i', row).style.width = p + '%'; $('.st', row).textContent = `Uploading ${p}%`;
      };
      xhr.onload = () => {
        let d = {}; try { d = JSON.parse(xhr.responseText); } catch {}
        if (xhr.status !== 200) { $('.st', row).className = 'chip bad st'; $('.st', row).textContent = 'Failed'; $('.msg', row).textContent = d.detail || 'Upload failed'; return; }
        $('i', row).style.width = '0%'; pokeWatcher(); trackItem(row, d.id, d.kind);
      };
      xhr.onerror = () => { $('.st', row).className = 'chip bad st'; $('.st', row).textContent = 'Failed'; $('.msg', row).textContent = 'Could not reach the app. Is it still running?'; };
      xhr.send(fd);
    });
  }
  function trackItem(row, id, kind) {
    const long = kind === 'video' || kind === 'audio';
    const t = setInterval(async () => {
      if (!document.body.contains(row)) return clearInterval(t);
      try {
        const it = await api('/items/' + id);
        $('i', row).style.width = Math.max(1, it.progress * 100) + '%';
        $('.st', row).textContent = `${Math.round(it.progress * 100)}% · ${it.stage}`;
        const j = it.job, eta = j && fmtEta(j.eta_seconds);
        let msg = '';
        if (it.status === 'error') msg = it.error;
        else if (it.status !== 'ready') {
          msg = (eta ? `${eta} left for transcription. ` : (j && j.engine && j.chunks_total ? 'Measuring speed… ' : '')) +
            `You can leave this page. It keeps working in the background while the app window stays open; find it in Library, and you will get a notice when it is done.`;
        }
        $('.msg', row).textContent = msg;
        if (it.status === 'ready') {
          clearInterval(t); $('.st', row).className = 'chip good st'; $('.st', row).textContent = 'Ready';
          $('.msg', row).innerHTML = `<a href="#/item/${id}/source">Open study kit</a>`;
        } else if (it.status === 'error') { clearInterval(t); $('.st', row).className = 'chip bad st'; $('.msg', row).innerHTML = `${esc(it.error)} <a href="#/item/${id}/source">Open to resume</a>`; }
      } catch {}
    }, 2000);
  }
});

/* ---------- library ---------- */
route(/^\/library$/, async () => {
  view().innerHTML = `<div class="stack"><div class="row spread"><h1>Library</h1><a class="btn" href="#/upload">Add material</a></div>
    <div class="row"><input id="q" type="search" placeholder="Search by title" style="max-width:320px">
    <select id="k" style="max-width:180px"><option value="">All types</option><option value="video">Video</option><option value="audio">Audio</option><option value="pdf">PDF</option><option value="image">Image</option><option value="text">Text</option></select></div>
    <div id="list"></div></div>`;
  async function load() {
    const items = await api(`/items?q=${encodeURIComponent($('#q').value)}&kind=${$('#k').value}`);
    $('#list').innerHTML = items.length ? `<div class="grid g3">${items.map(itemCard).join('')}</div>`
      : '<div class="card empty"><h3>No material found</h3><a href="#/upload">Add something</a></div>';
    clearPoll();
    if (items.some(i => i.status === 'queued' || i.status === 'processing')) pollTimer = setInterval(() => { if ($('#list')) load(); }, 4000);
  }
  let t; $('#q').oninput = () => { clearTimeout(t); t = setTimeout(load, 250); };
  $('#k').onchange = load; await load();
});

/* ---------- history ---------- */
route(/^\/history$/, async () => {
  const items = await api('/items');
  view().innerHTML = `<div class="stack"><h1>History</h1>
    <p class="muted">Everything you generated. Open any item to reuse it, or <a href="#/ask">ask questions across all your material</a>.</p>
    <div class="row"><input id="q" type="search" placeholder="Search titles and content" style="max-width:300px">
      <select id="k" style="max-width:180px"><option value="">All kinds</option>${Object.entries(KIND_LABEL).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select>
      <select id="i" style="max-width:240px"><option value="0">All materials</option>${items.map(i => `<option value="${i.id}">${esc(i.title)}</option>`).join('')}</select></div>
    <div id="list" class="stack"></div></div>`;
  async function load() {
    const rows = await api(`/history?q=${encodeURIComponent($('#q').value)}&kind=${$('#k').value}&item_id=${$('#i').value}`);
    $('#list').innerHTML = rows.length ? rows.map(r => `<div class="card flat row spread">
      <a href="#/item/${r.item_id}/${KIND_TAB[r.kind]}?o=${r.id}" style="text-decoration:none;color:inherit;flex:1;min-width:220px">
        <span class="chip ${r.kind === 'notes' ? 'teal' : r.kind === 'quiz' ? 'violet' : 'sun'}">${KIND_LABEL[r.kind] || r.kind}</span>
        <b style="margin-left:8px">${esc(r.title)}</b><div class="muted small">${ago(r.created_at)}</div></a>
      <button class="btn ghost sm" data-del="${r.id}">Delete</button></div>`).join('')
      : '<div class="card empty"><h3>Nothing generated yet</h3><p>Open a material and generate notes, a quiz or flashcards.</p></div>';
    $$('[data-del]').forEach(b => b.onclick = async () => { if (confirm('Delete this generated item?')) { await api('/outputs/' + b.dataset.del, { method: 'DELETE' }); load(); refreshDue(); } });
  }
  let t; $('#q').oninput = () => { clearTimeout(t); t = setTimeout(load, 250); };
  $('#k').onchange = load; $('#i').onchange = load; await load();
});

/* ---------- progress ---------- */
route(/^\/progress$/, async () => {
  const p = await api('/progress');
  const tile = (n, l, c) => `<div class="card flat"><div class="big-score" style="font-size:2.4rem;color:${c}">${n}</div><span class="muted">${l}</span></div>`;
  view().innerHTML = `<div class="stack"><h1>Progress</h1>
    <div class="dark-card"><h3>Your report</h3><p style="margin:0;color:#D5E1EC">${esc(p.report)}</p></div>
    <div class="grid g3">${tile(p.items, 'materials', 'var(--teal)')}${tile(p.by_kind.notes || 0, 'notes sets', 'var(--violet)')}${tile(p.cards_total, 'flashcards', '#B98700')}
      ${tile(p.quizzes, 'quizzes taken', 'var(--teal)')}${tile(p.chats, 'questions asked', 'var(--violet)')}${tile(p.streak, 'day streak', '#B98700')}</div>
    <div class="grid g2">
      <div class="card"><h3>Daily activity</h3>${barChart(p.daily)}</div>
      <div class="card"><h3>Weekly activity</h3>${barChart(p.weekly, { color: '#7C3AED' })}</div>
      <div class="card"><h3>Quiz scores</h3>${lineChart(p.quiz_perf)}</div>
      <div class="card"><h3>Weak areas</h3>${weakList(p.weak_areas)}</div>
    </div>
    <h2>Achievements</h2>
    <div class="grid g3">${p.achievements.map(a => `<div class="card flat ach ${a.earned ? 'on' : ''}"><div class="medal">${a.earned ? '★' : Math.round(a.value / a.target * 100) + '%'}</div>
      <div><b>${esc(a.title)}</b><div class="muted small">${esc(a.desc)}</div></div></div>`).join('')}</div></div>`;
});

/* ---------- spaced repetition review (SM-2) ---------- */
route(/^\/review$/, async () => {
  const itemId = query().get('item') || 0;
  const { cards, total_cards } = await api('/review/due?item_id=' + itemId);
  if (!cards.length) {
    view().innerHTML = `<div class="stack"><h1>Review cards</h1><div class="card empty"><h3>${total_cards ? 'You are all caught up' : 'No flashcards yet'}</h3>
      <p>${total_cards ? 'Come back tomorrow. Cards return right before you would forget them.' : 'Generate flashcards from any material and they appear here automatically.'}</p><a class="btn" href="#/library">Open library</a></div></div>`;
    return;
  }
  let i = 0, flipped = false, done = 0;
  const draw = () => {
    if (i >= cards.length) {
      view().innerHTML = `<div class="card empty"><div class="big-score">${done}</div><h3>cards reviewed</h3><p>Nice work. Your next reviews are scheduled automatically.</p><a class="btn" href="#/">Back home</a></div>`;
      refreshDue(); return;
    }
    const c = cards[i];
    view().innerHTML = `<div class="stack" style="max-width:680px;margin:auto"><div class="row spread"><h1 style="margin:0">Review</h1><span class="chip violet">${i + 1} / ${cards.length}</span></div>
      <div class="bar"><i style="width:${i / cards.length * 100}%"></i></div>
      <div class="flip ${flipped ? 'on' : ''}" id="fl"><div class="inner"><div class="face front"><div class="ft">${esc(c.front)}</div></div><div class="face back"><div class="ft">${esc(c.back)}</div></div></div></div>
      <p class="muted small" style="text-align:center">From: ${esc(c.title)} · click the card to flip</p>
      <div class="row" style="justify-content:center">${flipped ? `
        <button class="btn" style="background:var(--bad)" data-q="1">Again</button>
        <button class="btn sun" data-q="3">Hard</button>
        <button class="btn" data-q="4">Good</button>
        <button class="btn violet" data-q="5">Easy</button>` : '<button class="btn navy" id="show">Show answer</button>'}</div></div>`;
    $('#fl').onclick = () => { flipped = !flipped; draw(); };
    const show = $('#show'); if (show) show.onclick = () => { flipped = true; draw(); };
    $$('[data-q]').forEach(b => b.onclick = async () => {
      await api(`/cards/${c.id}/review`, { method: 'POST', body: { quality: +b.dataset.q } });
      done++; i++; flipped = false; draw();
    });
  };
  draw();
});

/* ---------- ask everything ---------- */
route(/^\/ask$/, async () => {
  view().innerHTML = `<div class="stack"><h1>Ask everything</h1><p class="muted">Questions are answered from all your uploaded material. Answers show where they came from.</p><div id="chat"></div></div>`;
  await chatPanel($('#chat'), 'all');
});
