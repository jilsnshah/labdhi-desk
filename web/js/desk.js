// The desk: what do I hold, what did I make, what needs me.
import { h, mount, costTint, pnlClass, countUp, searchBar, moreBar } from './ui.js';
import { api } from './api.js';
import { makeFilters } from './filters.js';
import * as f from './fmt.js';

export function renderTicker(bar, desk) {
  const cells = [
    ['Realised today', desk.realised_today_paise, true],
    ['Open P&L', desk.unrealised_paise, true],
    ['Stock', desk.stock_g, false]
  ];
  mount(bar,
    ...cells.map(([label, value, money]) => {
      const b = h('b', { class: 'num ' + (money ? pnlClass(value) : '') });
      b.dataset.value = 0;
      countUp(b, value, v => money ? f.inr(v, { sign: true, compact: true }) : f.qty(v));
      return h('div', { class: 'tick' }, b, h('span', {}, label));
    })
  );
}

// The positions list is paged and searchable: with a thousand open lines you
// can neither render them all nor scroll to the one you want.
let deskState = { query: '', filters: {}, rows: [], matched: 0, on: null };

export function renderDesk(root, desk, on) {
  deskState = { query: desk.query || '', filters: {}, rows: desk.positions,
                matched: desk.positions_matched, on };

  const cards = [
    ['Stock value', f.inr(desk.stock_value_paise, { compact: true }),
      `${f.qty(desk.stock_g)} across ${desk.materials} line${desk.materials === 1 ? '' : 's'}`, 'var(--ink)'],
    ['Open P&L', f.inr(desk.unrealised_paise, { sign: true, compact: true }),
      'if sold at today\'s marks', desk.unrealised_paise >= 0 ? 'var(--up)' : 'var(--down)'],
    ['Realised today', f.inr(desk.realised_today_paise, { sign: true, compact: true }),
      `${desk.buys_today} buys · ${desk.sells_today} sells`, 'var(--up)'],
    ['Realised · 30d', f.inr(desk.realised_month_paise, { sign: true, compact: true }),
      `all time ${f.inr(desk.realised_paise, { compact: true })}`,
      desk.realised_month_paise >= 0 ? 'var(--up)' : 'var(--down)']
  ];

  const grid = h('div', { class: 'grid' });
  const more = moreBar(loadMore);
  const search = searchBar('Search material, grade, manufacturer or supplier…', runSearch,
    { value: deskState.query });

  const filters = makeFilters({
    fields: [
      { key: 'material', label: 'Material', clears: ['grade', 'manufacturer'] },
      { key: 'grade', label: 'Grade', needs: 'material', clears: ['manufacturer'] },
      { key: 'manufacturer', label: 'Manufacturer', needs: 'grade' },
      { key: 'supplier_id', label: 'Bought from', any: 'Any supplier' }
    ],
    onChange: applyFilters
  });
  deskState.filters_ui = filters;
  loadFilterOptions();
  const counter = h('span', { class: 'dim', style: { fontSize: '14px' } }, '');

  deskState.grid = grid; deskState.more = more; deskState.counter = counter;

  mount(root,
    h('div', { class: 'view' },
      h('div', { class: 'pnl-row' }, ...cards.map(([label, value, sub, tone]) =>
        h('div', { class: 'pnl' },
          h('div', { class: 'label' }, label),
          h('div', { class: 'value num', style: { color: tone } }, value),
          h('div', { class: 'sub' }, sub)))),

      desk.attention.length ? h('div', { class: 'alerts' },
        ...desk.attention.map(a => h('div', { class: 'alert ' + a.level, onclick: () => a.deal_id && on.openDeal(a.deal_id) },
          h('i', { class: 'bar' }),
          h('div', {}, h('b', {}, a.title), h('div', {}, h('span', {}, a.detail)))))) : null,

      h('div', { class: 'section-head' },
        h('h2', {}, 'Positions'), h('i', { class: 'rule' }), counter),
      search,
      filters.el,
      grid,
      more));

  paintPositions();
  return root;
}

function paintPositions() {
  const { rows, grid, more, matched, counter, query } = deskState;
  mount(grid, ...rows.map(p => positionCard(p, deskState.on)));
  if (!rows.length) {
    mount(grid, h('div', { class: 'empty' },
      h('h3', {}, query ? `Nothing matches "${query}"` : 'No stock yet'),
      h('div', {}, query ? 'Try a material, grade, manufacturer or supplier.'
                         : 'Press B to book your first purchase.')));
  }
  counter.textContent = `${rows.length} of ${matched} shown`;
  more.update(rows.length, matched);
}

// The material tree cascades inside the filter bar exactly as it does in the
// ticket: pick PVC and the grade list is only PVC's grades.
async function loadFilterOptions() {
  const ui = deskState.filters_ui;
  const { material, grade } = ui.state;
  const [mats, parties] = await Promise.all([
    api.catalog('material'), api.parties('', 'supplier')
  ]);
  ui.setOptions('material', mats.options.map(o => ({ value: o.value, label: o.value })));
  ui.setOptions('supplier_id', parties.results.map(p => ({ value: p.id, label: p.name })));
  if (material) {
    const g = await api.catalog('grade', material);
    ui.setOptions('grade', g.options.map(o => ({ value: o.value, label: o.value })));
  } else ui.setOptions('grade', []);
  if (material && grade) {
    const k = await api.catalog('manufacturer', material, grade);
    ui.setOptions('manufacturer', k.options.map(o => ({ value: o.value, label: o.value })));
  } else ui.setOptions('manufacturer', []);
}

function applyFilters(values) {
  deskState.filters = values;
  loadFilterOptions();
  reloadPositions();
}

async function reloadPositions() {
  const d = await api.desk({ q: deskState.query, ...deskState.filters });
  deskState.rows = d.positions;
  deskState.matched = d.positions_matched;
  paintPositions();
}

function runSearch(term) {
  deskState.query = term;
  return reloadPositions();
}

async function loadMore() {
  const { more, rows, query, filters } = deskState;
  more.busy(true);
  try {
    const d = await api.desk({ q: query, offset: rows.length, ...filters });
    deskState.rows = rows.concat(d.positions);
    deskState.matched = d.positions_matched;
    paintPositions();
  } finally {
    more.busy(false);
  }
}

// The lot ladder is the heart of the card: every purchase lot is one segment,
// width proportional to quantity, colour to relative cost. The trader sees his
// cost structure without reading a single table row.
export function ladder(pos, { height = 34, labels = true } = {}) {
  const total = pos.lots.reduce((s, l) => s + l.available_g, 0) || 1;
  const low = pos.cost_low_paise, high = pos.cost_high_paise;
  return h('div', { class: 'ladder', style: { height: height + 'px' } },
    ...pos.lots.map(l => {
      const w = (l.available_g / total) * 100;
      return h('div', {
        class: 'rung',
        style: { width: w + '%', background: costTint(l.rate_paise, low, high) },
        title: `${l.supplier_name} · ${f.qty(l.available_g)} @ ${f.rate(l.rate_paise)}\n${l.deal_ref} · ${f.date(l.deal_date)}`
      }, labels && w > 13 ? h('span', { class: 'rung-rate' }, f.rate(l.rate_paise)) : null);
    })
  );
}

function positionCard(p, on) {
  const gain = p.unrealised_paise;
  return h('div', { class: 'pos', onclick: () => on.openPosition(p.sku_id) },
    h('div', { class: 'pos-head' },
      h('div', {},
        h('div', { class: 'pos-name' }, p.material),
        h('div', { class: 'pos-meta' },
          p.suppliers.slice(0, 3).join(' · ') + (p.suppliers.length > 3 ? ` +${p.suppliers.length - 3}` : ''))),
      h('div', { class: 'pos-qty' },
        h('b', { class: 'num' }, f.qty(p.stock_g)),
        h('span', {}, `${p.open_lots} lot${p.open_lots === 1 ? '' : 's'}`))),

    ladder(p),

    h('div', { class: 'ladder-legend' },
      ...p.lots.slice(0, 4).map(l => h('span', {},
        h('i', { style: { background: costTint(l.rate_paise, p.cost_low_paise, p.cost_high_paise) } }),
        `${l.supplier_name} ${f.qty(l.available_g, { short: true })} @ ${f.rate(l.rate_paise)}`)),
      p.lots.length > 4 ? h('span', { class: 'dim' }, `+${p.lots.length - 4} more`) : null),

    h('div', { class: 'pos-foot' },
      h('div', { class: 'stat' }, h('b', { class: 'num' }, f.rate(p.cost_paise)), h('span', {}, 'avg cost')),
      h('div', { class: 'stat' },
        h('b', { class: 'num' }, p.mark_paise ? f.rate(p.mark_paise) : '—'), h('span', {}, 'mark')),
      h('div', { class: 'stat' },
        h('b', { class: 'num ' + pnlClass(gain) }, f.inr(gain, { sign: true, compact: true })),
        h('span', {}, 'open p&l')),
      h('button', {
        class: 'pos-sell',
        onclick: e => { e.stopPropagation(); on.sell(p); }
      }, 'Sell')));
}
