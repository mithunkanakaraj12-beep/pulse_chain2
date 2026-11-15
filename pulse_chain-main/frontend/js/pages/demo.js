import * as api from '../api.js';
import { h, fmt, card, riskBadge, changeSpan, simBadge, toast } from '../ui.js';
import { lineChart, legend, groupedBars, COLORS } from '../charts.js';
import { renderMap, mapLegend } from '../map.js';

const STEPS = ['Normal conditions', 'Monsoon event', 'AI forecast', 'Inventory impact', 'Shortage detection', 'SMS alerts', 'Redistribution', 'Final dashboard'];
const RANK = { low: 0, moderate: 1, high: 2, critical: 3 };

async function ensureMonsoon(ctx) {
  const sc = (await api.get('/api/scenario')).data;
  if (!sc.monsoon) { await api.send('POST', '/api/scenario', { monsoon: true }); await ctx.refreshMeta(); }
}

function joinSeries(s, key) {
  const nh = s.history.length, last = s.history[nh - 1].value;
  return [...Array(nh - 1).fill(null), last, ...s.forecast.map((p) => p[key])];
}

export async function demo(_p, ctx) {
  const { data: w, fromCache } = await api.get('/api/demo/walkthrough');
  const st = { step: 0, smsSent: false, finalLive: null };
  const body = h('div', { class: 'step-body' });
  const dots = h('div', { class: 'stepper' });
  const nav = h('div', { class: 'controls', style: 'justify-content:space-between;margin-top:18px' });
  const online = navigator.onLine && !fromCache;

  const kb = w.baseline_kpis, ks = w.scenario_kpis;
  const intro = (title, text) => h('div', { style: 'margin-bottom:14px' }, h('h2', { style: 'font-size:20px' }, title, ' ', simBadge()), h('p', { class: 'muted', style: 'max-width:75ch' }, text));

  const stepViews = [
    // 1 -------------------------------------------------------------
    () => {
      const m = w.disease.malaria;
      const bs = m.baseline_series, lab = [...bs.history.map((p) => p.week), ...bs.forecast.map((p) => p.week)];
      return h('div', null, intro('Step 1 - Normal conditions', `${kb.clinics_monitored} rural clinics are being monitored with normal weather assumed. Disease activity follows its usual seasonal pattern and most stock is adequate; a handful of remote, lean clinics are already watched.`),
        h('div', { class: 'grid g-kpi' },
          h('div', { class: 'card kpi' }, h('div', { class: 'label' }, 'Clinic-medicine pairs at risk'), h('div', { class: 'value' }, kb.pairs_at_risk), h('div', { class: 'note' }, `of ${kb.pairs_total} (${kb.pairs_by_risk.critical} critical, ${kb.pairs_by_risk.high} high)`)),
          h('div', { class: 'card kpi' }, h('div', { class: 'label' }, 'Alerts needing SMS'), h('div', { class: 'value' }, kb.sms_eligible_alerts), h('div', { class: 'note' }, 'at or above the SMS threshold')),
          h('div', { class: 'card kpi' }, h('div', { class: 'label' }, 'Possible transfers'), h('div', { class: 'value' }, kb.redistribution_opportunities), h('div', { class: 'note' }, 'under normal conditions')),
          h('div', { class: 'card kpi' }, h('div', { class: 'label' }, 'Malaria, next 4 weeks'), h('div', { class: 'value' }, fmt.num(m.baseline_4w)), h('div', { class: 'note' }, 'cases forecast, all clinics'))),
        h('div', { style: 'margin-top:16px' }, card('Malaria: observed and forecast under normal weather', 'Weekly cases, all clinics', lineChart({ labels: lab, splitIndex: bs.history.length, series: [
          { name: 'Observed', color: COLORS.history, values: [...bs.history.map((p) => p.value), ...Array(bs.forecast.length).fill(null)] },
          { name: 'Forecast (normal weather)', color: COLORS.forecast, dashed: true, values: joinSeries(bs, 'median') }],
          bands: [{ color: COLORS.band, label: '80% range', lo: joinSeries(bs, 'lo'), hi: joinSeries(bs, 'hi') }] }))));
    },
    // 2 -------------------------------------------------------------
    () => {
      const regions = Object.values(w.rain);
      const status = h('div');
      const paint = (on) => status.replaceChildren(on ? h('div', { class: 'note-box' }, `Simulated monsoon is ON for the whole app (rainfall ${w.scenario.intensity}x normal, ramping up over 2 weeks, lasting ${w.scenario.weeks} weeks, then easing). Every page now reflects it.`) : h('div', { class: 'warn-box' }, 'The monsoon has not been triggered yet.'));
      api.get('/api/scenario').then(({ data }) => paint(data.monsoon)).catch(() => paint(false));
      const btn = h('button', { class: 'btn primary', disabled: !online, onclick: async () => {
        btn.disabled = true;
        try { await ensureMonsoon(ctx); paint(true); toast('Simulated monsoon triggered.', 'ok'); } catch (e) { toast(e.message, 'err'); }
        btn.disabled = false;
      } }, 'Trigger simulated monsoon');
      return h('div', null, intro('Step 2 - Simulated monsoon event', 'An early, heavy monsoon is injected into the weather outlook. This is an illustrative scenario for the demo, not a weather prediction.'),
        h('div', { class: 'controls', style: 'margin-bottom:12px' }, btn, h('a', { class: 'small', href: '#/forecast' }, 'See it in the Forecasting center')), status,
        h('div', { style: 'margin-top:16px' }, card('Weekly rainfall outlook, average of weeks 1-4', 'millimetres per week by region', groupedBars({ categories: regions.map((r) => r.label), fmtY: (v) => fmt.num(v), series: [
          { name: 'Normal', color: COLORS.baseline, values: regions.map((r) => avg(r.normal.slice(0, 4))) },
          { name: 'Simulated monsoon', color: COLORS.scenario, values: regions.map((r) => avg(r.scenario.slice(0, 4))) }] }),
        legend([{ color: COLORS.baseline, label: 'Normal outlook', box: true }, { color: COLORS.scenario, label: 'Simulated monsoon', box: true }]))));
    },
    // 3 -------------------------------------------------------------
    () => {
      const chartFor = (d) => {
        const x = w.disease[d], s = x.series, b = x.baseline_series;
        const lab = [...s.history.map((p) => p.week), ...s.forecast.map((p) => p.week)];
        return card(`${x.name} forecast`, `Next 4 weeks ${fmt.num(x.baseline_4w)} \u2192 ${fmt.num(x.scenario_4w)} cases (${fmt.pct(x.pct_4w)}); next 8 weeks ${fmt.pct(x.pct_8w)}`,
          lineChart({ labels: lab, splitIndex: s.history.length, height: 240, series: [
            { name: 'Observed', color: COLORS.history, values: [...s.history.map((p) => p.value), ...Array(s.forecast.length).fill(null)] },
            { name: 'Normal weather', color: COLORS.baseline, dashed: true, width: 2, values: joinSeries(b, 'median') },
            { name: 'Monsoon forecast', color: COLORS.scenario, dashed: true, values: joinSeries(s, 'median') }],
            bands: [{ color: COLORS.scenario, label: '80% range', lo: joinSeries(s, 'lo'), hi: joinSeries(s, 'hi'), opacity: 0.14 }] }));
      };
      return h('div', null, intro('Step 3 - AI forecast', 'The forecasting model reads the rainfall outlook and projects higher disease activity. Lagged rainfall drives malaria over several weeks; flooding drives cholera within one to three weeks.'),
        h('div', { class: 'grid g-kpi', style: 'margin-bottom:16px' }, ['malaria', 'cholera', 'pneumonia'].map((d) => h('div', { class: `card kpi ${w.disease[d].pct_8w > 20 ? 'warn' : ''}` }, h('div', { class: 'label' }, w.disease[d].name), h('div', { class: 'value' }, fmt.pct(w.disease[d].pct_8w)), h('div', { class: 'note' }, `8-week cases ${fmt.num(w.disease[d].baseline_8w)} \u2192 ${fmt.num(w.disease[d].scenario_8w)}`)))),
        h('div', { class: 'grid g-2' }, chartFor('malaria'), chartFor('cholera')),
        h('p', { class: 'muted small', style: 'margin-top:12px' }, 'Simulated illustration: the size of the response comes from the synthetic data generator, not from a validated epidemiological model.'));
    },
    // 4 -------------------------------------------------------------
    () => h('div', null, intro('Step 4 - Inventory impact', 'Forecast cases are converted to medicine demand with a fixed treatment protocol, so a surge in cases becomes a visible jump in the daily use of specific medicines.'),
      card('Largest increases in expected daily use', 'Average of the next 4 weeks vs the same forecast under normal weather', h('div', { class: 'table-wrap' }, h('table', null,
        h('thead', null, h('tr', null, ['Clinic', 'Medicine', 'Normal / day', 'Monsoon / day', 'Change', 'Stock lasts'].map((t, i) => h('th', { class: i > 1 ? 'num' : '' }, t)))),
        h('tbody', null, w.demand_uplift.map((u) => h('tr', null, h('td', null, u.clinic_name), h('td', null, u.short_name), h('td', { class: 'num' }, fmt.num(u.baseline_daily, 1)), h('td', { class: 'num' }, fmt.num(u.scenario_daily, 1)), h('td', { class: 'num' }, changeSpan(u.pct)), h('td', { class: 'num' }, `${fmt.days(u.baseline_days)} \u2192 ${fmt.days(u.scenario_days)}`)))))))),
    // 5 -------------------------------------------------------------
    () => {
      const total = kb.pairs_total;
      return h('div', null, intro('Step 5 - Shortage detection', 'Day-by-day stock depletion (earliest expiry first) finds when each clinic would run out and compares that with how long a delivery takes.'),
        h('div', { class: 'compare', style: 'margin-bottom:16px' },
          h('div', { class: 'x' }, 'Pairs at risk, normal', h('b', null, kb.pairs_at_risk)), h('span', { class: 'arrow' }, '\u2192'), h('div', { class: 'x' }, 'With monsoon', h('b', { style: 'color:var(--high)' }, ks.pairs_at_risk)),
          h('div', { class: 'x' }, 'Critical', h('b', null, `${kb.pairs_by_risk.critical} \u2192 ${ks.pairs_by_risk.critical}`)), h('div', { class: 'x' }, 'High risk', h('b', null, `${kb.pairs_by_risk.high} \u2192 ${ks.pairs_by_risk.high}`)), h('div', { class: 'x' }, 'Clinics affected', h('b', null, `${kb.clinics_at_risk} \u2192 ${ks.clinics_at_risk}`))),
        card(`Newly at risk because of the monsoon (${w.at_risk.newly.length} of ${total} pairs)`, 'Were adequate before; now projected to run short', w.at_risk.newly.length ? h('div', { class: 'table-wrap' }, h('table', null, h('tbody', null, w.at_risk.newly.slice(0, 12).map((u) => h('tr', null, h('td', null, u.clinic_name), h('td', null, u.short_name), h('td', { class: 'num' }, `${fmt.days(u.baseline_days)} \u2192 ${fmt.days(u.scenario_days)}`), h('td', null, riskBadge(u.scenario_risk)), h('td', { class: 'num' }, `reorder ${fmt.num(u.reorder_qty)} ${u.unit}`))))))
          : h('div', { class: 'empty' }, 'No additional shortages.')));
    },
    // 6 -------------------------------------------------------------
    () => {
      const msgs = w.sms.slice(0, 4);
      const phone = h('div', { class: 'phone' }, h('div', { class: 'ph-head' }, 'SIMULATED phone - nothing is sent'), ...msgs.map((m) => h('div', { class: 'msg' }, m.sms_body, h('small', null, `To ${m.health_worker}, ${m.to_number}`))));
      const btn = h('button', { class: 'btn primary', disabled: !online, onclick: async () => {
        btn.disabled = true;
        try {
          await ensureMonsoon(ctx);
          const list = (await api.get('/api/alerts')).data.alerts.filter((a) => a.sms_body && !a.acknowledged);
          let n = 0;
          for (const a of list) { const r = await api.send('POST', '/api/sms/send', { alert_key: a.key }); if (r.status === 'simulated') n++; }
          st.smsSent = true; toast(`Simulated ${n} SMS messages (SMS mode: simulated, nothing was sent).`, 'ok');
        } catch (e) { toast(e.message, 'err'); }
        btn.disabled = false;
      } }, `Simulate SMS to ${w.sms_total} alerts`);
      return h('div', null, intro('Step 6 - SMS alerts', `${w.sms_total} alerts are at or above the SMS threshold. Each message names the clinic, the medicine, the days left and the recommended response. In simulated mode they are only logged.`),
        h('div', { class: 'split', style: 'align-items:flex-start' }, h('div', null, phone), h('div', null, btn, h('p', { class: 'muted small', style: 'margin-top:12px' }, 'Real delivery through Twilio needs credentials plus an explicit confirmation on each send; see the README. The Alerts page shows the full log.'), h('a', { href: '#/alerts' }, 'Open the alerts center'))));
    },
    // 7 -------------------------------------------------------------
    () => {
      const box = h('div', { class: 'muted' }, 'Loading map...');
      api.get('/api/transfers').then(({ data }) => {
        const risk = {};
        for (const u of w.at_risk.scenario) if (!risk[u.clinic_id] || RANK[u.scenario_risk] > RANK[risk[u.clinic_id]]) risk[u.clinic_id] = u.scenario_risk;
        for (const c of data.clinics) risk[c.id] = risk[c.id] || 'low';
        box.replaceChildren(renderMap({ clinics: data.clinics, risk, surplus: new Set(w.surplus.map((s) => s.clinic_id)), proposals: w.transfers }), mapLegend());
      }).catch(() => { box.textContent = 'Map needs the server.'; });
      const top = w.transfers.slice(0, 3);
      const list = h('div', { class: 'grid', style: 'gap:10px' }, top.map((p) => {
        const btn = h('button', { class: 'btn small teal', disabled: !online, onclick: async () => {
          btn.disabled = true;
          try { await ensureMonsoon(ctx); await api.send('POST', '/api/transfers/decision', { key: p.key, decision: 'approve' }); toast(`Approved (simulated): ${p.quantity} ${p.unit} of ${p.short_name} to ${p.receiver_name}.`, 'ok'); btn.textContent = 'Approved'; } catch (e) { toast(e.message, 'err'); btn.disabled = false; }
        } }, 'Approve (simulated)');
        return h('div', { class: 'proposal' }, h('div', { class: 'route' }, p.donor_name, h('span', { class: 'arrow' }, '\u2192'), p.receiver_name), h('div', null, h('strong', null, `${fmt.num(p.quantity)} ${p.unit}`), ` of ${p.short_name}, ${p.road_km} km`), h('div', { class: 'ba' }, 'Stock lasts', h('strong', null, fmt.days(p.receiver_days_before)), '\u2192', h('strong', { class: 'down' }, fmt.days(p.receiver_days_after))), btn);
      }));
      return h('div', null, intro('Step 7 - Redistribution', `${w.transfers.length} transfers are recommended from clinics with safe surplus. Expired, near-expiry and safety-stock units are never proposed, and every transfer needs a human to approve it.`),
        h('div', { class: 'layout-map' }, h('div', null, box), h('div', null, h('h3', { style: 'margin-bottom:10px' }, 'Most urgent transfers'), list, h('p', { class: 'small', style: 'margin-top:10px' }, h('a', { href: '#/redistribution' }, `See all ${w.transfers.length} recommendations`)))));
    },
    // 8 -------------------------------------------------------------
    () => {
      const box = h('div', { class: 'loading' }, 'Reading the live dashboard...');
      api.get('/api/summary').then(({ data }) => {
        const k = data.kpis;
        box.replaceChildren(h('div', { class: 'grid g-kpi' },
          h('div', { class: 'card kpi' }, h('div', { class: 'label' }, 'Pairs at risk'), h('div', { class: 'compare', style: 'margin-top:8px' }, h('div', { class: 'x' }, 'Normal', h('b', null, kb.pairs_at_risk)), h('span', { class: 'arrow' }, '\u2192'), h('div', { class: 'x' }, 'Monsoon', h('b', { style: 'color:var(--high)' }, ks.pairs_at_risk)), h('span', { class: 'arrow' }, '\u2192'), h('div', { class: 'x' }, 'After your actions', h('b', { style: 'color:var(--low)' }, data.scenario.monsoon ? k.pairs_at_risk : '-')))),
          h('div', { class: 'card kpi' }, h('div', { class: 'label' }, 'Critical pairs now'), h('div', { class: 'value' }, k.pairs_by_risk.critical), h('div', { class: 'note' }, `${k.sms_eligible_alerts} alerts at SMS level`)),
          h('div', { class: 'card kpi' }, h('div', { class: 'label' }, 'Transfers still open'), h('div', { class: 'value' }, k.redistribution_opportunities), h('div', { class: 'note' }, `${fmt.num(k.redistribution_units)} units`))));
      }).catch(() => { box.textContent = 'The live dashboard needs the server.'; });
      return h('div', null, intro('Step 8 - Final dashboard', 'Clinics saw the shortage coming weeks ahead: warnings reached health workers, and surplus stock nearby can cover the gap, with people in control of every decision.'),
        box,
        h('div', { class: 'controls', style: 'margin-top:16px' }, h('a', { class: 'btn primary', href: '#/' }, 'Open the live dashboard'), h('a', { class: 'btn', href: '#/redistribution' }, 'Review transfers'), h('a', { class: 'btn', href: '#/alerts' }, 'Alerts center'),
          h('button', { class: 'btn danger', disabled: !online, onclick: async () => {
            try { await api.send('POST', '/api/admin/reset'); await ctx.refreshMeta(); toast('Demo reset to normal conditions.', 'ok'); st.step = 0; ctx.refresh(); } catch (e) { toast(e.message, 'err'); }
          } }, 'Reset demo to normal')));
    },
  ];

  function draw() {
    dots.replaceChildren(...STEPS.map((name, i) => h('button', { class: `step-dot ${i === st.step ? 'on' : i < st.step ? 'done' : ''}`, onclick: () => { st.step = i; draw(); }, 'aria-current': i === st.step ? 'step' : null }, `${i + 1}. ${name}`)));
    body.replaceChildren(stepViews[st.step]());
    body.style.animation = 'none'; void body.offsetWidth; body.style.animation = '';
    nav.replaceChildren(
      h('button', { class: 'btn', disabled: st.step === 0, onclick: () => { st.step--; draw(); } }, '\u2190 Back'),
      h('span', { class: 'muted small' }, `Step ${st.step + 1} of ${STEPS.length}`),
      h('button', { class: 'btn primary', disabled: st.step === STEPS.length - 1, onclick: () => { st.step++; draw(); } }, 'Next \u2192'));
  }
  draw();
  return h('div', null,
    h('div', { class: 'warn-box', style: 'margin-bottom:14px' }, 'Everything in this walkthrough is simulated. The monsoon is an illustrative scenario, and the forecasts come from a model trained on synthetic data.'),
    !online ? h('div', { class: 'warn-box', style: 'margin-bottom:14px' }, 'Offline: you can read the last synced walkthrough; triggering the monsoon, sending SMS and approving transfers need the server.') : null,
    dots, body, nav);
}
const avg = (a) => a.reduce((x, y) => x + y, 0) / a.length;
