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

// Rates always show paise - a trader argues over 5 paise.
export const rate = p => '₹' + ((p || 0) / RUPEE).toFixed(2);
export const rateDelta = p => (p >= 0 ? '+' : '−') + '₹' + Math.abs((p || 0) / RUPEE).toFixed(2);

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
