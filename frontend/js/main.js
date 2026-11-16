import * as api from './api.js';
import { h, fmt, toast, loading, errorBox } from './ui.js';
import { dashboard } from './pages/dashboard.js';
import { clinics } from './pages/clinics.js';
import { forecast } from './pages/forecast.js';
import { inventory } from './pages/inventory.js';
import { redistribution } from './pages/redistribution.js';
import { alerts } from './pages/alerts.js';
import { demo } from './pages/demo.js';

const ROUTES = {
  dashboard: { title: 'Dashboard', page: dashboard },
  clinics: { title: 'Clinics', page: clinics },
  forecast: { title: 'Forecasting center', page: forecast },
  inventory: { title: 'Inventory management', page: inventory },
  redistribution: { title: 'Redistribution center', page: redistribution },
  alerts: { title: 'Alerts & SMS', page: alerts },
  demo: { title: 'Monsoon scenario demo', page: demo },
};

export const ctx = { meta: null, refresh: () => render(), refreshMeta };
let token = 0;

async function refreshMeta() {
  try {
    const { data } = await api.get('/api/meta');
    ctx.meta = data;
    document.getElementById('banner').textContent = `${data.data_label}. Forecasts are model outputs on synthetic data, shown to demonstrate the method.`;
    const chip = document.getElementById('scenario-chip');
    chip.hidden = !data.scenario.monsoon;
    chip.textContent = data.scenario.monsoon ? `Simulated monsoon on (${data.scenario.intensity}x rain)` : '';
    const { data: sum } = await api.get('/api/summary');
    const badge = document.getElementById('nav-alerts');
    badge.hidden = !sum.kpis.active_alerts;
    badge.textContent = sum.kpis.active_alerts;
  } catch (e) {
    if (!ctx.meta) document.getElementById('banner').textContent = 'PulseChain AI - waiting for the server. Offline views appear once you have synced at least once.';
  }
}

function parseRoute() {
  const parts = (location.hash.replace(/^#\/?/, '') || '').split('/').filter(Boolean);
  const name = parts[0] || 'dashboard';
  return { name: ROUTES[name] ? name : 'dashboard', params: parts.slice(1) };
}

async function render() {
  const { name, params } = parseRoute();
  const my = ++token;
  const view = document.getElementById('view');
  document.getElementById('page-title').textContent = ROUTES[name].title;
  document.title = `${ROUTES[name].title} - PulseChain AI`;
  document.querySelectorAll('#nav a').forEach((a) => a.classList.toggle('active', a.dataset.route === name));
  view.replaceChildren(loading());
  try {
    const node = await ROUTES[name].page(params, ctx);
    if (my !== token) return;
    view.replaceChildren(node);
  } catch (e) {
    if (my !== token) return;
    console.error(e);
    view.replaceChildren(errorBox(e));
  }
  closeMenu();
}

function paintNetwork() {
  const chip = document.getElementById('net-chip');
  const online = api.state.online && navigator.onLine;
  chip.classList.toggle('offline', !online);
  const t = api.state.lastSync;
  document.getElementById('net-text').textContent = online
    ? `Online${t ? ` - synced ${fmt.time(t)}` : ''}`
    : `Offline${t ? ` - showing data from ${fmt.time(t)}` : ''}`;
  const q = api.queue.size();
  const qc = document.getElementById('queue-chip');
  qc.hidden = !q;
  qc.textContent = `${q} change${q === 1 ? '' : 's'} waiting to sync`;
}

async function sync() {
  if (!navigator.onLine || !api.queue.size()) return;
  const { done, failed } = await api.flushQueue();
  if (done) toast(`Synced ${done} offline change${done === 1 ? '' : 's'}.`, 'ok');
  failed.forEach((f) => toast(`Could not apply "${f.label || 'a change'}": ${f.message}`, 'err'));
  await refreshMeta();
  render();
}

function closeMenu() {
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('menu-btn').setAttribute('aria-expanded', 'false');
}

document.getElementById('menu-btn').addEventListener('click', () => {
  const open = document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('menu-btn').setAttribute('aria-expanded', String(open));
});
window.addEventListener('hashchange', render);
api.onChange(paintNetwork);
window.addEventListener('online', () => { paintNetwork(); sync(); });
window.addEventListener('offline', paintNetwork);

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch((e) => console.warn('Service worker not registered:', e));
}
paintNetwork();
refreshMeta().then(() => { render(); sync(); });
