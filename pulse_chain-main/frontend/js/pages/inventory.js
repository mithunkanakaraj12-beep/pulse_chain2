import * as api from '../api.js';
import { h, fmt, select, card, riskBadge, toast } from '../ui.js';

const RANK = { low: 0, moderate: 1, high: 2, critical: 3 };

export async function inventory(_p, ctx) {
  const { data, fromCache } = await api.get('/api/inventory');
  const meta = ctx.meta;
  const f = { clinic: 'all', medicine: 'all', risk: 'all', open: null };
  let rows = data.rows;
  const body = h('tbody'), count = h('span', { class: 'muted' });
  const clinicNames = [...new Map(rows.map((r) => [r.clinic_id, r.clinic_name])).entries()];

  function saveHandler(row, batch, input) {
    return async () => {
      const qty = input.value;
      try {
        const res = await api.send('PATCH', `/api/inventory/batches/${batch.id}`, { quantity: Number(qty) }, { queueable: true, label: `${row.short_name} stock at ${row.clinic_name}` });
        if (res.queued) { toast('Saved offline. It will sync when you are back online; projections update after syncing.'); return; }
        const before = row.days_to_stockout;
        const fresh = (await api.get('/api/inventory')).data.rows;
        rows = fresh;
        const after = fresh.find((r) => r.clinic_id === row.clinic_id && r.medicine_id === row.medicine_id);
        toast(`Recalculated: ${row.short_name} at ${row.clinic_name} now lasts ${fmt.days(after.days_to_stockout)} (was ${fmt.days(before)}); risk ${after.risk}.`, 'ok');
        ctx.refreshMeta(); draw();
      } catch (e) { toast(e.message, 'err'); }
    };
  }

  function batchPanel(r) {
    const tbody = h('tbody', null, r.batches.map((b) => {
      const input = h('input', { type: 'number', min: 0, max: 1000000, value: b.quantity, 'aria-label': `Quantity of batch ${b.id}` });
      return h('tr', null, h('td', null, `#${b.id}`), h('td', null, b.expired ? h('span', { class: 'exp' }, `Expired ${fmt.date(b.expiry_date)}`) : fmt.date(b.expiry_date)),
        h('td', null, b.origin || '-'), h('td', null, input), h('td', null, h('button', { class: 'btn small', onclick: saveHandler(r, b, input) }, 'Save')));
    }));
    const qty = h('input', { type: 'number', min: 1, placeholder: 'Quantity' });
    const exp = h('input', { type: 'date', 'aria-label': 'Expiry date' });
    const add = h('button', { class: 'btn small primary', onclick: async () => {
      try {
        const res = await api.send('POST', '/api/inventory/batches', { clinic_id: r.clinic_id, medicine_id: r.medicine_id, quantity: Number(qty.value), expiry_date: exp.value }, { queueable: true, label: `new ${r.short_name} stock at ${r.clinic_name}` });
        if (res.queued) { toast('Saved offline. It will sync when you are back online.'); return; }
        rows = (await api.get('/api/inventory')).data.rows;
        toast('Stock added and projections recalculated.', 'ok'); ctx.refreshMeta(); draw();
      } catch (e) { toast(e.message, 'err'); }
    } }, 'Add stock');
    return h('div', { class: 'batch-table', style: 'padding:10px 12px' },
      h('table', null, h('thead', null, h('tr', null, ['Batch', 'Expiry', 'Origin', 'Quantity', ''].map((t) => h('th', null, t)))), tbody),
      h('div', { class: 'controls', style: 'margin-top:10px' }, h('strong', { class: 'small' }, 'Receive new stock:'), qty, exp, add),
      h('p', { class: 'muted small' }, 'Demo edits change the simulated database only. Reset the demo from the Alerts or Demo page to restore the original data.'));
  }

  function draw() {
    const shown = rows.filter((r) => (f.clinic === 'all' || r.clinic_id === f.clinic) && (f.medicine === 'all' || r.medicine_id === f.medicine) && (f.risk === 'all' || r.risk === f.risk));
    count.textContent = `${shown.length} of ${rows.length} stock lines`;
    body.replaceChildren(...shown.flatMap((r) => {
      const key = `${r.clinic_id}:${r.medicine_id}`;
      const line = h('tr', { class: 'clickable', onclick: () => { f.open = f.open === key ? null : key; draw(); } },
        h('td', null, h('strong', null, r.short_name), h('div', { class: 'muted small' }, r.medicine_name)),
        h('td', null, r.clinic_name),
        h('td', { class: 'num' }, `${fmt.num(r.usable_stock)}`, h('div', { class: 'muted small' }, r.unit)),
        h('td', { class: 'num' }, fmt.num(r.avg_daily_usage, 1), h('div', { class: 'muted small' }, `forecast ${fmt.num(r.forecast_daily_usage_4w, 1)}`)),
        h('td', { class: 'num' }, r.stockout_date ? fmt.short(r.stockout_date) : '>180 d', h('div', { class: 'muted small' }, `${fmt.days(r.days_to_stockout)} (${fmt.days(r.days_to_stockout_high_demand)} if demand is high)`)),
        h('td', { class: 'num' }, r.reorder_qty ? fmt.num(r.reorder_qty) : '-', h('div', { class: 'muted small' }, r.reorder_qty ? (r.order_now ? 'order now' : `by ${fmt.short(r.order_by_date)}`) : '')),
        h('td', { class: 'num' }, fmt.short(r.nearest_expiry), r.expired_units ? h('div', { class: 'exp small' }, `${fmt.num(r.expired_units)} expired`) : null),
        h('td', null, riskBadge(r.risk)));
      return f.open === key ? [line, h('tr', null, h('td', { colspan: 8 }, batchPanel(r)))] : [line];
    }));
    if (!shown.length) body.replaceChildren(h('tr', null, h('td', { colspan: 8, class: 'empty' }, 'No stock lines match.')));
  }
  draw();
  return h('div', null,
    h('div', { class: 'page-head' }, h('div', null, h('p', null, 'Stock, usage, projected stock-out and reorder advice for every clinic and medicine. Select a row to edit batches or receive stock: all downstream numbers recalculate.')), count),
    fromCache ? h('div', { class: 'warn-box', style: 'margin-bottom:12px' }, 'Offline: showing the last synced inventory. Edits are queued and applied when you reconnect.') : null,
    h('div', { class: 'card', style: 'margin-bottom:16px' }, h('div', { class: 'controls' },
      select([['all', 'All clinics'], ...clinicNames], f.clinic, (v) => { f.clinic = v; f.open = null; draw(); }, 'Clinic'),
      select([['all', 'All medicines'], ...meta.medicines.map((m) => [m.id, m.short_name])], f.medicine, (v) => { f.medicine = v; f.open = null; draw(); }, 'Medicine'),
      select([['all', 'Any risk'], ['critical', 'Critical'], ['high', 'High risk'], ['moderate', 'Watch'], ['low', 'Adequate']], f.risk, (v) => { f.risk = v; f.open = null; draw(); }, 'Risk'))),
    h('div', { class: 'card' }, h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null, h('tr', null, ['Medicine', 'Clinic', 'Current stock', 'Avg daily use', 'Est. stock-out', 'Reorder qty', 'Nearest expiry', 'Risk'].map((t, i) => h('th', { class: [2, 3, 4, 5, 6].includes(i) ? 'num' : '' }, t)))), body))),
    h('details', { class: 'how card', style: 'margin-top:16px' }, h('summary', null, 'How these numbers are calculated'),
      h('ol', null,
        h('li', null, 'Weekly demand = routine baseline use + forecast cases x units per case (a simplified treatment protocol, not clinical guidance).'),
        h('li', null, 'Stock is depleted day by day, earliest-expiry first. Expired batches are excluded and units that expire unused are counted as waste.'),
        h('li', null, 'Margin = days until stock-out minus supplier delivery time. Critical below 0, high below 10 days, watch below 21 days.'),
        h('li', null, 'Reorder quantity = demand over (delivery time + 28 days) + safety stock - usable stock, rounded up to the pack size. Order-by date = day stock reaches safety level minus delivery time.'))));
}
