// Every call funnels through here so errors surface the same way everywhere.
// A deployed desk is protected by a shared token. It lives in localStorage on
// the device, is sent as a header, and is asked for once when the server says
// the request was not authorised.
const TOKEN_KEY = 'labdhi.token';
export const getToken = () => { try { return localStorage.getItem(TOKEN_KEY) || ''; } catch (_) { return ''; } };
export const setToken = t => { try { localStorage.setItem(TOKEN_KEY, t); } catch (_) {} };

// Same-origin by default; set window.LABDHI_API to point at a separate backend.
const base = () => (window.LABDHI_API || '').replace(/\/$/, '');

// The shell installs a real unlock screen over this; the prompt is only the
// fallback if something asks for a token before the UI has booted.
let askForToken = async () => {
  const v = window.prompt('Access token for this desk:');
  return v ? v.trim() : '';
};
export const setTokenPrompt = fn => { askForToken = fn; };

// Render's free tier stops the instance after ~15 minutes idle, and the next
// request pays ~50 seconds to boot it. Without a word on screen that reads as
// a broken app, so any call that takes more than a moment announces itself and
// a cold-start failure is retried rather than surfaced as an error.
let inFlight = 0;
const slow = new EventTarget();
export const onSlow = fn => slow.addEventListener('slow', e => fn(e.detail));

async function call(path, opts = {}, attempt = 0) {
  const token = getToken();
  inFlight++;
  const timer = setTimeout(() => slow.dispatchEvent(
    new CustomEvent('slow', { detail: true })), 2500);
  const done = () => {
    clearTimeout(timer);
    if (--inFlight <= 0) slow.dispatchEvent(new CustomEvent('slow', { detail: false }));
  };

  let res;
  try {
    res = await fetch(base() + path, {
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'X-Labdhi-Token': token } : {})
    },
      ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined
    });
  } catch (err) {
    done();
    // A dropped connection while the instance boots is not a real failure.
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
    throw new Error('Access token required');
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
  .filter(([, v]) => v !== undefined && v !== null && v !== '')
  .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');

export const api = {
  bootstrap: () => call('/api/bootstrap'),
  desk: (params) => call('/api/desk?' + qs(params)),
  tape: (params) => call('/api/tape?' + qs(params)),
  positions: (params) => call('/api/positions?' + qs(params)),
  position: id => call(`/api/positions/${id}`),
  graph: (params) => call('/api/graph?' + qs(params)),
  trace: (kind, id) => call(`/api/trace/${kind}/${id}`),
  parties: (q, role) => call('/api/search/parties?' + qs({ q, role })),
  materials: (q, in_stock) => call('/api/search/materials?' + qs({ q, in_stock })),
  catalog: (level, material, grade, in_stock) =>
    call('/api/catalog/options?' + qs({ level, material, grade, in_stock })),
  resolveSku: (material, grade, manufacturer) =>
    call('/api/catalog/resolve?' + qs({ material, grade, manufacturer })),
  catalogTree: () => call('/api/catalog/tree'),
  partyList: q => call('/api/parties' + (q ? '?q=' + encodeURIComponent(q) : '')),
  partySave: body => call('/api/parties', { method: 'POST', body }),
  partyRemove: id => call(`/api/parties/${id}/remove`, { method: 'POST' }),
  addCatalog: body => call('/api/catalog/entry', { method: 'POST', body }),
  removeCatalog: body => call('/api/catalog/remove', { method: 'POST', body }),
  lots: skuId => call(`/api/lots/${skuId}`),
  deals: params => call('/api/deals?' + qs(params)),
  deal: id => call(`/api/deals/${id}`),
  previewSell: body => call('/api/preview/sell', { method: 'POST', body }),
  createDeal: body => call('/api/deals', { method: 'POST', body }),
  reallocate: (id, body) => call(`/api/deals/${id}/reallocate`, { method: 'POST', body }),
  cancel: id => call(`/api/deals/${id}/cancel`, { method: 'POST' }),
  undo: () => call('/api/undo', { method: 'POST' }),
  setMark: (skuId, rate) => call(`/api/marks/${skuId}`, { method: 'POST', body: { rate } }),
  settings: body => call('/api/settings', { method: 'POST', body })
};
