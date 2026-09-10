// Stock: the physical book. What sits in which warehouse, what it cost, and
// every movement - purchase, sale, transfer, adjustment - that put it there.
import { h, mount, toast, searchBar } from './ui.js';
import * as f from './fmt.js';
import { api } from './api.js';
import { makeFilters } from './filters.js';
import { pagedList, counter } from './lists.js';
import { STOCK_FILTERS, loadStockFilterOptions } from './desk.js';
import { lotActions } from './tape.js';
import { warehouseForm } from './forms.js';

export const MOVE_LABEL = {
  receipt: 'Purchase', sale: 'Sale', transfer_in: 'Transfer in', transfer_out: 'Transfer out',
  loss: 'Written off', gain: 'Found'
};

export function renderStock(root, ctx) {
  let query = '', filters = {};

  const whs = pagedList({
    pageSize: 8, className: 'wh-grid',
    load: p => api.warehouses(p),
    row: w => h('button', {
      class: 'wh-card' + (String(filters.warehouse_id || '') === String(w.id) ? ' on' : ''),
      onclick: () => ui.set('warehouse_id', String(filters.warehouse_id) === String(w.id) ? '' : w.id)
    },
      h('div', { class: 'wh-name' }, w.name),
      h('div', { class: 'wh-addr' }, w.address || 'no address'),
      h('div', { class: 'wh-num num' }, f.qty(w.stock_g)),
      h('div', { class: 'wh-sub' }, `${f.inr(w.stock_value_paise, { compact: true })} · ${w.products} product${w.products === 1 ? '' : 's'}`)),
    empty: () => h('div', { class: 'empty small' }, 'No warehouses yet — add one to start receiving stock.')
  });

  const count = counter();
  const list = pagedList({
    pageSize: 25, className: 'stock',
    head: ['Product', 'Warehouse', { label: 'In stock (MT)', cls: 'r' }, { label: 'Lots', cls: 'r' },
           { label: 'Avg cost (₹/MT)', cls: 'r' }, { label: 'Value', cls: 'r' }, { label: 'Mark (₹/MT)', cls: 'r' }],
    load: p => api.stock(p),
    row: r => stockRow(r, afterMove),
    onPage: count.update,
    empty: () => h('div', { class: 'empty' }, h('h3', {}, 'Nothing in stock here'))
  });

  const ledger = pagedList({
    pageSize: 20, className: 'ledger',
    head: ['Date', 'Movement', 'Ref', 'Product', 'Warehouse', 'Counterparty / note',
           { label: 'In (MT)', cls: 'r' }, { label: 'Out (MT)', cls: 'r' }],
    load: p => api.moves(p),
    row: m => ledgerRow(m, { product: true, warehouse: true, balance: false })
  });

  const reload = () => {
    list.reload({ q: query, ...filters });
    ledger.reload({ warehouse_id: filters.warehouse_id });
    whs.repaint();
  };
  function afterMove() { reload(); whs.reload(); ctx.refresh(); }

  const ui = makeFilters({
    fields: STOCK_FILTERS,
    onChange: v => { filters = v; loadStockFilterOptions(ui); reload(); }
  });
  loadStockFilterOptions(ui);

  mount(root, h('div', { class: 'view' },
    h('div', { class: 'section-head' }, h('h2', {}, 'Warehouses'), h('i', { class: 'rule' }),
      h('button', { class: 'chip small', onclick: async () => { if (await warehouseForm()) { whs.reload(); loadStockFilterOptions(ui); } } },
        '+ Add warehouse')),
    whs.el,
    h('div', { class: 'section-head', style: { marginTop: '26px' } },
      h('h2', {}, 'Stock by warehouse'), h('i', { class: 'rule' }), count),
    searchBar('Search product, grade, manufacturer or warehouse…', t => { query = t; reload(); }),
    ui.el, list.el,
    h('div', { class: 'section-head', style: { marginTop: '30px' } },
      h('h2', {}, 'Movements'), h('i', { class: 'rule' }),
      h('span', { class: 'dim' }, 'purchases, sales, transfers and adjustments')),
    ledger.el));
  whs.reload({});
  reload();
}

function stockRow(r, after) {
  let open = null;
  const tr = h('tr', { class: 'click' },
    h('td', { class: 'product' }, h('b', {}, `${r.material} ${r.grade}`), h('small', {}, r.manufacturer)),
    h('td', {}, r.warehouse),
    h('td', { class: 'r mono strong' }, f.mt(r.stock_g)),
    h('td', { class: 'r mono' }, r.lots),
    h('td', { class: 'r mono' }, f.perMt(r.cost_paise).toLocaleString('en-IN')),
    h('td', { class: 'r mono' }, f.inr(r.stock_value_paise, { compact: true })),
    h('td', { class: 'r mono' }, r.mark_paise ? f.perMt(r.mark_paise).toLocaleString('en-IN') : '—'));
  tr.addEventListener('click', async () => {
    if (open) { open.remove(); open = null; tr.classList.remove('open'); return; }
    open = h('tr', { class: 'deal-detail' }, h('td', { colspan: 7 }, await stockDetail(r, after)));
    tr.after(open); tr.classList.add('open');
  });
  return tr;
}

// One product in one warehouse: its lots, with Move and Adjust, and its own
// ledger with a running balance that ends at today's figure.
export async function stockDetail(r, after) {
  const { items: lots } = await api.lots(r.product_id, r.warehouse_id);
  const ledger = pagedList({
    pageSize: 10, className: 'ledger',
    head: ['Date', 'Movement', 'Ref', 'Counterparty / note', { label: 'In', cls: 'r' },
           { label: 'Out', cls: 'r' }, { label: 'Balance (MT)', cls: 'r' }],
    load: p => api.moves({ ...p, product_id: r.product_id, warehouse_id: r.warehouse_id }),
    row: m => ledgerRow(m, { balance: true })
  });
  ledger.reload({});
  return h('div', { class: 'detail' },
    h('div', { class: 'lineage-title' }, `${r.product} in ${r.warehouse}`),
    h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl lots' },
      h('thead', {}, h('tr', {}, ...['Purchase', 'Supplier', 'Date', 'Cost (₹/MT)', 'Bought', 'Left', ''].map(c => h('th', {}, c)))),
      h('tbody', {}, ...lots.map(l => h('tr', {},
        h('td', { class: 'mono' }, l.deal_ref, l.parent_lot_id ? h('small', {}, 'moved in') : null),
        h('td', {}, l.supplier_name),
        h('td', {}, f.date(l.deal_date)),
        h('td', { class: 'mono' }, f.perMt(l.rate_paise).toLocaleString('en-IN')),
        h('td', { class: 'mono' }, f.mt(l.qty_g)),
        h('td', { class: 'mono strong' }, f.mt(l.available_g)),
        h('td', {}, lotActions({ ...l, product: r.product }, after))))))),
    h('div', { class: 'lineage-title', style: { marginTop: '16px' } }, 'Ledger'),
    ledger.el);
}

export function ledgerRow(m, { product = false, warehouse = false, balance = false } = {}) {
  const into = m.qty_g > 0;
  const who = m.kind === 'transfer_in' ? `from ${m.counterparty}`
    : m.kind === 'transfer_out' ? `to ${m.counterparty}` : (m.counterparty || m.note || '');
  return h('tr', { class: 'mv ' + m.kind },
    h('td', { class: 'nowrap' }, f.date(m.date)),
    h('td', {}, h('span', { class: 'mv-tag ' + m.kind }, MOVE_LABEL[m.kind] || m.kind)),
    h('td', { class: 'mono' }, m.ref || ''),
    product ? h('td', {}, m.product) : null,
    warehouse ? h('td', {}, m.warehouse) : null,
    h('td', {}, who, m.note && m.counterparty ? h('small', {}, m.note) : null),
    h('td', { class: 'r mono up' }, into ? f.mt(m.qty_g) : ''),
    h('td', { class: 'r mono down' }, into ? '' : f.mt(-m.qty_g)),
    balance ? h('td', { class: 'r mono strong' }, f.mt(m.balance_g)) : null);
}

export { toast };
