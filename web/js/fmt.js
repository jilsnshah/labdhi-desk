// Display helpers. All maths happened on the server in integers; this file
// only ever turns those integers into something a human reads fast.

export const KG = 1000, TON = 1e6, RUPEE = 100;

export const kg = g => (g || 0) / KG;

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

// Rates are stored as paise per kg and shown per MT, the way the trade quotes
// them: 9825 paise/kg is Rs 98,250/MT. One paisa per kg is Rs 10 per MT, so a
// per-MT figure is exact in Rs 10 steps and nothing finer.
export const PER_MT = 10;                         // Rs per MT for 1 paisa per kg
export const perMt = p => Math.round((p || 0) * PER_MT);
export const rate = p => '₹' + perMt(p).toLocaleString('en-IN');
export const rateDelta = p =>
  (p >= 0 ? '+' : '−') + '₹' + Math.abs(perMt(p)).toLocaleString('en-IN');

// Rs per MT typed by the trader -> paise per kg, or null when it cannot be held
// exactly. A figure that would have to be rounded is refused, never adjusted.
export function fromPerMt(rupees) {
  const r = Number(rupees);
  if (!isFinite(r) || r < 0) return null;
  if (Math.round(r) !== r || r % PER_MT !== 0) return null;
  return r / PER_MT;
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
