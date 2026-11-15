// Small DOM + formatting helpers (no framework, no build step).
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return el;
}

export const fmt = {
  num: (n, d = 0) => (n === null || n === undefined ? '-' : Number(n).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d })),
  pct: (n, d = 0) => (n === null || n === undefined ? '-' : `${n > 0 ? '+' : ''}${Number(n).toFixed(d)}%`),
  date: (iso) => (iso ? new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '-'),
  short: (iso) => (iso ? new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) : '-'),
  days: (d) => (d === null || d === undefined ? '>180 d' : `${Math.floor(d)} d`),
  time: (t) => new Date(t).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }),
};

export const RISK_LABEL = { low: 'Adequate', moderate: 'Watch', high: 'High risk', critical: 'Critical' };
export const riskBadge = (risk) => h('span', { class: `badge ${risk}` }, RISK_LABEL[risk] || risk);
export const changeSpan = (pct, d = 0) => h('span', { class: pct === null || pct === undefined ? 'flat' : pct > 5 ? 'up' : pct < -5 ? 'down' : 'flat' }, fmt.pct(pct, d));
export const simBadge = () => h('span', { class: 'badge sim', title: 'Synthetic demo data' }, 'Simulated');

export function toast(message, kind = '') {
  const box = document.getElementById('toasts');
  const t = h('div', { class: `toast ${kind}` }, message);
  box.append(t);
  setTimeout(() => t.remove(), kind === 'err' ? 7000 : 4000);
}

export const loading = (msg = 'Loading...') => h('div', { class: 'loading' }, h('span', { class: 'spinner' }), ' ', msg);

export function errorBox(err) {
  return h('div', { class: 'err-box' }, h('strong', null, 'Could not load this view. '), String(err && err.message ? err.message : err));
}

export function tabs(options, current, onChange) {
  const wrap = h('div', { class: 'tabs', role: 'tablist' });
  for (const [value, label] of options) {
    wrap.append(h('button', { class: value === current ? 'on' : '', role: 'tab', 'aria-selected': value === current, onclick: () => onChange(value) }, label));
  }
  return wrap;
}

export function select(options, value, onChange, label) {
  const sel = h('select', { onchange: (e) => onChange(e.target.value), 'aria-label': label || '' },
    options.map(([v, text]) => h('option', { value: v, selected: v === value }, text)));
  return label ? h('label', { class: 'field' }, label, sel) : sel;
}

export const card = (title, sub, ...body) => h('section', { class: 'card' },
  title ? h('h2', null, title) : null, sub ? h('div', { class: 'sub' }, sub) : null, ...body);

export function riskBars(rows, key, limit) {
  // CSS bars: days of cover vs the clinic's delivery time (marker).
  const max = Math.max(60, ...rows.map((r) => Math.min(r[key] ?? 180, 180)));
  const list = h('div', { class: 'bars' });
  for (const r of rows.slice(0, limit || rows.length)) {
    const v = Math.min(r[key] ?? 180, 180);
    list.append(h('div', { class: 'bar-row', title: `${r.clinic_name}: ${r.days_to_stockout === null ? 'over 180' : r.days_to_stockout} days of stock; delivery takes ${r.lead_time_days} days` },
      h('div', { class: 'name' }, r.clinic_name),
      h('div', { class: 'bar-track' },
        h('div', { class: `bar-fill fill-${r.risk}`, style: `width:${(v / max) * 100}%` }),
        h('div', { class: 'bar-mark', style: `left:${(r.lead_time_days / max) * 100}%` })),
      h('div', { class: 'val' }, fmt.days(r[key]))));
  }
  return list;
}
