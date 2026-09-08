// Every call funnels through here so errors surface the same way everywhere.
// A deployed desk is protected by a shared token. It lives in localStorage on
// the device, is sent as a header, and is asked for once when the server says
// the request was not authorised.
const TOKEN_KEY = 'labdhi.token';
export const getToken = () => { try { return localStorage.getItem(TOKEN_KEY) || ''; } catch (_) { return ''; } };
export const setToken = t => { try { localStorage.setItem(TOKEN_KEY, t); } catch (_) {} };

// Same-origin by default; set window.LABDHI_API to point at a separate backend.
const base = () => (window.LABDHI_API || '').replace(/\/$/, '');

async function call(path, opts = {}) {
  const token = getToken();
  const res = await fetch(base() + path, {
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'X-Labdhi-Token': token } : {})
    },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined
  });
  if (res.status === 401) {
    const entered = window.prompt('Access token for this desk:');
    if (entered) { setToken(entered.trim()); return call(path, opts); }
    throw new Error('Access token required');
  }
  let data = null;
  try { data = await res.json(); } catch (_) { data = null; }
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
