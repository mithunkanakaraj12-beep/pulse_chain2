import * as api from '../api.js';
import { h, fmt, select, card, riskBadge, toast } from '../ui.js';
import { renderMap, mapLegend } from '../map.js';

const RANK = { low: 0, moderate: 1, high: 2, critical: 3 };

export async function redistribution(_p, ctx) {
  const [{ data: t, fromCache }, { data: inv }] = await Promise.all([api.get('/api/transfers'), api.get('/api/inventory')]);
  const st = { medicine: 'all', selected: null };
  const mapBox = h('div'), list = h('div', { class: 'grid', style: 'gap:12px' }), summary = h('div', { class: 'muted' });

  function riskFor() {
    const risk = {};
    for (const r of inv.rows) {
      if (st.medicine !== 'all' && r.medicine_id !== st.medicine) continue;
      if (!risk[r.clinic_id] || RANK[r.risk] > RANK[risk[r.clinic_id]]) risk[r.clinic_id] = r.risk;
    }
    return risk;
  }
  async function decide(p, decision) {
    try {
      const res = await api.send('POST', '/api/transfers/decision', { key: p.key, decision }, { queueable: true, label: `${decision} transfer ${p.short_name}: ${p.donor_name} to ${p.receiver_name}` });
      if (res.queued) { toast('Saved offline. The decision will be applied when you reconnect.'); return; }
      toast(decision === 'approve' ? `Approved (simulated): ${p.quantity} ${p.unit} of ${p.short_name} to ${p.receiver_name}. Demo inventory updated.` : 'Recommendation rejected.', 'ok');
      ctx.refreshMeta(); ctx.refresh();
    } catch (e) { toast(e.message, 'err'); }
  }
  function draw() {
    const props = t.proposals.filter((p) => st.medicine === 'all' || p.medicine_id === st.medicine);
    const surplus = new Set(t.surplus.filter((s) => st.medicine === 'all' || s.medicine_id === st.medicine).map((s) => s.clinic_id));
    const shortages = t.shortages.filter((s) => st.medicine === 'all' || s.medicine_id === st.medicine);
    mapBox.replaceChildren(
      renderMap({ clinics: t.clinics, risk: riskFor(), surplus, proposals: props, selected: st.selected, onSelect: (k) => { st.selected = k; draw(); }, onClinic: (id) => { location.hash = `#/clinics/${id}`; } }),
      mapLegend());
    summary.textContent = `${props.length} recommended transfer${props.length === 1 ? '' : 's'} - ${shortages.length} shortages - ${surplus.size} clinics with surplus`;
    list.replaceChildren(...(props.length ? props.map((p) => h('div', { class: `proposal ${p.key === st.selected ? 'sel' : ''}`, onclick: () => { st.selected = p.key === st.selected ? null : p.key; draw(); } },
      h('div', { class: 'route' }, h('span', null, p.donor_name), h('span', { class: 'arrow' }, '\u2192'), h('span', null, p.receiver_name)),
      h('div', null, h('strong', null, `${fmt.num(p.quantity)} ${p.unit}`), ` of ${p.medicine_name} `, riskBadge(p.receiver_risk_before)),
      h('div', { class: 'muted small' }, `${p.road_km} km by road, about ${p.travel_hours} h - arrives ${fmt.short(p.arrival_date)}`),
      h('div', { class: 'ba' }, h('span', null, 'Receiver stock lasts'), h('strong', null, fmt.days(p.receiver_days_before)), '\u2192', h('strong', { class: 'down' }, fmt.days(p.receiver_days_after))),
      h('div', { class: 'ba muted small' }, `Donor keeps its safety stock (${fmt.num(p.donor_safety_stock)}) and still lasts ${fmt.days(p.donor_days_after_high_demand)} even if demand is high.`),
      h('div', { class: 'muted small' }, 'Batches: ', p.batches.map((b) => `#${b.batch_id} x${b.quantity} (expires ${fmt.date(b.expiry_date)}, ${b.shelf_days_at_arrival} d left on arrival)`).join('; ')),
      st.selected === p.key ? h('p', { class: 'small' }, p.rationale) : null,
      h('div', { class: 'controls' },
        h('button', { class: 'btn small teal', onclick: (e) => { e.stopPropagation(); decide(p, 'approve'); } }, 'Approve (simulated)'),
        h('button', { class: 'btn small danger', onclick: (e) => { e.stopPropagation(); decide(p, 'reject'); } }, 'Reject'),
        h('span', { class: 'badge info' }, 'Needs human approval')))) : [h('div', { class: 'empty card' }, 'No transfers recommended for this selection. Either nothing is short, or no nearby clinic has safe surplus.')]));
  }
  draw();
  const hist = t.history.length ? card('Decisions this session', 'Stored in the demo database', h('table', null, h('tbody', null, t.history.slice(0, 8).map((d) => h('tr', null,
    h('td', null, d.proposal_key), h('td', { class: 'num' }, fmt.num(d.quantity)), h('td', null, h('span', { class: `badge ${d.status === 'approved' ? 'low' : 'gray'}` }, d.status)), h('td', { class: 'muted small' }, fmt.time(d.decided_at))))))) : null;
  return h('div', null,
    h('div', { class: 'page-head' }, h('div', null, h('p', null, 'Nearby clinics with safe surplus can cover shortages elsewhere. Every transfer is a recommendation: expired, near-expiry and safety-stock units are never proposed. Approving updates the simulated demo inventory only.'))),
    fromCache ? h('div', { class: 'warn-box', style: 'margin-bottom:12px' }, 'Offline: showing the last synced recommendations. Decisions are queued until you reconnect and may be rejected if stock has changed.') : null,
    h('div', { class: 'controls', style: 'margin-bottom:14px' }, select([['all', 'All medicines'], ...ctx.meta.medicines.map((m) => [m.id, m.short_name])], st.medicine, (v) => { st.medicine = v; st.selected = null; draw(); }, 'Medicine'), h('div', { style: 'align-self:flex-end' }, summary)),
    h('div', { class: 'layout-map' }, h('div', null, mapBox, h('p', { class: 'muted small' }, 'Offline-friendly schematic map: positions come from clinic coordinates; dotted lines are illustrative road links; distances are estimated road kilometres (1.3 x straight line).')), list),
    h('div', { style: 'margin-top:16px' }, hist));
}
