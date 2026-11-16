import * as api from '../api.js';
import { h, fmt, tabs, select, card, riskBadge, changeSpan, simBadge, riskBars } from '../ui.js';
import { lineChart, legend, groupedBars, donut, COLORS } from '../charts.js';

const RISK_COLORS = { low: '#16a34a', moderate: '#e8b020', high: '#ea580c', critical: '#dc2626' };
const RISK_NAMES = { low: 'Adequate', moderate: 'Watch', high: 'High risk', critical: 'Critical' };

function seriesChart(series, name, unit) {
  const labels = [...series.history.map((p) => p.week), ...series.forecast.map((p) => p.week)];
  const nh = series.history.length, nf = series.forecast.length;
  const pad = (arr, before) => (before ? [...Array(nh).fill(null), ...arr] : [...arr, ...Array(nf).fill(null)]);
  const histVals = [...series.history.map((p) => p.value), ...Array(nf).fill(null)];
  // join the forecast line to the last observed point so the eye follows it
  const fVals = [...Array(nh - 1).fill(null), series.history[nh - 1].value, ...series.forecast.map((p) => p.median)];
  const lo = [...Array(nh - 1).fill(null), series.history[nh - 1].value, ...series.forecast.map((p) => p.lo)];
  const hi = [...Array(nh - 1).fill(null), series.history[nh - 1].value, ...series.forecast.map((p) => p.hi ?? p.median)];
  return lineChart({
    labels, splitIndex: nh,
    series: [{ name: `${name} (observed)`, color: COLORS.history, values: histVals }, { name: `${name} (forecast)`, color: COLORS.forecast, values: fVals, dashed: true }],
    bands: [{ color: COLORS.band, lo, hi, label: 'Forecast range' }],
    fmtY: (v) => fmt.num(v), ariaLabel: `${name} weekly ${unit}, observed and forecast`,
  });
}

export async function dashboard(_params, ctx) {
  const [{ data: sum }, { data: inv, fromCache }] = await Promise.all([api.get('/api/summary'), api.get('/api/inventory')]);
  const k = sum.kpis;
  const state = { disease: 'malaria', medicine: 'act', invMed: 'act', regionDisease: 'malaria' };
  const root = h('div');

  const outlook = sum.disease_outlook;
  const kpis = h('div', { class: 'grid g-kpi' },
    h('div', { class: 'card kpi' }, h('div', { class: 'label' }, 'Clinics monitored'), h('div', { class: 'value' }, k.clinics_monitored), h('div', { class: 'note' }, `${sum.regions.length} regions, ${fmt.num(sum.regional_activity.highland.population + sum.regional_activity.riverine.population + sum.regional_activity.coastal.population)} people in catchments`)),
    h('div', { class: 'card kpi' }, h('div', { class: 'label' }, 'Medicine inventory'), h('div', { class: 'value' }, fmt.num(k.total_inventory_units)), h('div', { class: 'note' }, `usable units across ${sum.medicines.length} medicines (expired stock excluded)`)),
    h('div', { class: `card kpi ${k.pairs_by_risk.critical ? 'crit' : k.pairs_at_risk ? 'warn' : ''}` }, h('div', { class: 'label' }, 'Clinics at risk'), h('div', { class: 'value' }, `${k.clinics_at_risk} / ${k.clinics_monitored}`), h('div', { class: 'note' }, `${k.pairs_at_risk} of ${k.pairs_total} clinic-medicine pairs flagged (${k.pairs_by_risk.critical} critical, ${k.pairs_by_risk.high} high)`)),
    h('div', { class: 'card kpi' }, h('div', { class: 'label' }, 'Predicted disease activity'), h('div', { class: 'note' }, 'next 4 weeks vs last 4'),
      h('div', { class: 'mini' }, Object.values(outlook).map((o) => h('div', null, h('span', null, o.name), h('span', null, changeSpan(o.change_pct)))))),
    h('div', { class: `card kpi ${k.sms_eligible_alerts ? 'warn' : ''}` }, h('div', { class: 'label' }, 'Active alerts'), h('div', { class: 'value' }, k.active_alerts), h('div', { class: 'note' }, `${k.sms_eligible_alerts} at or above the SMS threshold`), h('a', { href: '#/alerts', class: 'small' }, 'Open alerts center')),
    h('div', { class: 'card kpi' }, h('div', { class: 'label' }, 'Redistribution opportunities'), h('div', { class: 'value' }, k.redistribution_opportunities), h('div', { class: 'note' }, `${fmt.num(k.redistribution_units)} units could move between clinics (needs approval)`), h('a', { href: '#/redistribution', class: 'small' }, 'Review transfers')),
    h('div', { class: 'card kpi' }, h('div', { class: 'label' }, 'Upcoming replenishment'), h('div', { class: 'value' }, k.replenishment_items), h('div', { class: 'note' }, `orders due within 14 days, ${fmt.num(k.replenishment_units)} units`), h('a', { href: '#/inventory', class: 'small' }, 'Open inventory')));

  const diseaseBox = h('div'), demandBox = h('div'), coverBox = h('div'), regionBox = h('div');
  function drawDisease() {
    const name = sum.diseases.find((d) => d.id === state.disease).name;
    diseaseBox.replaceChildren(
      h('div', { class: 'controls', style: 'margin-bottom:8px' }, tabs(sum.diseases.map((d) => [d.id, d.name]), state.disease, (v) => { state.disease = v; drawDisease(); })),
      seriesChart(sum.series.disease[state.disease], name, 'cases'),
      legend([{ color: COLORS.history, label: 'Observed cases (all clinics)' }, { color: COLORS.forecast, label: 'Forecast median', dashed: true }, { color: '#c7dcf6', label: '80% range (sum of clinic bands)', box: true }]));
  }
  function drawDemand() {
    const med = sum.medicines.find((m) => m.id === state.medicine);
    const sr = sum.series.demand[state.medicine];
    const labels = [...sr.history.map((p) => p.week), ...sr.forecast.map((p) => p.week)];
    const nh = sr.history.length, nf = sr.forecast.length;
    const last = sr.history[nh - 1].value;
    demandBox.replaceChildren(
      h('div', { class: 'controls', style: 'margin-bottom:8px' }, select(sum.medicines.map((m) => [m.id, m.short_name]), state.medicine, (v) => { state.medicine = v; drawDemand(); })),
      lineChart({ labels, splitIndex: nh, series: [
        { name: 'Consumption', color: COLORS.history, values: [...sr.history.map((p) => p.value), ...Array(nf).fill(null)] },
        { name: 'Forecast demand', color: COLORS.forecast, dashed: true, values: [...Array(nh - 1).fill(null), last, ...sr.forecast.map((p) => p.median)] },
        { name: 'High-demand case', color: COLORS.scenario, dashed: true, width: 1.8, values: [...Array(nh - 1).fill(null), last, ...sr.forecast.map((p) => p.hi)] }],
      fmtY: (v) => fmt.num(v), ariaLabel: `Weekly ${med.short_name} demand` }),
      legend([{ color: COLORS.history, label: `Weekly use (${med.unit})` }, { color: COLORS.forecast, label: 'Forecast demand', dashed: true }, { color: COLORS.scenario, label: 'Pessimistic (90th percentile cases)', dashed: true }]));
  }
  function drawCover() {
    const rows = inv.rows.filter((r) => r.medicine_id === state.invMed).sort((a, b) => (a.days_to_stockout ?? 999) - (b.days_to_stockout ?? 999));
    coverBox.replaceChildren(
      h('div', { class: 'controls', style: 'margin-bottom:10px' }, select(sum.medicines.map((m) => [m.id, m.short_name]), state.invMed, (v) => { state.invMed = v; drawCover(); })),
      riskBars(rows, 'days_to_stockout'),
      h('div', { class: 'legend' }, h('span', null, h('i', { class: 'box', style: 'background:#16a34a' }), 'bar = days of stock'), h('span', null, h('i', { style: 'background:#0b1f3a;width:2px;height:12px' }), 'marker = delivery time')));
  }
  function drawRegion() {
    const cats = Object.values(sum.regional_activity).map((r) => r.label);
    const d = state.regionDisease;
    regionBox.replaceChildren(
      h('div', { class: 'controls', style: 'margin-bottom:8px' }, tabs(sum.diseases.map((x) => [x.id, x.name]), d, (v) => { state.regionDisease = v; drawRegion(); })),
      groupedBars({ categories: cats, fmtY: (v) => fmt.num(v, 1), ariaLabel: 'Cases per 1,000 people per week by region', series: [
        { name: 'Last 4 weeks', color: COLORS.history, values: Object.values(sum.regional_activity).map((r) => r.diseases[d].recent_per_1000) },
        { name: 'Next 4 weeks (forecast)', color: COLORS.forecast, values: Object.values(sum.regional_activity).map((r) => r.diseases[d].forecast_per_1000) }] }),
      legend([{ color: COLORS.history, label: 'Last 4 weeks, cases per 1,000 people per week', box: true }, { color: COLORS.forecast, label: 'Next 4 weeks (forecast)', box: true }]));
  }
  drawDisease(); drawDemand(); drawCover(); drawRegion();

  const total = Object.values(k.pairs_by_risk).reduce((a, b) => a + b, 0);
  const riskCard = card('Shortage risk distribution', 'All clinic x medicine pairs, by risk level',
    h('div', { class: 'split', style: 'align-items:center' },
      h('div', { style: 'text-align:center;flex:0 0 200px' }, donut({ parts: Object.entries(k.pairs_by_risk).map(([key, v]) => ({ label: RISK_NAMES[key], value: v, color: RISK_COLORS[key] })), center: `${k.pairs_at_risk}`, sub: 'pairs at risk' })),
      h('div', { class: 'bars' }, Object.entries(k.pairs_by_risk).map(([key, v]) => h('div', { class: 'bar-row', style: 'grid-template-columns:90px 1fr 40px' }, h('div', null, RISK_NAMES[key]), h('div', { class: 'bar-track' }, h('div', { class: `bar-fill fill-${key}`, style: `width:${(v / total) * 100}%` })), h('div', { class: 'val' }, v))))),
    h('details', { class: 'how' }, h('summary', null, 'How risk is classified'),
      h('p', null, 'Risk compares the days until stock runs out with the clinic\'s delivery time. Critical: stock runs out before an order placed today could arrive. High: less than 10 days of slack. Watch: less than 21 days of slack. Otherwise adequate.')));

  const top = card('Most urgent shortages', 'Ordered by risk, then days of stock left',
    k.pairs_at_risk ? h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null, h('tr', null, ['Clinic', 'Medicine', 'Risk', 'Stock lasts', 'Order', 'By'].map((t, i) => h('th', { class: i > 2 ? 'num' : '' }, t)))),
      h('tbody', null, sum.top_risks.map((r) => h('tr', { class: 'clickable', onclick: () => { location.hash = `#/clinics/${r.clinic_id}`; } },
        h('td', null, r.clinic_name), h('td', null, r.short_name), h('td', null, riskBadge(r.risk)), h('td', { class: 'num' }, fmt.days(r.days_to_stockout)),
        h('td', { class: 'num' }, `${fmt.num(r.reorder_qty)} ${r.unit}`), h('td', { class: 'num' }, fmt.short(r.order_by_date))))))) : h('div', { class: 'empty' }, 'No shortages projected.'));

  root.append(...[
    fromCache ? h('div', { class: 'warn-box', style: 'margin-bottom:14px' }, 'You are viewing the last synced data. Changes and new forecasts need a connection.') : null,
    kpis,
    h('div', { class: 'grid g-2', style: 'margin-top:16px' },
      card('Historical vs predicted disease activity', h('span', null, 'Weekly cases across all clinics, 26 weeks of history and an 8-week forecast ', simBadge()), diseaseBox),
      card('Medicine demand over time', 'Weekly units used (history) and projected from forecast cases', demandBox)),
    h('div', { class: 'grid g-2', style: 'margin-top:16px' },
      card('Inventory levels', 'Days of stock remaining by clinic compared with delivery time', coverBox),
      riskCard),
    h('div', { class: 'grid g-2', style: 'margin-top:16px' },
      card('Regional disease activity', 'Cases per 1,000 people per week', regionBox), top)].filter(Boolean));
  return root;
}
