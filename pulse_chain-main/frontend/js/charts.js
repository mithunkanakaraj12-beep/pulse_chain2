// Dependency-free SVG charts. All draw into a <div class="chart"> and work offline.
import { h, fmt } from './ui.js';

const NS = 'http://www.w3.org/2000/svg';
const s = (tag, attrs = {}, ...kids) => {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  kids.flat().forEach((c) => c && el.append(c.nodeType ? c : document.createTextNode(c)));
  return el;
};

export const COLORS = { history: '#12315c', forecast: '#1d6fd1', band: '#1d6fd1', scenario: '#ea580c', baseline: '#8a97a8', teal: '#0f9f93', grid: '#e3e9f1' };

function niceMax(v) {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

function tooltipFor(wrap) {
  const tip = h('div', { class: 'tip' });
  wrap.append(tip);
  return {
    show(x, y, title, rows) {
      tip.innerHTML = '';
      tip.append(h('b', null, title), ...rows.map((r) => h('div', null, h('span', { style: `color:${r.color}` }, '\u25CF '), `${r.name}: `, r.value)));
      tip.style.left = `${x}px`; tip.style.top = `${y}px`; tip.style.display = 'block';
    },
    hide() { tip.style.display = 'none'; },
  };
}

/** Line chart with optional prediction bands and a forecast-region divider. */
export function lineChart({ labels, series, bands = [], splitIndex = null, height = 280, fmtY = (v) => fmt.num(v), yMax = null, ariaLabel = 'Line chart' }) {
  const W = 760, H = height, m = { l: 48, r: 14, t: 14, b: 30 };
  const n = labels.length;
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const all = [];
  series.forEach((se) => se.values.forEach((v) => v !== null && v !== undefined && all.push(v)));
  bands.forEach((b) => b.hi.forEach((v) => v !== null && v !== undefined && all.push(v)));
  const top = yMax || niceMax(Math.max(1, ...all) * 1.05);
  const X = (i) => m.l + (n === 1 ? iw / 2 : (i / (n - 1)) * iw);
  const Y = (v) => m.t + ih - (v / top) * ih;
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': ariaLabel });

  if (splitIndex !== null && splitIndex < n) {
    const x0 = X(splitIndex) - (iw / Math.max(1, n - 1)) / 2;
    svg.append(s('rect', { x: x0, y: m.t, width: m.l + iw - x0, height: ih, fill: '#eef5fd' }));
    svg.append(s('text', { x: x0 + 6, y: m.t + 12, 'font-weight': 700, fill: '#1d6fd1' }, 'Forecast'));
  }
  for (let i = 0; i <= 4; i++) {
    const v = (top / 4) * i, y = Y(v);
    svg.append(s('line', { x1: m.l, x2: W - m.r, y1: y, y2: y, stroke: COLORS.grid, 'stroke-width': 1 }));
    svg.append(s('text', { x: m.l - 8, y: y + 4, 'text-anchor': 'end' }, fmtY(v)));
  }
  const step = Math.max(1, Math.ceil(n / 7));
  for (let i = 0; i < n; i += step) svg.append(s('text', { x: X(i), y: H - 8, 'text-anchor': 'middle' }, fmt.short(labels[i])));

  for (const b of bands) {
    const idx = b.hi.map((_, i) => i).filter((i) => b.hi[i] !== null && b.hi[i] !== undefined);
    if (idx.length < 2) continue;
    const d = idx.map((i, k) => `${k ? 'L' : 'M'}${X(i)},${Y(b.hi[i])}`).join(' ') + ' ' +
      [...idx].reverse().map((i) => `L${X(i)},${Y(b.lo[i])}`).join(' ') + ' Z';
    svg.append(s('path', { d, fill: b.color, opacity: b.opacity ?? 0.16 }));
  }
  for (const se of series) {
    let d = '', pen = false;
    se.values.forEach((v, i) => {
      if (v === null || v === undefined) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${X(i)},${Y(v)} `; pen = true;
    });
    svg.append(s('path', { d, fill: 'none', stroke: se.color, 'stroke-width': se.width || 2.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round', 'stroke-dasharray': se.dashed ? '6 5' : null }));
  }
  const guide = s('line', { y1: m.t, y2: m.t + ih, stroke: '#8a97a8', 'stroke-dasharray': '3 3', visibility: 'hidden' });
  svg.append(guide);
  const hit = s('rect', { x: m.l, y: m.t, width: iw, height: ih, fill: 'transparent' });
  svg.append(hit);

  const wrap = h('div', { class: 'chart' }, svg);
  const tip = tooltipFor(wrap);
  const move = (ev) => {
    const box = svg.getBoundingClientRect();
    const px = ((ev.clientX - box.left) / box.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - m.l) / iw) * (n - 1))));
    guide.setAttribute('x1', X(i)); guide.setAttribute('x2', X(i)); guide.setAttribute('visibility', 'visible');
    const rows = series.filter((se) => se.values[i] !== null && se.values[i] !== undefined)
      .map((se) => ({ name: se.name, color: se.color, value: fmtY(se.values[i]) }));
    const band = bands.find((b) => b.hi[i] !== null && b.hi[i] !== undefined);
    if (band && band.label) rows.push({ name: band.label, color: band.color, value: `${fmtY(band.lo[i])} - ${fmtY(band.hi[i])}` });
    tip.show((X(i) / W) * box.width, (m.t / H) * box.height + 44, fmt.date(labels[i]), rows);
  };
  hit.addEventListener('mousemove', move);
  hit.addEventListener('touchstart', (e) => move(e.touches[0]), { passive: true });
  hit.addEventListener('mouseleave', () => { tip.hide(); guide.setAttribute('visibility', 'hidden'); });
  return wrap;
}

export function legend(items) {
  return h('div', { class: 'legend' }, items.map((it) => h('span', null, h('i', { class: it.box ? 'box' : '', style: `background:${it.color};${it.dashed ? 'background:repeating-linear-gradient(90deg,' + it.color + ' 0 5px,transparent 5px 9px)' : ''}` }), it.label)));
}

/** Vertical grouped bar chart. */
export function groupedBars({ categories, series, height = 260, fmtY = (v) => fmt.num(v, 1), ariaLabel = 'Bar chart' }) {
  const W = 760, H = height, m = { l: 48, r: 10, t: 12, b: 36 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const top = niceMax(Math.max(0.0001, ...series.flatMap((se) => se.values)) * 1.08);
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': ariaLabel });
  for (let i = 0; i <= 4; i++) {
    const v = (top / 4) * i, y = m.t + ih - (v / top) * ih;
    svg.append(s('line', { x1: m.l, x2: W - m.r, y1: y, y2: y, stroke: COLORS.grid }));
    svg.append(s('text', { x: m.l - 8, y: y + 4, 'text-anchor': 'end' }, fmtY(v)));
  }
  const gw = iw / categories.length, bw = Math.min(46, (gw * 0.7) / series.length);
  const wrap = h('div', { class: 'chart' }, svg);
  const tip = tooltipFor(wrap);
  categories.forEach((cat, ci) => {
    const gx = m.l + gw * ci + gw / 2;
    svg.append(s('text', { x: gx, y: H - 14, 'text-anchor': 'middle' }, cat));
    series.forEach((se, si) => {
      const v = se.values[ci], bh = (v / top) * ih;
      const x = gx - (bw * series.length) / 2 + si * bw;
      const r = s('rect', { x: x + 1, y: m.t + ih - bh, width: bw - 2, height: Math.max(0, bh), rx: 4, fill: se.color });
      r.addEventListener('mousemove', (ev) => { const box = svg.getBoundingClientRect(); tip.show(ev.clientX - box.left, ev.clientY - box.top, cat, [{ name: se.name, color: se.color, value: fmtY(v) }]); });
      r.addEventListener('mouseleave', tip.hide);
      svg.append(r);
    });
  });
  return wrap;
}

/** Donut chart with a centre label. parts: [{label, value, color}] */
export function donut({ parts, size = 190, center = '', sub = '' }) {
  const total = parts.reduce((a, p) => a + p.value, 0) || 1;
  const r = 70, c = 2 * Math.PI * r;
  const svg = s('svg', { viewBox: '0 0 200 200', width: size, height: size, role: 'img', 'aria-label': parts.map((p) => `${p.label}: ${p.value}`).join(', ') });
  svg.append(s('circle', { cx: 100, cy: 100, r, fill: 'none', stroke: '#eef2f7', 'stroke-width': 26 }));
  let off = 0;
  for (const p of parts) {
    if (!p.value) continue;
    const len = (p.value / total) * c;
    svg.append(s('circle', { cx: 100, cy: 100, r, fill: 'none', stroke: p.color, 'stroke-width': 26, 'stroke-dasharray': `${len} ${c - len}`, 'stroke-dashoffset': -off, transform: 'rotate(-90 100 100)' }));
    off += len;
  }
  svg.append(s('text', { x: 100, y: 104, 'text-anchor': 'middle', style: 'font-size:30px;font-weight:800;fill:#0b1f3a' }, center));
  svg.append(s('text', { x: 100, y: 124, 'text-anchor': 'middle', style: 'font-size:11px' }, sub));
  return svg;
}

export function sparkline(values, color = COLORS.forecast, w = 110, hgt = 28) {
  const max = Math.max(1, ...values), min = 0;
  const pts = values.map((v, i) => `${(i / Math.max(1, values.length - 1)) * w},${hgt - 2 - ((v - min) / (max - min)) * (hgt - 4)}`).join(' ');
  return s('svg', { viewBox: `0 0 ${w} ${hgt}`, width: w, height: hgt, 'aria-hidden': 'true' }, s('polyline', { points: pts, fill: 'none', stroke: color, 'stroke-width': 2 }));
}
