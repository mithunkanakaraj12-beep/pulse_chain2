import * as api from '../api.js';
import { h, fmt, select, card, riskBadge, changeSpan, simBadge, loading, toast } from '../ui.js';
import { lineChart, legend, groupedBars, COLORS } from '../charts.js';

export async function forecast(_p, ctx) {
  const meta = (await api.get('/api/meta')).data;
  const st = { disease: 'malaria', region: 'all', weeks: 6 };
  const out = h('div');
  const run = h('button', { class: 'btn primary', onclick: () => draw(true) }, 'Run forecast');
  const stamp = h('span', { class: 'muted small' });

  async function draw(announce) {
    out.replaceChildren(loading('Running forecast...'));
    run.disabled = true;
    try {
      const { data: d, fromCache, cachedAt } = await api.get(`/api/forecast?disease=${st.disease}&region=${st.region}&weeks=${st.weeks}`);
      stamp.textContent = fromCache ? `Offline - showing the last synced result (${fmt.time(cachedAt)}). A new forecast needs the server.` : `Run ${fmt.time(Date.now())}`;
      out.replaceChildren(view(d, meta));
      if (announce && !fromCache) toast(`${d.disease_name} forecast updated.`, 'ok');
    } catch (e) { out.replaceChildren(h('div', { class: 'err-box' }, e.message)); }
    run.disabled = false;
  }
  draw(false);
  return h('div', null,
    h('div', { class: 'page-head' }, h('div', null, h('p', null, 'Choose a disease, region and period. The model forecasts weekly cases per clinic with an 80% range, then converts cases to medicine demand using a fixed treatment protocol.'))),
    h('div', { class: 'card', style: 'margin-bottom:16px' }, h('div', { class: 'controls' },
      select(meta.diseases.map((d) => [d.id, d.name]), st.disease, (v) => { st.disease = v; draw(false); }, 'Disease'),
      select([['all', 'All regions'], ...meta.regions.map((r) => [r.id, r.label])], st.region, (v) => { st.region = v; draw(false); }, 'Region'),
      select([[4, '4 weeks'], [6, '6 weeks'], [8, '8 weeks']], st.weeks, (v) => { st.weeks = Number(v); draw(false); }, 'Forecast period'),
      h('div', { style: 'align-self:flex-end' }, run), h('div', { style: 'align-self:flex-end' }, stamp))),
    out);
}

function view(d, meta) {
  const s = d.series, bs = d.baseline_series;
  const nh = s.history.length, nf = s.forecast.length, last = s.history[nh - 1].value;
  const labels = [...s.history.map((p) => p.week), ...s.forecast.map((p) => p.week)];
  const pad = Array(nh - 1).fill(null);
  const total = s.forecast.reduce((a, p) => a + p.median, 0), btotal = bs.forecast.reduce((a, p) => a + p.median, 0);
  const recent = s.history.slice(-nf).reduce((a, p) => a + p.value, 0);
  const series = [
    { name: 'Observed', color: COLORS.history, values: [...s.history.map((p) => p.value), ...Array(nf).fill(null)] },
    { name: d.scenario_active ? 'Forecast (monsoon scenario)' : 'Forecast', color: d.scenario_active ? COLORS.scenario : COLORS.forecast, dashed: true, values: [...pad, last, ...s.forecast.map((p) => p.median)] }];
  if (d.scenario_active) series.push({ name: 'Forecast (normal weather)', color: COLORS.baseline, dashed: true, width: 2, values: [...pad, last, ...bs.forecast.map((p) => p.median)] });
  const chart = lineChart({ labels, splitIndex: nh, series,
    bands: [{ color: d.scenario_active ? COLORS.scenario : COLORS.band, label: '80% range', lo: [...pad, last, ...s.forecast.map((p) => p.lo)], hi: [...pad, last, ...s.forecast.map((p) => p.hi)] }],
    ariaLabel: `${d.disease_name} weekly cases, observed and forecast` });

  const drv = d.drivers;
  const maxShare = drv ? Math.max(...drv.groups.map((g) => g.share), 0.01) : 1;
  const drivers = drv ? h('div', { class: 'bars drivers' }, drv.groups.map((g) => h('div', { class: 'bar-row' }, h('div', { class: 'name' }, g.group),
    h('div', { class: 'bar-track' }, h('div', { class: 'bar-fill fill-teal', style: `width:${(g.share / maxShare) * 100}%` })), h('div', { class: 'val' }, `${Math.round(g.share * 100)}%`)))) : h('p', { class: 'muted' }, 'Train the model to see drivers.');

  const rainCats = meta.regions.map((r) => r.label);
  const rainChart = groupedBars({ categories: rainCats, fmtY: (v) => fmt.num(v), ariaLabel: 'Rainfall outlook next 4 weeks by region', series: [
    { name: 'Normal weather', color: COLORS.baseline, values: meta.regions.map((r) => avg(d.weather_outlook[r.id].climatology.slice(0, 4))) },
    { name: 'Outlook used', color: d.scenario_active ? COLORS.scenario : COLORS.forecast, values: meta.regions.map((r) => avg(d.weather_outlook[r.id].scenario.slice(0, 4))) }] });

  const v = d.validation;
  const validation = v ? h('table', null, h('thead', null, h('tr', null, ['Method', 'Error (cases/week)', 'vs model'].map((t, i) => h('th', { class: i ? 'num' : '' }, t)))), h('tbody', null,
    h('tr', null, h('td', null, h('strong', null, 'This model')), h('td', { class: 'num' }, v.mae_model.toFixed(2)), h('td', { class: 'num' }, '-')),
    h('tr', null, h('td', null, 'Seasonal naive (same week, earlier years)'), h('td', { class: 'num' }, v.mae_seasonal_naive.toFixed(2)), h('td', { class: 'num' }, `model error ${Math.round(v.skill_vs_seasonal_naive * 100)}% lower`)),
    h('tr', null, h('td', null, 'Persistence (last observed week)'), h('td', { class: 'num' }, v.mae_persistence.toFixed(2)), h('td', { class: 'num' }, `model error ${Math.round(v.skill_vs_persistence * 100)}% lower`)))) : null;

  const meds = d.medicines.map((m) => {
    const ms = m.series, mh = ms.history.length, mf = ms.forecast.length, ml = ms.history[mh - 1].value, mp = Array(mh - 1).fill(null);
    const lab = [...ms.history.map((p) => p.week), ...ms.forecast.map((p) => p.week)];
    const need = m.forecast_total, have = m.stock_on_hand;
    return card(`${m.short_name} demand`, `${m.units_per_case} ${m.unit} per case (simplified protocol) - ${d.weeks}-week total ${fmt.num(need)} ${m.unit} vs ${fmt.num(have)} on hand`,
      lineChart({ labels: lab, splitIndex: mh, height: 190, series: [
        { name: 'Weekly use', color: COLORS.history, values: [...ms.history.map((p) => p.value), ...Array(mf).fill(null)] },
        { name: 'Projected demand', color: COLORS.forecast, dashed: true, values: [...mp, ml, ...ms.forecast.map((p) => p.median)] },
        ...(d.scenario_active ? [{ name: 'Normal weather', color: COLORS.baseline, dashed: true, width: 2, values: [...mp, ml, ...m.baseline_forecast.map((p) => p.median)] }] : [])],
        ariaLabel: `${m.short_name} demand` }),
      have < need ? h('div', { class: 'warn-box', style: 'margin-top:10px' }, `Network stock covers ${Math.round((have / need) * 100)}% of projected ${d.weeks}-week demand; clinics at risk: ${m.clinics.filter((c) => c.risk !== 'low').length}.`) : null,
      h('div', { class: 'table-wrap', style: 'margin-top:8px' }, h('table', null, h('tbody', null, m.clinics.filter((c) => c.risk !== 'low').slice(0, 5).map((c) => h('tr', null, h('td', null, c.clinic_name), h('td', null, riskBadge(c.risk)), h('td', { class: 'num' }, fmt.days(c.days_to_stockout)), h('td', { class: 'num' }, `reorder ${fmt.num(c.reorder_qty)}`)))))));
  });

  const clin = h('table', null, h('thead', null, h('tr', null, ['Clinic', 'Last 4 wk avg', `Next ${Math.min(4, d.weeks)} wk avg`, 'Change'].map((t, i) => h('th', { class: i ? 'num' : '' }, t)))),
    h('tbody', null, [...d.clinics].sort((a, b) => (b.change_pct ?? -999) - (a.change_pct ?? -999)).map((c) => h('tr', { class: 'clickable', onclick: () => { location.hash = `#/clinics/${c.clinic_id}`; } },
      h('td', null, c.name), h('td', { class: 'num' }, fmt.num(c.recent_weekly, 1)), h('td', { class: 'num' }, fmt.num(c.forecast_weekly, 1)), h('td', { class: 'num' }, changeSpan(c.change_pct))))));

  return h('div', null,
    h('div', { class: 'grid g-kpi', style: 'margin-bottom:16px' },
      h('div', { class: 'card kpi' }, h('div', { class: 'label' }, `${d.disease_name} - next ${d.weeks} weeks`), h('div', { class: 'value' }, fmt.num(total)), h('div', { class: 'note' }, `cases forecast vs ${fmt.num(recent)} in the previous ${d.weeks} weeks`)),
      h('div', { class: `card kpi ${total > recent * 1.3 ? 'warn' : ''}` }, h('div', { class: 'label' }, 'Change vs recent'), h('div', { class: 'value' }, fmt.pct((total / recent - 1) * 100)), h('div', { class: 'note' }, 'forecast total vs same-length recent period')),
      d.scenario_active ? h('div', { class: 'card kpi warn' }, h('div', { class: 'label' }, 'Monsoon effect'), h('div', { class: 'value' }, fmt.pct((total / btotal - 1) * 100)), h('div', { class: 'note' }, 'vs the same forecast under normal weather (simulated)')) : null),
    h('div', { class: 'grid g-2' },
      card(`${d.disease_name} forecast`, h('span', null, d.region === 'all' ? 'All regions' : meta.regions.find((r) => r.id === d.region).label, ' ', simBadge()), chart,
        legend([{ color: COLORS.history, label: 'Observed' }, { color: d.scenario_active ? COLORS.scenario : COLORS.forecast, label: 'Forecast median', dashed: true }, ...(d.scenario_active ? [{ color: COLORS.baseline, label: 'Normal-weather forecast', dashed: true }] : []), { color: '#c7dcf6', label: '80% prediction range', box: true }])),
      card('What drives this forecast', 'Share of the model\'s skill attributable to each input group (permutation importance on a held-out year)', drivers,
        h('div', { class: 'sub', style: 'margin-top:14px' }, 'Rainfall outlook used (mm per week, next 4 weeks)'), rainChart,
        h('details', { class: 'how' }, h('summary', null, 'How to read this'), h('p', null, 'Importance describes association in the synthetic data the model was trained on, not proof of cause. Rainfall matters here because the simulator was built with a lagged rainfall effect; real surveillance data would need its own validation.')))),
    h('div', { class: 'grid g-2', style: 'margin-top:16px' },
      card('Clinic-level outlook', 'Sorted by projected change', h('div', { class: 'table-wrap' }, clin)),
      card('Model check against simple baselines', `Held-out final year of data; 80% range covered ${v ? Math.round(v.interval_coverage_80 * 100) : '-'}% of outcomes`, validation,
        h('p', { class: 'muted small', style: 'margin-top:10px' }, 'Lower error is better. These numbers describe the method on simulated data only; they are not evidence of real-world accuracy.'))),
    h('h2', { style: 'margin:22px 0 12px;font-size:17px' }, 'Resulting medicine demand'),
    h('div', { class: 'grid g-2' }, meds));
}
const avg = (a) => a.reduce((x, y) => x + y, 0) / a.length;
