/* Core helpers: API calls, router, markdown, toasts. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const view = () => $('#view');
let pollTimer = null;

async function api(path, opts = {}) {
  const o = { method: opts.method || 'GET', headers: {} };
  if (opts.body instanceof FormData) o.body = opts.body;
  else if (opts.body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(opts.body); }
  let r;
  try { r = await fetch('/api' + path, o); }
  catch { throw new Error('Cannot reach the app. Is launch.py still running?'); }
  let data = null;
  try { data = await r.json(); } catch {}
  if (!r.ok) throw new Error((data && data.detail) || `Request failed (${r.status})`);
  return data;
}

function toast(msg, err = false) {
  const t = document.createElement('div');
  t.className = 'toast' + (err ? ' err' : '');
  t.textContent = msg;
  $('#toasts').append(t);
  setTimeout(() => t.remove(), err ? 7000 : 3200);
}

function busy(msg) {
  const o = document.createElement('div');
  o.className = 'overlay';
  o.innerHTML = `<div class="box"><div class="spinner"></div><b>${esc(msg)}</b><span class="muted small">Long material can take up to a minute.</span></div>`;
  document.body.append(o);
  return () => o.remove();
}

async function withBusy(msg, fn) {
  const done = busy(msg);
  try { return await fn(); }
  catch (e) { toast(e.message, true); throw e; }
  finally { done(); }
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

/* Overlay with a real stage name, progress bar and elapsed time (used for long AI jobs). */
function progressOverlay(title) {
  const o = document.createElement('div');
  o.className = 'overlay';
  o.innerHTML = `<div class="box wide"><div class="spinner"></div><b style="text-align:center">${esc(title)}</b>
    <div class="stage">Starting…</div><div class="bar"><i style="width:3%"></i></div>
    <span class="muted small note">Elapsed 0s</span></div>`;
  document.body.append(o);
  const t0 = Date.now();
  const tick = setInterval(() => {
    const s = Math.floor((Date.now() - t0) / 1000);
    $('.note', o).textContent = `Elapsed ${s >= 60 ? Math.floor(s / 60) + 'm ' : ''}${s % 60}s · long material can take a few minutes`;
  }, 1000);
  return {
    update(stage, pct) { $('.stage', o).textContent = stage; $('.bar i', o).style.width = Math.max(3, Math.round(pct * 100)) + '%'; },
    close() { clearInterval(tick); o.remove(); },
  };
}

/* Start a generation job and follow its progress until it finishes. Returns {id, kind}. */
async function runGeneration(itemId, kind, options, title) {
  const ov = progressOverlay(title);
  try {
    const { job_id } = await api(`/items/${itemId}/generate_async`, { method: 'POST', body: { kind, options } });
    for (;;) {
      await sleep(1000);
      const j = await api('/jobs/' + job_id);
      ov.update(j.stage, j.pct);
      if (j.status === 'done') return j.result;
      if (j.status === 'error') throw new Error(j.error || 'Generation failed');
    }
  } catch (e) { toast(e.message, true); throw e; }
  finally { ov.close(); }
}

function fmtEta(sec) {
  if (sec == null || !isFinite(sec)) return null;
  if (sec < 45) return 'less than a minute';
  const m = Math.round(sec / 60);
  if (m < 60) return `about ${m} min`;
  const h = Math.floor(m / 60);
  return `about ${h} h ${m % 60} min`;
}
function fmtElapsed(sec) {
  sec = Math.floor(sec || 0);
  return sec >= 3600 ? `${Math.floor(sec / 3600)}h ${Math.floor(sec % 3600 / 60)}m` : sec >= 60 ? `${Math.floor(sec / 60)}m ${sec % 60}s` : `${sec}s`;
}

/* Watches background processing from any page: nav badge, tab title, and a toast when something finishes. */
const watch = { prev: new Map(), timer: null };
async function watchActive() {
  let delay = 15000;
  try {
    const list = await api('/active');
    const ids = new Set(list.map(i => i.id));
    for (const [id, title] of watch.prev) {
      if (!ids.has(id)) {
        try {
          const it = await api('/items/' + id);
          if (it.status === 'ready') toast(`“${title}” is ready to study`);
          else if (it.status === 'error') toast(`“${title}” stopped: open it to resume`, true);
        } catch {}
      }
    }
    watch.prev = new Map(list.map(i => [i.id, i.title]));
    const pill = $('#proc-pill');
    if (pill) { pill.hidden = !list.length; pill.textContent = list.length; }
    const top = list[0];
    document.title = top ? `(${Math.round(top.progress * 100)}%) Lumina StudyHub` : 'Lumina StudyHub';
    if (list.length) delay = 4000;
  } catch {}
  watch.timer = setTimeout(watchActive, delay);
}
function pokeWatcher() { clearTimeout(watch.timer); watchActive(); }

function fmtTime(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(s).padStart(2, '0');
}
function ago(ts) {
  const d = (Date.now() / 1000 - ts);
  if (d < 60) return 'just now';
  if (d < 3600) return Math.floor(d / 60) + ' min ago';
  if (d < 86400) return Math.floor(d / 3600) + ' h ago';
  return new Date(ts * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}
const KIND_LABEL = { notes: 'Notes', flashcards: 'Flashcards', quiz: 'Quiz', mindmap: 'Mind map', audio: 'Audio recap',
  revision: 'Revision sheet', plan: '7-day plan', exam: 'Exam mode', concepts: 'Concept map' };
const KIND_TAB = { notes: 'notes', flashcards: 'flashcards', quiz: 'quiz', mindmap: 'mindmap', audio: 'audio',
  revision: 'revision', plan: 'plan', exam: 'exam', concepts: 'concepts' };

/* ---- markdown (small, safe: everything is escaped first) ---- */
function inline(t) {
  return t.replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>');
}
function md(src) {
  const lines = String(src || '').replace(/\r/g, '').split('\n');
  const out = []; const stack = []; let i = 0;
  const closeTo = n => { while (stack.length > n) out.push('</' + stack.pop() + '>'); };
  while (i < lines.length) {
    const raw = lines[i]; const line = esc(raw);
    if (/^```/.test(raw)) {
      closeTo(0); const code = []; i++;
      while (i < lines.length && !/^```/.test(lines[i])) code.push(esc(lines[i++]));
      i++; out.push('<pre><code>' + code.join('\n') + '</code></pre>'); continue;
    }
    if (/^\s*\|.*\|\s*$/.test(raw) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      closeTo(0);
      const cells = l => esc(l).trim().replace(/^\||\|$/g, '').split('|').map(c => inline(c.trim()));
      let html = '<table><thead><tr>' + cells(raw).map(c => `<th>${c}</th>`).join('') + '</tr></thead><tbody>';
      i += 2;
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) html += '<tr>' + cells(lines[i++]).map(c => `<td>${c}</td>`).join('') + '</tr>';
      out.push(html + '</tbody></table>'); continue;
    }
    let m;
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) { closeTo(0); const n = m[1].length + 1; out.push(`<h${n}>${inline(m[2])}</h${n}>`); }
    else if (/^\s*([-*•])\s+/.test(raw) || /^\s*\d+[.)]\s+/.test(raw)) {
      const ordered = /^\s*\d+[.)]\s+/.test(raw);
      const depth = Math.floor(raw.match(/^\s*/)[0].length / 2) + 1;
      while (stack.length < depth) { const t = ordered ? 'ol' : 'ul'; out.push('<' + t + '>'); stack.push(t); }
      closeTo(depth);
      const want = ordered ? 'ol' : 'ul';
      if (stack[depth - 1] !== want) { out.push('</' + stack.pop() + '>'); out.push('<' + want + '>'); stack.push(want); }
      out.push('<li>' + inline(line.replace(/^\s*([-*•]|\d+[.)])\s+/, '')) + '</li>');
    }
    else if (/^\s*(-{3,}|\*{3,})\s*$/.test(raw)) { closeTo(0); out.push('<hr>'); }
    else if (/^&gt;\s?/.test(line)) { closeTo(0); out.push('<blockquote>' + inline(line.replace(/^&gt;\s?/, '')) + '</blockquote>'); }
    else if (!raw.trim()) closeTo(0);
    else { closeTo(0); out.push('<p>' + inline(line) + '</p>'); }
    i++;
  }
  closeTo(0);
  return out.join('');
}

/* ---- router ---- */
const ROUTES = [];
function route(re, fn) { ROUTES.push([re, fn]); }
function clearPoll() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }
function go(hash) { location.hash = hash; }

async function navigate() {
  clearPoll();
  window.speechSynthesis && window.speechSynthesis.cancel();
  const hash = (location.hash.slice(1) || '/').split('?')[0];
  $('#side').classList.remove('open');
  $$('#nav a').forEach(a => {
    const n = a.dataset.nav;
    a.classList.toggle('on', n === '/' ? hash === '/' : hash.startsWith(n) || (n === '/library' && hash.startsWith('/item')));
  });
  for (const [re, fn] of ROUTES) {
    const m = hash.match(re);
    if (m) {
      try { await fn(...m.slice(1)); }
      catch (e) { view().innerHTML = `<div class="card empty"><h3>Something went wrong</h3><p>${esc(e.message)}</p></div>`; }
      window.scrollTo(0, 0);
      return;
    }
  }
  view().innerHTML = '<div class="card empty"><h3>Page not found</h3><a href="#/">Go home</a></div>';
}
function query() { return new URLSearchParams((location.hash.split('?')[1]) || ''); }

async function refreshDue() {
  try {
    const d = await api('/review/due');
    const p = $('#due-pill');
    p.hidden = !d.cards.length; p.textContent = d.cards.length;
  } catch {}
}

async function boot() {
  $('#menu-btn').onclick = () => $('#side').classList.toggle('open');
  window.addEventListener('hashchange', navigate);
  try {
    const h = await api('/health');
    const bad = [];
    if (!h.api_key) bad.push('Add GEMINI_API_KEY to .env');
    if (!h.ffmpeg) bad.push('Install ffmpeg');
    $('#health').innerHTML = bad.length
      ? `<span class="chip bad">Setup needed</span><br>${bad.map(esc).join('<br>')}`
      : `<span class="chip good">Ready</span> <span>${esc(h.model)}</span>`;
  } catch {}
  refreshDue();
  navigate();
  watchActive();
}
