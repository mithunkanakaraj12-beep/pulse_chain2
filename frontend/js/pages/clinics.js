import * as api from '../api.js';
import { h, fmt, select, card, riskBadge, changeSpan, simBadge } from '../ui.js';
import { lineChart, legend, COLORS } from '../charts.js';

const RANK = { low: 0, moderate: 1, high: 2, critical: 3 };

function clinicCard(c) {
  const so = c.soonest_stockout;
  return h('article', { class: 'card clinic-card', tabindex: 0, role: 'link', onclick: () => { location.hash = `#/clinics/${c.id}`; }, onkeydown: (e) => { if (e.key === 'Enter') location.hash = `#/clinics/${c.id}`; } },
    h('div', { class: 'card-head' }, h('div', null, h('h3', null, c.name), h('div', { class: 'muted small' }, `${c.id} - ${c.region_label}`)), riskBadge(c.worst_risk)),
    h('div', { class: 'kv' },
      h('span', null, 'Catchment'), h('span', null, `${fmt.num(c.catchment_pop)} people`),
      h('span', null, 'Delivery time'), h('span', null, `${c.lead_time_days} days`),
      h('span', null, 'Soonest stock-out'), h('span', null, so ? `${so.short_name}, ${fmt.days(so.days)}` : '-'),
      h('span', null, 'Pairs at risk'), h('span', null, c.pairs_by_risk.moderate + c.pairs_by_risk.high + c.pairs_by_risk.critical)),
    h('div', { class: 'kv' }, ['malaria', 'cholera', 'pneumonia'].map((d) => [h('span', null, `${c.disease_outlook[d].name} (4 wk)`), h('span', null, changeSpan(c.disease_outlook[d].change_pct))]).flat()));
}

async function list() {
  const { data } = await api.get('/api/clinics');
  const f = { q: '', region: 'all', risk: 'all', sort: 'risk' };
  const grid = h('div', { class: 'clinic-grid' });
  const count = h('span', { class: 'muted' });
  const regions = [...new Set(data.clinics.map((c) => c.region))];
  function draw() {
    let rows = data.clinics.filter((c) => (f.region === 'all' || c.region === f.region) && (f.risk === 'all' || c.worst_risk === f.risk)
      && (!f.q || `${c.name} ${c.id} ${c.health_worker}`.toLowerCase().includes(f.q.toLowerCase())));
    rows.sort((a, b) => f.sort === 'name' ? a.name.localeCompare(b.name) : f.sort === 'pop' ? b.catchment_pop - a.catchment_pop
      : (RANK[b.worst_risk] - RANK[a.worst_risk]) || ((a.soonest_stockout?.days ?? 999) - (b.soonest_stockout?.days ?? 999)));
    count.textContent = `${rows.length} of ${data.clinics.length} clinics`;
    grid.replaceChildren(...(rows.length ? rows.map(clinicCard) : [h('div', { class: 'empty' }, 'No clinics match these filters.')]));
  }
  draw();
  return h('div', null,
    h('div', { class: 'page-head' }, h('div', null, h('p', null, 'Every monitored clinic with its worst stock risk, forecast disease trend and soonest stock-out. Select a clinic for its inventory, trends and recommendations.')),
      count),
    h('div', { class: 'controls', style: 'margin-bottom:16px' },
      h('label', { class: 'field' }, 'Search', h('input', { type: 'search', placeholder: 'Clinic, ID or health worker', oninput: (e) => { f.q = e.target.value; draw(); } })),
      select([['all', 'All regions'], ...regions.map((r) => [r, data.clinics.find((c) => c.region === r).region_label])], f.region, (v) => { f.region = v; draw(); }, 'Region'),
      select([['all', 'Any risk'], ['critical', 'Critical'], ['high', 'High risk'], ['moderate', 'Watch'], ['low', 'Adequate']], f.risk, (v) => { f.risk = v; draw(); }, 'Worst risk'),
      select([['risk', 'Most urgent first'], ['name', 'Name'], ['pop', 'Population']], f.sort, (v) => { f.sort = v; draw(); }, 'Sort')),
    grid);
}

async function detail(id) {
  const { data } = await api.get(`/api/clinics/${encodeURIComponent(id)}`);
  const c = data.clinic;
  const trend = (d, title) => {
    const t = data.trends[d];
    const labels = [...t.history.map((p) => p.week), ...t.forecast.map((p) => p.week)];
    const nh = t.history.length, nf = t.forecast.length, last = t.history[nh - 1].value;
    return card(title, '52 weeks observed and 8 weeks forecast',
      lineChart({ labels, splitIndex: nh, height: 200, series: [
        { name: 'Observed', color: COLORS.history, values: [...t.history.map((p) => p.value), ...Array(nf).fill(null)] },
        { name: 'Forecast', color: COLORS.forecast, dashed: true, values: [...Array(nh - 1).fill(null), last, ...t.forecast.map((p) => p.median)] }],
        bands: [{ color: COLORS.band, label: 'Forecast range', lo: [...Array(nh - 1).fill(null), last, ...t.forecast.map((p) => p.lo)], hi: [...Array(nh - 1).fill(null), last, ...t.forecast.map((p) => p.hi)] }],
        ariaLabel: `${title} weekly cases at ${c.name}` }));
  };
  const inv = h('table', null, h('thead', null, h('tr', null, ['Medicine', 'Stock', 'Lasts', 'Delivery', 'Risk', 'Reorder'].map((t, i) => h('th', { class: i && i !== 4 ? 'num' : '' }, t)))),
    h('tbody', null, data.inventory.map((r) => h('tr', null, h('td', null, r.medicine_name), h('td', { class: 'num' }, `${fmt.num(r.usable_stock)} ${r.unit}`), h('td', { class: 'num' }, fmt.days(r.days_to_stockout)),
      h('td', { class: 'num' }, `${r.lead_time_days} d`), h('td', null, riskBadge(r.risk)), h('td', { class: 'num' }, r.reorder_qty ? `${fmt.num(r.reorder_qty)} by ${fmt.short(r.order_by_date)}` : '-')))));
  return h('div', null,
    h('p', null, h('a', { href: '#/clinics' }, '\u2190 All clinics')),
    h('div', { class: 'page-head' }, h('div', null, h('h2', { style: 'font-size:22px' }, c.name), h('p', null, `${c.region_label} - ${fmt.num(c.catchment_pop)} people - health worker ${c.health_worker} - ${c.phone} (fictional demo number)`)), h('div', null, riskBadge(c.worst_risk), ' ', simBadge())),
    h('div', { class: 'grid g-2' },
      card('Recommendations', 'Generated from the transparent inventory rules, not free-form AI text', h('ul', { class: 'rec-list' }, data.recommendations.map((r) => h('li', null, r)))),
      card('Nearby clinics', 'Estimated road distance and travel time', h('table', null, h('tbody', null, data.nearby.map((n) => h('tr', { class: 'clickable', onclick: () => { location.hash = `#/clinics/${n.clinic_id}`; } }, h('td', null, n.name), h('td', { class: 'num' }, `${n.road_km} km`), h('td', { class: 'num' }, `${n.travel_hours} h`))))))),
    h('div', { style: 'margin-top:16px' }, card('Inventory', 'Usable stock only - expired batches are excluded', h('div', { class: 'table-wrap' }, inv))),
    h('div', { class: 'grid g-3', style: 'margin-top:16px' }, trend('malaria', 'Malaria'), trend('cholera', 'Cholera'), trend('pneumonia', 'Pneumonia')),
    data.transfers.length ? h('div', { style: 'margin-top:16px' }, card('Transfer recommendations involving this clinic', 'Approve them on the Redistribution page',
      h('ul', { class: 'rec-list' }, data.transfers.map((p) => h('li', null, `${p.quantity} ${p.unit} of ${p.short_name}: ${p.donor_name} \u2192 ${p.receiver_name} (${p.road_km} km)`))))) : null);
}

export async function clinics(params) { return params[0] ? detail(params[0]) : list(); }
