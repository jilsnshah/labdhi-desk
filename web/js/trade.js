// The trade screen — a full page, not a dialog.
//
// A deal is three records and three numbers. Party, product and warehouse are
// each chosen from their own list; one that is not there yet is added through
// its form without leaving the ticket, and comes straight back selected.
// Nothing about a record is typed into the deal itself.
//
// On a sale the warehouse decides everything below it: only lots sitting there
// are offered, the quantity is capped at what is there, and every lot starts at
// zero - the trader types what comes out of each and watches one number, what
// is still unplaced. Book only unlocks when that number is exactly zero.

import { h, mount, toast, celebrate, costTint, pnlClass, debounce } from './ui.js';
import * as f from './fmt.js';
import { api } from './api.js';
import { partyForm, productForm, warehouseForm, pickParty, partySub } from './forms.js';

const MT = 1e6;
const rnd = n => (n < 0 ? -Math.round(-n) : Math.round(n));
const valuePaise = (qty_g, rate_paise) => rnd(qty_g * rate_paise / 1000);
const LAST_WH = 'labdhi.lastWarehouse';
const lastWh = () => { try { return +localStorage.getItem(LAST_WH) || 0; } catch (_) { return 0; } };
const rememberWh = id => { try { localStorage.setItem(LAST_WH, String(id)); } catch (_) {} };

let state = null, ctx = null, root = null;
const seq = { party: 0, product: 0, wh: 0 };

// Live references into the rendered screen. Typing a number must not rebuild
// the DOM - it only rewrites the nodes whose value actually moved.
let ui = blankRefs();
function blankRefs() {
  return { rows: new Map(), assign: {}, bar: {}, qtyDial: null, qtyBox: null, qtyChips: [], rateDial: null };
}

export function startTrade(side, opts = {}, appCtx = {}) {
  ctx = appCtx;
  state = {
    side,
    party: opts.party || null, partyQ: '', parties: { items: [], total: 0 },
    product: null, position: null, productQ: '', hits: null,
    pick: { material: null, grade: null },
    levels: { materials: [], grades: [], products: [] },
    warehouse: null, whs: { items: [], total: 0 }, whQ: '',
    lots: [], alloc: {}, allocText: {},
    qty_g: 0, rate_paise: 0,
    transporter: null,
    terms: { freight_by: '', delivery_by: '', payment_terms: '', eway: '', remarks: '' },
    busy: false, error: '',
    termsOpen: false,
    sauda_no: '', saudaAuto: true,
    plus_gst: true, payment_due: '', ex_place: '',
    rateInvalid: false, rateText: '',
    date: f.today()
  };
  loadParties();
  loadLevels();
  refreshSauda(true);
  if (opts.product && opts.product.id) chooseProduct(opts.product.id);
}

export function renderTrade(mountNode) {
  root = mountNode;
  if (!state) return;
  render();
}

// ---------------------------------------------------------------- data
async function loadParties(append = false) {
  const my = ++seq.party;
  const offset = append ? state.parties.items.length : 0;
  const r = await api.parties({ q: state.partyQ, limit: 8, offset }).catch(() => null);
  if (!state || !r || my !== seq.party) return;
  state.parties = { items: append ? state.parties.items.concat(r.items) : r.items, total: r.total };
  render();
}
const partyTyped = debounce(() => loadParties(), 200);

async function loadLevels() {
  const sell = state.side === 'sell';
  const { material, grade } = state.pick;
  const [m, g, p] = await Promise.all([
    api.materials({ has_products: true, in_stock: sell, limit: 60 }),
    material ? api.grades({ material_id: material.id, has_products: true, in_stock: sell, limit: 60 }) : null,
    grade ? api.products({ grade_id: grade.id, in_stock: sell, limit: 60 }) : null
  ]).catch(() => [{ items: [] }, null, null]);
  if (!state) return;
  state.levels = { materials: m.items, grades: g ? g.items : [], products: p ? p.items : [] };
  render();
}

const productTyped = debounce(async () => {
  const my = ++seq.product;
  const q = state.productQ.trim();
  if (!q) { state.hits = null; render(); return; }
  const r = await api.products({ q, in_stock: state.side === 'sell', limit: 12 }).catch(() => null);
  if (!state || !r || my !== seq.product) return;
  state.hits = r;
  render();
}, 200);

async function chooseProduct(id) {
  const pos = await api.position(id).catch(() => null);
  if (!state || !pos) return;
  state.position = pos;
  state.product = { ...pos.product, stock_g: pos.stock_g };
  state.productQ = ''; state.hits = null;
  state.warehouse = null; state.lots = [];
  resetAlloc();
  if (!state.rate_paise) {
    const last = pos.lots.length ? pos.lots[pos.lots.length - 1].rate_paise : 0;
    state.rate_paise = state.side === 'buy'
      ? (last || pos.mark_paise || 0)
      : (pos.mark_paise || (pos.cost_paise ? pos.cost_paise + 300 : 0));
  }
  render();
  loadWarehouses(true);
}

function clearProduct() {
  state.product = null; state.position = null; state.warehouse = null; state.lots = [];
  resetAlloc(); render(); loadLevels();
}

// Buy: any warehouse can receive. Sell: only those holding this product, led
// by where most of it sits; if only one holds any, it is the answer.
async function loadWarehouses(auto = false, append = false) {
  if (!state.product) return;
  const my = ++seq.wh;
  const sell = state.side === 'sell';
  const offset = append ? state.whs.items.length : 0;
  const r = await (sell
    ? api.warehouses({ product_id: state.product.id, in_stock: true, limit: 50 })
    : api.warehouses({ q: state.whQ, limit: 12, offset })).catch(() => null);
  if (!state || !r || my !== seq.wh) return;
  state.whs = { items: append ? state.whs.items.concat(r.items) : r.items, total: r.total };
  if (sell && auto && r.items.length === 1) return chooseWarehouse(r.items[0]);
  render();
}
const whTyped = debounce(() => loadWarehouses(), 200);

async function chooseWarehouse(w) {
  state.warehouse = w;
  resetAlloc();
  if (state.side === 'sell') {
    const r = await api.lots(state.product.id, w.id).catch(() => ({ items: [] }));
    if (!state) return;
    state.lots = r.items;
    resetAlloc();
    const max = stockG();
    if (!state.qty_g || state.qty_g > max) state.qty_g = max;
  } else {
    rememberWh(w.id);
  }
  render();
}

// On a sale the most you can sell is what sits in the dispatching warehouse.
const stockG = () => state.lots.reduce((s, l) => s + (l.available_g || 0), 0);

async function refreshSauda(force) {
  const r = await api.saudaNext(state.date).catch(() => null);
  if (!state || !r) return;
  if (force || state.saudaAuto) { state.sauda_no = r.sauda_no; state.saudaAuto = true; render(); }
}

// ---------------------------------------------------------------- the split
function flow() {
  const rows = state.lots.map(lot => {
    const take = Math.max(0, Math.min(lot.available_g, state.alloc[lot.id] || 0));
    const marginRate = state.rate_paise ? state.rate_paise - lot.rate_paise : 0;
    return { lot, take, marginRate, margin: valuePaise(take, marginRate) };
  });
  const assigned = rows.reduce((s, r) => s + r.take, 0);
  const costTotal = rows.reduce((s, r) => s + r.take * r.lot.rate_paise, 0);
  return {
    rows, assigned,
    left: state.qty_g - assigned,
    short: Math.max(0, state.qty_g - assigned),
    over: Math.max(0, assigned - state.qty_g),
    used: rows.filter(r => r.take > 0),
    margin: rows.reduce((s, r) => s + r.margin, 0),
    avgCost: assigned ? rnd(costTotal / assigned) : 0,
    low: Math.min(...state.lots.map(l => l.rate_paise), Infinity),
    high: Math.max(...state.lots.map(l => l.rate_paise), -Infinity)
  };
}

function resetAlloc() {
  state.alloc = {}; state.allocText = {};
  for (const lot of state.lots) state.alloc[lot.id] = 0;
}

function setAmount(lot, grams) {
  state.alloc[lot.id] = Math.max(0, Math.min(lot.available_g, Math.round(grams)));
}

function typeAmount(lot, raw) {
  const kg = parseFloat(String(raw).replace(/[^0-9.]/g, ''));
  const wanted = isNaN(kg) ? 0 : Math.round(kg * 1000);
  setAmount(lot, wanted);
  const actual = state.alloc[lot.id];
  state.allocText[lot.id] = (wanted > actual && actual > 0) ? String(Math.round(actual / 1000)) : raw;
  sync();
}

function fillRest(lot) {
  delete state.allocText[lot.id];
  setAmount(lot, (state.alloc[lot.id] || 0) + Math.max(0, flow().left));
  sync();
}

// ---------------------------------------------------------------- steps
const sellSide = () => state.side === 'sell';
const ready = () => state.party && state.product && state.warehouse && state.qty_g > 0 && state.rate_paise > 0;

function canBook() {
  if (!ready() || state.busy || state.rateInvalid) return false;
  if (!sellSide()) return true;
  const fl = flow();
  return fl.short === 0 && fl.over === 0;
}

// ---------------------------------------------------------------- render
function render() {
  if (!root || !state) return;
  ui = blankRefs();
  const focusKey = document.activeElement && document.activeElement.dataset
    ? document.activeElement.dataset.fkey : null;
  const caret = focusKey && document.activeElement.selectionStart;
  const scrollY = root.scrollTop;
  const sell = sellSide();

  mount(root,
    h('div', { class: 'trade-screen ' + state.side },
      h('div', { class: 'trade-head' },
        h('div', { class: 'trade-kind' }, sell ? 'SELL' : 'BUY'),
        h('div', { class: 'trade-sub' }, sell ? 'stock out of one warehouse · margin booked'
                                              : 'stock into one warehouse'),
        h('button', { class: 'trade-back', onclick: () => ctx.go('desk') }, 'Cancel')),
      blockSauda(),
      blockParty(),
      blockProduct(),
      blockWarehouse(),
      blockQty(),
      blockRate(),
      sell ? blockSplit() : null,
      h('div', { class: 'block' }, blockTerms())),
    resultBar());

  root.scrollTop = scrollY;
  if (focusKey) {
    const el = root.querySelector(`[data-fkey="${focusKey}"]`);
    if (el) { el.focus(); if (caret !== null && el.setSelectionRange) el.setSelectionRange(caret, caret); }
  }
}

function label(text, done, hint) {
  return h('div', { class: 'block-label' },
    done ? h('span', { class: 'tick-ok' }, '✓') : null, text,
    hint ? h('span', { class: 'hint' }, hint) : null);
}

const picked = (name, sub, onChange) => h('div', { class: 'picked' },
  h('div', { class: 'picked-name' }, h('b', {}, name), sub ? h('small', {}, sub) : null),
  h('button', { onclick: onChange }, 'Change'));

function blockSauda() {
  return h('div', { class: 'sauda-row' },
    h('span', { class: 'sauda-label' }, 'Sauda No.'),
    h('input', {
      class: 'ghost-input num sauda-input', data: { fkey: 'sauda' }, value: state.sauda_no,
      placeholder: 'LE/26-27/0001',
      oninput: e => { state.sauda_no = e.target.value; state.saudaAuto = false; }
    }),
    h('button', { class: 'chip', title: 'Issue the next number', onclick: () => refreshSauda(true) }, '↻'),
    h('span', { class: 'dim', style: { fontSize: '13px' } },
      state.saudaAuto ? 'next in this financial year' : 'your number'));
}

// ---- party: one record, chosen from the list or added through its form
function blockParty() {
  const sell = sellSide();
  if (state.party) {
    return h('div', { class: 'block' }, label(sell ? 'Selling to' : 'Buying from', true),
      picked(state.party.name, partySub(state.party), () => { state.party = null; render(); }));
  }
  const q = state.partyQ.trim();
  const { items, total } = state.parties;
  return h('div', { class: 'block' },
    label(sell ? 'Selling to' : 'Buying from', false,
      q ? `${total} match${total === 1 ? '' : 'es'}` : `${total} part${total === 1 ? 'y' : 'ies'} on file`),
    h('div', { class: 'find' }, h('span', { class: 'dim' }, '⌕'),
      h('input', {
        data: { fkey: 'party' }, placeholder: 'Search by name, GSTIN or phone…', value: state.partyQ,
        oninput: e => { state.partyQ = e.target.value; partyTyped(); },
        onkeydown: e => { if (e.key === 'Enter' && items[0]) { e.preventDefault(); pickPartyRow(items[0]); } }
      })),
    h('div', { class: 'pick-rows' },
      ...items.map(p => h('button', { class: 'pick-row', onclick: () => pickPartyRow(p) },
        h('div', { class: 'pick-main' }, h('b', {}, p.name), h('span', {}, partySub(p))),
        p.last_deal ? h('span', { class: 'pick-tag' }, f.ago(p.last_deal)) : null))),
    h('div', { class: 'block-actions' },
      items.length < total ? h('button', { class: 'chip small', onclick: () => loadParties(true) },
        `Show more (${total - items.length})`) : null,
      q && !items.length ? h('span', { class: 'dim' }, `No party matches "${q}".`) : null,
      h('button', { class: 'chip ghost', onclick: addParty }, '+ Add new party')));
}

function pickPartyRow(p) { state.party = p; state.partyQ = ''; render(); }

async function addParty() {
  const p = await partyForm(null, { name: state.partyQ.trim() });
  if (p && state) { pickPartyRow(p); loadParties(); }
}

// ---- product: material -> grade -> manufacturer, all from the product list
function blockProduct() {
  const sell = sellSide();
  if (state.product) {
    const p = state.product;
    const where = (state.position ? state.position.warehouses : [])
      .map(w => `${w.name} ${f.qty(w.stock_g, { short: true })}`).join(' · ');
    return h('div', { class: 'block' }, label('Product', true,
      p.stock_g ? `you hold ${f.qty(p.stock_g)}` : 'none in stock yet'),
      picked(p.display, where || (p.packing || ''), clearProduct));
  }
  const q = state.productQ.trim();
  const { material, grade } = state.pick;
  const L = state.levels;
  const chip = (text, on, click, small) => h('button', { class: 'chip' + (on ? ' on' : ''), onclick: click },
    text, small ? h('small', {}, small) : null);
  const stockTag = x => (x.stock_g ? f.qty(x.stock_g, { short: true }) : null);

  return h('div', { class: 'block' + (state.party ? '' : ' pending') },
    label('Product', false, sell ? 'only what you hold' : 'material → grade → manufacturer'),
    h('div', { class: 'find' }, h('span', { class: 'dim' }, '⌕'),
      h('input', {
        data: { fkey: 'product' }, placeholder: 'Search product — e.g. PVC S65 Reliance', value: state.productQ,
        oninput: e => { state.productQ = e.target.value; productTyped(); }
      })),
    q
      ? h('div', { class: 'pick-rows' },
          ...(state.hits ? state.hits.items : []).map(p => h('button', { class: 'pick-row', onclick: () => chooseProduct(p.id) },
            h('div', { class: 'pick-main' }, h('b', {}, p.display), h('span', {}, p.packing || '')),
            h('span', { class: 'pick-tag' }, p.stock_g ? f.qty(p.stock_g) : 'no stock'))),
          state.hits && !state.hits.items.length ? h('div', { class: 'dim' }, `No product matches "${q}".`) : null)
      : h('div', { class: 'tree' },
          h('div', { class: 'tree-row' }, h('span', {}, 'Material'),
            h('div', { class: 'chips' }, ...L.materials.map(m => chip(m.name, material && material.id === m.id,
              () => { state.pick = { material: material && material.id === m.id ? null : m, grade: null }; loadLevels(); render(); },
              stockTag(m))),
              !L.materials.length ? h('span', { class: 'dim' }, sell ? 'Nothing in stock.' : 'No products yet.') : null)),
          material ? h('div', { class: 'tree-row' }, h('span', {}, 'Grade'),
            h('div', { class: 'chips' }, ...L.grades.map(g => chip(g.name, grade && grade.id === g.id,
              () => { state.pick.grade = grade && grade.id === g.id ? null : g; loadLevels(); render(); },
              stockTag(g))))) : null,
          grade ? h('div', { class: 'tree-row' }, h('span', {}, 'Manufacturer'),
            h('div', { class: 'chips' }, ...L.products.map(p => chip(p.manufacturer, false,
              () => chooseProduct(p.id), stockTag(p))))) : null),
    h('div', { class: 'block-actions' },
      !sell ? h('button', { class: 'chip ghost', onclick: addProduct }, '+ Add new product') : null,
      h('button', { class: 'setup-link', onclick: () => { ctx.setupTab = 'products'; ctx.go('setup'); } },
        'Manage products')));
}

async function addProduct() {
  const { material, grade } = state.pick;
  const p = await productForm(null, { material: material ? material.name : '', grade: grade ? grade.name : '' });
  if (p && state) chooseProduct(p.id);
}

// ---- warehouse: receive into (buy) / dispatch from (sell)
function blockWarehouse() {
  const sell = sellSide();
  const title = sell ? 'Dispatch from' : 'Receive into';
  if (!state.product) return h('div', { class: 'block pending' }, label(title, false));
  if (state.warehouse) {
    const w = state.warehouse;
    return h('div', { class: 'block' },
      label(title, true, sell ? `${f.qty(stockG())} of it sits here` : null),
      picked(w.name, w.address || '', () => { state.warehouse = null; state.lots = []; resetAlloc(); render(); loadWarehouses(); }));
  }
  const { items, total } = state.whs;
  const last = lastWh();
  return h('div', { class: 'block' },
    label(title, false, sell ? 'the lots below come only from here' : 'where this stock will sit'),
    !sell && total > 12 ? h('div', { class: 'find' }, h('span', { class: 'dim' }, '⌕'),
      h('input', { data: { fkey: 'wh' }, placeholder: 'Search warehouse…', value: state.whQ,
        oninput: e => { state.whQ = e.target.value; whTyped(); } })) : null,
    h('div', { class: 'chips' },
      ...items.map(w => h('button', { class: 'chip', onclick: () => chooseWarehouse(w) },
        w.name,
        sell ? h('small', {}, f.qty(w.product_stock_g, { short: true }))
             : (w.id === last ? h('small', {}, 'last used') : null))),
      sell && !items.length ? h('span', { class: 'dim' }, 'No warehouse holds this product.') : null),
    h('div', { class: 'block-actions' },
      !sell && items.length < total ? h('button', { class: 'chip small', onclick: () => loadWarehouses(false, true) },
        `Show more (${total - items.length})`) : null,
      !sell ? h('button', { class: 'chip ghost', onclick: addWarehouse }, '+ Add new warehouse') : null));
}

async function addWarehouse() {
  const w = await warehouseForm();
  if (w && state) { chooseWarehouse(w); loadWarehouses(); }
}

// ---- numbers
function blockQty() {
  const done = state.qty_g > 0;
  const sell = sellSide();
  const max = sell ? stockG() : 0;
  const quick = sell && max
    ? [['25%', Math.round(max * .25)], ['50%', Math.round(max * .5)],
       ['75%', Math.round(max * .75)], ['All ' + f.qty(max, { short: true }), max]]
    : [['5 MT', 5 * MT], ['10 MT', 10 * MT], ['20 MT', 20 * MT], ['25 MT', 25 * MT]];

  return h('div', { class: 'block' + (state.warehouse ? '' : ' pending') },
    label('Quantity', done, sell && max ? `most you can sell from ${state.warehouse.name} is ${f.qty(max)}` : null),
    h('div', { class: 'dial' },
      h('button', { class: 'step', onclick: () => setQty(state.qty_g - MT) }, '−'),
      ui.qtyDial = h('div', { class: 'dial-value num' },
        (state.qty_g / MT).toFixed(state.qty_g % MT === 0 ? 0 : 3), h('small', {}, 'MT')),
      h('button', { class: 'step', onclick: () => setQty(state.qty_g + MT) }, '+'),
      (ui.qtyBox = h('input', {
        class: 'ghost-input num', data: { fkey: 'qty' }, placeholder: 'kg',
        value: state.qty_g ? Math.round(state.qty_g / 1000) : '',
        oninput: e => setQty(Math.round(parseFloat(e.target.value || 0) * 1000))
      })),
      h('span', { class: 'dim' }, 'kg')),
    h('div', { class: 'chips', style: { marginTop: '16px' } },
      ...quick.map(([text, g]) => {
        const el = h('button', { class: 'chip' + (state.qty_g === g ? ' on' : ''), onclick: () => setQty(g) }, text);
        ui.qtyChips.push({ el, g });
        return el;
      })));
}

// No short selling: a sale can never exceed what the warehouse holds.
function setQty(g) {
  const max = sellSide() ? stockG() : Infinity;
  const before = state.qty_g;
  state.qty_g = Math.max(0, Math.min(max, g || 0));
  if ((before > 0) !== (state.qty_g > 0)) render(); else sync();
}

function blockRate() {
  const done = state.rate_paise > 0;
  const pos = state.position;
  const hint = sellSide()
    ? (pos && pos.cost_paise ? `your average cost is ${f.rate(pos.cost_paise)}/MT` : null)
    : (pos && pos.mark_paise ? `last sold at ${f.rate(pos.mark_paise)}/MT` : null);
  return h('div', { class: 'block' + (state.qty_g ? '' : ' pending') },
    label('Rate  (₹ per MT)', done, hint),
    h('div', { class: 'dial' },
      h('button', { class: 'step', onclick: () => bumpRate(-10) }, '−'),
      ui.rateDial = h('div', { class: 'dial-value num' }, f.rate(state.rate_paise)),
      h('button', { class: 'step', onclick: () => bumpRate(10) }, '+'),
      h('input', {
        class: 'ghost-input num', data: { fkey: 'rate' }, placeholder: '98250',
        value: state.rateInvalid ? state.rateText : (state.rate_paise ? String(f.perMt(state.rate_paise)) : ''),
        oninput: e => typeRate(e.target.value)
      }),
      h('span', { class: 'dim' }, '/MT')),
    ui.rateErr = h('div', { class: 'rate-err' }, state.rateInvalid ? rateErrorText(state.rateText) : ''),
    h('div', { class: 'chips', style: { marginTop: '14px' } },
      ...[-50, -25, -10, 10, 25, 50].map(d =>
        h('button', { class: 'chip', onclick: () => bumpRate(d) },
          (d > 0 ? '+' : '−') + '₹' + Math.abs(d * f.PER_MT).toLocaleString('en-IN')))),
    h('div', { class: 'rate-extras' },
      h('label', { class: 'gst-check' },
        h('input', { type: 'checkbox', checked: state.plus_gst || undefined,
          onchange: e => { state.plus_gst = e.target.checked; } }),
        h('span', {}, 'GST extra'), h('small', {}, 'rate excludes GST')),
      h('label', { class: 'explace' },
        h('span', {}, 'Ex-Place'),
        h('input', { class: 'ghost-input', data: { fkey: 'explace' }, value: state.ex_place,
          placeholder: 'Mundra / Aslali / Other', oninput: e => { state.ex_place = e.target.value; } }))));
}

// A per-MT figure is exact only in Rs 10 steps. Anything finer is refused and
// the book button locks - and the refusal lives in state, so a re-render can
// never wipe it and leave a valid-looking figure beside a dead button.
function rateErrorText(text) {
  const lo = Math.floor(Number(text) / f.PER_MT) * f.PER_MT;
  return `Rates go in steps of ₹10 per MT — ₹${lo.toLocaleString('en-IN')} or ₹${(lo + f.PER_MT).toLocaleString('en-IN')}?`;
}

function typeRate(raw) {
  const text = String(raw).replace(/[^0-9.]/g, '');
  if (!text) {
    state.rateInvalid = false; state.rateText = '';
    if (ui.rateErr) ui.rateErr.textContent = '';
    return setRate(0, true);
  }
  const paise = f.fromPerMt(Number(text));
  if (paise === null) {
    state.rateInvalid = true; state.rateText = text;
    if (ui.rateErr) ui.rateErr.textContent = rateErrorText(text);
    return sync();
  }
  state.rateInvalid = false; state.rateText = '';
  if (ui.rateErr) ui.rateErr.textContent = '';
  setRate(paise, true);
}

function bumpRate(d) { state.rateInvalid = false; state.rateText = ''; setRate(state.rate_paise + d); }

function setRate(paise, typing = false) {
  const before = state.rate_paise;
  state.rate_paise = Math.max(0, paise || 0);
  if ((before > 0) !== (state.rate_paise > 0)) render();
  else { sync(); if (typing && ui.rateDial) ui.rateDial.textContent = f.rate(state.rate_paise); }
}

// ---------------------------------------------------------------- the split
function blockSplit() {
  if (!state.warehouse || !state.qty_g) return h('div', { class: 'block pending' }, label('Which stock goes out', false));
  const fl = flow();
  const rows = fl.rows.slice().sort((a, b) => a.lot.rate_paise - b.lot.rate_paise);
  return h('div', { class: 'block' },
    label(`Which stock goes out of ${state.warehouse.name}`, fl.left === 0, 'type how much you are taking from each'),
    h('div', { class: 'split-wrap' },
      h('div', { class: 'lotlist' }, ...rows.map(row => lotRow(row, fl))),
      assignPanel(fl)));
}

function assignPanel(fl) {
  const r = ui.assign;
  r.label = h('div', { class: 'assign-label' }, '');
  r.num = h('div', { class: 'assign-num num' }, '');
  r.of = h('div', { class: 'assign-of' }, '');
  r.barI = h('i', {});
  r.done = h('div', { class: 'assign-done num' }, '');
  r.rate = h('b', { class: 'num' }, '');
  r.cost = h('b', { class: 'num' }, '');
  r.margin = h('b', { class: 'num' }, '');
  r.el = h('div', { class: 'assign' },
    r.label, r.num, r.of, h('div', { class: 'assign-bar' }, r.barI), r.done,
    h('div', { class: 'assign-facts' },
      h('div', {}, h('span', {}, 'Selling at'), r.rate),
      h('div', {}, h('span', {}, 'Your cost'), r.cost),
      h('div', { class: 'assign-margin' }, h('span', {}, 'Margin'), r.margin)),
    h('button', { class: 'assign-clear', onclick: () => { resetAlloc(); sync(); } }, 'Clear all'));
  paintAssign(fl);
  return r.el;
}

function paintAssign(fl) {
  const r = ui.assign;
  if (!r.el) return;
  const done = fl.left === 0, over = fl.left < 0;
  r.el.className = 'assign' + (done ? ' done' : over ? ' over' : '');
  r.label.textContent = done ? 'Ready' : over ? 'Too much by' : 'Left to assign';
  r.num.textContent = done ? f.qty(state.qty_g) : f.qty(Math.abs(fl.left));
  r.of.textContent = done ? `placed across ${fl.used.length} lot${fl.used.length === 1 ? '' : 's'}` : `of ${f.qty(state.qty_g)}`;
  r.barI.style.width = (state.qty_g ? Math.min(100, (fl.assigned / state.qty_g) * 100) : 0) + '%';
  r.done.textContent = `${f.qty(fl.assigned)} assigned`;
  r.rate.textContent = state.rate_paise ? f.rate(state.rate_paise) : '—';
  r.cost.textContent = fl.assigned ? f.rate(fl.avgCost) : '—';
  r.margin.textContent = fl.assigned && state.rate_paise ? f.inr(fl.margin, { sign: true }) : '—';
  r.margin.className = 'num ' + (fl.assigned ? pnlClass(fl.margin) : 'dim');
}

function lotRow(row, fl) {
  const { lot } = row;
  const tint = costTint(lot.rate_paise, fl.low, fl.high);
  const ref = {};
  ref.barI = h('i', { style: { background: tint } });
  ref.marginB = h('b', {});
  ref.marginS = h('span', {});
  ref.margin = h('div', { class: 'qmargin' }, ref.marginB, ref.marginS);
  ref.input = h('input', {
    class: 'num', type: 'text', inputmode: 'numeric', data: { fkey: 'lot' + lot.id },
    placeholder: '0', value: '',
    onfocus: e => e.target.select(),
    oninput: e => typeAmount(lot, e.target.value),
    onblur: () => { delete state.allocText[lot.id]; sync(); }
  });
  ref.rest = h('button', { class: 'qrest', onclick: () => fillRest(lot) }, 'fill rest');
  ref.el = h('div', { class: 'qrow', style: { '--tint': tint } },
    h('div', { class: 'qrate num', style: { color: tint } }, f.rate(lot.rate_paise)),
    h('div', { class: 'qwho' },
      h('b', {}, lot.supplier_name),
      h('span', {}, `${lot.deal_ref} · ${f.date(lot.deal_date)} · ${f.qty(lot.available_g)} here`),
      h('div', { class: 'qbarline' }, ref.barI)),
    ref.margin,
    h('div', { class: 'qtake' }, h('div', { class: 'qbox' }, ref.input, h('span', { class: 'qunit' }, 'kg')), ref.rest));
  ui.rows.set(lot.id, ref);
  paintRow(row, fl);
  return ref.el;
}

function paintRow(row, fl) {
  const ref = ui.rows.get(row.lot.id);
  if (!ref) return;
  const { lot, take } = row;
  ref.el.classList.toggle('used', take > 0);
  ref.barI.style.width = (lot.available_g ? Math.min(100, (take / lot.available_g) * 100) : 0) + '%';
  ref.margin.className = 'qmargin ' + (take ? pnlClass(row.marginRate) : 'dim');
  ref.marginB.textContent = take ? f.inr(row.margin, { sign: true }) : '';
  ref.marginS.textContent = state.rate_paise ? f.rateDelta(row.marginRate) + '/MT' : '';
  ref.rest.disabled = fl.left <= 0 || take >= lot.available_g;
  if (document.activeElement !== ref.input) {
    const shown = state.allocText[lot.id] !== undefined ? state.allocText[lot.id]
      : (take ? String(Math.round(take / 1000)) : '');
    if (ref.input.value !== shown) ref.input.value = shown;
  }
}

// Everything that can change without the page changing shape.
function sync() {
  if (!ui.bar.el) return render();
  const fl = sellSide() && state.warehouse ? flow() : null;
  if (ui.qtyDial) ui.qtyDial.firstChild.textContent = (state.qty_g / MT).toFixed(state.qty_g % MT === 0 ? 0 : 3);
  for (const chip of ui.qtyChips) chip.el.classList.toggle('on', state.qty_g === chip.g);
  if (ui.qtyBox && document.activeElement !== ui.qtyBox) {
    const want = state.qty_g ? String(Math.round(state.qty_g / 1000)) : '';
    if (ui.qtyBox.value !== want) ui.qtyBox.value = want;
  }
  if (ui.rateDial) ui.rateDial.textContent = f.rate(state.rate_paise);
  if (fl) { for (const row of fl.rows) paintRow(row, fl); paintAssign(fl); }
  paintBar(fl);
}

// ---------------------------------------------------------------- terms
function blockTerms() {
  const t = state.terms;
  const seg = (key, options) => h('div', { class: 'seg' },
    ...options.map(o => h('button', {
      class: t[key] === o ? 'on' : '', type: 'button',
      onclick: () => { t[key] = t[key] === o ? '' : o; render(); }
    }, o)));

  return h('details', { class: 'more', open: state.termsOpen, ontoggle: e => { state.termsOpen = e.target.open; } },
    h('summary', {}, 'Transport, payment, e-way  (optional)'),
    h('div', { class: 'terms' },
      h('div', { class: 'field' }, h('label', {}, 'Transporter'),
        h('div', { class: 'pick-btn' + (state.transporter ? '' : ' empty'), onclick: chooseTransporter },
          h('span', {}, state.transporter ? state.transporter.name : 'Choose from parties…'),
          state.transporter ? h('button', { class: 'pick-clear', onclick: e => { e.stopPropagation(); state.transporter = null; render(); } }, '×') : null)),
      h('div', { class: 'field' }, h('label', {}, 'Freight paid by'), seg('freight_by', ['Buyer', 'Seller'])),
      h('div', { class: 'field' }, h('label', {}, 'Delivery by'), seg('delivery_by', ['Buyer', 'Seller'])),
      h('div', { class: 'field' }, h('label', {}, 'Payment terms'),
        h('input', { data: { fkey: 'pay' }, value: t.payment_terms, placeholder: '30 days',
          oninput: e => { t.payment_terms = e.target.value; } })),
      h('div', { class: 'field' }, h('label', {}, 'Payment due'),
        h('input', { type: 'date', data: { fkey: 'due' }, value: state.payment_due || '',
          oninput: e => { state.payment_due = e.target.value; } }),
        h('div', { class: 'due-chips' },
          ...[['Today', 0], ['+7d', 7], ['+15d', 15], ['+30d', 30], ['+45d', 45]].map(([l, n]) =>
            h('button', { type: 'button', class: 'chip', onclick: () => { state.payment_due = f.addDays(state.date, n); render(); } }, l)))),
      h('div', { class: 'field' }, h('label', {}, 'E-way bill'),
        h('input', { data: { fkey: 'eway' }, value: t.eway, placeholder: 'ASL to buyer',
          oninput: e => { t.eway = e.target.value; } })),
      h('div', { class: 'field' }, h('label', {}, 'Deal date'),
        h('input', { type: 'date', data: { fkey: 'date' }, value: state.date,
          oninput: e => { state.date = e.target.value; refreshSauda(false); } })),
      h('div', { class: 'field', style: { gridColumn: '1/-1' } }, h('label', {}, 'Note'),
        h('input', { data: { fkey: 'remarks' }, value: t.remarks, placeholder: 'anything worth remembering',
          oninput: e => { t.remarks = e.target.value; } }))));
}

async function chooseTransporter() {
  const p = await pickParty('Choose transporter');
  if (p && state) { state.transporter = p; render(); }
}

// ---------------------------------------------------------------- result bar
function resultBar() {
  const r = ui.bar;
  r.money = h('b', { class: 'num' }, '');
  r.moneyLabel = h('span', {}, '');
  r.line = h('div', { class: 'rb-line' }, '');
  r.btn = h('button', { class: 'confirm ' + state.side, onclick: confirm }, '');
  r.el = h('div', { class: 'resultbar' },
    h('div', { class: 'resultbar-in' }, h('div', { class: 'rb-money' }, r.money, r.moneyLabel), r.line, r.btn));
  paintBar(sellSide() && state.warehouse ? flow() : null);
  return r.el;
}

function paintBar(fl) {
  const r = ui.bar;
  if (!r.el) return;
  const sell = sellSide();
  const value = valuePaise(state.qty_g, state.rate_paise);
  if (sell && fl && fl.assigned) {
    r.money.textContent = ready() ? f.inr(fl.margin, { sign: true }) : '—';
    r.money.className = 'num ' + pnlClass(fl.margin);
    r.moneyLabel.textContent = 'margin';
  } else {
    r.money.textContent = ready() ? f.inr(value) : '—';
    r.money.className = 'num';
    r.moneyLabel.textContent = sell ? 'sale value' : 'purchase value';
  }
  const line = lineText(fl, value);
  r.line.textContent = line.text;
  r.line.className = 'rb-line' + (line.err ? ' err' : '');
  r.btn.disabled = !canBook();
  r.btn.textContent = state.busy ? 'Booking…' : (sell ? 'Book sale' : 'Book purchase');
}

function lineText(fl, value) {
  if (state.error) return { text: state.error, err: true };
  if (state.rateInvalid) return { text: 'Rate must be in steps of ₹10 per MT', err: true };
  if (!ready()) {
    const need = !state.party ? (sellSide() ? 'a buyer' : 'a supplier')
      : !state.product ? 'a product'
      : !state.warehouse ? (sellSide() ? 'the warehouse it ships from' : 'the warehouse it goes into')
      : !state.qty_g ? 'a quantity' : 'a rate';
    return { text: `Choose ${need} to continue` };
  }
  if (fl && fl.short > 0) return { text: `${f.qty(fl.short)} still to assign`, err: true };
  if (fl && fl.over > 0) return { text: `${f.qty(fl.over)} more than you are selling — lower an amount`, err: true };
  const bits = [`${f.qty(state.qty_g)} ${state.product.display} @ ${f.rate(state.rate_paise)}`,
    `${sellSide() ? 'from' : 'into'} ${state.warehouse.name}`, `= ${f.inr(value)}`];
  if (fl) bits.push(`cost ${f.rate(fl.avgCost)} · ${fl.used.length} lot${fl.used.length === 1 ? '' : 's'}`);
  return { text: bits.join('   ·   ') };
}

// ---------------------------------------------------------------- commit
async function confirm() {
  if (!canBook()) return;
  const sell = sellSide();
  const fl = sell ? flow() : null;
  state.busy = true; state.error = ''; render();
  try {
    const deal = await api.createDeal({
      side: state.side,
      party_id: state.party.id,
      product_id: state.product.id,
      warehouse_id: state.warehouse.id,
      qty_g: state.qty_g,
      rate_paise: state.rate_paise,
      deal_date: state.date,
      sauda_no: (state.sauda_no || '').trim() || undefined,
      plus_gst: !!state.plus_gst,
      payment_due: state.payment_due || undefined,
      ex_place: (state.ex_place || '').trim() || undefined,
      pins: fl ? fl.rows.map(r => ({ lot_id: r.lot.id, qty_g: r.take })) : undefined,
      transporter_id: state.transporter ? state.transporter.id : undefined,
      freight_by: state.terms.freight_by || undefined,
      delivery_by: state.terms.delivery_by || undefined,
      payment_terms: state.terms.payment_terms || undefined,
      eway: state.terms.eway || undefined,
      remarks: state.terms.remarks || undefined,
      confirm: true
    });
    celebrate(sell ? f.inr(deal.margin_paise || 0, { sign: true }) : f.qty(deal.qty_g),
      `${deal.ref} · ${deal.party_name}`, sell ? 'sell' : 'buy');
    toast(sell
      ? `Sold ${f.qty(deal.qty_g)} ${deal.product} to ${deal.party_name} from ${deal.warehouse}`
      : `Bought ${f.qty(deal.qty_g)} ${deal.product} from ${deal.party_name} into ${deal.warehouse}`,
      { action: async () => { await api.undo(); toast('Reversed'); ctx.refresh(); } });
    state = null;
    ctx.go('desk');
    ctx.refresh();
  } catch (err) {
    state.busy = false; state.error = err.message; render();
  }
}
