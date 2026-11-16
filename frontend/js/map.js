// Offline-friendly schematic map: pure SVG (no tile server), clinics placed from real lat/lon values.
import { h } from './ui.js';

const NS = 'http://www.w3.org/2000/svg';
const s = (tag, attrs = {}, ...kids) => {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) el.setAttribute(k, v);
  kids.flat().forEach((c) => c !== null && c !== undefined && el.append(c.nodeType ? c : document.createTextNode(String(c))));
  return el;
};
const RISK_FILL = { low: '#16a34a', moderate: '#e8b020', high: '#ea580c', critical: '#dc2626', none: '#8a97a8' };

/**
 * clinics: [{id,name,lat,lon,catchment_pop}]; risk: {clinicId: level}; surplus: Set of donor clinic ids;
 * proposals: transfer proposals to draw as arrows; selected: proposal key; onSelect(key|null); onClinic(id)
 */
export function renderMap({ clinics, risk = {}, surplus = new Set(), proposals = [], selected = null, onSelect = () => {}, onClinic = () => {} }) {
  const W = 820, H = 520, pad = 60;
  const lats = clinics.map((c) => c.lat), lons = clinics.map((c) => c.lon);
  const latMin = Math.min(...lats), latMax = Math.max(...lats), lonMin = Math.min(...lons), lonMax = Math.max(...lons);
  const kx = Math.cos(((latMin + latMax) / 2) * Math.PI / 180);
  const spanX = (lonMax - lonMin) * kx || 1, spanY = latMax - latMin || 1;
  const scale = Math.min((W - 2 * pad) / spanX, (H - 2 * pad) / spanY);
  const offX = (W - spanX * scale) / 2, offY = (H - spanY * scale) / 2;
  const P = (c) => [offX + (c.lon - lonMin) * kx * scale, H - offY - (c.lat - latMin) * scale];
  const by = Object.fromEntries(clinics.map((c) => [c.id, c]));

  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Schematic map of clinics and proposed medicine transfers' });
  const defs = s('defs', {},
    s('marker', { id: 'arrow', viewBox: '0 0 10 10', refX: 8, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' }, s('path', { d: 'M0,0 L10,5 L0,10 z', fill: '#0f9f93' })),
    s('marker', { id: 'arrow-sel', viewBox: '0 0 10 10', refX: 8, refY: 5, markerWidth: 8, markerHeight: 8, orient: 'auto-start-reverse' }, s('path', { d: 'M0,0 L10,5 L0,10 z', fill: '#1d6fd1' })));
  svg.append(defs);
  for (let i = 1; i < 6; i++) {
    svg.append(s('line', { x1: (W / 6) * i, x2: (W / 6) * i, y1: 0, y2: H, stroke: '#dbe6f1', 'stroke-width': 1 }));
    if (i < 4) svg.append(s('line', { x1: 0, x2: W, y1: (H / 4) * i, y2: (H / 4) * i, stroke: '#dbe6f1', 'stroke-width': 1 }));
  }
  // faint road links: each clinic to its two nearest neighbours (illustrative only)
  const drawn = new Set();
  for (const c of clinics) {
    const near = clinics.filter((o) => o.id !== c.id).map((o) => ({ o, d: Math.hypot(P(c)[0] - P(o)[0], P(c)[1] - P(o)[1]) })).sort((a, b) => a.d - b.d).slice(0, 2);
    for (const { o } of near) {
      const k = [c.id, o.id].sort().join('-');
      if (drawn.has(k)) continue; drawn.add(k);
      svg.append(s('line', { x1: P(c)[0], y1: P(c)[1], x2: P(o)[0], y2: P(o)[1], stroke: '#c5d3e3', 'stroke-width': 2, 'stroke-dasharray': '2 5', 'stroke-linecap': 'round' }));
    }
  }
  for (const p of proposals) {
    const a = by[p.donor_id], b = by[p.receiver_id];
    if (!a || !b) continue;
    const [x1, y1] = P(a), [x2, y2] = P(b);
    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
    const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy) || 1;
    const cx = mx - (dy / len) * 28, cy = my + (dx / len) * 28;
    const sel = p.key === selected;
    const path = s('path', { d: `M${x1},${y1} Q${cx},${cy} ${x2},${y2}`, fill: 'none', stroke: sel ? '#1d6fd1' : '#0f9f93', 'stroke-width': sel ? 4 : 2.5, 'marker-end': `url(#${sel ? 'arrow-sel' : 'arrow'})`, opacity: selected && !sel ? 0.35 : 0.95, style: 'cursor:pointer' });
    path.append(s('title', {}, `${p.quantity} ${p.unit} of ${p.short_name}: ${p.donor_name} to ${p.receiver_name} (${p.road_km} km)`));
    path.addEventListener('click', () => onSelect(sel ? null : p.key));
    svg.append(path);
    if (sel) {
      const tx = 0.25 * x1 + 0.5 * cx + 0.25 * x2, ty = 0.25 * y1 + 0.5 * cy + 0.25 * y2;
      svg.append(s('rect', { x: tx - 52, y: ty - 25, width: 104, height: 34, rx: 8, fill: '#0b1f3a' }));
      svg.append(s('text', { x: tx, y: ty - 12, 'text-anchor': 'middle', fill: '#fff', style: 'font-size:12px;font-weight:700' }, `${p.quantity} ${p.unit}`));
      svg.append(s('text', { x: tx, y: ty + 3, 'text-anchor': 'middle', fill: '#9fc3f0', style: 'font-size:11px' }, `${p.road_km} km - ${p.travel_hours} h`));
    }
  }
  for (const c of clinics) {
    const [x, y] = P(c);
    const r = 9 + Math.sqrt(c.catchment_pop) / 22;
    const g = s('g', { style: 'cursor:pointer', tabindex: 0, role: 'button', 'aria-label': `${c.name}, risk ${risk[c.id] || 'unknown'}` });
    if (surplus.has(c.id)) g.append(s('circle', { cx: x, cy: y, r: r + 6, fill: 'none', stroke: '#0f9f93', 'stroke-width': 3, 'stroke-dasharray': '4 3' }));
    g.append(s('circle', { cx: x, cy: y, r, fill: RISK_FILL[risk[c.id] || 'none'], stroke: '#fff', 'stroke-width': 3 }));
    g.append(s('text', { x, y: y + r + 15, 'text-anchor': 'middle', style: 'font-size:11.5px;font-weight:600;fill:#12315c;paint-order:stroke;stroke:#f4f9fd;stroke-width:3px' }, c.name.replace(/ (Health Post|Sub-Centre|PHC|Dispensary|Rural Clinic|Clinic)$/, '')));
    g.append(s('title', {}, `${c.name} - population ${c.catchment_pop.toLocaleString()}`));
    g.addEventListener('click', () => onClinic(c.id));
    g.addEventListener('keydown', (e) => { if (e.key === 'Enter') onClinic(c.id); });
    svg.append(g);
  }
  // scale bar (approximate: 1 degree latitude = 111 km)
  const kmPx = scale / 111;
  const barKm = 25;
  svg.append(s('line', { x1: 24, x2: 24 + barKm * kmPx, y1: H - 22, y2: H - 22, stroke: '#12315c', 'stroke-width': 3 }));
  svg.append(s('text', { x: 24, y: H - 30, style: 'font-size:11px;fill:#12315c' }, `${barKm} km (straight line)`));
  return h('div', { class: 'map-box' }, svg);
}

export function mapLegend() {
  const dot = (c, t) => h('span', null, h('i', { class: 'box', style: `background:${c}` }), t);
  return h('div', { class: 'legend' },
    dot(RISK_FILL.low, 'Adequate'), dot(RISK_FILL.moderate, 'Watch'), dot(RISK_FILL.high, 'High risk'), dot(RISK_FILL.critical, 'Critical'),
    h('span', null, h('i', { class: 'box', style: 'background:#fff;border:2px dashed #0f9f93' }), 'Has surplus to share'),
    h('span', null, h('i', { style: 'background:#0f9f93' }), 'Proposed transfer (needs approval)'));
}
