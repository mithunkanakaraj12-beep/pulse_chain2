import * as api from '../api.js';
import { h, fmt, card, riskBadge, toast, simBadge } from '../ui.js';

export async function alerts(_p, ctx) {
  const { data, fromCache } = await api.get('/api/alerts');
  const live = data.sms_mode === 'live';
  const online = navigator.onLine && !fromCache;
  const confirmBox = h('input', { type: 'checkbox', id: 'confirm-live' });

  async function send(a) {
    try {
      const res = await api.send('POST', '/api/sms/send', { alert_key: a.key, confirm_live: live && confirmBox.checked });
      toast(res.status === 'simulated' ? `Simulated SMS to ${a.health_worker} (nothing was sent).` : `SMS ${res.status}.`, res.status === 'failed' ? 'err' : 'ok');
    } catch (e) { toast(e.message, 'err'); }
    ctx.refresh();
  }
  async function ack(a) {
    try {
      const res = await api.send('POST', '/api/alerts/ack', { key: a.key }, { queueable: true, label: `acknowledge ${a.title}` });
      toast(res.queued ? 'Saved offline; will sync later.' : 'Alert acknowledged.');
    } catch (e) { toast(e.message, 'err'); }
    ctx.refreshMeta(); ctx.refresh();
  }
  const sentKeys = new Set(data.sms_log.filter((l) => l.status !== 'blocked').map((l) => l.alert_key));
  const alertCard = (a) => h('article', { class: `card alert-card ${a.severity} ${a.acknowledged ? 'ack' : ''}` },
    h('div', { class: 'card-head' }, h('h3', null, a.title), h('span', null, riskBadge(a.severity), a.acknowledged ? h('span', { class: 'badge gray', style: 'margin-left:6px' }, 'acknowledged') : null)),
    h('div', null, a.message),
    a.type === 'stock' ? h('div', { class: 'muted small' }, `Estimated stock-out ${fmt.date(a.stockout_date)} - reorder ${fmt.num(a.reorder_qty)} ${a.unit} ${a.order_by_date ? 'by ' + fmt.date(a.order_by_date) : ''}`) : null,
    a.sms_body ? h('div', { class: 'sms' }, a.sms_body, h('small', null, `To ${a.health_worker} ${a.to_number} - ${a.sms_body.length} characters`)) : h('div', { class: 'muted small' }, 'Below the SMS threshold: shown here only.'),
    h('div', { class: 'controls' },
      a.sms_body ? h('button', { class: 'btn small primary', disabled: !online || (live && !confirmBox.checked), title: !online ? 'Sending needs a connection to the server' : '', onclick: () => send(a) }, live ? (sentKeys.has(a.key) ? 'Send again (live)' : 'Send SMS (live)') : (sentKeys.has(a.key) ? 'Simulate again' : 'Send simulated SMS')) : null,
      a.acknowledged ? null : h('button', { class: 'btn small', onclick: () => ack(a) }, 'Acknowledge')));
  const group = (title, sub, items) => items.length ? h('section', { style: 'margin-bottom:22px' }, h('h2', { style: 'font-size:17px;margin-bottom:2px' }, title), h('div', { class: 'muted small', style: 'margin-bottom:10px' }, sub), h('div', { class: 'grid g-2' }, items.map(alertCard))) : null;

  const A = data.alerts;
  const critical = A.filter((a) => a.type === 'stock' && a.severity === 'critical');
  const upcoming = A.filter((a) => a.type === 'stock' && a.severity !== 'critical');
  const surges = A.filter((a) => a.type === 'surge');
  const eligible = A.filter((a) => a.sms_body && !a.acknowledged);

  const phone = h('div', { class: 'phone', 'aria-label': 'Simulated phone showing SMS messages' }, h('div', { class: 'ph-head' }, data.sms_mode === 'live' ? 'LIVE SMS log' : 'SIMULATED phone - no real messages'),
    ...(data.sms_log.length ? data.sms_log.slice(0, 6).map((l) => h('div', { class: 'msg' }, l.body, h('small', null, `${l.to_number} - ${l.status}${l.error ? ' - ' + l.error : ''} - ${fmt.time(l.created_at)}`))) : [h('div', { class: 'muted small', style: 'text-align:center;margin-top:40px' }, 'Messages you send appear here.')]));

  const repl = h('table', null, h('thead', null, h('tr', null, ['Clinic', 'Medicine', 'Order', 'By', 'Delivery', 'Risk'].map((t, i) => h('th', { class: [2, 4].includes(i) ? 'num' : '' }, t)))),
    h('tbody', null, data.replenishment.slice(0, 14).map((r) => h('tr', null, h('td', null, r.clinic_name), h('td', null, r.short_name), h('td', { class: 'num' }, `${fmt.num(r.reorder_qty)} ${r.unit}`), h('td', null, r.order_now ? h('strong', null, 'now') : fmt.short(r.order_by_date)), h('td', { class: 'num' }, `${r.lead_time_days} d`), h('td', null, riskBadge(r.risk))))));

  return h('div', null,
    h('div', { class: 'page-head' }, h('div', null, h('p', null, `Shortage and surge warnings. SMS text is generated for alerts at or above "${data.threshold}" risk. Nothing is ever sent automatically.`)), h('div', null, simBadge())),
    fromCache ? h('div', { class: 'warn-box', style: 'margin-bottom:12px' }, 'Offline: showing the last synced alerts. Sending SMS needs the server.') : null,
    live ? h('div', { class: 'warn-box', style: 'margin-bottom:12px' }, h('strong', null, 'Live SMS mode. '), 'Real messages can be sent only when Twilio is configured and you tick this box for each send: ', h('label', null, confirmBox, ' I confirm sending a real SMS')) :
      h('div', { class: 'note-box', style: 'margin-bottom:12px' }, 'SMS mode is simulated: messages are logged below and never leave this app. Real delivery via Twilio is available by configuring the environment (see the README).'),
    h('div', { class: 'layout-map', style: 'grid-template-columns:minmax(0,1.7fr) minmax(280px,.8fr)' },
      h('div', null,
        group('Critical shortages', 'Stock runs out before an order placed today could arrive', critical),
        group('Upcoming shortages', 'Stock projected to run low; order soon', upcoming),
        group('Disease surge warnings', 'Forecast cases rising sharply versus the last four weeks', surges),
        !A.length ? h('div', { class: 'empty card' }, 'No active alerts.') : null,
        h('section', null, h('h2', { style: 'font-size:17px;margin-bottom:8px' }, 'Replenishment recommendations'), card(null, 'Orders due within 14 days', data.replenishment.length ? h('div', { class: 'table-wrap' }, repl) : h('div', { class: 'empty' }, 'No orders due soon.')))),
      h('div', { style: 'position:sticky;top:80px' }, card('SMS simulator', `${eligible.length} unacknowledged alert${eligible.length === 1 ? '' : 's'} ready to send`, phone,
        !live ? h('div', { class: 'controls', style: 'margin-top:12px;justify-content:center' }, h('button', { class: 'btn primary', disabled: !online || !eligible.length, onclick: async () => { for (const a of eligible) await api.send('POST', '/api/sms/send', { alert_key: a.key }).catch(() => {}); toast(`Simulated ${eligible.length} SMS messages.`, 'ok'); ctx.refresh(); } }, `Simulate all (${eligible.length})`)) : null))));
}
