// Changing a booked sauda, on the desktop and the phone alike.
//
// Nothing is saved until the trader has seen what the change does. The server
// runs the change for real and rolls it back (see backend/services/revise.py),
// so the "before -> after" shown here is exactly what Save will do: which
// fields change, which sales' cost and margin move, how much stock moves.
//
// Cancelling a purchase whose stock is already sold offers the same review:
// those sales can move onto other stock of the same product in the same
// warehouse, keeping their Sauda No., buyer and rate. What other stock cannot
// cover is sold short again, until the next stock arrives there.

import { h, toast, pnlClass } from './ui.js';
import * as f from './fmt.js';
import { api } from './api.js';
import { sendSauda } from './whatsapp.js';

const TEXT = ['payment_due', 'ex_place', 'freight_by', 'delivery_by', 'payment_terms', 'eway', 'remarks'];
const blank = v => (v === undefined || v === null || String(v).trim() === '' ? null : String(v).trim());

// What differs between the booked deal and the ticket. `pins` is the split the
// ticket shows (sales only); it is sent only when the split itself changed.
export function diffDeal(deal, next, pins) {
  const c = {};
  for (const k of ['party_id', 'product_id', 'warehouse_id', 'qty_g', 'rate_paise']) {
    if (next[k] !== undefined && next[k] !== deal[k]) c[k] = next[k];
  }
  if (next.plus_gst !== undefined && !!next.plus_gst !== !!deal.plus_gst) c.plus_gst = !!next.plus_gst;
  if (next.deal_date && next.deal_date !== deal.deal_date) c.deal_date = next.deal_date;
  if ((next.transporter_id || null) !== (deal.transporter_id || null)) c.transporter_id = next.transporter_id || null;
  for (const k of TEXT) if (blank(next[k]) !== blank(deal[k])) c[k] = blank(next[k]);
  if (deal.side === 'sell' && pins) {
    const had = {};
    for (const a of deal.allocations || []) had[a.lot_id] = (had[a.lot_id] || 0) + a.qty_g;
    const now = {};
    for (const p of pins) if (p.qty_g > 0) now[p.lot_id] = (now[p.lot_id] || 0) + p.qty_g;
    const same = Object.keys(had).length === Object.keys(now).length &&
      Object.keys(had).every(k => had[k] === now[k]);
    if (!same || c.product_id !== undefined || c.warehouse_id !== undefined) c.pins = pins.filter(p => p.qty_g > 0);
  }
  return c;
}

const company = ctx => (ctx && ctx.boot && ctx.boot.settings && ctx.boot.settings.company_name);

function sheet(title, sub, ...content) {
  let done;
  const result = new Promise(r => { done = r; });
  const onKey = e => { if (e.key === 'Escape') close(null); };
  const close = value => { overlay.remove(); document.removeEventListener('keydown', onKey); done(value); };
  const card = h('div', { class: 'modal-card review-card' },
    h('div', { class: 'modal-head' },
      h('div', {}, h('div', { class: 'modal-title' }, title), sub ? h('div', { class: 'modal-sub' }, sub) : null),
      h('button', { type: 'button', class: 'modal-x', onclick: () => close(null) }, '×')),
    ...content.map(c => (typeof c === 'function' ? c(close) : c)));
  const overlay = h('div', { class: 'modal', onmousedown: e => { if (e.target === overlay) close(null); } }, card);
  document.addEventListener('keydown', onKey);
  document.body.appendChild(overlay);
  return result;
}

const arrow = (a, b, cls = '') => h('span', { class: 'rv-ab ' + cls },
  h('s', {}, a === '' || a === null || a === undefined ? '—' : a), ' → ',
  h('b', {}, b === '' || b === null || b === undefined ? '—' : b));

// a sale with nothing covered has no cost yet
const costText = p => (p === null || p === undefined ? 'not known yet' : f.rate(p));

// Every sale whose margin moves, and the stock that moves with it.
function effectsBlock(effects) {
  if (!effects) return null;
  const sales = effects.sales || [];
  const stock = effects.stock || [];
  const realised = effects.realised_after_paise - effects.realised_before_paise;
  return h('div', { class: 'rv-effects' },
    sales.length ? h('div', { class: 'rv-title' }, sales.length === 1 ? 'Margin' : `${sales.length} sales change`) : null,
    ...sales.map(s => h('div', { class: 'rv-sale' },
      h('div', { class: 'rv-sale-who' }, h('b', {}, s.ref), h('span', {}, `${s.party} · ${f.qty(s.qty_g)}`),
        s.margin_after_paise !== null && s.short_before_g !== s.short_after_g
          ? h('em', { class: 'rv-short' + (s.short_after_g ? '' : ' ok') }, s.short_after_g
              ? `${f.qty(s.short_after_g)} sold short` + (s.short_before_g ? ` (was ${f.qty(s.short_before_g)})` : '')
              : 'no longer short') : null),
      s.margin_after_paise === null
        ? h('div', { class: 'rv-sale-nums' }, h('span', { class: 'dim' }, 'cancelled'),
            arrow(f.inr(s.margin_before_paise, { sign: true }), '₹0'))
        : h('div', { class: 'rv-sale-nums' },
            s.cost_before_paise !== s.cost_after_paise
              ? h('span', { class: 'dim' }, 'cost ', arrow(costText(s.cost_before_paise), costText(s.cost_after_paise)))
              : null,
            s.margin_before_paise === null
              ? h('b', { class: pnlClass(s.margin_after_paise) }, f.inr(s.margin_after_paise, { sign: true }))
              : arrow(f.inr(s.margin_before_paise, { sign: true }), f.inr(s.margin_after_paise, { sign: true }),
                      pnlClass(s.margin_after_paise - s.margin_before_paise))))),
    stock.length ? h('div', { class: 'rv-title' }, 'Stock') : null,
    ...stock.map(c => h('div', { class: 'rv-line' },
      h('span', {}, `${c.product} · ${c.warehouse}`),
      arrow(f.qty(c.before_g), f.qty(c.after_g)))),
    realised ? h('div', { class: 'rv-line rv-total' }, h('span', {}, 'Realised profit, all sales'),
      h('b', { class: pnlClass(realised) }, f.inr(realised, { sign: true }))) : null);
}

// Review an edit; on "Save changes" it is made. Resolves the saved deal, or null.
export async function reviewEdit(deal, changes, ctx) {
  if (!Object.keys(changes).length) { toast('Nothing changed'); return null; }
  let p;
  try { p = await api.editPreview(deal.id, changes); }
  catch (err) { toast(err.message, { kind: 'err', ms: 9000 }); return null; }

  const fields = p.changes || [];
  const saved = await sheet(`Save changes to ${deal.ref}?`,
    `${deal.side === 'sell' ? 'Sale to' : 'Purchase from'} ${deal.party_name} · same Sauda No.`,
    h('div', { class: 'rv-fields' },
      ...fields.map(c => h('div', { class: 'rv-line' }, h('span', {}, c.label), arrow(c.before, c.after))),
      changes.pins && !changes.qty_g && !changes.product_id && !changes.warehouse_id
        ? h('div', { class: 'rv-line' }, h('span', {}, 'Lots'), h('b', {}, 'new split')) : null),
    p.needs_rehome ? h('div', { class: 'rv-note' }, p.error
      ? h('b', {}, p.reason)
      : [h('b', {}, `${f.qty(p.rehome_grams)} already sold from ${deal.ref} moves to other stock.`),
         h('span', {}, 'Those sales keep their Sauda No., buyer and rate — only where the material comes from, and so their cost and margin, change. Whatever other stock cannot cover is sold short again.')])
      : null,
    p.needs_short && !p.error ? h('div', { class: 'rv-note short' },
      h('b', {}, `${f.qty(p.short_grams)} will be sold short.`),
      h('span', {}, 'The warehouse shows it as negative stock until the next purchase or transfer in covers it; its margin is known then.'))
      : null,
    effectsBlock(p.effects),
    p.error ? h('div', { class: 'form-err' }, p.error) : null,
    close => h('div', { class: 'modal-foot' },
      h('button', { type: 'button', class: 'form-cancel', onclick: () => close(null) }, 'Back'),
      h('button', {
        type: 'button', class: 'form-save', disabled: !!p.error,
        onclick: async e => {
          e.target.disabled = true; e.target.textContent = 'Saving…';
          try { close(await api.editDeal(deal.id, { ...changes, rehome: !!p.needs_rehome, allow_short: !!p.needs_short })); }
          catch (err) { e.target.disabled = false; e.target.textContent = 'Save changes'; toast(err.message, { kind: 'err', ms: 9000 }); }
        }
      }, p.needs_rehome ? 'Move sales and save' : p.needs_short ? 'Save — sell short' : 'Save changes')));
  if (!saved) return null;
  toast(`${saved.ref} updated`);
  sendSauda(saved, company(ctx), { revised: true });
  return saved;
}

// Cancel with a review. Resolves true once cancelled.
export async function cancelWithReview(deal, ctx) {
  let p;
  try { p = await api.cancelPreview(deal.id); }
  catch (err) { toast(err.message, { kind: 'err', ms: 9000 }); return false; }
  if (!p.needs_rehome && p.error) { toast(p.error, { kind: 'err', ms: 9000 }); return false; }

  const done = await sheet(`Cancel ${deal.ref}?`,
    p.needs_rehome ? 'Its stock is already sold' : 'Stock and profit go back as if it was never booked',
    p.needs_rehome ? h('div', { class: 'rv-note' },
      h('b', {}, p.reason),
      p.error ? null : h('span', {}, 'Those sales move onto other stock of the same product in the same warehouse, then it is cancelled. They keep their Sauda No., buyer and rate; whatever other stock cannot cover is sold short again, until the next stock arrives there.'))
      : null,
    effectsBlock(p.effects),
    p.error ? h('div', { class: 'form-err' }, p.error) : null,
    close => h('div', { class: 'modal-foot' },
      h('button', { type: 'button', class: 'form-cancel', onclick: () => close(false) }, 'Keep it'),
      h('button', {
        type: 'button', class: 'form-save danger', disabled: !!p.error,
        onclick: async e => {
          e.target.disabled = true;
          try { await api.cancel(deal.id, !!p.needs_rehome); close(true); }
          catch (err) { e.target.disabled = false; toast(err.message, { kind: 'err', ms: 9000 }); }
        }
      }, p.needs_rehome ? 'Move sales and cancel' : `Cancel ${deal.ref}`)));
  if (done) toast('Cancelled ' + deal.ref);
  return !!done;
}
