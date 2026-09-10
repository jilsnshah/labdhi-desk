// The phone ticket.
//
// The trader is mid-call, one hand on the phone, and the deal has to be
// recorded before the conversation moves on. So: one decision per screen and
// choosing it advances; a numeric pad drawn into the page; the hardware back
// button walks back through the steps.
//
// Party, product and warehouse are chosen from their own lists, exactly as on
// the desktop, and "Add new" opens the same record form as a bottom sheet.

import { h, mount, toast, debounce } from './ui.js';
import * as f from './fmt.js';
import { api } from './api.js';
import { openForm, partyForm, productForm, warehouseForm, pickParty, partySub } from './forms.js';

const MT = 1e6;
const rnd = n => (n < 0 ? -Math.round(-n) : Math.round(n));
const valuePaise = (qty_g, rate_paise) => rnd(qty_g * rate_paise / 1000);
const LAST_WH = 'labdhi.lastWarehouse';
const lastWh = () => { try { return +localStorage.getItem(LAST_WH) || 0; } catch (_) { return 0; } };
const rememberWh = id => { try { localStorage.setItem(LAST_WH, String(id)); } catch (_) {} };

let ctx = null;
let ms = null;
let screen = null;
let listSeq = 0;

export function startTicket(side, opts = {}, appCtx) {
  ctx = appCtx || ctx;
  ms = {
    side,
    party: opts.party || null,
    product: null, position: null,
    warehouse: null, whAuto: false,
    lots: [], alloc: {},
    qty_g: 0, rate_paise: 0, entry: '',
    list: { items: [], total: 0 }, query: '',
    browse: null, pick: { material: null, grade: null },
    editingLot: null,
    transporter: null,
    terms: { freight_by: '', delivery_by: '', payment_terms: '', eway: '', remarks: '' },
    sauda_no: '', saudaAuto: true,
    plus_gst: true, payment_due: '', ex_place: '',
    date: f.today(), busy: false, error: '', stepIndex: 0
  };
  document.body.classList.add('trading');
  screen = h('div', { class: 'mtrade ' + side });
  document.body.appendChild(screen);
  history.pushState({ ticket: true }, '');
  window.addEventListener('popstate', onPop);
  refreshSauda(true);
  if (opts.product && opts.product.id) chooseProduct(opts.product.id, 2);
  loadStep();
  paint();
}

async function refreshSauda(force) {
  const r = await api.saudaNext(ms.date).catch(() => null);
  if (!ms || !r) return;
  if (force || ms.saudaAuto) { ms.sauda_no = r.sauda_no; ms.saudaAuto = true; paint(); }
}

function onPop() {
  if (!ms) return;
  if (document.querySelector('.modal')) { history.pushState({ ticket: true }, ''); return; }
  if (stepIndex() > 0) { history.pushState({ ticket: true }, ''); back(); }
  else closeTicket(false);
}

function closeTicket(popHistory = true) {
  window.removeEventListener('popstate', onPop);
  if (screen) screen.remove();
  screen = null; ms = null;
  document.body.classList.remove('trading');
  if (popHistory && history.state && history.state.ticket) history.back();
}

// The steps that apply to this ticket. A sale whose product sits in one
// warehouse only needs no "where" screen; a sale from a single lot has
// nothing to split.
function steps() {
  const list = ['who', 'what'];
  if (!(ms.side === 'sell' && ms.whAuto)) list.push('where');
  list.push('qty', 'rate');
  if (ms.side === 'sell' && ms.lots.filter(l => l.available_g > 0).length > 1) list.push('split');
  list.push('review');
  return list;
}
const stepIndex = () => ms.stepIndex || 0;
const current = () => steps()[Math.min(stepIndex(), steps().length - 1)];

function go(delta) {
  ms.stepIndex = Math.max(0, Math.min(steps().length - 1, stepIndex() + delta));
  ms.entry = ''; ms.query = ''; ms.browse = null; ms.editingLot = null;
  ms.list = { items: [], total: 0 };
  loadStep();
  paint();
  const body = screen && screen.querySelector('.mt-body');
  if (body) body.scrollTop = 0;
}
const next = () => go(1);
const back = () => (stepIndex() === 0 ? closeTicket() : go(-1));

function jump(name) {
  if (name === 'where') ms.whAuto = false;
  const i = steps().indexOf(name);
  if (i >= 0) { ms.stepIndex = i; ms.entry = ''; ms.query = ''; ms.browse = null; loadStep(); paint(); }
}

// --------------------------------------------------------------- data
async function loadStep(append = false) {
  if (!ms) return;
  const step = current();
  const sell = ms.side === 'sell';
  const offset = append ? ms.list.items.length : 0;
  const my = ++listSeq;
  let page = null;
  if (step === 'who') page = await api.parties({ q: ms.query, limit: 20, offset });
  else if (step === 'what' && ms.browse === 'material') page = await api.materials({ has_products: true, in_stock: sell, limit: 40, offset });
  else if (step === 'what' && ms.browse === 'grade') page = await api.grades({ material_id: ms.pick.material.id, has_products: true, in_stock: sell, limit: 40, offset });
  else if (step === 'what' && ms.browse === 'product') page = await api.products({ grade_id: ms.pick.grade.id, in_stock: sell, limit: 40, offset });
  else if (step === 'what') page = await api.products({ q: ms.query, in_stock: sell, limit: 20, offset });
  else if (step === 'where' && ms.product) {
    page = sell ? await api.warehouses({ product_id: ms.product.id, in_stock: true, limit: 20, offset })
                : await api.warehouses({ q: ms.query, limit: 20, offset });
  }
  if (!ms || !page || my !== listSeq) return;
  ms.list = { items: append ? ms.list.items.concat(page.items) : page.items, total: page.total };
  paint();
}
const typed = debounce(() => loadStep(), 220);

async function chooseProduct(id, advanceTo = 1) {
  const pos = await api.position(id).catch(() => null);
  if (!ms || !pos) return;
  ms.position = pos;
  ms.product = { ...pos.product, stock_g: pos.stock_g };
  ms.warehouse = null; ms.whAuto = false; ms.lots = []; ms.alloc = {};
  if (!ms.rate_paise) {
    const last = pos.lots.length ? pos.lots[pos.lots.length - 1].rate_paise : 0;
    ms.rate_paise = ms.side === 'sell'
      ? (pos.mark_paise || (pos.cost_paise ? pos.cost_paise + 300 : 0))
      : (last || pos.mark_paise || 0);
  }
  if (ms.side === 'sell') {
    const r = await api.warehouses({ product_id: id, in_stock: true, limit: 2 }).catch(() => ({ items: [] }));
    if (r.items.length === 1) await chooseWarehouse(r.items[0], true);
  }
  if (!ms) return;
  if (advanceTo) { ms.stepIndex = advanceTo === 2 ? steps().indexOf('what') + 1 : stepIndex(); go(advanceTo === 2 ? 0 : 1); }
  else paint();
}

async function chooseWarehouse(w, auto = false) {
  ms.warehouse = w; ms.whAuto = auto; ms.alloc = {};
  if (ms.side === 'sell') {
    const r = await api.lots(ms.product.id, w.id).catch(() => ({ items: [] }));
    if (!ms) return;
    ms.lots = r.items.slice().sort((a, b) => a.rate_paise - b.rate_paise);
    if (ms.qty_g > stockHere()) ms.qty_g = 0;
  } else {
    rememberWh(w.id);
  }
  if (!auto) next();
}

const stockHere = () => ms.lots.reduce((s, l) => s + (l.available_g || 0), 0);

// --------------------------------------------------------------- allocation
function flow() {
  const rows = ms.lots.map(lot => {
    const take = Math.max(0, Math.min(lot.available_g, ms.alloc[lot.id] || 0));
    const marginRate = ms.rate_paise ? ms.rate_paise - lot.rate_paise : 0;
    return { lot, take, marginRate, margin: valuePaise(take, marginRate) };
  });
  const assigned = rows.reduce((s, r) => s + r.take, 0);
  const cost = rows.reduce((s, r) => s + r.take * r.lot.rate_paise, 0);
  return { rows, assigned, left: ms.qty_g - assigned,
           margin: rows.reduce((s, r) => s + r.margin, 0), avgCost: assigned ? rnd(cost / assigned) : 0 };
}

// Fill what is still unassigned from the oldest stock, leaving alone any
// amount he typed himself.
function autoAssign() {
  let left = ms.qty_g - Object.values(ms.alloc).reduce((a, b) => a + (b || 0), 0);
  for (const lot of ms.lots.slice().sort((a, b) => String(a.deal_date).localeCompare(b.deal_date) || a.id - b.id)) {
    if (left <= 0) break;
    const already = ms.alloc[lot.id] || 0;
    const take = Math.min(lot.available_g - already, left);
    if (take <= 0) continue;
    ms.alloc[lot.id] = already + take;
    left -= take;
  }
}

const openLots = () => ms.lots.filter(l => l.available_g > 0);

function pins() {
  if (ms.side !== 'sell') return undefined;
  const open = openLots();
  if (open.length === 1) return [{ lot_id: open[0].id, qty_g: ms.qty_g }];
  return flow().rows.map(r => ({ lot_id: r.lot.id, qty_g: r.take }));
}

function sellCost() {
  const open = openLots();
  if (open.length === 1) return open[0].rate_paise;
  const fl = flow();
  return fl.assigned ? fl.avgCost : (ms.position ? ms.position.cost_paise : 0);
}

function ready() {
  if (!ms.party || !ms.product || !ms.warehouse || ms.qty_g <= 0 || ms.rate_paise <= 0) return false;
  if (ms.side === 'sell' && openLots().length > 1) return flow().left === 0;
  return true;
}

// ==================================================================== paint
function paint() {
  if (!screen || !ms) return;
  const step = current();
  // a search box must keep its focus and caret while its list refreshes
  const active = document.activeElement;
  const fkey = active && active.dataset ? active.dataset.fkey : null;
  const caret = fkey ? active.selectionStart : null;

  mount(screen,
    h('div', { class: 'mt-head' },
      h('button', { class: 'mt-back', onclick: back }, '‹'),
      h('div', { class: 'mt-title' },
        h('b', { class: 'mt-kind' }, ms.side === 'sell' ? 'Sell' : 'Buy'),
        h('span', {}, crumbs())),
      h('button', { class: 'mt-back', onclick: () => closeTicket() }, '✕')),
    h('div', { class: 'mt-steps' }, ...steps().map((_, i) => h('i', { class: i <= stepIndex() ? 'done' : '' }))),
    h('div', { class: 'mt-body' }, body(step)),
    foot(step));

  if (fkey) {
    const el = screen.querySelector(`[data-fkey="${fkey}"]`);
    if (el) { el.focus(); if (caret !== null && el.setSelectionRange) el.setSelectionRange(caret, caret); }
  }
}

function crumbs() {
  const bits = [];
  if (ms.party) bits.push(ms.party.name);
  if (ms.product) bits.push(ms.product.display);
  if (ms.warehouse) bits.push((ms.side === 'sell' ? 'from ' : 'into ') + ms.warehouse.name);
  if (ms.qty_g) bits.push(f.qty(ms.qty_g));
  return bits.join('  ·  ') || (ms.side === 'sell' ? 'stock out' : 'stock in');
}

function body(step) {
  if (step === 'who') return stepWho();
  if (step === 'what') return stepWhat();
  if (step === 'where') return stepWhere();
  if (step === 'qty') return stepQty();
  if (step === 'rate') return stepRate();
  if (step === 'split') return stepSplit();
  return stepReview();
}

const search = (placeholder, key) => h('div', { class: 'msearch' },
  h('span', { class: 'dim' }, '⌕'),
  h('input', { placeholder, value: ms.query, data: { fkey: key }, type: 'search',
    oninput: e => { ms.query = e.target.value; typed(); } }));

const moreBtn = () => ms.list.items.length < ms.list.total
  ? h('button', { class: 'mmore', onclick: () => loadStep(true) },
      `Show more · ${ms.list.total - ms.list.items.length} left`)
  : null;

// --------------------------------------------------------------- who
function stepWho() {
  const isBuy = ms.side === 'buy';
  return [
    h('div', { class: 'mt-q' }, isBuy ? 'Buying from?' : 'Selling to?'),
    h('div', { class: 'mt-hint' }, `${ms.list.total} part${ms.list.total === 1 ? 'y' : 'ies'} · most recent first`),
    search('Name, GSTIN or phone', 'who'),
    h('button', { class: 'mopt ghost', onclick: async () => {
      const p = await partyForm(null, { name: ms.query.trim() });
      if (p && ms) { ms.party = p; next(); }
    } }, '+ Add new party'),
    ...ms.list.items.map(p => h('button', {
      class: 'mopt' + (ms.party && ms.party.id === p.id ? ' on' : ''), onclick: () => { ms.party = p; next(); }
    },
      h('div', { class: 'mopt-main' }, h('b', {}, p.name), h('span', {}, partySub(p))),
      p.last_deal ? h('span', { class: 'mopt-tag' }, f.ago(p.last_deal)) : null)),
    moreBtn()
  ];
}

// --------------------------------------------------------------- what
function stepWhat() {
  if (ms.browse) return stepBrowse();
  const sell = ms.side === 'sell';
  return [
    h('div', { class: 'mt-q' }, sell ? 'Selling what?' : 'Buying what?'),
    h('div', { class: 'mt-hint' }, sell ? 'Only what you hold' : 'Your products'),
    search('Material, grade or maker', 'what'),
    h('div', { class: 'mchips g2', style: { paddingTop: 0 } },
      h('button', { class: 'mchip', onclick: () => { ms.browse = 'material'; ms.list = { items: [], total: 0 }; loadStep(); paint(); } },
        'Browse by material'),
      !sell ? h('button', { class: 'mchip', onclick: addProduct }, '+ New product') : null),
    ...ms.list.items.map(p => h('button', {
      class: 'mopt', onclick: () => chooseProduct(p.id)
    },
      h('div', { class: 'mopt-main' }, h('b', {}, p.display),
        h('span', {}, p.last_deal ? `last traded ${f.ago(p.last_deal)}` : (p.packing || 'never traded'))),
      p.stock_g ? h('span', { class: 'mopt-tag up' }, f.qty(p.stock_g)) : null)),
    moreBtn(),
    !ms.list.items.length ? h('div', { class: 'mt-hint' }, sell ? 'Nothing in stock matches.' : 'No product matches — add it.') : null
  ];
}

async function addProduct() {
  const { material, grade } = ms.pick;
  const p = await productForm(null, { material: material ? material.name : '', grade: grade ? grade.name : '' });
  if (p && ms) chooseProduct(p.id);
}

const BROWSE = { material: 'Which material?', grade: 'Which grade?', product: 'Which manufacturer?' };

function stepBrowse() {
  const level = ms.browse;
  return [
    h('div', { class: 'mt-q' }, BROWSE[level]),
    h('div', { class: 'mt-hint' }, level === 'product' ? 'Who made it — not who you trade with'
      : level === 'grade' ? ms.pick.material.name : ' '),
    ...ms.list.items.map(o => h('button', {
      class: 'mopt',
      onclick: () => {
        if (level === 'material') { ms.pick = { material: o, grade: null }; ms.browse = 'grade'; }
        else if (level === 'grade') { ms.pick.grade = o; ms.browse = 'product'; }
        else return chooseProduct(o.id);
        ms.list = { items: [], total: 0 }; loadStep(); paint();
      }
    },
      h('div', { class: 'mopt-main' }, h('b', {}, level === 'product' ? o.manufacturer : o.name)),
      o.stock_g ? h('span', { class: 'mopt-tag up' }, f.qty(o.stock_g)) : null)),
    moreBtn(),
    ms.side === 'buy' ? h('button', { class: 'mopt ghost', onclick: addProduct }, '+ New product') : null
  ];
}

// --------------------------------------------------------------- where
function stepWhere() {
  const sell = ms.side === 'sell';
  const last = lastWh();
  const items = sell ? ms.list.items : ms.list.items.slice().sort((a, b) => (b.id === last) - (a.id === last));
  return [
    h('div', { class: 'mt-q' }, sell ? 'Dispatch from?' : 'Receive into?'),
    h('div', { class: 'mt-hint' }, sell ? `Where the ${ms.product.display} sits` : 'Which warehouse it will sit in'),
    !sell && ms.list.total > 8 ? search('Warehouse name', 'where') : null,
    !sell ? h('button', { class: 'mopt ghost', onclick: async () => {
      const w = await warehouseForm(null, { name: ms.query.trim() });
      if (w && ms) chooseWarehouse(w);
    } }, '+ Add new warehouse') : null,
    ...items.map(w => h('button', {
      class: 'mopt' + (ms.warehouse && ms.warehouse.id === w.id ? ' on' : ''), onclick: () => chooseWarehouse(w)
    },
      h('div', { class: 'mopt-main' }, h('b', {}, w.name), h('span', {}, w.address || '')),
      h('span', { class: 'mopt-tag' + (sell ? ' up' : '') },
        sell ? f.qty(w.product_stock_g) : (w.id === last ? 'last used' : (w.stock_g ? f.qty(w.stock_g) : 'empty'))))),
    moreBtn(),
    sell && !items.length ? h('div', { class: 'mt-hint' }, 'No warehouse holds this product.') : null
  ];
}

// --------------------------------------------------------------- quantity
function stepQty() {
  const stock = ms.side === 'sell' ? stockHere() : 0;
  const live = ms.entry !== '' ? Math.round(parseFloat(ms.entry || '0') * MT) : ms.qty_g;
  const over = stock > 0 && live > stock;
  const quick = stock
    ? [['25%', Math.round(stock * .25)], ['50%', Math.round(stock * .5)], ['75%', Math.round(stock * .75)], ['All', stock]]
    : [['5 MT', 5 * MT], ['10 MT', 10 * MT], ['20 MT', 20 * MT], ['25 MT', 25 * MT]];
  return [
    h('div', { class: 'mt-q' }, 'How much?'),
    h('div', { class: 'mt-hint' }, stock ? `${f.qty(stock)} of ${ms.product.display} in ${ms.warehouse.name}` : ms.product.display),
    h('div', { class: 'mnum-value' + (over ? ' warn' : '') },
      h('b', {}, (ms.entry !== '' ? ms.entry : (live / MT || 0).toString()) + ' MT'),
      h('small', {}, over ? `More than ${ms.warehouse.name} holds — max ${f.qty(stock)}`
        : (live ? `${Math.round(live / 1000).toLocaleString('en-IN')} kg` : 'tap a shortcut or type'))),
    h('div', { class: 'mchips g4' }, ...quick.map(([label, g]) => h('button', {
      class: 'mchip' + (ms.qty_g === g && ms.entry === '' ? ' on' : ''),
      onclick: () => { ms.qty_g = g; ms.entry = ''; autoIfSingle(); next(); }
    }, label))),
    numpad()
  ];
}

function autoIfSingle() {
  const open = openLots();
  if (open.length === 1) ms.alloc = { [open[0].id]: ms.qty_g };
}

// --------------------------------------------------------------- rate
function stepRate() {
  const typedNow = ms.entry !== '';
  const live = typedNow ? f.fromPerMt(Number(ms.entry)) : ms.rate_paise;
  const bad = typedNow && live === null;
  const paise = live || 0;
  const cost = ms.side === 'sell' ? sellCost() : 0;
  const marginRate = cost && paise ? paise - cost : 0;
  const margin = valuePaise(ms.qty_g, marginRate);
  const good = marginRate >= 0;
  const shown = typedNow ? Number(ms.entry || 0).toLocaleString('en-IN') : f.perMt(ms.rate_paise).toLocaleString('en-IN');
  return [
    h('div', { class: 'mt-q' }, ms.side === 'sell' ? 'At what rate?' : 'At what cost?'),
    h('div', { class: 'mt-hint' }, `${f.qty(ms.qty_g)} · ${ms.product.display}`),
    ms.side === 'sell' && cost
      ? h('div', { class: 'mlive ' + (paise ? (good ? 'good' : 'bad') : '') },
          h('div', { class: 'mlive-top' },
            h('b', { class: 'num ' + (good ? 'up' : 'down') }, f.inr(margin, { sign: true })),
            h('span', { class: 'num ' + (good ? 'up' : 'down') }, f.rateDelta(marginRate) + '/MT')),
          h('div', { class: 'mlive-sub' }, `your cost ${f.rate(cost)}/MT · sale value ${f.inr(valuePaise(ms.qty_g, paise))}`))
      : (paise ? h('div', { class: 'mlive' },
          h('div', { class: 'mlive-top' }, h('b', { class: 'num' }, f.inr(valuePaise(ms.qty_g, paise)))),
          h('div', { class: 'mlive-sub' }, 'total value of this purchase')) : null),
    h('div', { class: 'mnum-value' + (bad ? ' warn' : '') },
      h('b', {}, '₹' + shown),
      h('small', {}, bad ? 'Rates go in steps of ₹10 per MT' : 'per MT, basic rate')),
    h('div', { class: 'mchips g3' },
      ...[-50, -25, -10, 10, 25, 50].map(d => h('button', {
        class: 'mchip',
        onclick: () => { const base = typedNow && live !== null ? live : ms.rate_paise; ms.rate_paise = Math.max(0, base + d); ms.entry = ''; paint(); }
      }, (d > 0 ? '+' : '−') + '₹' + Math.abs(d * f.PER_MT).toLocaleString('en-IN')))),
    h('button', { class: 'mgst' + (ms.plus_gst ? ' on' : ''), onclick: () => { ms.plus_gst = !ms.plus_gst; paint(); } },
      ms.plus_gst ? '✓ GST extra — rate excludes GST' : 'GST included in this rate'),
    numpad({ noDot: true })
  ];
}

// --------------------------------------------------------------- split
function stepSplit() {
  if (ms.editingLot) return lotEditor();
  const fl = flow();
  const done = fl.left === 0, over = fl.left < 0;
  return [
    h('div', { class: 'mt-q' }, `From which stock in ${ms.warehouse.name}?`),
    h('div', { class: 'mt-hint' }, 'Tap a lot to set how much comes out of it'),
    h('div', { class: 'massign' + (done ? ' done' : '') },
      h('div', {},
        h('b', { class: 'num ' + (done ? 'up' : over ? 'down' : '') }, done ? 'All set' : f.qty(Math.abs(fl.left))),
        h('span', {}, done ? `${f.qty(ms.qty_g)} assigned` : over ? 'too much — take some back' : 'still to assign')),
      h('span', { class: 'grow' }),
      fl.assigned ? h('button', { class: 'massign-act', onclick: () => { ms.alloc = {}; paint(); } }, 'Clear') : null,
      h('button', { class: 'massign-act', onclick: () => { autoAssign(); paint(); } }, fl.assigned ? 'Fill rest' : 'Auto')),
    ...fl.rows.map(row => h('button', {
      class: 'mlot' + (row.take > 0 ? ' on' : ''), onclick: () => { ms.editingLot = row.lot.id; ms.entry = ''; paint(); }
    },
      h('div', { class: 'mlot-rate num' }, f.rate(row.lot.rate_paise)),
      h('div', { class: 'mlot-main' },
        h('b', {}, row.lot.supplier_name),
        h('span', {}, `${f.qty(row.lot.available_g)} free · ${row.lot.deal_ref} · ${f.date(row.lot.deal_date)}`)),
      h('div', { class: 'mlot-take' },
        h('b', { class: 'num ' + (row.take ? '' : 'dim') }, row.take ? f.qty(row.take) : '—'),
        h('span', { class: row.marginRate >= 0 ? 'up' : 'down' }, f.rateDelta(row.marginRate) + '/MT'))))
  ];
}

function lotRoom(lot) {
  const others = flow().assigned - (ms.alloc[lot.id] || 0);
  return Math.min(lot.available_g, ms.qty_g - others);
}

function lotEditor() {
  const lot = ms.lots.find(l => l.id === ms.editingLot);
  const room = lotRoom(lot);
  const live = ms.entry !== '' ? Math.round(parseFloat(ms.entry || '0') * MT) : (ms.alloc[lot.id] || 0);
  const set = g => { ms.alloc[lot.id] = Math.max(0, Math.min(room, g)); ms.entry = ''; ms.editingLot = null; paint(); };
  return [
    h('div', { class: 'mt-q' }, lot.supplier_name),
    h('div', { class: 'mt-hint' }, `${f.rate(lot.rate_paise)} · ${f.qty(lot.available_g)} free · ${f.qty(Math.max(0, room))} can go here`),
    h('div', { class: 'mnum-value' },
      h('b', {}, (ms.entry !== '' ? ms.entry : (live / MT || 0).toString()) + ' MT'),
      h('small', {}, `${Math.round(live / 1000).toLocaleString('en-IN')} kg`)),
    h('div', { class: 'mchips g2' },
      h('button', { class: 'mchip', onclick: () => set(room) }, `Rest · ${f.qty(room)}`),
      h('button', { class: 'mchip', onclick: () => set(0) }, 'None')),
    numpad()
  ];
}

// --------------------------------------------------------------- review
function stepReview() {
  const sell = ms.side === 'sell';
  const cost = sell ? sellCost() : 0;
  const marginRate = cost ? ms.rate_paise - cost : 0;
  const margin = valuePaise(ms.qty_g, marginRate);
  return [
    h('div', { class: 'mt-q' }, sell ? 'Confirm the sale' : 'Confirm the purchase'),
    h('div', { class: 'mt-hint' }, 'One tap to book. You can undo straight after.'),
    h('div', { class: 'mrev' },
      row('Sauda No.', ms.sauda_no || '—', editSauda),
      row(sell ? 'Buyer' : 'Supplier', ms.party.name, () => jump('who')),
      row('Product', ms.product.display, () => jump('what')),
      row(sell ? 'Dispatch from' : 'Receive into', ms.warehouse.name, () => jump('where')),
      row('Quantity', f.qty(ms.qty_g), () => jump('qty')),
      row('Rate', f.rate(ms.rate_paise) + '/MT', () => jump('rate')),
      row('GST', ms.plus_gst ? 'Extra' : 'Included', () => { ms.plus_gst = !ms.plus_gst; paint(); }),
      row('Value', f.inr(valuePaise(ms.qty_g, ms.rate_paise))),
      row('Date', f.date(ms.date), openTerms),
      row('Payment due', ms.payment_due ? f.date(ms.payment_due) : 'Not set', openTerms),
      row('Transport', termsSummary(), openTerms)),
    sell && cost
      ? h('div', { class: 'mlive ' + (margin >= 0 ? 'good' : 'bad') },
          h('div', { class: 'mlive-top' },
            h('b', { class: 'num ' + (margin >= 0 ? 'up' : 'down') }, f.inr(margin, { sign: true })),
            h('span', { class: 'num ' + (margin >= 0 ? 'up' : 'down') }, f.rateDelta(marginRate) + '/MT')),
          h('div', { class: 'mlive-sub' }, `bought at ${f.rate(cost)}/MT, selling at ${f.rate(ms.rate_paise)}/MT`))
      : null,
    ms.error ? h('div', { class: 'need' }, ms.error) : null
  ];
}

function termsSummary() {
  const t = ms.terms, bits = [];
  if (ms.transporter) bits.push(ms.transporter.name);
  if (t.freight_by) bits.push(`freight: ${t.freight_by}`);
  if (t.delivery_by) bits.push(`delivery: ${t.delivery_by}`);
  if (t.payment_terms) bits.push(t.payment_terms);
  if (t.eway) bits.push(`e-way: ${t.eway}`);
  if (t.remarks) bits.push(t.remarks);
  if (ms.ex_place) bits.unshift(`Ex-${ms.ex_place}`);
  return bits.length ? bits.join(' · ') : 'Not set';
}

// Transport, payment and e-way are recorded, never calculated, so they sit
// behind one tap on the last screen. The transporter is a party record too.
function openTerms() {
  const t = ms.terms;
  const credit = [['Today', 0], ['+7d', 7], ['+15d', 15], ['+30d', 30], ['+45d', 45]];
  openForm({
    title: 'Transport & payment', submitLabel: 'Done',
    fields: [
      { key: 'date', label: 'Deal date', type: 'date', value: ms.date, half: true },
      { key: 'payment_due', label: 'Payment due', type: 'date', value: ms.payment_due, half: true,
        chips: credit.map(([l, n]) => [l, v => f.addDays(v.date || ms.date, n)]) },
      { key: 'transporter', label: 'Transporter', type: 'picker', placeholder: 'Choose from parties…',
        value: ms.transporter ? { id: ms.transporter.id, label: ms.transporter.name } : null,
        pick: async () => { const p = await pickParty('Choose transporter'); return p ? { id: p.id, label: p.name, party: p } : null; } },
      { key: 'ex_place', label: 'Ex-Place', value: ms.ex_place, placeholder: 'Mundra / Aslali / Other' },
      { key: 'freight_by', label: 'Freight paid by', type: 'choice', options: ['Buyer', 'Seller'], value: t.freight_by },
      { key: 'delivery_by', label: 'Delivery by', type: 'choice', options: ['Buyer', 'Seller'], value: t.delivery_by },
      { key: 'payment_terms', label: 'Payment terms', value: t.payment_terms, placeholder: '30 days' },
      { key: 'eway', label: 'E-way bill', value: t.eway, placeholder: 'ASL to buyer' },
      { key: 'remarks', label: 'Note', value: t.remarks, placeholder: 'anything worth remembering' }
    ],
    submit: v => {
      const dateChanged = v.date && v.date !== ms.date;
      ms.date = v.date || ms.date;
      ms.payment_due = v.payment_due || '';
      ms.ex_place = v.ex_place || '';
      ms.transporter = v.transporter ? { id: v.transporter.id, name: v.transporter.label } : null;
      ms.terms = { freight_by: v.freight_by, delivery_by: v.delivery_by, payment_terms: v.payment_terms,
                   eway: v.eway, remarks: v.remarks };
      if (dateChanged) refreshSauda(false);
      paint();
      return true;
    }
  });
}

function editSauda() {
  openForm({
    title: 'Sauda No.', submitLabel: 'Done',
    fields: [{ key: 'sauda', label: 'Sauda No.', value: ms.sauda_no, placeholder: 'LE/26-27/0001',
               hint: 'Leave it blank to take the next number in this financial year.' }],
    submit: async v => {
      if (v.sauda.trim()) { ms.sauda_no = v.sauda.trim(); ms.saudaAuto = false; }
      else await refreshSauda(true);
      paint();
      return true;
    }
  });
}

function row(label, value, onEdit) {
  return h('div', { class: 'mrev-row', onclick: onEdit },
    h('span', {}, label), h('b', {}, value), onEdit ? h('em', {}, 'change') : null);
}

// --------------------------------------------------------------- numpad
function numpad(opts = {}) {
  const press = key => {
    if (key === 'del') ms.entry = ms.entry.slice(0, -1);
    else if (key === '.') { if (!ms.entry.includes('.')) ms.entry = (ms.entry || '0') + '.'; }
    else ms.entry = (ms.entry === '0' ? '' : ms.entry) + key;
    paint();
  };
  // Per-MT rates are whole rupees and usually end in zeros, so the rate pad
  // swaps the decimal point for a double-zero key.
  const keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', opts.noDot ? '00' : '.', '0'];
  return h('div', { class: 'mpad' },
    ...keys.map(k => h('button', { class: 'mkey', onclick: () => press(k) }, k)),
    h('button', { class: 'mkey fn', onclick: () => press('del') }, '⌫'));
}

// --------------------------------------------------------------- footer
function foot(step) {
  if (['who', 'what', 'where'].includes(step)) return null;
  if (step === 'review') {
    return h('div', { class: 'mt-foot' },
      h('button', { class: 'mt-next', disabled: !ready() || ms.busy, onclick: book },
        ms.busy ? 'Booking…' : (ms.side === 'sell' ? 'Book sale' : 'Book purchase')));
  }
  if (step === 'split') {
    if (ms.editingLot) {
      const lot = ms.lots.find(l => l.id === ms.editingLot);
      return h('div', { class: 'mt-foot' },
        h('button', { class: 'mt-skip', onclick: () => { ms.editingLot = null; ms.entry = ''; paint(); } }, 'Cancel'),
        h('button', { class: 'mt-next', onclick: () => {
          const v = ms.entry !== '' ? Math.round(parseFloat(ms.entry) * MT) : (ms.alloc[lot.id] || 0);
          ms.alloc[lot.id] = Math.max(0, Math.min(lotRoom(lot), v));
          ms.entry = ''; ms.editingLot = null; paint();
        } }, 'Set amount'));
    }
    const left = flow().left;
    return h('div', { class: 'mt-foot' },
      h('button', { class: 'mt-next', disabled: left !== 0, onclick: next },
        left === 0 ? 'Review' : `${f.qty(Math.abs(left))} ${left < 0 ? 'too much' : 'to assign'}`));
  }
  const value = step === 'qty'
    ? (ms.entry !== '' ? Math.round(parseFloat(ms.entry || '0') * MT) : ms.qty_g)
    : (ms.entry !== '' ? f.fromPerMt(Number(ms.entry)) : ms.rate_paise);
  return h('div', { class: 'mt-foot' },
    h('button', { class: 'mt-next', disabled: !(value > 0), onclick: () => {
      if (step === 'qty') {
        const stock = ms.side === 'sell' ? stockHere() : 0;
        ms.qty_g = stock ? Math.min(value, stock) : value;
        autoIfSingle();
      } else ms.rate_paise = value;
      next();
    } }, 'Next'));
}

// --------------------------------------------------------------- commit
async function book() {
  if (!ready() || ms.busy) return;
  ms.busy = true; ms.error = ''; paint();
  const sell = ms.side === 'sell';
  try {
    const deal = await api.createDeal({
      side: ms.side,
      party_id: ms.party.id,
      product_id: ms.product.id,
      warehouse_id: ms.warehouse.id,
      qty_g: ms.qty_g,
      rate_paise: ms.rate_paise,
      deal_date: ms.date,
      sauda_no: (ms.sauda_no || '').trim() || undefined,
      plus_gst: !!ms.plus_gst,
      payment_due: ms.payment_due || undefined,
      ex_place: (ms.ex_place || '').trim() || undefined,
      transporter_id: ms.transporter ? ms.transporter.id : undefined,
      freight_by: ms.terms.freight_by || undefined,
      delivery_by: ms.terms.delivery_by || undefined,
      payment_terms: ms.terms.payment_terms || undefined,
      eway: ms.terms.eway || undefined,
      remarks: ms.terms.remarks || undefined,
      pins: pins(),
      confirm: true
    });
    done(sell, deal);
    closeTicket();
    toast(sell ? `Sold ${f.qty(deal.qty_g)} to ${deal.party_name} from ${deal.warehouse}`
               : `Bought ${f.qty(deal.qty_g)} from ${deal.party_name} into ${deal.warehouse}`,
      { action: async () => { await api.undo(); toast('Reversed'); ctx.refresh(); } });
    ctx.refresh();
  } catch (err) {
    ms.busy = false; ms.error = err.message; paint();
  }
}

function done(sell, deal) {
  const el = h('div', { class: 'mdone' },
    h('div', {},
      h('b', { class: sell ? 'up' : '' }, sell ? f.inr(deal.margin_paise || 0, { sign: true }) : f.qty(deal.qty_g)),
      h('span', {}, `${deal.ref} · ${deal.party_name}`)));
  document.body.appendChild(el);
  if (navigator.vibrate) navigator.vibrate(18);
  setTimeout(() => el.remove(), 1100);
}
