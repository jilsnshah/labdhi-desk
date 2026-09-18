// Display helpers. All maths happened on the server in integers; this file
// only ever turns those integers into something a human reads fast.

export const KG = 1000, TON = 1e6, RUPEE = 100;

export const kg = g => (g || 0) / KG;
// stock below zero is sold short: shown in red wherever stock is shown
export const neg = g => ((g || 0) < 0 ? ' neg' : '');

export function qty(g, opts = {}) {
  const t = (g || 0) / TON;
  if (Math.abs(t) >= 1 || g === 0) {
    const s = t % 1 === 0 ? t.toFixed(0) : t.toFixed(t % 0.1 === 0 ? 1 : 3);
    return opts.short ? `${s}T` : `${s} MT`;
  }
  return `${Math.round(kg(g)).toLocaleString('en-IN')} kg`;
}

export function inr(paise, opts = {}) {
  const v = (paise || 0) / RUPEE;
  const sign = v < 0 ? '-' : (opts.sign && v > 0 ? '+' : '');
  const a = Math.abs(v);
  if (opts.compact && a >= 1e5) {
    if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)}Cr`;
    return `${sign}₹${(a / 1e5).toFixed(2)}L`;
  }
  return sign + '₹' + a.toLocaleString('en-IN', {
    minimumFractionDigits: opts.paise ? 2 : 0,
    maximumFractionDigits: opts.paise ? 2 : 0
  });
}

// Rates are stored as integer paise per kg and shown as rupees per kg, the
// way they are quoted: 9825 paise/kg is ₹98.25/kg. Only the display and the
// input change; every calculation stays on the integer paise.
const kgFmt = { minimumFractionDigits: 2, maximumFractionDigits: 2 };
export const perKg = p => ((p || 0) / 100).toLocaleString('en-IN', kgFmt);
export const rate = p => '₹' + perKg(p);
export const rateDelta = p => (p >= 0 ? '+' : '−') + '₹' + perKg(Math.abs(p || 0));
// the bare figure for an input box: 98.25, 98.5, 98
export const perKgPlain = p => String((p || 0) / 100);

// ₹ per kg typed by the trader -> integer paise per kg, or null when it cannot
// be held exactly (more than two decimals, or not a number). A figure that
// would have to be rounded is refused, never adjusted. Parsed as text, so no
// floating-point step can turn 98.29 into 9828.
export function fromPerKg(text) {
  const m = /^(\d*)(?:\.(\d{0,2}))?$/.exec(String(text ?? '').trim());
  if (!m || (m[1] === '' && !m[2])) return null;
  return Number(m[1] || 0) * 100 + Number(((m[2] || '') + '00').slice(0, 2));
}

export function date(iso) {
  if (!iso) return '';
  const d = new Date(iso + (iso.length === 10 ? 'T00:00:00' : ''));
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' });
}

export function ago(iso) {
  if (!iso) return '';
  const days = Math.round((Date.now() - new Date(iso + (iso.length === 10 ? 'T00:00:00' : '')).getTime()) / 864e5);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 30) return `${days}d ago`;
  return `${Math.round(days / 30)}mo ago`;
}

export const initials = name => (name || '?').split(/\s+/).slice(0, 2).map(w => w[0]).join('').toUpperCase();

// A quantity as the bare MT figure a column shows: 12, 12.5, 0.25.
export const mt = g => ((g || 0) / TON).toLocaleString('en-IN', { maximumFractionDigits: 3 });

// "2.5" MT typed into a form -> grams, or null when it is not a positive number.
export function fromMt(text) {
  const v = parseFloat(String(text || '').replace(/[^0-9.]/g, ''));
  return isFinite(v) && v > 0 ? Math.round(v * TON) : null;
}

export function today() {
  const d = new Date(), p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export function addDays(iso, days) {
  const d = new Date((iso || today()) + 'T00:00:00');
  d.setDate(d.getDate() + days);
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// The same GSTIN rule the server applies, run as it is typed: format, then the
// check character over the first fourteen.
const B36 = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ';
export function gstinProblem(g) {
  if (!g) return null;
  if (!/^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/.test(g)) return 'is not in GSTIN format';
  let total = 0;
  for (let i = 0; i < 14; i++) {
    const v = B36.indexOf(g[i]) * (i % 2 ? 2 : 1);
    total += Math.floor(v / 36) + (v % 36);
  }
  return B36[(36 - (total % 36)) % 36] === g[14] ? null : 'has a wrong check character — likely a typo';
}

export const sideLabel = s => (s === 'sell' ? 'SELL' : 'BUY');
export const statusLabel = s => ({ booked: 'Booked', cancelled: 'Cancelled', draft: 'Draft' }[s] || s);
