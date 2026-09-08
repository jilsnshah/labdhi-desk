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

import { h, mount, toast } from './ui.js';
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
  const repeats = recentCombos(ctx.boot ? ctx.boot.tape : []);

  // No stat cards: the header strip above already carries stock, today's P&L
  // and book value, and a phone screen is better spent on what he can act on.
  mount(root, h('div', { class: 'view' },
    desk.attention && desk.attention.length
      ? h('div', { class: 'mdesk' },
          ...desk.attention.slice(0, 3).map(a => h('div', { class: 'alert ' + a.level },
            h('i', { class: 'bar' }),
            h('div', {}, h('b', {}, a.title), h('div', {}, h('span', {}, a.detail))))))
      : null,

    repeats.length
      ? [h('div', { class: 'mlabel', style: { padding: '0 14px' } }, 'Do it again'),
         h('div', { class: 'mrepeat' }, ...repeats.map(r => h('button', {
           class: 'mrep ' + r.side, onclick: () => startTicket(r.side, r)
         },
           h('i', {}, r.side === 'buy' ? 'BUY' : 'SELL'),
           h('b', {}, r.party_name),
           h('span', {}, r.material))))]
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

// The same party and grade come round again and again; offering the last few
// as one tap removes two whole screens from the common case.
function recentCombos(tape) {
  const seen = new Set(), out = [];
  for (const d of tape || []) {
    if (d.status !== 'booked') continue;
    const key = `${d.side}|${d.party_id}|${d.sku_id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({
      side: d.side, party_id: d.party_id, party_name: d.party_name,
      sku_id: d.sku_id, material: d.material, rate_paise: d.rate_paise
    });
    if (out.length === 6) break;
  }
  return out;
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
    h('div', { class: 'mchips' }, ...quick.map(([label, g]) => h('button', {
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

    h('div', { class: 'mchips' },
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
    h('div', { class: 'mchips' },
      h('button', { class: 'mchip', onclick: () => set(room) },
        `All the rest (${f.qty(room)})`),
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
