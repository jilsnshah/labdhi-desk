// Every call funnels through here so errors surface the same way everywhere.
// A deployed desk is protected by a shared password. It lives in localStorage
// on the device, is sent as a header, and is asked for once when the server
// says the request was not authorised.
//
// Every list endpoint answers one shape - {items, total, limit, offset,
// has_more} - and every master record is saved by POST with or without an id
// and comes back whole.
const TOKEN_KEY = 'labdhi.token';
export const getToken = () => { try { return localStorage.getItem(TOKEN_KEY) || ''; } catch (_) { return ''; } };
export const setToken = t => { try { localStorage.setItem(TOKEN_KEY, t); } catch (_) {} };

// Same-origin by default; set window.LABDHI_API to point at a separate backend.
const base = () => (window.LABDHI_API || '').replace(/\/$/, '');

let askForToken = async () => {
  const v = window.prompt('Password for this desk:');
  return v ? v.trim() : '';
};
export const setTokenPrompt = fn => { askForToken = fn; };

// Render's free tier stops the instance after ~15 minutes idle, and the next
// request pays ~50 seconds to boot it. Any call that takes more than a moment
// announces itself, and a cold-start failure is retried rather than surfaced.
let inFlight = 0;
const slow = new EventTarget();
export const onSlow = fn => slow.addEventListener('slow', e => fn(e.detail));

async function call(path, opts = {}, attempt = 0) {
  const token = getToken();
  inFlight++;
  const timer = setTimeout(() => slow.dispatchEvent(new CustomEvent('slow', { detail: true })), 2500);
  const done = () => {
    clearTimeout(timer);
    if (--inFlight <= 0) slow.dispatchEvent(new CustomEvent('slow', { detail: false }));
  };

  let res;
  try {
    res = await fetch(base() + path, {
      headers: { 'Content-Type': 'application/json', ...(token ? { 'X-Labdhi-Token': token } : {}) },
      ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined
    });
  } catch (err) {
    done();
    if (attempt < 3) {
      await new Promise(r => setTimeout(r, 1500 * (attempt + 1)));
      return call(path, opts, attempt + 1);
    }
    throw new Error('Could not reach the server. Check your connection.');
  }
  if ((res.status === 502 || res.status === 503) && attempt < 4) {
    done();
    await new Promise(r => setTimeout(r, 2000 * (attempt + 1)));
    return call(path, opts, attempt + 1);
  }
  if (res.status === 401) {
    done();
    const entered = await askForToken();
    if (entered) { setToken(entered); return call(path, opts); }
    throw new Error('Password required');
  }
  let data = null;
  try { data = await res.json(); } catch (_) { data = null; }
  done();
  if (!res.ok) {
    const msg = (data && (data.error || data.detail)) || `Request failed (${res.status})`;
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return data;
}

const qs = o => Object.entries(o || {})
  .filter(([, v]) => v !== undefined && v !== null && v !== '' && v !== false)
  .map(([k, v]) => `${k}=${encodeURIComponent(v === true ? 1 : v)}`).join('&');
const get = (path, params) => call(path + (params && qs(params) ? '?' + qs(params) : ''));
const post = (path, body) => call(path, { method: 'POST', body: body || {} });

export const api = {
  bootstrap: () => get('/api/bootstrap'),
  summary: () => get('/api/summary'),

  // master records
  parties: p => get('/api/parties', p),
  party: id => get(`/api/parties/${id}`),
  partySave: body => post('/api/parties', body),
  partyRemove: id => post(`/api/parties/${id}/remove`),
  gstin: g => get(`/api/gstin/${encodeURIComponent(g)}`),
  states: p => get('/api/states', p),

  warehouses: p => get('/api/warehouses', p),
  warehouse: id => get(`/api/warehouses/${id}`),
  warehouseSave: body => post('/api/warehouses', body),
  warehouseRemove: id => post(`/api/warehouses/${id}/remove`),

  materials: p => get('/api/materials', p),
  materialSave: body => post('/api/materials', body),
  materialRemove: id => post(`/api/materials/${id}/remove`),
  grades: p => get('/api/grades', p),
  gradeSave: body => post('/api/grades', body),
  gradeRemove: id => post(`/api/grades/${id}/remove`),
  manufacturers: p => get('/api/manufacturers', p),
  manufacturerSave: body => post('/api/manufacturers', body),
  manufacturerRemove: id => post(`/api/manufacturers/${id}/remove`),
  products: p => get('/api/products', p),
  product: id => get(`/api/products/${id}`),
  productSave: body => post('/api/products', body),
  productRemove: id => post(`/api/products/${id}/remove`),

  // stock
  positions: p => get('/api/positions', p),
  position: id => get(`/api/positions/${id}`),
  stock: p => get('/api/stock', p),
  lots: (product_id, warehouse_id) => get('/api/stock/lots', { product_id, warehouse_id }),
  moves: p => get('/api/stock/moves', p),
  transfer: body => post('/api/stock/transfer', body),
  adjust: body => post('/api/stock/adjust', body),
  cancelMove: id => post(`/api/stock/moves/${id}/cancel`),
  graph: p => get('/api/graph', p),
  trace: (kind, id) => get(`/api/trace/${kind}/${id}`),

  // deals
  deals: p => get('/api/deals', p),
  deal: id => get(`/api/deals/${id}`),
  createDeal: body => post('/api/deals', body),
  previewSell: body => post('/api/preview/sell', body),
  reallocate: (id, body) => post(`/api/deals/${id}/reallocate`, body),
  cancel: id => post(`/api/deals/${id}/cancel`),
  saudaNext: date => get('/api/sauda/next', { date }),
  counterparties: p => get('/api/counterparties', p),
  undo: () => post('/api/undo'),
  events: p => get('/api/events', p),
  setMark: (productId, rate_paise) => post(`/api/marks/${productId}`, { rate_paise }),
  settings: body => post('/api/settings', body)
};
