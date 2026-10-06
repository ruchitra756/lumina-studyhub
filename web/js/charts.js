/* SVG charts, mind map and concept graph renderers. */
const PALETTE = ['#1E6F70', '#7C3AED', '#F5B301', '#E5484D', '#2FA36B', '#3B82F6', '#EC4899', '#F97316'];

function barChart(data, { color = '#1E6F70', h = 170, max = null } = {}) {
  const w = 560, pad = 26, m = max || Math.max(1, ...data.map(d => d.value));
  const bw = (w - pad * 2) / data.length;
  const bars = data.map((d, i) => {
    const bh = Math.round((d.value / m) * (h - 50));
    const x = pad + i * bw + bw * 0.15;
    return `<rect x="${x}" y="${h - 24 - bh}" width="${bw * 0.7}" height="${Math.max(bh, 2)}" rx="5" fill="${color}"><title>${esc(d.label)}: ${d.value}</title></rect>
      <text x="${x + bw * 0.35}" y="${h - 8}" text-anchor="middle">${esc(d.label.slice(0, 6))}</text>
      ${d.value ? `<text x="${x + bw * 0.35}" y="${h - 28 - bh}" text-anchor="middle">${d.value}</text>` : ''}`;
  }).join('');
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" width="100%" role="img">${bars}</svg>`;
}

function lineChart(data, { color = '#7C3AED', h = 170 } = {}) {
  if (!data.length) return '<p class="muted">No quizzes yet.</p>';
  const w = 560, pad = 30;
  const step = data.length > 1 ? (w - pad * 2) / (data.length - 1) : 0;
  const pts = data.map((d, i) => [pad + i * step, h - 26 - (d.value / 100) * (h - 56)]);
  const path = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  const dots = pts.map((p, i) => `<circle cx="${p[0]}" cy="${p[1]}" r="5" fill="${color}"><title>${data[i].value}%</title></circle>
    <text x="${p[0]}" y="${p[1] - 10}" text-anchor="middle">${data[i].value}%</text>`).join('');
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" width="100%" role="img">
    <line x1="${pad}" x2="${w - pad}" y1="${h - 26}" y2="${h - 26}" stroke="#E2E7EF"/>
    <path d="${path}" fill="none" stroke="${color}" stroke-width="3" stroke-linejoin="round"/>${dots}</svg>`;
}

/* ---- text measuring + wrapping: node sizes come from the REAL rendered text width, never from a guess ---- */
let _mctx = null;
function measureText(text, size, weight = 600) {
  try {
    _mctx = _mctx || document.createElement('canvas').getContext('2d');
    _mctx.font = `${weight} ${size}px "Plus Jakarta Sans", system-ui, "Segoe UI", sans-serif`;
    return _mctx.measureText(text).width * 1.05;   // small safety margin for font differences
  } catch { return text.length * size * 0.64; }
}
function wrapLabel(text, maxW, size, maxLines = 3) {
  const words = String(text).split(/\s+/).filter(Boolean);
  let lines = [], cur = '';
  words.forEach(w => {
    const test = cur ? cur + ' ' + w : w;
    if (!cur || measureText(test, size) <= maxW) cur = test; else { lines.push(cur); cur = w; }
  });
  if (cur) lines.push(cur);
  if (lines.length > maxLines) {
    lines = lines.slice(0, maxLines);
    lines[maxLines - 1] = lines[maxLines - 1].replace(/\s*\S*$/, '') + '…';
  }
  return lines.length ? lines : [''];
}
const tspans = (lines, x, cy, size, lh) => lines.map((l, i) =>
  `<tspan x="${x}" y="${(cy - lines.length * lh / 2 + lh * (i + 0.5) + size * 0.35).toFixed(1)}">${esc(l)}</tspan>`).join('');

/* ---- mind map: left-to-right tree, one colour per main branch, long labels wrap instead of being cut ---- */
function renderMindMap(root) {
  const PADX = 16, GAPX = 64, GAPY = 12;
  const MAXW = [300, 240, 230, 220, 210];
  const size = d => [17, 14, 13, 12.5, 12][Math.min(d, 4)];
  const colW = []; let maxDepth = 0;
  (function prep(n, d) {
    n.d = d; maxDepth = Math.max(maxDepth, d);
    n.label = (n.icon ? n.icon + ' ' : '') + (n.title || '');
    n.size = size(d); n.lh = n.size * 1.3;
    n.lines = wrapLabel(n.label, MAXW[Math.min(d, 4)] - PADX * 2, n.size, 3);
    n.w = Math.max(...n.lines.map(l => measureText(l, n.size))) + PADX * 2;
    n.h = n.lines.length * n.lh + (d === 0 ? 20 : 14);
    colW[d] = Math.max(colW[d] || 0, n.w);
    (n.children || []).forEach(c => prep(c, d + 1));
  })(root, 0);
  const xs = []; let acc = 20;
  for (let d = 0; d <= maxDepth; d++) { xs[d] = acc; acc += colW[d] + GAPX; }
  const bottom = []; let cursor = 20;
  const shift = (n, dy) => { n.cy += dy; bottom[n.d] = Math.max(bottom[n.d] ?? -1e9, n.cy + n.h / 2); cursor = Math.max(cursor, n.cy + n.h / 2 + GAPY); (n.children || []).forEach(c => shift(c, dy)); };
  (function place(n) {
    if (!n.children || !n.children.length) {
      const top = Math.max(cursor, (bottom[n.d] ?? -1e9) + GAPY);
      n.cy = top + n.h / 2; cursor = top + n.h + GAPY; bottom[n.d] = n.cy + n.h / 2; return;
    }
    n.children.forEach(place);
    n.cy = (n.children[0].cy + n.children[n.children.length - 1].cy) / 2;
    const need = (bottom[n.d] ?? -1e9) + GAPY - (n.cy - n.h / 2);
    if (need > 0) shift(n, need);
    bottom[n.d] = Math.max(bottom[n.d] ?? -1e9, n.cy + n.h / 2);
    cursor = Math.max(cursor, n.cy + n.h / 2 + GAPY);
  })(root);
  const W = acc - GAPX + 20, H = Math.max(...bottom.filter(b => b !== undefined)) + 24;
  let edges = '', nodes = '';
  (function draw(n, color) {
    const x = xs[n.d], y = n.cy;
    (n.children || []).forEach((c, i) => {
      const col = n.d === 0 ? PALETTE[i % PALETTE.length] : color;
      const sx = x + n.w, cx = xs[c.d];
      edges += `<path d="M${sx} ${y} C ${sx + 30} ${y}, ${cx - 30} ${c.cy}, ${cx} ${c.cy}" fill="none" stroke="${col}" stroke-width="${Math.max(1.5, 3.5 - c.d * 0.6)}" opacity=".75"/>`;
      draw(c, col);
    });
    const fill = n.d === 0 ? '#14324A' : n.d === 1 ? color : color + '22';
    const txt = n.d <= 1 ? '#fff' : '#14324A';
    nodes += `<g><title>${esc(n.label)}</title><rect x="${x}" y="${y - n.h / 2}" width="${n.w}" height="${n.h}" rx="${Math.min(n.h / 2, 18)}" fill="${fill}" stroke="${color}" stroke-width="1.5"/>
      <text font-size="${n.size}" fill="${txt}">${tspans(n.lines, x + PADX, y, n.size, n.lh)}</text></g>`;
  })(root, '#14324A');
  return `<svg id="mm-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">${edges}${nodes}</svg>`;
}

/* ---- concept dependency graph: layered by longest prerequisite chain; long names wrap to two lines ---- */
function renderConceptGraph(g) {
  const nodes = (g.nodes || []).map(n => ({ ...n, level: 0 }));
  const byId = Object.fromEntries(nodes.map(n => [n.id, n]));
  const edges = (g.edges || []).filter(e => byId[e.from] && byId[e.to] && e.from !== e.to);
  for (let pass = 0; pass < nodes.length; pass++) {
    let changed = false;
    edges.forEach(e => { if (byId[e.to].level < byId[e.from].level + 1 && byId[e.from].level < nodes.length) { byId[e.to].level = byId[e.from].level + 1; changed = true; } });
    if (!changed) break;
  }
  const cols = {};
  nodes.forEach(n => (cols[n.level] ||= []).push(n));
  const NW = 200, NH = 54, GX = 90, GY = 20, SZ = 13, PAD = 12;
  nodes.forEach(n => { n.lines = wrapLabel(n.label, NW - PAD * 2, SZ, 2); });
  const nLevels = Math.max(...Object.keys(cols).map(Number), 0) + 1;
  const tallest = Math.max(...Object.values(cols).map(c => c.length), 1);
  const H = tallest * (NH + GY) + 20, W = nLevels * (NW + GX) + 20;
  Object.entries(cols).forEach(([lv, arr]) => {
    const off = (H - arr.length * (NH + GY)) / 2;
    arr.forEach((n, i) => { n.x = 10 + lv * (NW + GX); n.y = off + i * (NH + GY); });
  });
  const lines = edges.map(e => {
    const a = byId[e.from], b = byId[e.to];
    return `<path class="edge" data-from="${esc(e.from)}" data-to="${esc(e.to)}" d="M${a.x + NW} ${a.y + NH / 2} C ${a.x + NW + 45} ${a.y + NH / 2}, ${b.x - 45} ${b.y + NH / 2}, ${b.x} ${b.y + NH / 2}" fill="none" stroke="#9AA9B8" stroke-width="2" marker-end="url(#arr)"/>`;
  }).join('');
  const boxes = nodes.map(n => {
    const c = PALETTE[n.level % PALETTE.length];
    return `<g class="cnode" data-id="${esc(n.id)}" style="cursor:pointer"><title>${esc(n.label)}</title><rect x="${n.x}" y="${n.y}" width="${NW}" height="${NH}" rx="12" fill="${c}"/>
      <text text-anchor="middle" font-size="${SZ}" fill="#fff">${n.lines.map((l, i) => `<tspan x="${n.x + NW / 2}" y="${(n.y + NH / 2 - n.lines.length * SZ * 1.3 / 2 + SZ * 1.3 * (i + 0.5) + SZ * 0.35).toFixed(1)}">${esc(l)}</tspan>`).join('')}</text></g>`;
  }).join('');
  return `<svg id="cg-svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}"><defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#9AA9B8"/></marker></defs>${lines}${boxes}</svg>`;
}

function wireConceptGraph(root) {
  const edges = $$('.edge', root), nodes = $$('.cnode', root);
  const prereq = id => { const seen = new Set([id]); const q = [id];
    while (q.length) { const c = q.pop(); edges.forEach(e => { if (e.dataset.to === c && !seen.has(e.dataset.from)) { seen.add(e.dataset.from); q.push(e.dataset.from); } }); }
    return seen; };
  nodes.forEach(n => n.onclick = () => {
    const keep = prereq(n.dataset.id);
    nodes.forEach(x => x.style.opacity = keep.has(x.dataset.id) ? 1 : 0.2);
    edges.forEach(e => { const on = keep.has(e.dataset.from) && keep.has(e.dataset.to); e.style.opacity = on ? 1 : 0.1; e.setAttribute('stroke', on ? '#7C3AED' : '#9AA9B8'); });
  });
  root.ondblclick = () => { nodes.forEach(x => x.style.opacity = 1); edges.forEach(e => { e.style.opacity = 1; e.setAttribute('stroke', '#9AA9B8'); }); };
}
