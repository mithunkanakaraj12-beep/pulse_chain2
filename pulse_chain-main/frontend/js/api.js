// API client with an offline cache for GET requests and a replay queue for edits made while offline.
const CACHE = 'pc:cache:';
const QUEUE = 'pc:queue';
const listeners = new Set();
export const state = { online: navigator.onLine, lastSync: null };

export const onChange = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
const emit = () => listeners.forEach((fn) => { try { fn(); } catch (e) { console.error(e); } });

export class ApiError extends Error { constructor(msg, status) { super(msg); this.status = status; } }
export class OfflineError extends Error { constructor() { super('You are offline and this view has not been synced yet.'); } }

function readJSON(key, fallback) {
  try { const raw = localStorage.getItem(key); return raw ? JSON.parse(raw) : fallback; } catch { return fallback; }
}
function writeJSON(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; }
}
function pruneCache() {
  const keys = Object.keys(localStorage).filter((k) => k.startsWith(CACHE));
  keys.sort((a, b) => (readJSON(a, {}).t || 0) - (readJSON(b, {}).t || 0));
  keys.slice(0, Math.ceil(keys.length / 2)).forEach((k) => localStorage.removeItem(k));
}

async function request(method, path, body, timeoutMs = 20000) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await fetch(path, {
      method, signal: ctl.signal,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch { /* non-JSON error page */ }
    if (!res.ok) throw new ApiError((data && data.error) || `Request failed (${res.status})`, res.status);
    if (!state.online) { state.online = true; emit(); }
    return data;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if (state.online) { state.online = false; emit(); }
    throw new OfflineError();
  } finally { clearTimeout(timer); }
}

/** GET with cache fallback. Returns {data, fromCache, cachedAt}. */
export async function get(path) {
  if (navigator.onLine) {
    try {
      const data = await request('GET', path);
      const entry = { t: Date.now(), data };
      if (!writeJSON(CACHE + path, entry)) { pruneCache(); writeJSON(CACHE + path, entry); }
      state.lastSync = entry.t;
      writeJSON('pc:lastSync', entry.t);
      emit();
      return { data, fromCache: false, cachedAt: entry.t };
    } catch (e) {
      if (!(e instanceof OfflineError)) throw e;
    }
  }
  const cached = readJSON(CACHE + path, null);
  if (cached) return { data: cached.data, fromCache: true, cachedAt: cached.t };
  throw new OfflineError();
}

export const queue = {
  all: () => readJSON(QUEUE, []),
  size: () => readJSON(QUEUE, []).length,
};

/** Write request. If offline and `queueable`, it is stored and replayed on reconnect. */
export async function send(method, path, body, { queueable = false, label = '' } = {}) {
  if (!navigator.onLine && queueable) {
    const q = queue.all();
    q.push({ method, path, body, label, at: Date.now() });
    writeJSON(QUEUE, q);
    emit();
    return { queued: true };
  }
  return request(method, path, body);
}

let flushing = false;
export async function flushQueue() {
  if (flushing || !navigator.onLine) return { done: 0, failed: [] };
  flushing = true;
  const failed = []; let done = 0;
  try {
    let q = queue.all();
    while (q.length) {
      const item = q[0];
      try { await request(item.method, item.path, item.body); done++; }
      catch (e) {
        if (e instanceof OfflineError) break;       // still offline: keep the rest queued
        failed.push({ label: item.label, message: e.message });
      }
      q = queue.all().slice(1);
      writeJSON(QUEUE, q);
    }
  } finally { flushing = false; emit(); }
  return { done, failed };
}

window.addEventListener('online', () => { state.online = true; emit(); });
window.addEventListener('offline', () => { state.online = false; emit(); });
state.lastSync = readJSON('pc:lastSync', null);
