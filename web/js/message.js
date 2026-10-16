// What a sauda says on WhatsApp, in two messages the trader writes himself.
//
//   party     the confirmation the buyer or supplier gets - no cost, no margin
//   internal  the copy for the firm - whatever the trader wants in it
//
// Both are plain text kept in settings, with {tokens} filled in from the deal.
// Nothing here decides what a message contains: the defaults below are only a
// starting point, and Setup > Messages edits them. A token nobody knows is left
// as it was typed, so a mistyped {qnty} shows up instead of vanishing.
import * as f from './fmt.js';
import { api } from './api.js';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const dmy = iso => {
  if (!iso) return '';
  const [y, m, d] = iso.slice(0, 10).split('-');
  return `${d}-${MONTHS[+m - 1]}-${y}`;
};
const dash = v => (v === null || v === undefined || String(v).trim() === '' ? '—' : String(v).trim());
const byWhom = (verb, who) => (who ? `${verb} by ${String(who).toLowerCase()}` : '');
const RULE = '──────────────';

export const DEFAULTS = {
  party: [
    '🏢 *{company}*',
    '*SAUDA CONFIRMATION*',
    RULE,
    '📅 Date: {date}',
    '🔖 Sauda No.: {ref}',
    'Seller: {seller}',
    'Buyer: {buyer}',
    'Product: {product}',
    'Grade: {grade}',
    'Quantity: {qty}',
    'Rate: {rate}',
    'Ex-Place: {ex_place}',
    'Transport: {transport}',
    'Freight: {freight_by}',
    'Payment Terms: {payment_terms}',
    'Payment Due: {payment_due}',
    'E-way Bill: {eway}',
    '{warehouse_label}: {warehouse}',
    'Note: {note}',
    RULE,
    '⚠️ Payment must be made strictly as per the agreed terms above. Delayed payment will attract interest @ 2% per month on the overdue amount until the date of actual payment.',
    RULE,
    'Kindly verify the above details and reply *CONFIRMED* to confirm this Sauda.',
    '',
    '*{company}*'
  ].join('\n'),

  internal: [
    '📋 *{ref}* · {side} · {date}',
    '{party_label}: {party}',
    '{product} · {qty} @ {rate_plain}',
    '{warehouse_label}: {warehouse}',
    RULE,
    'Value: {value}',
    'Cost: {cost}',
    'Margin: {margin}  ({margin_per_kg}/kg)',
    'Labour: {labour}   Freight: {freight}',
    '*After costs: {net_margin}*',
    RULE,
    'From:',
    '{lots}',
    RULE,
    'Payment: {payment_terms} · due {payment_due}',
    'Note: {note}'
  ].join('\n')
};

// Everything a message can mention. Grouped the way Setup lists them.
export const TOKENS = [
  ['The sauda', ['company', 'ref', 'date', 'side', 'party', 'party_label', 'party_gstin', 'party_phone',
                 'seller', 'buyer', 'product', 'material', 'grade', 'manufacturer',
                 'qty', 'qty_mt', 'qty_kg', 'rate', 'rate_plain', 'gst', 'value',
                 'warehouse', 'warehouse_label']],
  ['Paperwork', ['ex_place', 'transport', 'transporter', 'freight_by', 'delivery_by',
                 'payment_terms', 'payment_due', 'eway', 'note']],
  ['Money (keep these out of the party\'s message)',
   ['cost', 'margin', 'margin_per_kg', 'labour', 'freight', 'labour_per_mt', 'freight_per_kg',
    'costs', 'net_margin', 'lots', 'short', 'stock_after']]
];

const money = paise => (paise === null || paise === undefined ? '—' : f.inr(paise, { sign: false }));

// A sale's lots, one per line: 4 MT from Supplier A @ ₹95.00 (LE/26-27/0001)
function lotLines(deal) {
  const lines = (deal.allocations || []).map(a =>
    `• ${f.qty(a.qty_g)} from ${a.supplier_name} @ ${f.rate(a.cost_paise)} (${a.buy_ref})`);
  if (deal.short_g) lines.push(`• ${f.qty(deal.short_g)} sold short — not covered yet`);
  if (deal.side === 'buy') {
    return (deal.sold || []).map(s => `• ${f.qty(s.qty_g)} to ${s.customer_name} @ ${f.rate(s.sale_rate_paise)} (${s.sale_ref})`)
      .join('\n') || '—';
  }
  return lines.join('\n') || '—';
}

export function values(deal, settings = {}, extra = {}) {
  const sell = deal.side === 'sell';
  const company = String(settings.company_name || 'Labdhi Exim').toUpperCase();
  const product = deal.manufacturer ? `${deal.material} (${deal.manufacturer})` : deal.material;
  const kg = Math.round(deal.qty_g / 1000);
  const perMt = paise => (deal.qty_g ? f.rate(Math.round(paise * 1e6 / deal.qty_g)) : '—');
  const perKg = paise => (deal.qty_g ? f.rate(Math.round(paise * 1000 / deal.qty_g)) : '—');
  return {
    company,
    ref: dash(deal.ref),
    date: dash(dmy(deal.deal_date)),
    side: sell ? 'SELL' : 'BUY',
    party: dash(deal.party_name),
    party_label: sell ? 'Buyer' : 'Supplier',
    party_gstin: dash(deal.party_gstin),
    party_phone: dash(extra.party_phone),
    seller: dash(sell ? company : deal.party_name),
    buyer: dash(sell ? deal.party_name : company),
    product: dash(product),
    material: dash(deal.material),
    grade: dash(deal.grade),
    manufacturer: dash(deal.manufacturer),
    qty: deal.qty_g ? `${f.mt(deal.qty_g)} MT (${kg.toLocaleString('en-IN')} kg)` : '—',
    qty_mt: f.mt(deal.qty_g),
    qty_kg: kg.toLocaleString('en-IN'),
    rate: deal.rate_paise ? `${f.rate(deal.rate_paise)}/kg ${deal.plus_gst ? '+ GST' : 'incl. GST'}` : '—',
    rate_plain: deal.rate_paise ? `${f.rate(deal.rate_paise)}/kg` : '—',
    gst: deal.plus_gst ? 'extra' : 'included',
    value: money(deal.value_paise),
    warehouse: dash(deal.warehouse),
    warehouse_label: sell ? 'Dispatch From' : 'Delivery At',
    ex_place: dash(deal.ex_place),
    transport: dash([byWhom('Arranged', deal.delivery_by), deal.transporter_name].filter(Boolean).join(' · ')),
    transporter: dash(deal.transporter_name),
    freight_by: dash(byWhom('Paid', deal.freight_by)),
    delivery_by: dash(deal.delivery_by),
    payment_terms: dash(deal.payment_terms),
    payment_due: dash(dmy(deal.payment_due)),
    eway: dash(deal.eway),
    note: dash(deal.remarks),
    // the firm's side of it
    cost: sell && deal.cost_paise ? `${f.rate(deal.cost_paise)}/kg` : '—',
    margin: sell ? money(deal.margin_paise) : '—',
    margin_per_kg: sell && deal.qty_g ? perKg(deal.margin_paise) : '—',
    labour: deal.labour_paise ? money(deal.labour_paise) : '—',
    freight: deal.freight_paise ? money(deal.freight_paise) : '—',
    labour_per_mt: deal.labour_paise ? perMt(deal.labour_paise) : '—',
    freight_per_kg: deal.freight_paise ? perKg(deal.freight_paise) : '—',
    costs: deal.costs_paise ? money(deal.costs_paise) : '—',
    net_margin: sell ? money(deal.net_margin_paise) : money(-(deal.costs_paise || 0)),
    lots: lotLines(deal),
    short: deal.short_g ? f.qty(deal.short_g) : '—',
    stock_after: extra.stock_after === undefined ? '—' : f.qty(extra.stock_after)
  };
}

export function render(template, filled) {
  return String(template || '').replace(/\{(\w+)\}/g, (whole, key) =>
    (key in filled ? filled[key] : whole));
}

// The stock line needs one more read, so it is only fetched when a message asks for it.
export async function build(kind, deal, settings = {}, extra = {}) {
  const template = settings[kind === 'internal' ? 'internal_template' : 'sauda_template'] || DEFAULTS[kind];
  const more = { ...extra };
  if (/\{stock_after\}/.test(template) && more.stock_after === undefined) {
    try {
      const pos = await api.position(deal.product_id);
      const here = (pos.warehouses || []).find(w => w.name === deal.warehouse);
      more.stock_after = here ? here.stock_g : pos.stock_g;
    } catch (_) { /* leave it as a dash */ }
  }
  return render(template, values(deal, settings, more));
}
