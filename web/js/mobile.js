// The phone app.
//
// This is not the desktop screen at a narrower width. The workflow it is built
// around: the trader is mid-call, one hand on the phone, and the deal has to be
// recorded before the conversation moves on. Everything below follows from that.
//
//   * one decision per screen, and choosing it advances - no scrolling a form
//   * a numeric pad drawn into the page, because the OS keyboard covers half a
//     phone and asks for precision the situation does not allow
//   * Buy and Sell parked under the thumb on every screen
//   * "repeat" shortcuts, because the same party and grade come round again
//   * the hardware back button walks back through the steps
//
// Desktop is untouched; app.js picks between the two.

import { h, mount, svg, toast } from './ui.js';
import * as f from './fmt.js';
import { api } from './api.js';

const MT = 1e6;
const rnd = n => (n < 0 ? -Math.round(-n) : Math.round(n));
const valuePaise = (qty_g, rate_paise) => rnd(qty_g * rate_paise / 1000);
const todayISO = () => {
  const d = new Date(), p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};

let ctx = null;
let ms = null;          // the in-flight ticket
let screen = null;      // its root element

// ==================================================================== shell
export function mountShell(appCtx) {
  ctx = appCtx;
  const head = h('header', { class: 'mhead' },
    h('div', { class: 'mhead-top' },
      h('i', { class: 'mhead-dot' }),
      h('div', { class: 'mhead-name', id: 'm-company' }, 'Labdhi Desk'),
      h('div', { class: 'mhead-stat' },
        h('b', { id: 'm-pnl', class: 'num' }, '—'),
        h('span', {}, 'open p&l'))),
    h('div', { class: 'mhead-strip', id: 'm-strip' }));

  const dock = h('div', { class: 'dock' },
    h('div', { class: 'dock-actions' },
      h('button', { class: 'dock-btn buy', onclick: () => startTicket('buy') }, '↓ Buy'),
      h('button', { class: 'dock-btn sell', onclick: () => startTicket('sell') }, '↑ Sell')),
    h('div', { class: 'dock-tabs' },
      ...[['desk', '▦', 'Desk'], ['flow', '⤳', 'Flow'], ['tape', '≡', 'Tape'], ['setup', '⚙', 'Setup']]
        .map(([route, icon, label]) => h('button', {
          class: 'dock-tab', data: { route }, onclick: () => ctx.go(route)
        }, h('i', {}, icon), label))));

  document.getElementById('app').prepend(head);
  document.body.appendChild(dock);
  syncTabs();
}

export function syncTabs() {
  for (const b of document.querySelectorAll('.dock-tab')) {
    b.classList.toggle('on', b.dataset.route === ctx.route);
  }
}

export function paintHeader(desk) {
  const company = document.getElementById('m-company');
  if (company) company.textContent = desk.company || 'Labdhi Desk';
  const pnl = document.getElementById('m-pnl');
  if (pnl) {
    pnl.textContent = f.inr(desk.unrealised_paise, { sign: true, compact: true });
    pnl.className = 'num ' + (desk.unrealised_paise >= 0 ? 'up' : 'down');
  }
  const strip = document.getElementById('m-strip');
  if (strip) {
    mount(strip,
      h('div', {}, 'Stock ', h('b', {}, f.qty(desk.stock_g))),
      h('div', {}, 'Today ', h('b', { class: desk.realised_today_paise >= 0 ? 'up' : 'down' },
        f.inr(desk.realised_today_paise, { sign: true, compact: true }))),
      h('div', {}, 'Value ', h('b', {}, f.inr(desk.stock_value_paise, { compact: true }))));
  }
}

// ===================================================================== desk
export function renderMobileDesk(root, desk) {
  paintHeader(desk);
  // No stat cards: the header strip above already carries stock, today's P&L
  // and book value, and a phone screen is better spent on what he can act on.
  mount(root, h('div', { class: 'view' },
    desk.attention && desk.attention.length
      ? h('div', { class: 'mflow', style: { paddingTop: '14px' } },
          ...desk.attention.slice(0, 3).map(a => h('div', { class: 'alert ' + a.level },
            h('i', { class: 'bar' }),
            h('div', {}, h('b', {}, a.title), h('div', {}, h('span', {}, a.detail))))))
      : null,

    h('div', { class: 'mdesk' },
      h('div', { class: 'mlabel' }, 'Positions',
        h('span', {}, `${desk.positions.length} of ${desk.positions_matched}`)),
      ...desk.positions.map(p => h('div', { class: 'mpos' },
        h('div', { class: 'mpos-main', onclick: () => ctx.go('position', p.sku_id) },
          h('div', { class: 'mpos-name' }, p.material),
          h('div', { class: 'mpos-sub' },
            `${f.rate(p.cost_paise)} avg · ${p.open_lots} lot${p.open_lots === 1 ? '' : 's'}`)),
        h('div', { class: 'mpos-qty', onclick: () => ctx.go('position', p.sku_id) },
          h('b', { class: 'num' }, f.qty(p.stock_g)),
          h('span', { class: 'num ' + (p.unrealised_paise >= 0 ? 'up' : 'down') },
            f.inr(p.unrealised_paise, { sign: true, compact: true }))),
        h('button', {
          class: 'mpos-go', title: 'Sell from this position',
          onclick: () => startTicket('sell', { sku_id: p.sku_id, material: p.material })
        }, '↑'))),
      desk.positions.length ? null : h('div', { class: 'empty' },
        h('h3', {}, 'No stock yet'), h('div', {}, 'Tap Buy to record your first purchase.')))));
}

// =================================================================== ticket
export function startTicket(side, prefill = {}) {
  ms = {
    side,
    party: prefill.party_id ? { id: prefill.party_id, name: prefill.party_name } : null,
    sku: prefill.sku_id ? { id: prefill.sku_id, display: prefill.material } : null,
    line: { material: '', grade: '', manufacturer: '' },
    position: null, lots: [], alloc: {},
    qty_g: 0, rate_paise: 0, entry: '',
    lastRate: prefill.rate_paise || 0,
    options: [], query: '', browse: null, editingLot: null,
    date: todayISO(), busy: false, error: ''
  };
  document.body.classList.add('trading');
  screen = h('div', { class: 'mtrade ' + side });
  document.body.appendChild(screen);
  history.pushState({ ticket: true }, '');
  window.addEventListener('popstate', onPop);
  if (ms.sku && ms.sku.id) loadSku(ms.sku.id);
  loadStep();
  paint();
}

function onPop() {
  if (!ms) return;
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

// The steps that actually apply to this ticket. A sale with a single lot has
// nothing to split, so that screen simply does not exist.
function steps() {
  const list = ['who', 'what', 'qty', 'rate'];
  if (ms.side === 'sell' && ms.lots.filter(l => l.available_g > 0).length > 1) list.push('split');
  list.push('review');
  return list;
}

function stepIndex() { return ms.stepIndex || 0; }
function current() { return steps()[Math.min(stepIndex(), steps().length - 1)]; }

function go(delta) {
  const max = steps().length - 1;
  ms.stepIndex = Math.max(0, Math.min(max, stepIndex() + delta));
  ms.entry = ''; ms.query = ''; ms.browse = null; ms.editingLot = null;
  loadStep();
  paint();
  const body = screen && screen.querySelector('.mt-body');
  if (body) body.scrollTop = 0;
}
const next = () => go(1);
const back = () => (stepIndex() === 0 ? closeTicket() : go(-1));

// --------------------------------------------------------------- data
async function loadStep() {
  const step = current();
  if (step === 'who') {
    const r = await api.parties(ms.query, ms.side === 'buy' ? 'supplier' : 'customer');
    if (ms) { ms.options = r.results; paint(); }
  } else if (step === 'what') {
    if (ms.browse) return loadBrowse();
    if (ms.side === 'sell') {
      const r = await api.positions({ q: ms.query, limit: 40 });
      if (ms) { ms.options = r.positions; paint(); }
    } else {
      const r = await api.materials(ms.query, false);
      if (ms) { ms.options = r.results; paint(); }
    }
  }
}

async function loadBrowse() {
  const level = ms.browse;
  const r = await api.catalog(level,
    level === 'material' ? undefined : ms.line.material,
    level === 'manufacturer' ? ms.line.grade : undefined,
    ms.side === 'sell' ? true : undefined);
  if (ms) { ms.options = r.options; paint(); }
}

async function loadSku(skuId) {
  const [pos, lots] = await Promise.all([
    api.position(skuId).catch(() => null),
    api.lots(skuId).catch(() => ({ lots: [] }))
  ]);
  if (!ms) return;
  ms.position = pos;
  ms.lots = (lots.lots || []).slice().sort((a, b) => a.rate_paise - b.rate_paise);
  ms.alloc = {};
  if (pos) {
    ms.line = { material: pos.sku.material, grade: pos.sku.grade, manufacturer: pos.sku.manufacturer };
    ms.sku = { id: pos.sku.id, display: pos.sku.display };
    if (!ms.rate_paise) {
      ms.rate_paise = ms.lastRate || (ms.side === 'sell'
        ? (pos.mark_paise || (pos.cost_paise ? pos.cost_paise + 300 : 0))
        : (pos.cost_paise || pos.mark_paise || 0));
    }
  }
  paint();
}

// --------------------------------------------------------------- allocation
function flow() {
  const rows = ms.lots.map(lot => {
    const take = Math.max(0, Math.min(lot.available_g, ms.alloc[lot.id] || 0));
    const marginRate = ms.rate_paise ? ms.rate_paise - lot.rate_paise : 0;
    return { lot, take, marginRate, margin: valuePaise(take, marginRate) };
  });
  const assigned = rows.reduce((s, r) => s + r.take, 0);
  const cost = rows.reduce((s, r) => s + r.take * r.lot.rate_paise, 0);
  return {
    rows, assigned, left: ms.qty_g - assigned,
    margin: rows.reduce((s, r) => s + r.margin, 0),
    avgCost: assigned ? rnd(cost / assigned) : 0
  };
}

// Fill whatever is still unassigned from the oldest stock, leaving alone any
// amount he typed himself. Wiping his own numbers to "help" is the behaviour
// that made the desktop version infuriating.
function autoAssign() {
  let left = ms.qty_g - Object.values(ms.alloc).reduce((a, b) => a + (b || 0), 0);
  if (left <= 0) return;
  for (const lot of ms.lots.slice().sort((a, b) =>
    String(a.deal_date).localeCompare(b.deal_date) || a.id - b.id)) {
    if (left <= 0) break;
    const already = ms.alloc[lot.id] || 0;
    const take = Math.min(lot.available_g - already, left);
    if (take <= 0) continue;
    ms.alloc[lot.id] = already + take;
    left -= take;
  }
}

function effectiveAlloc() {
  const open = ms.lots.filter(l => l.available_g > 0);
  if (ms.side !== 'sell') return null;
  if (open.length === 1) return [{ lot_id: open[0].id, qty_g: ms.qty_g }];
  return flow().rows.map(r => ({ lot_id: r.lot.id, qty_g: r.take }));
}

function sellCost() {
  const open = ms.lots.filter(l => l.available_g > 0);
  if (open.length === 1) return open[0].rate_paise;
  const fl = flow();
  return fl.assigned ? fl.avgCost : (ms.position ? ms.position.cost_paise : 0);
}

function ready() {
  if (!ms.party || !ms.sku || ms.qty_g <= 0 || ms.rate_paise <= 0) return false;
  if (ms.side === 'sell' && ms.lots.filter(l => l.available_g > 0).length > 1) {
    return flow().left === 0;
  }
  return true;
}

// ==================================================================== paint
function paint() {
  if (!screen || !ms) return;
  const step = current();
  const all = steps();

  mount(screen,
    h('div', { class: 'mt-head' },
      h('button', { class: 'mt-back', onclick: back }, '‹'),
      h('div', { class: 'mt-title' },
        h('b', { class: 'mt-kind' }, ms.side === 'sell' ? 'Sell' : 'Buy'),
        h('span', {}, crumbs())),
      h('button', { class: 'mt-back', onclick: () => closeTicket() }, '✕')),
    h('div', { class: 'mt-steps' },
      ...all.map((_, i) => h('i', { class: i <= stepIndex() ? 'done' : '' }))),
    h('div', { class: 'mt-body' }, body(step)),
    foot(step));
}

function crumbs() {
  const bits = [];
  if (ms.party) bits.push(ms.party.name);
  if (ms.sku) bits.push(ms.sku.display);
  if (ms.qty_g) bits.push(f.qty(ms.qty_g));
  if (ms.rate_paise && current() !== 'rate') bits.push(f.rate(ms.rate_paise));
  return bits.join('  ·  ') || (ms.side === 'sell' ? 'material out' : 'material in');
}

function body(step) {
  if (step === 'who') return stepWho();
  if (step === 'what') return stepWhat();
  if (step === 'qty') return stepQty();
  if (step === 'rate') return stepRate();
  if (step === 'split') return stepSplit();
  return stepReview();
}

// --------------------------------------------------------------- who
function stepWho() {
  const isBuy = ms.side === 'buy';
  const typed = ms.query.trim();
  const exact = ms.options.some(p => p.name.toLowerCase() === typed.toLowerCase());
  return [
    h('div', { class: 'mt-q' }, isBuy ? 'Buying from?' : 'Selling to?'),
    h('div', { class: 'mt-hint' }, 'Most recent first'),
    h('div', { class: 'msearch' },
      h('span', { class: 'dim' }, '⌕'),
      h('input', {
        placeholder: isBuy ? 'Supplier name' : 'Buyer name', value: ms.query,
        oninput: e => { ms.query = e.target.value; loadStep(); }
      })),
    ...ms.options.slice(0, 12).map(p => h('button', {
      class: 'mopt', onclick: () => { ms.party = p; next(); }
    },
      h('div', { class: 'mopt-main' },
        h('b', {}, p.name),
        p.last_deal ? h('span', {}, `${p.deal_count} deals · ${f.ago(p.last_deal)}`) : null))),
    typed && !exact
      ? h('button', {
          class: 'mopt ghost',
          onclick: () => { ms.party = { id: null, name: typed }; next(); }
        }, `+ New: ${typed}`)
      : null
  ];
}

// --------------------------------------------------------------- what
function stepWhat() {
  if (ms.browse) return stepBrowse();
  const sell = ms.side === 'sell';
  return [
    h('div', { class: 'mt-q' }, sell ? 'Selling what?' : 'Buying what?'),
    h('div', { class: 'mt-hint' }, sell ? 'Only what you hold' : 'Recently traded'),
    h('div', { class: 'msearch' },
      h('span', { class: 'dim' }, '⌕'),
      h('input', {
        placeholder: 'Material, grade or maker', value: ms.query,
        oninput: e => { ms.query = e.target.value; loadStep(); }
      })),
    ...ms.options.slice(0, 14).map(o => {
      const id = o.sku_id || o.id;
      const label = o.display || o.material;
      return h('button', {
        class: 'mopt', onclick: () => { ms.sku = { id, display: label }; loadSku(id); next(); }
      },
        h('div', { class: 'mopt-main' },
          h('b', {}, label),
          o.cost_paise ? h('span', {}, `cost ${f.rate(o.cost_paise)}`)
            : (o.last_deal ? h('span', {}, `last traded ${f.ago(o.last_deal)}`) : null)),
        o.stock_g ? h('span', { class: 'mopt-tag up' }, f.qty(o.stock_g)) : null);
    }),
    h('button', {
      class: 'mopt ghost', onclick: () => { ms.browse = 'material'; ms.query = ''; loadBrowse(); }
    }, sell ? 'Browse all materials' : '+ New material, grade or maker')
  ];
}

const BROWSE_TITLE = { material: 'Which material?', grade: 'Which grade?', manufacturer: 'Which manufacturer?' };

function stepBrowse() {
  const level = ms.browse;
  const typed = ms.query.trim();
  const shown = typed
    ? ms.options.filter(o => o.value.toLowerCase().includes(typed.toLowerCase()))
    : ms.options;
  const exact = ms.options.some(o => o.value.toLowerCase() === typed.toLowerCase());
  return [
    h('div', { class: 'mt-q' }, BROWSE_TITLE[level]),
    h('div', { class: 'mt-hint' },
      level === 'manufacturer' ? 'Who made it — not who you trade with' : ' '),
    h('div', { class: 'msearch' },
      h('span', { class: 'dim' }, '⌕'),
      h('input', {
        placeholder: 'Type to filter or add', value: ms.query,
        oninput: e => { ms.query = e.target.value; paint(); }
      })),
    ...shown.slice(0, 14).map(o => h('button', {
      class: 'mopt', onclick: () => pickLevel(level, o.value)
    },
      h('div', { class: 'mopt-main' }, h('b', {}, o.value)),
      o.stock_g ? h('span', { class: 'mopt-tag up' }, f.qty(o.stock_g)) : null)),
    ms.side === 'buy' && typed && !exact
      ? h('button', { class: 'mopt ghost', onclick: () => addLevel(level, typed) },
          `+ Add "${typed}"`)
      : null
  ];
}

async function pickLevel(level, value) {
  ms.line[level] = value;
  ms.query = '';
  if (level === 'material') { ms.line.grade = ''; ms.line.manufacturer = ''; ms.browse = 'grade'; }
  else if (level === 'grade') { ms.line.manufacturer = ''; ms.browse = 'manufacturer'; }
  else {
    ms.browse = null;
    const r = await api.resolveSku(ms.line.material, ms.line.grade, ms.line.manufacturer)
      .catch(() => ({ sku: null }));
    if (!ms) return;
    if (r.sku) { ms.sku = { id: r.sku.id, display: r.sku.display }; loadSku(r.sku.id); }
    else {
      ms.sku = { id: null, display: `${ms.line.material} ${ms.line.grade} · ${ms.line.manufacturer}` };
      ms.lots = []; ms.position = null;
    }
    return next();
  }
  loadBrowse();
  paint();
}

async function addLevel(level, value) {
  const body = { material: level === 'material' ? value : ms.line.material };
  if (level === 'grade') body.grade = value;
  if (level === 'manufacturer') { body.grade = ms.line.grade; body.manufacturer = value; }
  try { await api.addCatalog(body); } catch (err) { toast(err.message, { kind: 'err' }); return; }
  pickLevel(level, value);
}

// --------------------------------------------------------------- quantity
function stepQty() {
  const stock = ms.side === 'sell' && ms.position ? ms.position.stock_g : 0;
  const live = ms.entry !== '' ? Math.round(parseFloat(ms.entry || '0') * MT) : ms.qty_g;
  const over = stock > 0 && live > stock;
  const quick = stock
    ? [['25%', Math.round(stock * .25)], ['50%', Math.round(stock * .5)],
       ['75%', Math.round(stock * .75)], ['All', stock]]
    : [['5 MT', 5 * MT], ['10 MT', 10 * MT], ['20 MT', 20 * MT], ['25 MT', 25 * MT]];

  return [
    h('div', { class: 'mt-q' }, 'How much?'),
    h('div', { class: 'mt-hint' },
      stock ? `You hold ${f.qty(stock)} of ${ms.sku.display}` : ms.sku.display),
    h('div', { class: 'mnum-value' + (over ? ' warn' : '') },
      h('b', {}, (ms.entry !== '' ? ms.entry : (live / MT || 0).toString()) + ' MT'),
      h('small', {}, over
        ? `More than you hold — max ${f.qty(stock)}`
        : (live ? `${Math.round(live / 1000).toLocaleString('en-IN')} kg` : 'tap a shortcut or type'))),
    h('div', { class: 'mchips g4' }, ...quick.map(([label, g]) => h('button', {
      class: 'mchip' + (ms.qty_g === g && ms.entry === '' ? ' on' : ''),
      onclick: () => { ms.qty_g = g; ms.entry = ''; if (ms.side === 'sell') autoIfSingle(); next(); }
    }, label))),
    numpad(1, () => {
      const v = Math.round(parseFloat(ms.entry || '0') * MT);
      ms.qty_g = stock ? Math.min(v, stock) : v;
    })
  ];
}

function autoIfSingle() {
  const open = ms.lots.filter(l => l.available_g > 0);
  if (open.length === 1) ms.alloc = { [open[0].id]: ms.qty_g };
}

// --------------------------------------------------------------- rate
function stepRate() {
  const live = ms.entry !== '' ? Math.round(parseFloat(ms.entry || '0') * 100) : ms.rate_paise;
  const cost = ms.side === 'sell' ? sellCost() : 0;
  const marginRate = cost ? live - cost : 0;
  const margin = valuePaise(ms.qty_g, marginRate);
  const good = marginRate >= 0;

  return [
    h('div', { class: 'mt-q' }, ms.side === 'sell' ? 'At what rate?' : 'At what cost?'),
    h('div', { class: 'mt-hint' }, `${f.qty(ms.qty_g)} · ${ms.sku.display}`),

    ms.side === 'sell' && cost
      ? h('div', { class: 'mlive ' + (live ? (good ? 'good' : 'bad') : '') },
          h('div', { class: 'mlive-top' },
            h('b', { class: 'num ' + (good ? 'up' : 'down') }, f.inr(margin, { sign: true })),
            h('span', { class: 'num ' + (good ? 'up' : 'down') }, f.rateDelta(marginRate) + '/kg')),
          h('div', { class: 'mlive-sub' },
            `your cost ${f.rate(cost)} · sale value ${f.inr(valuePaise(ms.qty_g, live))}`))
      : (live ? h('div', { class: 'mlive' },
          h('div', { class: 'mlive-top' }, h('b', { class: 'num' }, f.inr(valuePaise(ms.qty_g, live)))),
          h('div', { class: 'mlive-sub' }, 'total value of this purchase')) : null),

    h('div', { class: 'mnum-value' },
      h('b', {}, '₹' + (ms.entry !== '' ? ms.entry : (live / 100).toFixed(2))),
      h('small', {}, 'per kg, basic rate')),

    h('div', { class: 'mchips g3' },
      ...[-100, -50, -25, 25, 50, 100].map(d => h('button', {
        class: 'mchip',
        onclick: () => {
          const base = ms.entry !== '' ? Math.round(parseFloat(ms.entry) * 100) : ms.rate_paise;
          ms.rate_paise = Math.max(0, base + d); ms.entry = ''; paint();
        }
      }, (d > 0 ? '+' : '−') + '₹' + Math.abs(d / 100).toFixed(2)))),

    numpad(2, () => { ms.rate_paise = Math.round(parseFloat(ms.entry || '0') * 100); })
  ];
}

// --------------------------------------------------------------- split
function stepSplit() {
  if (ms.editingLot) return lotEditor();
  const fl = flow();
  const done = fl.left === 0;
  const over = fl.left < 0;

  return [
    h('div', { class: 'mt-q' }, 'From which stock?'),
    h('div', { class: 'mt-hint' }, 'Tap a lot to set how much comes out of it'),
    h('div', { class: 'massign' + (done ? ' done' : '') },
      h('div', {},
        h('b', { class: 'num ' + (done ? 'up' : over ? 'down' : '') },
          done ? 'All set' : f.qty(Math.abs(fl.left))),
        h('span', {}, done ? `${f.qty(ms.qty_g)} assigned`
          : over ? 'too much — take some back' : 'still to assign')),
      h('span', { class: 'grow' }),
      fl.assigned
        ? h('button', { class: 'massign-act', onclick: () => { ms.alloc = {}; paint(); } }, 'Clear')
        : null,
      h('button', {
        class: 'massign-act',
        onclick: () => { autoAssign(); paint(); }
      }, fl.assigned ? 'Fill rest' : 'Auto')),
    ...fl.rows.map(row => h('button', {
      class: 'mlot' + (row.take > 0 ? ' on' : ''),
      onclick: () => { ms.editingLot = row.lot.id; ms.entry = ''; paint(); }
    },
      h('div', { class: 'mlot-rate num' }, f.rate(row.lot.rate_paise)),
      h('div', { class: 'mlot-main' },
        h('b', {}, row.lot.supplier_name),
        h('span', {}, `${f.qty(row.lot.available_g)} free · ${f.date(row.lot.deal_date)}`)),
      h('div', { class: 'mlot-take' },
        h('b', { class: 'num ' + (row.take ? '' : 'dim') }, row.take ? f.qty(row.take) : '—'),
        h('span', { class: row.marginRate >= 0 ? 'up' : 'down' },
          f.rateDelta(row.marginRate) + '/kg'))))
  ];
}

function lotEditor() {
  const lot = ms.lots.find(l => l.id === ms.editingLot);
  const fl = flow();
  const others = fl.assigned - (ms.alloc[lot.id] || 0);
  const room = Math.min(lot.available_g, ms.qty_g - others);
  const live = ms.entry !== '' ? Math.round(parseFloat(ms.entry || '0') * MT) : (ms.alloc[lot.id] || 0);

  const set = g => {
    ms.alloc[lot.id] = Math.max(0, Math.min(room, g));
    ms.entry = ''; ms.editingLot = null; paint();
  };

  return [
    h('div', { class: 'mt-q' }, lot.supplier_name),
    h('div', { class: 'mt-hint' },
      `${f.rate(lot.rate_paise)} · ${f.qty(lot.available_g)} free · ${f.qty(Math.max(0, ms.qty_g - others))} still to assign`),
    h('div', { class: 'mnum-value' },
      h('b', {}, (ms.entry !== '' ? ms.entry : (live / MT || 0).toString()) + ' MT'),
      h('small', {}, `${Math.round(live / 1000).toLocaleString('en-IN')} kg`)),
    h('div', { class: 'mchips g2' },
      h('button', { class: 'mchip', onclick: () => set(room) }, `Rest · ${f.qty(room)}`),
      h('button', { class: 'mchip', onclick: () => set(0) }, 'None')),
    numpad(3, () => set(Math.round(parseFloat(ms.entry || '0') * MT)), 'Set')
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
      row(sell ? 'Buyer' : 'Supplier', ms.party.name, () => jump('who')),
      row('Material', ms.sku.display, () => jump('what')),
      row('Quantity', f.qty(ms.qty_g), () => jump('qty')),
      row('Rate', f.rate(ms.rate_paise) + '/kg', () => jump('rate')),
      row('Value', f.inr(valuePaise(ms.qty_g, ms.rate_paise)))),
    sell && cost
      ? h('div', { class: 'mlive ' + (margin >= 0 ? 'good' : 'bad') },
          h('div', { class: 'mlive-top' },
            h('b', { class: 'num ' + (margin >= 0 ? 'up' : 'down') }, f.inr(margin, { sign: true })),
            h('span', { class: 'num ' + (margin >= 0 ? 'up' : 'down') }, f.rateDelta(marginRate) + '/kg')),
          h('div', { class: 'mlive-sub' }, `bought at ${f.rate(cost)}, selling at ${f.rate(ms.rate_paise)}`))
      : null,
    ms.error ? h('div', { class: 'need' }, ms.error) : null
  ];
}

function row(label, value, onEdit) {
  return h('div', { class: 'mrev-row', onclick: onEdit },
    h('span', {}, label), h('b', {}, value), onEdit ? h('em', {}, 'change') : null);
}

function jump(name) {
  const i = steps().indexOf(name);
  if (i >= 0) { ms.stepIndex = i; ms.entry = ''; loadStep(); paint(); }
}

// --------------------------------------------------------------- numpad
function numpad(id, commit, label) {
  const press = key => {
    if (key === 'del') ms.entry = ms.entry.slice(0, -1);
    else if (key === '.') { if (!ms.entry.includes('.')) ms.entry = (ms.entry || '0') + '.'; }
    else ms.entry = (ms.entry === '0' ? '' : ms.entry) + key;
    paint();
  };
  const keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '.', '0'];
  return h('div', { class: 'mpad', data: { pad: id } },
    ...keys.map(k => h('button', { class: 'mkey', onclick: () => press(k) }, k)),
    h('button', { class: 'mkey fn', onclick: () => press('del') }, '⌫'));
}

// --------------------------------------------------------------- footer
function foot(step) {
  if (step === 'who' || step === 'what') return null;

  if (step === 'review') {
    return h('div', { class: 'mt-foot' },
      h('button', {
        class: 'mt-next', disabled: !ready() || ms.busy, onclick: book
      }, ms.busy ? 'Booking…' : (ms.side === 'sell' ? 'Book sale' : 'Book purchase')));
  }

  if (step === 'split') {
    if (ms.editingLot) {
      const lot = ms.lots.find(l => l.id === ms.editingLot);
      const fl = flow();
      const others = fl.assigned - (ms.alloc[lot.id] || 0);
      const room = Math.min(lot.available_g, ms.qty_g - others);
      return h('div', { class: 'mt-foot' },
        h('button', { class: 'mt-skip', onclick: () => { ms.editingLot = null; ms.entry = ''; paint(); } }, 'Cancel'),
        h('button', {
          class: 'mt-next',
          onclick: () => {
            const v = ms.entry !== '' ? Math.round(parseFloat(ms.entry) * MT) : (ms.alloc[lot.id] || 0);
            ms.alloc[lot.id] = Math.max(0, Math.min(room, v));
            ms.entry = ''; ms.editingLot = null; paint();
          }
        }, 'Set amount'));
    }
    return h('div', { class: 'mt-foot' },
      h('button', { class: 'mt-next', disabled: flow().left !== 0, onclick: next },
        flow().left === 0 ? 'Review' : `${f.qty(Math.abs(flow().left))} ${flow().left < 0 ? 'too much' : 'to assign'}`));
  }

  // qty and rate
  const value = step === 'qty'
    ? (ms.entry !== '' ? Math.round(parseFloat(ms.entry || '0') * MT) : ms.qty_g)
    : (ms.entry !== '' ? Math.round(parseFloat(ms.entry || '0') * 100) : ms.rate_paise);

  return h('div', { class: 'mt-foot' },
    h('button', {
      class: 'mt-next', disabled: !(value > 0),
      onclick: () => {
        if (step === 'qty') {
          const stock = ms.side === 'sell' && ms.position ? ms.position.stock_g : 0;
          ms.qty_g = stock ? Math.min(value, stock) : value;
          autoIfSingle();
        } else {
          ms.rate_paise = value;
        }
        next();
      }
    }, 'Next'));
}

// --------------------------------------------------------------- commit
async function book() {
  if (!ready() || ms.busy) return;
  ms.busy = true; ms.error = ''; paint();
  const sell = ms.side === 'sell';
  try {
    const deal = await api.createDeal({
      side: ms.side,
      party_id: ms.party.id || undefined,
      party_name: ms.party.id ? undefined : ms.party.name,
      sku_id: ms.sku.id || undefined,
      material: ms.line.material || undefined,
      grade: ms.line.grade || undefined,
      manufacturer: ms.line.manufacturer || undefined,
      qty_g: ms.qty_g,
      rate_paise: ms.rate_paise,
      deal_date: ms.date,
      pins: effectiveAlloc() || undefined,
      allow_short: false,
      confirm: true
    });
    celebrate(sell, deal);
    closeTicket();
    toast(sell
      ? `Sold ${f.qty(deal.qty_g)} to ${deal.party_name}`
      : `Bought ${f.qty(deal.qty_g)} from ${deal.party_name}`,
      { action: async () => { await api.undo(); toast('Reversed'); ctx.refresh(); } });
    ctx.refresh();
  } catch (err) {
    ms.busy = false; ms.error = err.message; paint();
  }
}

function celebrate(sell, deal) {
  const el = h('div', { class: 'mdone' },
    h('div', {},
      h('b', { class: sell ? 'up' : '' },
        sell ? f.inr(deal.margin_paise || 0, { sign: true }) : f.qty(deal.qty_g)),
      h('span', {}, `${deal.ref} · ${deal.party_name}`)));
  document.body.appendChild(el);
  if (navigator.vibrate) navigator.vibrate(18);
  setTimeout(() => el.remove(), 1100);
}

// ============================================================= visualisation
// The desktop sankey could not survive a 390px screen sideways, but the thing
// it actually communicated - proportion - can. Two pieces do that job here:
//
//   1. a stacked strip on every card, so the split is visible while scanning
//   2. a converging fan you can open on one trade, drawn top-to-bottom so it
//      grows the way a phone scrolls instead of the way it cannot
//
// Colour carries the same meanings as the desktop graph: cheap stock green
// through to dear amber, and ribbons green or red by the margin they earned.

// Cost is shaded along one hue, light for cheap through to dark for dear.
// The desktop green-to-amber scale cannot be reused here: red and green mean
// loss and profit everywhere else in the app, and two perfectly profitable
// lots eighty paise apart should not read as one good and one bad.
function costShade(rate, low, high) {
  if (!isFinite(low) || !isFinite(high) || high === low) return 'hsl(199 42% 46%)';
  const t = Math.max(0, Math.min(1, (rate - low) / (high - low)));
  return `hsl(${202 - t * 10} ${38 + t * 16}% ${62 - t * 28}%)`;
}

function proportionBar(parts, opts = {}) {
  const total = parts.reduce((s, p) => s + p.qty, 0) || 1;
  return h('div', { class: 'mbar' + (opts.thin ? ' thin' : '') },
    ...parts.map(p => h('i', {
      style: { width: (p.qty / total * 100) + '%', background: p.color },
      title: p.label
    })));
}

// One trade, its sources fanning into it. Sized to whatever width it is given,
// so it can never be the thing that makes a page scroll sideways.
function fanDiagram(sources, target, opts = {}) {
  const W = 340, H = 186, BAND = 30, TOP = 10, BOT = H - BAND - 26;
  const total = sources.reduce((s, x) => s + x.qty, 0) || 1;
  const targetQty = Math.max(total, target.qty || 0);
  const gap = sources.length > 1 ? 5 : 0;
  const usable = W - gap * (sources.length - 1);

  let x = 0, tx = 0;
  const bands = [], ribbons = [], labels = [];

  for (const src of sources) {
    const w = Math.max(9, (src.qty / total) * usable);
    const tw = (src.qty / targetQty) * W;
    bands.push(svg('rect', {
      x, y: TOP, width: w, height: BAND, rx: 6, fill: src.color, 'fill-opacity': .95
    }));
    // ribbon from this source down to its slice of the trade
    const x0 = x, x1 = x + w, t0 = tx, t1 = tx + tw, my = (TOP + BAND + BOT) / 2;
    ribbons.push(svg('path', {
      d: `M${x0},${TOP + BAND} C${x0},${my} ${t0},${my} ${t0},${BOT}
          L${t1},${BOT} C${t1},${my} ${x1},${my} ${x1},${TOP + BAND} Z`,
      fill: src.good === false ? 'var(--down)' : 'var(--up)', 'fill-opacity': .22
    }));
    if (w > 46) {
      labels.push(svg('text', {
        x: x + w / 2, y: TOP + BAND / 2 + 4, 'text-anchor': 'middle',
        'font-size': 11, 'font-weight': 700, fill: '#fff'
      }, src.short));
    }
    x += w + gap; tx += tw;
  }

  const shortfall = targetQty > total ? ((targetQty - total) / targetQty) * W : 0;
  return h('div', { class: 'mfan' },
    svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'mfan-svg' },
      ...ribbons, ...bands, ...labels,
      svg('rect', {
        x: 0, y: BOT, width: W - shortfall, height: BAND, rx: 6,
        fill: opts.targetColor || 'var(--ink)', 'fill-opacity': .88
      }),
      shortfall ? svg('rect', {
        x: W - shortfall, y: BOT, width: shortfall, height: BAND, rx: 6,
        fill: 'var(--down)', 'fill-opacity': .35
      }) : null,
      svg('text', {
        x: W / 2, y: BOT + BAND / 2 + 4, 'text-anchor': 'middle',
        'font-size': 11.5, 'font-weight': 700, fill: '#fff'
      }, target.short)),
    h('div', { class: 'mfan-key' },
      ...sources.map(src => h('span', {},
        h('i', { style: { background: src.color } }), src.label)),
      sources.length > 1
        ? h('span', { class: 'mfan-note' }, 'lighter = cheaper stock')
        : null));
}

// ===================================================================== flow
// The lineage, told downwards. Each sale carries the lots it came from; each
// purchase still holding stock says where the rest of it went. Same data as the
// sankey on desktop, none of the dragging.
const RANGES = [['7d', 7], ['30d', 30], ['90d', 90], ['All', 0]];

function shiftDays(days) {
  const d = new Date(); d.setDate(d.getDate() - days);
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export async function renderMobileFlow(root, appCtx) {
  ctx = appCtx;
  if (ctx.flowDays === undefined) ctx.flowDays = 30;
  const from = ctx.flowDays ? shiftDays(ctx.flowDays) : undefined;

  const [graph, posPage] = await Promise.all([
    api.graph({ sku_id: ctx.skuFilter || undefined, date_from: from, limit: 40 }),
    api.positions({ limit: 60 })
  ]);

  const lots = new Map();
  for (const n of graph.nodes) if (n.kind === 'lot') lots.set(n.id, n);
  const sales = graph.nodes.filter(n => n.kind === 'sale').reverse();
  const bySale = new Map(), byLot = new Map();
  for (const e of graph.edges) {
    if (!bySale.has(e.target)) bySale.set(e.target, []);
    bySale.get(e.target).push(e);
    if (!byLot.has(e.source)) byLot.set(e.source, []);
    byLot.get(e.source).push(e);
  }
  const idle = graph.nodes
    .filter(n => n.kind === 'lot' && n.remaining_g > 0)
    .sort((a, b) => b.remaining_g - a.remaining_g);

  mount(root, h('div', { class: 'view' },
    h('div', { class: 'mflow', style: { paddingTop: '14px' } },
      h('div', { class: 'mchips g4', style: { padding: '0 0 12px' } },
        ...RANGES.map(([label, days]) => h('button', {
          class: 'mchip' + (ctx.flowDays === days ? ' on' : ''),
          onclick: () => { ctx.flowDays = days; renderMobileFlow(root, ctx); }
        }, label))),
      h('select', {
        class: 'mselect',
        onchange: e => {
          ctx.skuFilter = e.target.value ? +e.target.value : null;
          renderMobileFlow(root, ctx);
        }
      },
        h('option', { value: '' }, 'All materials'),
        ...posPage.positions.map(p => h('option', {
          value: p.sku_id, selected: ctx.skuFilter === p.sku_id || undefined
        }, `${p.material} — ${f.qty(p.stock_g)}`)))),

    h('div', { class: 'mlabel', style: { padding: '0 14px' } }, 'Sales',
      h('span', {}, graph.truncated ? `newest ${sales.length} of ${graph.sales_total}` : `${sales.length} in range`)),
    h('div', { class: 'mflow' },
      ...sales.map(sale => {
        const sources = bySale.get(sale.id) || [];
        const margin = sources.reduce((s, e) => s + e.margin_paise, 0);
        const costs = sources.map(e => e.cost_paise);
        const low = Math.min(...costs), high = Math.max(...costs);
        const parts = sources.map(e => ({
          qty: e.qty_g, color: costShade(e.cost_paise, low, high),
          label: (lots.get(e.source) || {}).party || ''
        }));
        if (sale.uncovered_g) parts.push({ qty: sale.uncovered_g, color: 'var(--down)', label: 'uncovered' });
        const card = h('div', { class: 'mflow-card' },
          h('div', { class: 'mflow-head' },
            h('span', { class: 'mflow-side sell' }, 'SOLD'),
            h('div', { class: 'grow' },
              h('b', {}, sale.party),
              h('span', {}, `${sale.material} · ${f.date(sale.date)}`)),
            h('div', { class: 'mflow-money' },
              h('b', { class: 'num ' + (margin >= 0 ? 'up' : 'down') },
                f.inr(margin, { sign: true, compact: true })),
              h('span', { class: 'num' }, `${f.qty(sale.qty_g)} @ ${f.rate(sale.rate_paise)}`))),
          h('div', { class: 'mflow-links' },
            ...sources.map(e => {
              const lot = lots.get(e.source) || {};
              return h('div', { class: 'mflow-link' },
                h('i', { style: { background: e.margin_paise >= 0 ? 'var(--up)' : 'var(--down)' } }),
                h('span', { class: 'qty' }, f.qty(e.qty_g)),
                h('span', { class: 'who' }, `from ${lot.party || '—'} @ ${f.rate(e.cost_paise)}`),
                h('span', { class: 'pl ' + (e.margin_paise >= 0 ? 'up' : 'down') },
                  f.rateDelta(e.margin_rate_paise)));
            }),
            sale.uncovered_g
              ? h('div', { class: 'mflow-link down' },
                  h('i', { style: { background: 'var(--down)' } }),
                  h('span', { class: 'qty' }, f.qty(sale.uncovered_g)),
                  h('span', { class: 'who' }, 'uncovered'))
              : null,
            sources.length ? null : h('div', { class: 'mflow-empty' }, 'No stock allocated.')));

        // The strip is always visible; the fan opens on demand so a long list
        // stays scannable.
        if (parts.length) {
          if (parts.length > 1) card.insertBefore(proportionBar(parts), card.querySelector('.mflow-links'));
          card.onclick = () => {
            const open = card.querySelector('.mfan');
            if (open) { open.remove(); return; }
            card.appendChild(fanDiagram(
              sources.map(e => {
                const lot = lots.get(e.source) || {};
                return {
                  qty: e.qty_g, color: costShade(e.cost_paise, low, high),
                  good: e.margin_paise >= 0,
                  short: f.qty(e.qty_g, { short: true }),
                  label: `${lot.party || '—'} @ ${f.rate(e.cost_paise)}`
                };
              }),
              { qty: sale.qty_g, short: `${sale.party} · ${f.qty(sale.qty_g)} @ ${f.rate(sale.rate_paise)}` },
              { targetColor: 'var(--up)' }));
          };
        }
        return card;
      }),
      sales.length ? null : h('div', { class: 'empty' },
        h('h3', {}, 'No sales in this window'), h('div', {}, 'Try a longer range.'))),

    idle.length ? [
      h('div', { class: 'mlabel', style: { padding: '0 14px' } }, 'Still in stock'),
      h('div', { class: 'mflow' }, ...idle.slice(0, 20).map(lot => {
        const gone = byLot.get(lot.id) || [];
        const gonePart = gone.map(e => ({
          qty: e.qty_g, color: e.margin_paise >= 0 ? 'var(--up)' : 'var(--down)', label: 'sold'
        }));
        gonePart.push({ qty: lot.remaining_g, color: 'var(--line-2)', label: 'in stock' });
        return h('div', { class: 'mflow-card' },
          h('div', { class: 'mflow-head' },
            h('span', { class: 'mflow-side lot' }, 'HELD'),
            h('div', { class: 'grow' },
              h('b', {}, lot.party),
              h('span', {}, `${lot.material} · ${lot.deal_ref} · ${f.date(lot.date)}`)),
            h('div', { class: 'mflow-money' },
              h('b', { class: 'num' }, f.qty(lot.remaining_g)),
              h('span', { class: 'num' }, `of ${f.qty(lot.qty_g)} @ ${f.rate(lot.rate_paise)}`))),
          gonePart.length > 1 ? proportionBar(gonePart, { thin: true }) : null,
          gone.length
            ? h('div', { class: 'mflow-links' }, ...gone.map(e => h('div', { class: 'mflow-link' },
                h('i', { style: { background: 'var(--line-2)' } }),
                h('span', { class: 'qty' }, f.qty(e.qty_g)),
                h('span', { class: 'who' }, 'sold on'),
                h('span', { class: 'pl ' + (e.margin_paise >= 0 ? 'up' : 'down') },
                  f.inr(e.margin_paise, { sign: true, compact: true })))))
            : null);
      }))
    ] : null));
}

// ===================================================================== tape
export async function renderMobileTape(root, appCtx) {
  ctx = appCtx;
  const state = { q: '', side: '', rows: [], matched: 0, open: null };

  const list = h('div', { class: 'mflow' });
  const more = h('button', { class: 'mmore', onclick: loadMore }, 'Load more');
  const count = h('span', {}, '');

  async function reload() {
    const page = await api.tape({ limit: 20, q: state.q, side: state.side || undefined });
    state.rows = page.deals; state.matched = page.matched; paintList();
  }
  async function loadMore() {
    const page = await api.tape({ limit: 20, offset: state.rows.length, q: state.q, side: state.side || undefined });
    state.rows = state.rows.concat(page.deals); state.matched = page.matched; paintList();
  }

  function paintList() {
    count.textContent = `${state.rows.length} of ${state.matched}`;
    more.hidden = state.rows.length >= state.matched;
    mount(list, ...state.rows.map(d => dealCard(d)), more);
    if (!state.rows.length) mount(list, h('div', { class: 'empty' }, h('h3', {}, 'No deals')));
  }

  function dealCard(d) {
    const sell = d.side === 'sell';
    const card = h('div', { class: 'mflow-card' + (d.status === 'cancelled' ? ' off' : '') },
      h('div', { class: 'mflow-head' },
        h('span', { class: 'mflow-side ' + (sell ? 'sell' : 'lot') }, sell ? 'SOLD' : 'BOUGHT'),
        h('div', { class: 'grow' },
          h('b', {}, d.party_name),
          h('span', {}, `${d.material} · ${f.date(d.deal_date)} · ${d.ref}`)),
        h('div', { class: 'mflow-money' },
          sell
            ? h('b', { class: 'num ' + (d.margin_paise >= 0 ? 'up' : 'down') },
                f.inr(d.margin_paise, { sign: true, compact: true }))
            : h('b', { class: 'num' }, f.inr(d.value_paise, { compact: true })),
          h('span', { class: 'num' }, `${f.qty(d.qty_g)} @ ${f.rate(d.rate_paise)}`))));
    card.onclick = async () => {
      if (card.dataset.open) { card.querySelector('.mflow-links').remove(); delete card.dataset.open; return; }
      const full = await api.deal(d.id);
      const lines = sell ? full.allocations : full.sold;
      const costs = lines.map(a => a.cost_paise);
      const lo = Math.min(...costs), hi = Math.max(...costs);
      if (lines.length) {
        card.appendChild(fanDiagram(
          lines.map(a => ({
            qty: a.qty_g,
            color: sell ? costShade(a.cost_paise, lo, hi) : 'hsl(214 60% 55%)',
            good: a.margin_paise >= 0,
            short: f.qty(a.qty_g, { short: true }),
            label: sell ? `${a.supplier_name} @ ${f.rate(a.cost_paise)}`
                        : `${a.customer_name} @ ${f.rate(a.sale_rate_paise)}`
          })),
          { qty: full.qty_g, short: `${full.party_name} · ${f.qty(full.qty_g)}` },
          { targetColor: sell ? 'var(--up)' : 'var(--accent)' }));
      }
      card.appendChild(h('div', { class: 'mflow-links' },
        ...lines.map(a => h('div', { class: 'mflow-link' },
          h('i', { style: { background: a.margin_paise >= 0 ? 'var(--up)' : 'var(--down)' } }),
          h('span', { class: 'qty' }, f.qty(a.qty_g)),
          h('span', { class: 'who' }, sell
            ? `from ${a.supplier_name} @ ${f.rate(a.cost_paise)}`
            : `to ${a.customer_name} @ ${f.rate(a.sale_rate_paise)}`),
          h('span', { class: 'pl ' + (a.margin_paise >= 0 ? 'up' : 'down') },
            f.inr(a.margin_paise, { sign: true, compact: true })))),
        lines.length ? null : h('div', { class: 'mflow-empty' },
          sell ? 'Nothing allocated.' : 'None of this lot sold yet.'),
        full.status === 'booked'
          ? h('button', {
              class: 'mmore', style: { marginTop: '10px' },
              onclick: async e => {
                e.stopPropagation();
                if (!window.confirm(`Cancel ${full.ref}?`)) return;
                try { await api.cancel(full.id); toast('Cancelled ' + full.ref); reload(); ctx.refresh(); }
                catch (err) { toast(err.message, { kind: 'err', ms: 8000 }); }
              }
            }, 'Cancel this deal')
          : null));
      card.dataset.open = '1';
    };
    return card;
  }

  mount(root, h('div', { class: 'view' },
    h('div', { class: 'mflow', style: { paddingTop: '14px' } },
      h('div', { class: 'msearch', style: { marginBottom: '10px' } },
        h('span', { class: 'dim' }, '⌕'),
        h('input', {
          placeholder: 'Party, material, deal ref', value: state.q,
          oninput: e => { state.q = e.target.value; clearTimeout(state.t); state.t = setTimeout(reload, 250); }
        })),
      h('div', { class: 'mchips g3', style: { padding: '0 0 6px' } },
        ...[['', 'All'], ['buy', 'Bought'], ['sell', 'Sold']].map(([v, label]) => h('button', {
          class: 'mchip' + (state.side === v ? ' on' : ''),
          onclick: () => { state.side = v; reload(); }
        }, label)))),
    h('div', { class: 'mlabel', style: { padding: '0 14px' } }, 'Trades', count),
    list));
  reload();
}

// ================================================================== position
export async function renderMobilePosition(root, skuId, appCtx) {
  ctx = appCtx;
  const p = await api.position(skuId);
  const lots = p.lots.filter(l => l.available_g > 0);

  mount(root, h('div', { class: 'view' },
    h('div', { class: 'mflow', style: { paddingTop: '14px' } },
      h('button', { class: 'mmore', style: { marginBottom: '12px' }, onclick: () => ctx.go('desk') },
        '‹ Back to desk'),
      h('div', { class: 'mpos-hero' },
        h('b', {}, p.sku.display),
        h('div', { class: 'mpos-hero-row' },
          heroStat('In stock', f.qty(p.stock_g)),
          heroStat('Avg cost', f.rate(p.cost_paise)),
          heroStat('Mark', p.mark_paise ? f.rate(p.mark_paise) : '—'),
          heroStat('Open P&L', f.inr(p.unrealised_paise, { sign: true, compact: true }),
            p.unrealised_paise >= 0 ? 'up' : 'down'))),
      h('button', {
        class: 'dock-btn sell', style: { marginTop: '12px', width: '100%' },
        onclick: () => startTicket('sell', { sku_id: skuId, material: p.sku.display })
      }, '↑ Sell from this position')),

    h('div', { class: 'mlabel', style: { padding: '0 14px' } }, 'Lots', h('span', {}, `${lots.length} open`)),
    h('div', { class: 'mflow' }, ...p.lots.map(lot => h('div', { class: 'mflow-card' },
      h('div', { class: 'mflow-head' },
        h('span', { class: 'mflow-side lot' }, f.rate(lot.rate_paise)),
        h('div', { class: 'grow' },
          h('b', {}, lot.supplier_name),
          h('span', {}, `${lot.deal_ref} · ${f.date(lot.deal_date)}`)),
        h('div', { class: 'mflow-money' },
          h('b', { class: 'num' }, f.qty(lot.available_g)),
          h('span', { class: 'num' }, `of ${f.qty(lot.qty_g)}`))),
      lot.outflows && lot.outflows.length
        ? h('div', { class: 'mflow-links' }, ...lot.outflows.map(o => h('div', { class: 'mflow-link' },
            h('i', { style: { background: o.margin_paise >= 0 ? 'var(--up)' : 'var(--down)' } }),
            h('span', { class: 'qty' }, f.qty(o.qty_g)),
            h('span', { class: 'who' }, `to ${o.customer_name} @ ${f.rate(o.sale_rate_paise)}`),
            h('span', { class: 'pl ' + (o.margin_paise >= 0 ? 'up' : 'down') },
              f.inr(o.margin_paise, { sign: true, compact: true })))))
        : null)))));
}

function heroStat(label, value, tone) {
  return h('div', {}, h('span', {}, label), h('b', { class: 'num ' + (tone || '') }, value));
}

// A proper sheet. Three fields deserve better than three browser prompts.
function sheet(title, fields, onSave) {
  const inputs = {};
  const body = fields.map(fl => {
    if (fl.type === 'toggle') {
      const btn = h('button', {
        class: 'msheet-toggle' + (fl.value ? ' on' : ''),
        onclick: () => { btn.classList.toggle('on'); }
      }, fl.label);
      inputs[fl.key] = () => btn.classList.contains('on');
      return btn;
    }
    const input = h('input', {
      type: fl.type || 'text', value: fl.value || '', placeholder: fl.placeholder || '',
      inputmode: fl.type === 'tel' ? 'tel' : undefined
    });
    inputs[fl.key] = () => input.value.trim();
    return h('label', { class: 'msheet-field' }, h('span', {}, fl.label), input);
  });

  const toggles = body.filter(el => el.classList && el.classList.contains('msheet-toggle'));
  const rest = body.filter(el => !toggles.includes(el));
  const overlay = h('div', { class: 'msheet' },
    h('div', { class: 'msheet-card' },
      h('div', { class: 'msheet-title' }, title),
      ...rest,
      toggles.length ? h('div', { class: 'msheet-toggles' }, ...toggles) : null,
      h('div', { class: 'msheet-foot' },
        h('button', { class: 'mt-skip', onclick: () => overlay.remove() }, 'Cancel'),
        h('button', {
          class: 'mt-next',
          onclick: async () => {
            const values = {};
            for (const [k, get] of Object.entries(inputs)) values[k] = get();
            try { await onSave(values); overlay.remove(); }
            catch (err) { toast(err.message, { kind: 'err', ms: 8000 }); }
          }
        }, 'Save'))));
  document.body.appendChild(overlay);
  const first = overlay.querySelector('input');
  if (first) setTimeout(() => first.focus(), 60);
}

// ==================================================================== setup
export async function renderMobileSetup(root, appCtx) {
  ctx = appCtx;
  const [{ tree }, { parties }] = await Promise.all([api.catalogTree(), api.partyList()]);
  const open = ctx.setupOpen || (ctx.setupOpen = {});

  const add = async (body, what) => {
    try { await api.addCatalog(body); toast(`Added ${what}`); renderMobileSetup(root, ctx); }
    catch (err) { toast(err.message, { kind: 'err', ms: 8000 }); }
  };
  const remove = async (body, what) => {
    if (!window.confirm(`Remove ${what}?`)) return;
    try { await api.removeCatalog(body); toast(`Removed ${what}`); renderMobileSetup(root, ctx); }
    catch (err) { toast(err.message, { kind: 'err', ms: 8000 }); }
  };
  const ask = (label, run) => {
    const v = window.prompt(label);
    if (v && v.trim()) run(v.trim());
  };

  const editParty = (p) => sheet(p ? 'Edit party' : 'Add buyer or seller', [
    { key: 'name', label: 'Name', value: p ? p.name : '', placeholder: 'Krishna Dehgam' },
    { key: 'phone', label: 'Phone', type: 'tel', value: p ? (p.phone || '') : '',
      placeholder: '+91 98250 00000' },
    { key: 'city', label: 'Location', value: p ? (p.city || '') : '', placeholder: 'Ahmedabad' },
    { key: 'is_customer', label: 'Buys from me', type: 'toggle', value: p ? !!p.is_customer : true },
    { key: 'is_supplier', label: 'Sells to me', type: 'toggle', value: p ? !!p.is_supplier : false }
  ], async values => {
    if (!values.name) throw new Error('Name is required');
    await api.partySave({ ...values, id: p ? p.id : undefined });
    toast(p ? 'Saved' : `Added ${values.name}`);
    renderMobileSetup(root, ctx);
  });

  const dropParty = async (p) => {
    if (!window.confirm(`Remove ${p.name}?`)) return;
    try { await api.partyRemove(p.id); toast(`Removed ${p.name}`); renderMobileSetup(root, ctx); }
    catch (err) { toast(err.message, { kind: 'err', ms: 8000 }); }
  };

  mount(root, h('div', { class: 'view' },
    h('div', { class: 'mlabel', style: { padding: '14px 14px 10px' } },
      'Buyers & sellers', h('span', {}, `${parties.length}`)),
    h('div', { class: 'mflow' },
      h('button', { class: 'mmore', style: { marginBottom: '10px' }, onclick: () => editParty(null) },
        '+ Add buyer or seller'),
      ...parties.map(p => h('div', { class: 'mflow-card' },
        h('div', { class: 'mflow-head' },
          h('div', { class: 'grow', onclick: () => editParty(p) },
            h('b', {}, p.name),
            h('span', {}, [p.phone, p.city].filter(Boolean).join(' · ') || 'no contact details')),
          h('div', { class: 'mflow-money' },
            h('b', { class: 'num' }, p.deal_count ? `${p.deal_count}` : '—'),
            h('span', {}, p.deal_count ? 'deals' : 'unused')),
          h('button', { class: 'msetup-x', onclick: () => dropParty(p) }, '×')),
        h('div', { class: 'mparty-tags' },
          p.is_customer ? h('span', { class: 'tag up' }, 'Buyer') : null,
          p.is_supplier ? h('span', { class: 'tag' }, 'Seller') : null,
          !p.is_customer && !p.is_supplier ? h('span', { class: 'tag' }, 'No role set') : null)))),

    h('div', { class: 'mlabel', style: { padding: '18px 14px 10px' } }, 'Materials'),
    h('div', { class: 'mflow', style: { paddingTop: '0' } },
      h('div', { class: 'mt-hint', style: { marginBottom: '10px' } },
        'Manufacturer means who made the resin, not who you trade with.'),
      h('button', {
        class: 'mmore', onclick: () => ask('New material, e.g. LLDPE', v => add({ material: v }, v))
      }, '+ Add material')),

    h('div', { class: 'mflow' }, ...tree.map(m => h('div', { class: 'mflow-card' },
      h('div', {
        class: 'mflow-head', onclick: () => { open[m.material] = !open[m.material]; renderMobileSetup(root, ctx); }
      },
        h('span', { class: 'mflow-side lot' }, open[m.material] ? '▾' : '▸'),
        h('div', { class: 'grow' },
          h('b', {}, m.material),
          h('span', {}, `${m.grades.length} grade${m.grades.length === 1 ? '' : 's'}`)),
        h('div', { class: 'mflow-money' },
          m.stock_g ? h('b', { class: 'num up' }, f.qty(m.stock_g)) : null)),

      open[m.material]
        ? h('div', { class: 'mflow-links' },
            ...m.grades.map(g => h('div', {},
              h('div', { class: 'mflow-link', style: { fontWeight: '620' } },
                h('i', { style: { background: 'var(--accent)' } }),
                h('span', { class: 'who' }, g.grade),
                h('span', { class: 'qty' }, g.stock_g ? f.qty(g.stock_g) : ''),
                h('button', {
                  class: 'msetup-x',
                  onclick: () => remove({ material: m.material, grade: g.grade }, `${m.material} ${g.grade}`)
                }, '×')),
              ...g.manufacturers.map(k => h('div', { class: 'mflow-link', style: { paddingLeft: '14px' } },
                h('i', { style: { background: 'var(--line-2)' } }),
                h('span', { class: 'who' }, k.manufacturer),
                h('span', { class: 'pl' }, k.deals ? `${k.deals} deals` : 'unused'),
                h('button', {
                  class: 'msetup-x',
                  onclick: () => remove(
                    { material: m.material, grade: g.grade, manufacturer: k.manufacturer },
                    k.manufacturer)
                }, '×'))),
              h('button', {
                class: 'mmore', style: { margin: '4px 0 10px 14px' },
                onclick: () => ask(`New manufacturer for ${m.material} ${g.grade}`,
                  v => add({ material: m.material, grade: g.grade, manufacturer: v }, v))
              }, '+ Manufacturer'))),
            h('button', {
              class: 'mmore',
              onclick: () => ask(`New grade for ${m.material}`,
                v => add({ material: m.material, grade: v }, v))
            }, '+ Grade'))
        : null)))));
}
