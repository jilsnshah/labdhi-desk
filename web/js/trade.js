// The trade screen — a full page, not a dialog.
//
// The split used to ask the trader to type a quantity against every lot and
// then watch a "left to assign" counter until it hit zero. That is bookkeeping,
// not trading: arithmetic he has to do, and a number he has to keep checking.
//
// This version asks a different question. He taps the lots he wants to sell,
// in the order he wants them used, and the quantity waterfalls down that
// order — each lot gives what it has, the last one gives the remainder. The
// total is exact by construction, so there is no counter to watch and no way
// to end up short or long. He types nothing unless he wants to cap a lot.

import { h, mount, toast, celebrate, costTint, pnlClass } from './ui.js';
import * as f from './fmt.js';
import { api } from './api.js';

const MT = 1e6;

function today() {
  const d = new Date();
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

const rnd = n => (n < 0 ? -Math.round(-n) : Math.round(n));
const valuePaise = (qty_g, rate_paise) => rnd(qty_g * rate_paise / 1000);

let state = null, ctx = null, root = null;

// Live references into the rendered screen. Typing a number must not rebuild
// the DOM - it only rewrites the handful of nodes whose value actually moved.
// A full render is reserved for changes that alter the structure of the page.
let ui = blankRefs();
function blankRefs() {
  return { rows: new Map(), assign: {}, bar: {}, qtyDial: null, qtyBox: null, qtyChips: [], rateDial: null };
}

export function startTrade(side, opts = {}, appCtx = {}) {
  ctx = appCtx;
  state = {
    side,
    party: opts.party || null,
    partyQuery: '',
    partyOptions: appCtx.boot ? (side === 'buy' ? appCtx.boot.suppliers : appCtx.boot.customers) : [],
    sku: null,
    // material -> grade -> manufacturer. The three together are the stock line.
    // "manufacturer" is who made the resin; it is never the party you trade with.
    line: { material: '', grade: '', manufacturer: '' },
    opts: { material: [], grade: [], manufacturer: [] },
    query: { material: '', grade: '', manufacturer: '' },
    position: null,
    lots: [],
    alloc: {},        // lot_id -> grams. Starts at zero for every lot.
    allocText: {},    // lot_id -> mid-typing text, so a re-render cannot eat it
    qty_g: opts.qty_g || 0,
    rate_paise: 0,
    terms: { freight_by: '', delivery_by: '', payment_terms: '', transporter: '', eway: '', remarks: '' },
    busy: false,
    error: '',
    termsOpen: false,   // must live in state: a re-render rebuilds the <details>
    sauda_no: '',       // shown before booking; the trader may overwrite it
    saudaAuto: true,    // still the issued number, so a date change may renumber it
    warehouse: '',      // buy: where it lands. sell: narrows lots to one godown
    warehouses: [],
    whQuery: '',
    plus_gst: true,     // the trade convention: "98.25+" means GST extra
    payment_due: '',
    ex_place: '',       // pricing basis, recorded as typed
    rateInvalid: false, // the box holds a figure that cannot be stored exactly
    rateText: '',       // ...and this is that figure, kept so a re-render cannot erase it
    date: today()
  };
  loadParties('');
  loadOptions('material');
  loadWarehouses();
  refreshSauda(true);
  if (opts.sku && (opts.sku.id || opts.sku.sku_id)) preloadSku(opts.sku.id || opts.sku.sku_id);
}

export function renderTrade(mountNode) {
  root = mountNode;
  if (!state) return;
  render();
}

// ---------------------------------------------------------------- data
async function loadParties(q) {
  const role = state.side === 'buy' ? 'supplier' : 'customer';
  const r = await api.parties(q, role);
  if (!state) return;
  state.partyOptions = r.results;
  render();
}

const LEVELS = ['material', 'grade', 'manufacturer'];

async function loadOptions(level) {
  const sell = state.side === 'sell';
  const r = await api.catalog(
    level,
    level === 'material' ? undefined : state.line.material,
    level === 'manufacturer' ? state.line.grade : undefined,
    sell ? true : undefined
  ).catch(() => ({ options: [], suggestions: [] }));
  if (!state) return;
  state.opts[level] = r.options;
  state.suggestions = state.suggestions || {};
  state.suggestions[level] = r.suggestions || [];
  render();
}

function pickLevel(level, value) {
  value = String(value || '').trim();
  if (!value) return;
  state.line[level] = value;
  state.query[level] = '';
  // Choosing higher up invalidates everything below it.
  const below = LEVELS.slice(LEVELS.indexOf(level) + 1);
  for (const lower of below) { state.line[lower] = ''; state.opts[lower] = []; }
  state.sku = null; state.position = null; state.lots = [];
  state.alloc = {}; state.allocText = {};
  render();
  if (below.length) loadOptions(below[0]);
  else resolveLine();
}

function clearLevel(level) {
  for (const lower of LEVELS.slice(LEVELS.indexOf(level))) {
    state.line[lower] = ''; state.opts[lower] = [];
  }
  state.sku = null; state.position = null; state.lots = [];
  state.alloc = {}; state.allocText = {};
  render();
  loadOptions(level);
}

// All three chosen: find the stock line, and with it the lots and the marks.
async function resolveLine() {
  const { material, grade, manufacturer } = state.line;
  if (!material || !grade || !manufacturer) return;
  const r = await api.resolveSku(material, grade, manufacturer).catch(() => ({ sku: null }));
  if (!state) return;
  if (r.sku) {
    state.sku = r.sku;
    loadMaterial(r.sku.id);
  } else {
    // A line we have never traded: fine on a purchase, impossible on a sale.
    state.sku = { id: null, display: `${material} ${grade} \u00b7 ${manufacturer}` };
    state.position = null; state.lots = []; state.alloc = {};
    render();
  }
}

async function preloadSku(skuId) {
  const pos = await api.position(skuId).catch(() => null);
  if (!state || !pos) return;
  state.line = {
    material: pos.sku.material, grade: pos.sku.grade, manufacturer: pos.sku.manufacturer
  };
  state.sku = pos.sku;
  for (const level of LEVELS) loadOptions(level);
  loadMaterial(skuId);
}

async function loadMaterial(skuId) {
  const [pos, lots] = await Promise.all([
    api.position(skuId).catch(() => null),
    api.lots(skuId, state.side === 'sell' ? (state.warehouse || undefined) : undefined)
      .catch(() => ({ lots: [] }))
  ]);
  if (!state) return;
  state.position = pos;
  state.lots = lots.lots || [];
  if (!state.rate_paise && pos) {
    state.rate_paise = state.side === 'buy'
      ? (state.lots.length ? state.lots[state.lots.length - 1].rate_paise : (pos.mark_paise || 0))
      : (pos.mark_paise || (pos.cost_paise ? pos.cost_paise + 300 : 0));
  }
  if (state.side === 'sell' && !state.qty_g && pos) state.qty_g = pos.stock_g;
  resetAlloc();
  render();
}

// On a sale narrowed to one warehouse, the most you can sell is what sits there.
const stockG = () => {
  if (state.side === 'sell' && state.warehouse) {
    return state.lots.reduce((s, l) => s + (l.available_g || 0), 0);
  }
  return state.position ? state.position.stock_g : 0;
};

async function loadWarehouses() {
  const r = await api.warehouses().catch(() => ({ warehouses: [] }));
  if (!state) return;
  state.warehouses = r.warehouses;
  render();
}

async function refreshSauda(force) {
  const r = await api.saudaNext(state.date).catch(() => null);
  if (!state || !r) return;
  if (force || state.saudaAuto) {
    state.sauda_no = r.sauda_no; state.saudaAuto = true;
    render();
  }
}

function addDays(iso, days) {
  const d = new Date(iso + 'T00:00:00');
  d.setDate(d.getDate() + days);
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// ---------------------------------------------------------------- sauda no
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

// ---------------------------------------------------------------- warehouse
// Buying: which godown the material lands in. Selling: the godown to sell out
// of, which narrows the lots on offer. "Any" leaves every lot available, and the
// sale then records whichever warehouses its lots actually came from.
function blockWarehouse() {
  const sell = state.side === 'sell';
  if (!lineChosen()) return h('div', { class: 'block pending' }, label('Warehouse', false));

  if (sell) {
    const held = new Map();
    let unrecorded = 0;
    for (const l of (state.position ? state.position.lots : [])) {
      if (!(l.available_g > 0)) continue;
      if (l.warehouse) held.set(l.warehouse, (held.get(l.warehouse) || 0) + l.available_g);
      else unrecorded += l.available_g;
    }
    return h('div', { class: 'block' },
      label('Selling out of', true,
        unrecorded ? `${f.qty(unrecorded)} has no warehouse recorded` : 'narrows the lots below'),
      h('div', { class: 'chips' },
        h('button', { class: 'chip' + (state.warehouse ? '' : ' on'), onclick: () => pickSellWarehouse('') },
          'Any warehouse'),
        ...[...held].map(([name, g]) => h('button', {
          class: 'chip' + (state.warehouse === name ? ' on' : ''), onclick: () => pickSellWarehouse(name)
        }, name, h('small', {}, f.qty(g, { short: true }))))));
  }

  const q = state.whQuery.trim();
  const exact = state.warehouses.some(w => w.name.toLowerCase() === q.toLowerCase());
  return h('div', { class: 'block' },
    label('Warehouse', !!state.warehouse, 'where this stock will sit'),
    h('div', { class: 'chips' },
      ...state.warehouses.map(w => h('button', {
        class: 'chip' + (state.warehouse === w.name ? ' on' : ''),
        onclick: () => { state.warehouse = state.warehouse === w.name ? '' : w.name; render(); }
      }, w.name, w.stock_g ? h('small', {}, f.qty(w.stock_g, { short: true })) : null))),
    h('div', { class: 'wh-add' },
      h('input', {
        class: 'ghost-input', data: { fkey: 'wh' }, placeholder: 'New warehouse, e.g. Mundra',
        value: state.whQuery, style: { textAlign: 'left', fontFamily: 'var(--sans)', width: '260px' },
        oninput: e => { state.whQuery = e.target.value; },
        onkeydown: e => { if (e.key === 'Enter') addWarehouseHere(); }
      }),
      q && !exact ? h('button', { class: 'chip ghost', onclick: addWarehouseHere }, `+ Add "${q}"`) : null));
}

async function addWarehouseHere() {
  const name = state.whQuery.trim();
  if (!name) return;
  try {
    await api.addWarehouse(name);
    state.whQuery = ''; state.warehouse = name;
    loadWarehouses();
  } catch (err) { state.error = err.message; render(); }
}

async function pickSellWarehouse(name) {
  state.warehouse = name;
  state.alloc = {}; state.allocText = {};
  const skuId = state.sku && state.sku.id;
  if (skuId) {
    const r = await api.lots(skuId, name || undefined).catch(() => ({ lots: [] }));
    if (!state) return;
    state.lots = r.lots || [];
    const max = stockG();
    if (state.qty_g > max) state.qty_g = max;
  }
  render();
}

// ---------------------------------------------------------------- the split
// Every lot starts at zero. The trader types what he wants out of each one and
// watches a single number - how much of the sale is still unplaced. When that
// number reaches zero the sale is exactly covered, which is the only state the
// book button will accept: no short, no over.
function flow() {
  const rows = state.lots.map(lot => {
    const take = Math.max(0, Math.min(lot.available_g, state.alloc[lot.id] || 0));
    const marginRate = state.rate_paise ? state.rate_paise - lot.rate_paise : 0;
    return { lot, take, marginRate, margin: valuePaise(take, marginRate) };
  });
  const assigned = rows.reduce((s2, r) => s2 + r.take, 0);
  const costTotal = rows.reduce((s2, r) => s2 + r.take * r.lot.rate_paise, 0);
  return {
    rows, assigned,
    left: state.qty_g - assigned,   // negative means he has typed too much
    short: Math.max(0, state.qty_g - assigned),
    over: Math.max(0, assigned - state.qty_g),
    used: rows.filter(r => r.take > 0),
    margin: rows.reduce((s2, r) => s2 + r.margin, 0),
    avgCost: assigned ? rnd(costTotal / assigned) : 0,
    low: Math.min(...state.lots.map(l => l.rate_paise), Infinity),
    high: Math.max(...state.lots.map(l => l.rate_paise), -Infinity)
  };
}

function resetAlloc() {
  state.alloc = {};
  state.allocText = {};
  for (const lot of state.lots) state.alloc[lot.id] = 0;
}

function setAmount(lot, grams) {
  state.alloc[lot.id] = Math.max(0, Math.min(lot.available_g, Math.round(grams)));
}

function typeAmount(lot, raw) {
  const kg = parseFloat(String(raw).replace(/[^0-9.]/g, ''));
  const wanted = isNaN(kg) ? 0 : Math.round(kg * 1000);
  setAmount(lot, wanted);
  // If he asks a lot for more than it holds, the box stops at what it holds
  // right away rather than showing a figure that is not what will be sold.
  const actual = state.alloc[lot.id];
  state.allocText[lot.id] = (wanted > actual && actual > 0)
    ? String(Math.round(actual / 1000))
    : raw;
  sync();
}

// The one shortcut worth keeping: drop whatever is still unplaced into this
// lot, so the last row never needs mental arithmetic.
function fillRest(lot) {
  delete state.allocText[lot.id];
  const left = flow().left;
  setAmount(lot, (state.alloc[lot.id] || 0) + Math.max(0, left));
  sync();
}

// ---------------------------------------------------------------- steps
const lineChosen = () => LEVELS.every(l => !!state.line[l]);
const ready = () => state.party && lineChosen() && state.qty_g > 0 && state.rate_paise > 0
  && (state.side === 'buy' || (state.sku && state.sku.id));

function canBook() {
  if (!ready() || state.busy || state.rateInvalid) return false;
  if (state.side === 'buy') return true;
  const fl = flow();
  return fl.short === 0 && fl.over === 0;   // exact, or not at all
}

// ---------------------------------------------------------------- render
function render() {
  if (!root || !state) return;
  ui = blankRefs();
  const focusKey = document.activeElement && document.activeElement.dataset
    ? document.activeElement.dataset.fkey : null;
  const caret = focusKey && document.activeElement.selectionStart;
  const scrollY = root.scrollTop;

  const sell = state.side === 'sell';
  mount(root,
    h('div', { class: 'trade-screen ' + state.side },
      h('div', { class: 'trade-head' },
        h('div', { class: 'trade-kind' }, sell ? 'SELL' : 'BUY'),
        h('div', { class: 'trade-sub' }, sell ? 'material out · margin booked' : 'material in · new stock'),
        h('button', { class: 'trade-back', onclick: () => ctx.go('desk') }, 'Cancel')),

      blockSauda(),
      blockParty(),
      blockLevel('material'),
      blockLevel('grade'),
      blockLevel('manufacturer'),
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

function blockParty() {
  const done = !!state.party;
  return h('div', { class: 'block' + (done ? '' : ' ') },
    label(state.side === 'buy' ? 'Buying from' : 'Selling to', done),
    done
      ? h('div', { class: 'picked' }, h('b', {}, state.party.name),
          h('button', { onclick: () => { state.party = null; render(); } }, 'Change'))
      : [
          h('div', { class: 'find' }, h('span', { class: 'dim' }, '⌕'),
            h('input', {
              data: { fkey: 'party' }, placeholder: 'Type a name, or tap one below…',
              value: state.partyQuery,
              oninput: e => { state.partyQuery = e.target.value; loadParties(e.target.value); },
              onkeydown: e => {
                if (e.key !== 'Enter') return;
                e.preventDefault();
                const first = state.partyOptions[0];
                pickParty(first && (!state.partyQuery || first.name.toLowerCase().startsWith(state.partyQuery.toLowerCase()))
                  ? first : { name: state.partyQuery.trim(), id: null });
              }
            })),
          h('div', { class: 'chips' },
            ...state.partyOptions.slice(0, 8).map(p =>
              h('button', { class: 'chip', onclick: () => pickParty(p) },
                p.name, p.last_deal ? h('small', {}, f.ago(p.last_deal)) : null)),
            state.partyQuery.trim() && !state.partyOptions.some(p => p.name.toLowerCase() === state.partyQuery.trim().toLowerCase())
              ? h('button', { class: 'chip ghost', onclick: () => pickParty({ name: state.partyQuery.trim(), id: null }) },
                  `+ New: ${state.partyQuery.trim()}`)
              : null)
        ]);
}

function pickParty(p) { if (p && p.name) { state.party = p; state.partyQuery = ''; render(); } }

// material -> grade -> manufacturer, one block each. Each list is narrowed by
// the choice above it, and on a sale only lines with stock are offered.
const LEVEL_TITLE = { material: 'Material', grade: 'Grade', manufacturer: 'Manufacturer' };
const LEVEL_HINT = {
  material: 'e.g. PVC',
  grade: 'e.g. HS1000',
  manufacturer: 'who made it — not who you are trading with'
};

function blockLevel(level) {
  const idx = LEVELS.indexOf(level);
  const above = idx === 0 ? true : !!state.line[LEVELS[idx - 1]];
  const chosen = state.line[level];
  const opts = state.opts[level] || [];
  const sell = state.side === 'sell';
  const query = state.query[level] || '';
  const shown = query
    ? opts.filter(o => o.value.toLowerCase().includes(query.toLowerCase()))
    : opts;
  const exact = opts.some(o => o.value.toLowerCase() === query.trim().toLowerCase());

  return h('div', { class: 'block' + (above ? '' : ' pending') },
    label(LEVEL_TITLE[level], !!chosen,
      chosen && level === 'manufacturer' && state.position
        ? `you hold ${f.qty(stockG())}` : LEVEL_HINT[level]),
    chosen
      ? h('div', { class: 'picked' }, h('b', {}, chosen),
          h('button', { onclick: () => clearLevel(level) }, 'Change'))
      : !above
        ? h('div', { class: 'dim' }, `Choose a ${LEVEL_TITLE[LEVELS[idx - 1]].toLowerCase()} first`)
        : [
            opts.length > 6 || query
              ? h('div', { class: 'find' }, h('span', { class: 'dim' }, '⌕'),
                  h('input', {
                    data: { fkey: 'q-' + level }, placeholder: `Filter ${LEVEL_TITLE[level].toLowerCase()}…`,
                    value: query,
                    oninput: e => { state.query[level] = e.target.value; render(); },
                    onkeydown: e => {
                      if (e.key === 'Enter' && shown.length) { e.preventDefault(); pickLevel(level, shown[0].value); }
                    }
                  }))
              : null,
            shown.length
              ? h('div', { class: 'chips' },
                  ...shown.map(o => h('button', {
                    class: 'chip', onclick: () => pickLevel(level, o.value)
                  }, o.value, o.stock_g ? h('small', {}, f.qty(o.stock_g, { short: true })) : null)))
              : h('div', { class: 'dim' },
                  sell ? 'Nothing in stock here.' : 'Nothing set up yet — add it in Setup.'),
            !sell && query.trim() && !exact
              ? h('div', { class: 'chips', style: { marginTop: '10px' } },
                  h('button', {
                    class: 'chip ghost', onclick: () => addAndPick(level, query.trim())
                  }, `+ Add ${LEVEL_TITLE[level].toLowerCase()} "${query.trim()}"`))
              : null,
            h('button', { class: 'setup-link', onclick: () => ctx.go('setup') },
              'Manage materials, grades and manufacturers')
          ]);
}

// Adding from inside the ticket writes into the same master tree the Setup
// screen edits, so the list never diverges from what he actually trades.
async function addAndPick(level, value) {
  const body = { material: level === 'material' ? value : state.line.material };
  if (level === 'grade') body.grade = value;
  if (level === 'manufacturer') { body.grade = state.line.grade; body.manufacturer = value; }
  try {
    await api.addCatalog(body);
    await loadOptions(level);
    pickLevel(level, value);
  } catch (err) {
    state.error = err.message; render();
  }
}

function blockQty() {
  const done = state.qty_g > 0;
  const sell = state.side === 'sell';
  const max = sell ? stockG() : 0;
  const quick = sell && max
    ? [['25%', Math.round(max * .25)], ['50%', Math.round(max * .5)],
       ['75%', Math.round(max * .75)], ['All ' + f.qty(max, { short: true }), max]]
    : [['5 MT', 5 * MT], ['10 MT', 10 * MT], ['20 MT', 20 * MT], ['25 MT', 25 * MT]];

  return h('div', { class: 'block' + (lineChosen() ? '' : ' pending') },
    label('Quantity', done, sell && max ? `most you can sell is ${f.qty(max)}` : null),
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
        const el = h('button', {
          class: 'chip' + (state.qty_g === g ? ' on' : ''), onclick: () => setQty(g)
        }, text);
        ui.qtyChips.push({ el, g });
        return el;
      })));
}

// A sale can never exceed what is actually in stock — that is what "no short
// selling" means, and the cleanest place to enforce it is the input itself.
function setQty(g) {
  const max = state.side === 'sell' ? stockG() : Infinity;
  const before = state.qty_g;
  state.qty_g = Math.max(0, Math.min(max, g || 0));
  // Crossing zero adds or removes whole blocks; anything else is just numbers.
  if ((before > 0) !== (state.qty_g > 0)) render(); else sync();
}

function blockRate() {
  const done = state.rate_paise > 0;
  const pos = state.position;
  const hint = state.side === 'sell'
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
        // an inexact figure stays in the box exactly as typed; otherwise the box
        // mirrors the stored rate
        value: state.rateInvalid ? state.rateText
             : (state.rate_paise ? String(f.perMt(state.rate_paise)) : ''),
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
        h('input', {
          type: 'checkbox', checked: state.plus_gst || undefined,
          onchange: e => { state.plus_gst = e.target.checked; }
        }),
        h('span', {}, 'GST extra'),
        h('small', {}, 'rate excludes GST')),
      h('label', { class: 'explace' },
        h('span', {}, 'Ex-Place'),
        h('input', {
          class: 'ghost-input', data: { fkey: 'explace' }, value: state.ex_place,
          placeholder: 'Mundra / Aslali / Other',
          oninput: e => { state.ex_place = e.target.value; }
        }))));
}

// A per-MT figure is exact only in Rs 10 steps (1 paisa per kg). Anything finer
// is refused and the book button locks, rather than storing a rounded number
// the trader never typed.
//
// The refusal lives in state, not in the DOM. It used to be written straight
// into the error element, so any re-render - a lot list arriving, the Sauda
// number refreshing - rebuilt the box from the last good rate and blanked the
// message, leaving a valid-looking figure beside a dead Book button.
function rateErrorText(text) {
  const lo = Math.floor(Number(text) / f.PER_MT) * f.PER_MT;
  return `Rates go in steps of ₹10 per MT — ₹${lo.toLocaleString('en-IN')} or ` +
         `₹${(lo + f.PER_MT).toLocaleString('en-IN')}?`;
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
  else {
    sync();
    // The dial mirrors the box; while he is in the box, leave the box alone.
    if (typing && ui.rateDial) ui.rateDial.textContent = f.rate(state.rate_paise);
  }
}

// ---------------------------------------------------------------- the split
function blockSplit() {
  if (!lineChosen() || !state.qty_g) {
    return h('div', { class: 'block pending' }, label('Which stock goes out', false));
  }
  const fl = flow();
  const rows = fl.rows.slice().sort((a, b) => a.lot.rate_paise - b.lot.rate_paise);

  return h('div', { class: 'block' },
    label('Which stock goes out', fl.left === 0,
      'type how much you are taking from each'),
    h('div', { class: 'split-wrap' },
      h('div', { class: 'lotlist' }, ...rows.map(row => lotRow(row, fl))),
      assignPanel(fl)));
}

// Always in view while the rows scroll past it: the rate he is selling at, how
// much of the sale is still unplaced, and what the margin looks like so far.
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
    r.label, r.num, r.of,
    h('div', { class: 'assign-bar' }, r.barI),
    r.done,
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
  const done = fl.left === 0;
  const over = fl.left < 0;
  r.el.className = 'assign' + (done ? ' done' : over ? ' over' : '');
  r.label.textContent = done ? 'Ready' : over ? 'Too much by' : 'Left to assign';
  r.num.textContent = done ? f.qty(state.qty_g) : f.qty(Math.abs(fl.left));
  r.of.textContent = done
    ? `placed across ${fl.used.length} lot${fl.used.length === 1 ? '' : 's'}`
    : `of ${f.qty(state.qty_g)}`;
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
      h('span', {}, `${lot.deal_ref} · ${f.date(lot.deal_date)} · ${f.qty(lot.available_g)} in stock`),
      h('div', { class: 'qbarline' }, ref.barI)),
    ref.margin,
    h('div', { class: 'qtake' },
      h('div', { class: 'qbox' }, ref.input, h('span', { class: 'qunit' }, 'kg')),
      ref.rest));

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
  // Never write into the box he is typing in - that is the whole point.
  if (document.activeElement !== ref.input) {
    const shown = state.allocText[lot.id] !== undefined
      ? state.allocText[lot.id]
      : (take ? String(Math.round(take / 1000)) : '');
    if (ref.input.value !== shown) ref.input.value = shown;
  }
}

// Everything that can change without the page changing shape. This is what a
// keystroke runs, instead of rebuilding the screen underneath the cursor.
function sync() {
  if (!ui.assign.el) return render();
  const fl = state.side === 'sell' && lineChosen() ? flow() : null;

  if (ui.qtyDial) {
    ui.qtyDial.firstChild.textContent = (state.qty_g / MT).toFixed(state.qty_g % MT === 0 ? 0 : 3);
  }
  for (const chip of ui.qtyChips) chip.el.classList.toggle('on', state.qty_g === chip.g);
  if (ui.qtyBox && document.activeElement !== ui.qtyBox) {
    const want = state.qty_g ? String(Math.round(state.qty_g / 1000)) : '';
    if (ui.qtyBox.value !== want) ui.qtyBox.value = want;
  }
  if (ui.rateDial) ui.rateDial.textContent = f.rate(state.rate_paise);

  if (fl) {
    for (const row of fl.rows) paintRow(row, fl);
    paintAssign(fl);
  }
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

  return h('details', {
    class: 'more', open: state.termsOpen,
    // Record it, but do not re-render from here — the browser has already
    // applied the toggle, and re-rendering inside the event fights it.
    ontoggle: e => { state.termsOpen = e.target.open; }
  },
    h('summary', {}, 'Transport, payment, e-way  (optional)'),
    h('div', { class: 'terms' },
      h('div', { class: 'field' }, h('label', {}, 'Freight paid by'), seg('freight_by', ['Buyer', 'Seller'])),
      h('div', { class: 'field' }, h('label', {}, 'Delivery by'), seg('delivery_by', ['Buyer', 'Seller'])),
      h('div', { class: 'field' }, h('label', {}, 'Transporter'),
        h('input', { data: { fkey: 'transporter' }, value: t.transporter, placeholder: 'Ekta',
          oninput: e => { t.transporter = e.target.value; } })),
      h('div', { class: 'field' }, h('label', {}, 'Payment'),
        h('input', { data: { fkey: 'pay' }, value: t.payment_terms, placeholder: '30 days',
          oninput: e => { t.payment_terms = e.target.value; } })),
      h('div', { class: 'field' }, h('label', {}, 'Payment due'),
        h('input', {
          type: 'date', data: { fkey: 'due' }, value: state.payment_due || '',
          oninput: e => { state.payment_due = e.target.value; }
        }),
        h('div', { class: 'due-chips' },
          ...[['Today', 0], ['+7d', 7], ['+15d', 15], ['+30d', 30], ['+45d', 45]].map(([l, n]) =>
            h('button', {
              type: 'button', class: 'chip',
              onclick: () => { state.payment_due = addDays(state.date, n); render(); }
            }, l)))),
      h('div', { class: 'field' }, h('label', {}, 'E-way bill'),
        h('input', { data: { fkey: 'eway' }, value: t.eway, placeholder: 'ASL to buyer',
          oninput: e => { t.eway = e.target.value; } })),
      h('div', { class: 'field' }, h('label', {}, 'Deal date'),
        h('input', { type: 'date', data: { fkey: 'date' }, value: state.date,
          // a new date can cross 1 April, which changes the Sauda series
          oninput: e => { state.date = e.target.value; refreshSauda(false); } })),
      h('div', { class: 'field', style: { gridColumn: '1/-1' } }, h('label', {}, 'Note'),
        h('input', { data: { fkey: 'remarks' }, value: t.remarks, placeholder: 'anything worth remembering',
          oninput: e => { t.remarks = e.target.value; } }))));
}

// ---------------------------------------------------------------- result bar
function resultBar() {
  const r = ui.bar;
  r.money = h('b', { class: 'num' }, '');
  r.moneyLabel = h('span', {}, '');
  r.line = h('div', { class: 'rb-line' }, '');
  r.btn = h('button', { class: 'confirm ' + state.side, onclick: confirm }, '');
  r.el = h('div', { class: 'resultbar' },
    h('div', { class: 'resultbar-in' },
      h('div', { class: 'rb-money' }, r.money, r.moneyLabel),
      r.line, r.btn));
  paintBar(state.side === 'sell' && lineChosen() ? flow() : null);
  return r.el;
}

function paintBar(fl) {
  const r = ui.bar;
  if (!r.el) return;
  const sell = state.side === 'sell';
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
    const need = !state.party ? (state.side === 'buy' ? 'a supplier' : 'a buyer')
      : !state.line.material ? 'a material'
      : !state.line.grade ? 'a grade'
      : !state.line.manufacturer ? 'a manufacturer'
      : !state.qty_g ? 'a quantity' : 'a rate';
    return { text: `Choose ${need} to continue` };
  }
  if (fl && fl.short > 0) return { text: `${f.qty(fl.short)} still to assign`, err: true };
  if (fl && fl.over > 0) {
    return { text: `${f.qty(fl.over)} more than you are selling — lower an amount`, err: true };
  }
  const bits = [`${f.qty(state.qty_g)} ${state.sku ? state.sku.display : ''} @ ${f.rate(state.rate_paise)}`,
    `= ${f.inr(value)}`];
  if (fl) bits.push(`cost ${f.rate(fl.avgCost)} · ${fl.used.length} lot${fl.used.length === 1 ? '' : 's'}`);
  return { text: bits.join('   ·   ') };
}

// ---------------------------------------------------------------- commit
async function confirm() {
  if (!canBook()) return;
  const sell = state.side === 'sell';
  const fl = sell ? flow() : null;
  state.busy = true; state.error = ''; render();
  try {
    const deal = await api.createDeal({
      side: state.side,
      party_id: state.party.id || undefined,
      party_name: state.party.id ? undefined : state.party.name,
      sku_id: state.sku && state.sku.id ? state.sku.id : undefined,
      material: state.line.material,
      grade: state.line.grade,
      manufacturer: state.line.manufacturer,
      qty_g: state.qty_g,
      rate_paise: state.rate_paise,
      deal_date: state.date,
      sauda_no: (state.sauda_no || '').trim() || undefined,
      warehouse: state.warehouse || undefined,
      plus_gst: !!state.plus_gst,
      payment_due: state.payment_due || undefined,
      ex_place: (state.ex_place || '').trim() || undefined,
      pins: fl ? fl.rows.map(r => ({ lot_id: r.lot.id, qty_g: r.take })) : undefined,
      allow_short: false,
      transporter: state.terms.transporter || undefined,
      freight_by: state.terms.freight_by || undefined,
      delivery_by: state.terms.delivery_by || undefined,
      payment_terms: state.terms.payment_terms || undefined,
      eway: state.terms.eway || undefined,
      remarks: state.terms.remarks || undefined,
      confirm: true
    });
    celebrate(sell ? f.inr(deal.margin_paise || 0, { sign: true }) : f.qty(deal.qty_g),
      sell ? `${deal.ref} · ${deal.party_name}` : `${deal.ref} · ${deal.material} into stock`,
      sell ? 'sell' : 'buy');
    toast(sell
      ? `Sold ${f.qty(deal.qty_g)} ${deal.material} to ${deal.party_name}`
      : `Bought ${f.qty(deal.qty_g)} ${deal.material} from ${deal.party_name}`,
      { action: async () => { await api.undo(); toast('Reversed'); ctx.refresh(); } });
    state = null;
    ctx.go('desk');
    ctx.refresh();
  } catch (err) {
    state.busy = false; state.error = err.message; render();
  }
}
