// The desk: what do I hold, what did I make, what needs me.
import { h, mount, costTint, pnlClass, countUp, searchBar } from './ui.js';
import { api } from './api.js';
import { makeFilters } from './filters.js';
import { pagedList, counter } from './lists.js';
import * as f from './fmt.js';

export function renderTicker(bar, s) {
  const cells = [
    ['Realised today', s.realised_today_paise, true],
    ['Open P&L', s.unrealised_paise, true],
    ['Stock', s.stock_g, false]
  ];
  mount(bar, ...cells.map(([label, value, money]) => {
    const b = h('b', { class: 'num ' + (money ? pnlClass(value) : '') });
    b.dataset.value = 0;
    countUp(b, value, v => money ? f.inr(v, { sign: true, compact: true }) : f.qty(v));
    return h('div', { class: 'tick' }, b, h('span', {}, label));
  }));
}

// Filter options come from the same master records the ticket uses.
export async function loadStockFilterOptions(ui, { suppliers = false } = {}) {
  const { material_id } = ui.state;
  const [wh, mats, makers, sups] = await Promise.all([
    api.warehouses({ limit: 200 }), api.materials({ has_products: true, limit: 200 }),
    api.manufacturers({ limit: 200 }), suppliers ? api.parties({ holding: true, limit: 200 }) : null
  ]);
  ui.setOptions('warehouse_id', wh.items.map(w => ({ value: w.id, label: w.name })));
  ui.setOptions('material_id', mats.items.map(m => ({ value: m.id, label: m.name })));
  ui.setOptions('manufacturer_id', makers.items.map(k => ({ value: k.id, label: k.name })));
  if (sups) ui.setOptions('supplier_id', sups.items.map(p => ({ value: p.id, label: p.name })));
  ui.setOptions('grade_id', material_id
    ? (await api.grades({ material_id, limit: 200 })).items.map(g => ({ value: g.id, label: g.name }))
    : []);
}

export const STOCK_FILTERS = [
  { key: 'warehouse_id', label: 'Warehouse', any: 'All warehouses' },
  { key: 'material_id', label: 'Material', clears: ['grade_id'] },
  { key: 'grade_id', label: 'Grade', needs: 'material_id', needsLabel: 'material' },
  { key: 'manufacturer_id', label: 'Manufacturer' }
];

export function renderDesk(root, s, ctx) {
  const cards = [
    ['Stock value', f.inr(s.stock_value_paise, { compact: true }),
      `${f.qty(s.stock_g)} across ${s.products} product${s.products === 1 ? '' : 's'}`, 'var(--ink)'],
    ['Open P&L', f.inr(s.unrealised_paise, { sign: true, compact: true }),
      "if sold at today's marks", s.unrealised_paise >= 0 ? 'var(--up)' : 'var(--down)'],
    ['Realised today', f.inr(s.realised_today_paise, { sign: true, compact: true }),
      `${s.buys_today} buys · ${s.sells_today} sells`, 'var(--up)'],
    ['Realised · 30d', f.inr(s.realised_month_paise, { sign: true, compact: true }),
      `all time ${f.inr(s.realised_paise, { compact: true })}`,
      s.realised_month_paise >= 0 ? 'var(--up)' : 'var(--down)']
  ];

  let query = '', filters = {};
  const count = counter();
  const list = pagedList({
    pageSize: 12, className: 'grid',
    load: p => api.positions(p),
    row: p => positionCard(p, ctx),
    onPage: count.update,
    empty: p => h('div', { class: 'empty' },
      h('h3', {}, p.q ? `Nothing matches "${p.q}"` : 'No stock here'),
      h('div', {}, p.q || Object.keys(filters).length ? 'Try another search or clear a filter.'
                                                     : 'Press B to book your first purchase.'))
  });
  const reload = () => list.reload({ q: query, ...filters });

  const ui = makeFilters({
    fields: [...STOCK_FILTERS, { key: 'supplier_id', label: 'Bought from', any: 'Any supplier' }],
    onChange: v => { filters = v; loadStockFilterOptions(ui, { suppliers: true }); reload(); }
  });
  loadStockFilterOptions(ui, { suppliers: true });

  mount(root, h('div', { class: 'view' },
    h('div', { class: 'pnl-row' }, ...cards.map(([label, value, sub, tone]) =>
      h('div', { class: 'pnl' },
        h('div', { class: 'label' }, label),
        h('div', { class: 'value num', style: { color: tone } }, value),
        h('div', { class: 'sub' }, sub)))),
    s.attention.length ? h('div', { class: 'alerts' },
      ...s.attention.map(a => h('div', { class: 'alert ' + a.level, onclick: () => a.deal_id && ctx.openDeal(a.deal_id) },
        h('i', { class: 'bar' }),
        h('div', {}, h('b', {}, a.title), h('div', {}, h('span', {}, a.detail)))))) : null,
    h('div', { class: 'section-head' }, h('h2', {}, 'Positions'), h('i', { class: 'rule' }), count),
    searchBar('Search product, grade, manufacturer, supplier or warehouse…', t => { query = t; reload(); }),
    ui.el,
    list.el));
  reload();
}

// The lot ladder is the heart of the card: every lot is one segment, width
// proportional to quantity, colour to relative cost.
export function ladder(pos, { height = 34, labels = true } = {}) {
  const total = pos.lots.reduce((s, l) => s + l.available_g, 0) || 1;
  const low = pos.cost_low_paise, high = pos.cost_high_paise;
  return h('div', { class: 'ladder', style: { height: height + 'px' } },
    ...pos.lots.map(l => {
      const w = (l.available_g / total) * 100;
      return h('div', {
        class: 'rung',
        style: { width: w + '%', background: costTint(l.rate_paise, low, high) },
        title: `${l.supplier_name} · ${f.qty(l.available_g)} @ ${f.rate(l.rate_paise)} · ${l.warehouse}\n${l.deal_ref} · ${f.date(l.deal_date)}`
      }, labels && w > 13 ? h('span', { class: 'rung-rate' }, f.rate(l.rate_paise)) : null);
    }));
}

export function whTags(list, max = 4) {
  return h('div', { class: 'wh-tags' },
    ...list.slice(0, max).map(w => h('span', { class: 'wh-tag' }, w.name, h('b', {}, f.qty(w.stock_g, { short: true })))),
    list.length > max ? h('span', { class: 'dim' }, `+${list.length - max}`) : null);
}

function positionCard(p, ctx) {
  const gain = p.unrealised_paise;
  return h('div', { class: 'pos', onclick: () => ctx.openPosition(p.product_id) },
    h('div', { class: 'pos-head' },
      h('div', {},
        h('div', { class: 'pos-name' }, p.product),
        h('div', { class: 'pos-meta' },
          p.suppliers.slice(0, 3).join(' · ') + (p.suppliers.length > 3 ? ` +${p.suppliers.length - 3}` : ''))),
      h('div', { class: 'pos-qty' },
        h('b', { class: 'num' }, f.qty(p.stock_g)),
        h('span', {}, `${p.open_lots} lot${p.open_lots === 1 ? '' : 's'}`))),
    whTags(p.warehouses),
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
      h('button', { class: 'pos-sell', onclick: e => { e.stopPropagation(); ctx.sell(p); } }, 'Sell')));
}
