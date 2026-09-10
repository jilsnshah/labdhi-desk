// Tape = the book as a table, one row per sauda. Position = one product,
// drilled down to which kilos of which lot, in which warehouse, went where.
import { h, mount, costTint, pnlClass, toast, searchBar } from './ui.js';
import * as f from './fmt.js';
import { api } from './api.js';
import { ladder, whTags } from './desk.js';
import { makeFilters } from './filters.js';
import { pagedList, counter } from './lists.js';
import { showNode } from './flow.js';
import { transferForm, adjustForm } from './forms.js';

const COLUMNS = ['Sauda No.', 'Date', 'Type', 'Party', 'Product / Grade',
  { label: 'Qty (MT)', cls: 'r' }, { label: 'Rate (₹/MT)', cls: 'r' }, 'Warehouse', 'Status', 'Delivery',
  { label: 'Margin', cls: 'r' }];

export async function renderTape(root, ctx) {
  let query = '', filters = {};
  const count = counter();
  const list = pagedList({
    pageSize: 30, head: COLUMNS, className: 'deals',
    load: p => api.deals(p),
    row: d => dealRow(d, ctx, () => list.reload()),
    onPage: count.update,
    empty: p => h('div', { class: 'empty' }, h('h3', {}, p.q ? `No deals match "${p.q}"` : 'No deals yet'))
  });
  const reload = () => list.reload({ q: query, ...filters });

  const ui = makeFilters({
    fields: [
      { key: 'side', type: 'chips', label: 'Type',
        chips: [{ value: '', label: 'All' }, { value: 'buy', label: 'Buy' }, { value: 'sell', label: 'Sell' }] },
      { key: 'status', type: 'chips', label: 'Status',
        chips: [{ value: '', label: 'Any' }, { value: 'booked', label: 'Booked' },
                { value: 'cancelled', label: 'Cancelled' }] },
      { key: 'date_from', type: 'date', label: 'From' },
      { key: 'date_to', type: 'date', label: 'To' },
      { key: 'warehouse_id', label: 'Warehouse', any: 'All warehouses' },
      { key: 'material_id', label: 'Material', clears: ['grade_id'] },
      { key: 'grade_id', label: 'Grade', needs: 'material_id', needsLabel: 'material' },
      { key: 'manufacturer_id', label: 'Manufacturer' }
    ],
    onChange: v => { filters = v; options(); reload(); }
  });
  async function options() {
    const { loadStockFilterOptions } = await import('./desk.js');
    loadStockFilterOptions(ui);
  }
  options();

  const cps = pagedList({
    pageSize: 12, className: 'grid',
    load: p => api.counterparties(p),
    row: c => h('div', { class: 'pos', style: { cursor: 'default' } },
      h('div', { class: 'pos-head' },
        h('div', {}, h('div', { class: 'pos-name' }, c.name),
          h('div', { class: 'pos-meta' }, `${c.deals} deals · last ${f.ago(c.last_deal)}`)),
        h('div', { class: 'pos-qty' },
          h('b', { class: 'num ' + pnlClass(c.margin_paise) }, f.inr(c.margin_paise, { sign: true, compact: true })),
          h('span', {}, 'margin earned'))),
      h('div', { class: 'chipline' },
        c.bought_g ? h('span', { class: 'tag' }, `bought ${f.qty(c.bought_g)}`) : null,
        c.sold_g ? h('span', { class: 'tag' }, `sold ${f.qty(c.sold_g)}`) : null))
  });

  mount(root, h('div', { class: 'view' },
    h('div', { class: 'section-head' }, h('h2', {}, 'Trade tape'), h('i', { class: 'rule' }), count),
    searchBar('Search Sauda No., party, GSTIN, product, warehouse or transporter…', t => { query = t; reload(); }),
    ui.el, list.el,
    h('div', { class: 'section-head', style: { marginTop: '32px' } },
      h('h2', {}, 'Counterparties'), h('i', { class: 'rule' })),
    cps.el));
  reload();
  cps.reload({});
}

function dealRow(d, ctx, reload) {
  const sell = d.side === 'sell';
  let open = null;
  const tr = h('tr', { class: 'deal-row ' + d.status },
    h('td', { class: 'mono strong' }, d.ref),
    h('td', { class: 'nowrap' }, f.date(d.deal_date)),
    h('td', {}, h('span', { class: 'side-tag ' + d.side }, f.sideLabel(d.side))),
    h('td', { class: 'party' }, h('b', {}, d.party_name), d.party_gstin ? h('small', {}, d.party_gstin) : null),
    h('td', { class: 'product' }, h('b', {}, `${d.material} ${d.grade}`), h('small', {}, d.manufacturer)),
    h('td', { class: 'r mono' }, f.mt(d.qty_g)),
    h('td', { class: 'r mono' }, f.perMt(d.rate_paise).toLocaleString('en-IN')),
    h('td', {}, d.warehouse || h('span', { class: 'dim' }, 'two warehouses')),
    h('td', {}, h('span', { class: 'pill ' + d.status }, f.statusLabel(d.status))),
    h('td', {}, d.delivery_by || h('span', { class: 'dim' }, '—')),
    h('td', { class: 'r mono' }, sell
      ? h('b', { class: pnlClass(d.margin_paise) }, f.inr(d.margin_paise, { sign: true, compact: true }))
      : h('span', { class: 'dim' }, d.sold_g ? `${f.mt(d.sold_g)} sold` : '—')));
  tr.addEventListener('click', async () => {
    if (open) { open.remove(); open = null; tr.classList.remove('open'); return; }
    const full = await api.deal(d.id);
    open = h('tr', { class: 'deal-detail' },
      h('td', { colspan: COLUMNS.length }, dealDetail(full, ctx, reload)));
    tr.after(open); tr.classList.add('open');
  });
  return tr;
}

// Everything recorded on one sauda, then where its kilos came from or went.
export function dealDetail(deal, ctx, reload) {
  const sell = deal.side === 'sell';
  const lines = sell ? deal.allocations : deal.sold;
  const fact = (label, value) => h('div', { class: 'fact' }, h('span', {}, label),
    h('b', {}, value === null || value === undefined || value === '' ? '—' : value));
  return h('div', { class: 'detail' },
    h('div', { class: 'facts' },
      fact('Sauda No.', deal.ref),
      fact('Deal date', f.date(deal.deal_date)),
      fact('Type', sell ? 'Sell' : 'Buy'),
      fact('Status', f.statusLabel(deal.status)),
      fact(sell ? 'Buyer' : 'Supplier', deal.party_name + (deal.party_gstin ? ` · ${deal.party_gstin}` : '')),
      fact('Product', deal.product),
      fact(sell ? 'Dispatch from' : 'Received into', deal.warehouse),
      fact('Quantity', f.qty(deal.qty_g)),
      fact('Rate', `${f.rate(deal.rate_paise)}/MT`),
      fact('GST', deal.plus_gst ? 'Extra' : 'Included in rate'),
      fact('Value', f.inr(deal.value_paise)),
      fact('Payment due', deal.payment_due ? f.date(deal.payment_due) : null),
      fact('Ex-Place', deal.ex_place),
      fact('Transporter', deal.transporter_name),
      fact('Freight paid by', deal.freight_by),
      fact('Delivery by', deal.delivery_by),
      fact('Payment terms', deal.payment_terms),
      fact('E-way bill', deal.eway),
      fact('Note', deal.remarks),
      sell ? fact('Margin', f.inr(deal.margin_paise, { sign: true })) : fact('Sold so far', f.qty(deal.sold_g))),

    h('div', { class: 'lineage' },
      h('div', { class: 'lineage-title' }, sell ? 'Drawn from' : 'Went to'),
      ...lines.map(a => h('div', { class: 'lin' },
        h('i', { class: 'pipe', style: { background: a.margin_paise >= 0 ? 'var(--up)' : 'var(--down)' } }),
        h('b', {}, f.qty(a.qty_g)),
        h('span', { class: 'muted' }, sell ? '←' : '→'),
        h('b', {}, sell ? a.supplier_name : a.customer_name),
        h('span', { class: 'dim num' }, sell
          ? `cost ${f.rate(a.cost_paise)} · ${a.buy_ref} · ${a.warehouse}`
          : `sold ${f.rate(a.sale_rate_paise)} · ${a.sale_ref} · ${a.warehouse}`),
        h('span', { class: 'grow' }),
        h('b', { class: 'num ' + pnlClass(a.margin_paise) }, f.inr(a.margin_paise, { sign: true })))),
      !lines.length ? h('div', { class: 'dim' },
        sell ? 'No stock was allocated.' : 'None of this purchase has been sold yet.') : null),

    h('div', { class: 'chips', style: { marginTop: '14px' } },
      deal.status === 'booked' ? h('button', {
        class: 'chip', onclick: async e => {
          e.stopPropagation();
          if (!window.confirm(`Cancel ${deal.ref}? Stock goes back the way it was.`)) return;
          try { await api.cancel(deal.id); toast('Cancelled ' + deal.ref); reload && reload(); ctx.refresh(); }
          catch (err) { toast(err.message, { kind: 'err', ms: 9000 }); }
        }
      }, '✕ Cancel deal') : null,
      h('button', { class: 'chip', onclick: e => { e.stopPropagation(); ctx.openPosition(deal.product_id); } },
        'Open product'),
      h('button', {
        class: 'chip', onclick: e => {
          e.stopPropagation(); ctx.go('flow');
          setTimeout(() => showNode(sell ? 'sale' : 'lot', sell ? deal.id : (deal.lot && deal.lot.id)), 260);
        }
      }, '⇄ See in flow')));
}

// ---------------------------------------------------------------- position
export async function renderPosition(root, productId, ctx) {
  const p = await api.position(productId);
  const open = p.lots.filter(l => l.available_g > 0);
  const pos = {
    lots: open,
    cost_low_paise: Math.min(...p.lots.map(l => l.rate_paise)),
    cost_high_paise: Math.max(...p.lots.map(l => l.rate_paise))
  };
  const again = () => { ctx.refresh(); renderPosition(root, productId, ctx); };

  mount(root, h('div', { class: 'view' },
    h('div', { class: 'chips', style: { marginBottom: '14px' } },
      h('button', { class: 'chip', onclick: () => ctx.go('desk') }, '← Desk'),
      open.length ? h('button', { class: 'chip on', onclick: () => ctx.sell({ product_id: productId, product: p.product.display }) },
        'Sell this product') : null),
    h('div', { class: 'pnl-row' },
      stat('In stock', f.qty(p.stock_g), `${open.length} open lots · ${p.warehouses.length} warehouse${p.warehouses.length === 1 ? '' : 's'}`, 'var(--ink)'),
      stat('Weighted cost', f.rate(p.cost_paise), f.inr(p.stock_value_paise, { compact: true }) + ' tied up', 'var(--ink)'),
      stat('Mark', p.mark_paise ? f.rate(p.mark_paise) : '—', p.mark_source || 'tap to set', 'var(--accent)', async () => {
        const v = prompt('Current market rate, ₹ per MT', p.mark_paise ? String(f.perMt(p.mark_paise)) : '');
        if (!v) return;
        const paise = f.fromPerMt(Number(String(v).replace(/[^0-9.]/g, '')));
        if (paise === null) { toast('Rates go in steps of ₹10 per MT', { kind: 'err' }); return; }
        await api.setMark(productId, paise); again();
      }),
      stat('Open P&L', f.inr(p.unrealised_paise, { sign: true, compact: true }),
        `realised ${f.inr(p.realised_paise, { compact: true })}`,
        p.unrealised_paise >= 0 ? 'var(--up)' : 'var(--down)')),

    h('div', { class: 'section-head' }, h('h2', {}, p.product.display), h('i', { class: 'rule' })),
    p.warehouses.length ? whTags(p.warehouses, 12) : null,
    pos.lots.length ? ladder(pos, { height: 46 }) : h('div', { class: 'dim' }, 'Flat — nothing in stock.'),

    h('div', { class: 'section-head', style: { marginTop: '22px' } },
      h('h2', {}, 'Every lot, where it sits, and where it went'), h('i', { class: 'rule' })),
    h('div', { class: 'tape' }, ...p.lots.map(l => lotRow({ ...l, product: p.product.display }, pos, again)))));
}

function stat(label, value, sub, tone, onclick) {
  return h('div', { class: 'pnl', style: { cursor: onclick ? 'pointer' : 'default' }, onclick },
    h('div', { class: 'label' }, label),
    h('div', { class: 'value num', style: { color: tone } }, value),
    h('div', { class: 'sub' }, sub));
}

export function lotActions(lot, after) {
  if (!(lot.available_g > 0)) return null;
  return h('div', { class: 'lot-acts' },
    h('button', { class: 'chip small', onclick: async e => {
      e.stopPropagation();
      const mv = await transferForm(lot);
      if (mv) { toast(`Moved ${f.qty(mv.qty_g)} to ${mv.to_warehouse}`, { action: async () => { await api.undo(); after(); } }); after(); }
    } }, '⇄ Move'),
    h('button', { class: 'chip small', onclick: async e => {
      e.stopPropagation();
      const mv = await adjustForm(lot);
      if (mv) { toast(mv.qty_g < 0 ? `Wrote off ${f.qty(-mv.qty_g)}` : `Recorded ${f.qty(mv.qty_g)} found`); after(); }
    } }, '± Adjust'));
}

function lotRow(l, pos, after) {
  const sold = l.qty_allocated_g, pct = l.qty_g ? (sold / l.qty_g) * 100 : 0;
  const tint = costTint(l.rate_paise, pos.cost_low_paise, pos.cost_high_paise);
  return h('div', { class: 'trade lot', style: { flexDirection: 'column', alignItems: 'stretch', gap: '9px', cursor: 'default' } },
    h('div', { style: { display: 'flex', alignItems: 'center', gap: '13px', flexWrap: 'wrap' } },
      h('i', { style: { width: '9px', height: '30px', borderRadius: '3px', background: tint } }),
      h('div', { class: 'what' },
        h('b', {}, `${l.supplier_name} · ${f.qty(l.qty_g)} @ ${f.rate(l.rate_paise)}`),
        h('div', {}, `${l.deal_ref} · ${f.date(l.deal_date)} · ${f.qty(l.available_g)} left` +
          (l.parent_lot_id ? ' · moved in' : ''))),
      h('span', { class: 'wh-tag' }, l.warehouse),
      lotActions(l, after),
      h('div', { class: 'nums' },
        h('b', { class: 'num ' + pnlClass(l.margin_paise) }, f.inr(l.margin_paise, { sign: true, compact: true })),
        h('span', {}, `${pct.toFixed(0)}% sold`))),
    h('div', { style: { display: 'flex', height: '8px', borderRadius: '5px', overflow: 'hidden', background: 'var(--line)' } },
      h('i', { style: { width: pct + '%', background: tint } })),
    (l.outflows.length || l.moves.length) ? h('div', { class: 'lineage' },
      ...l.outflows.map(o => h('div', { class: 'lin' },
        h('i', { class: 'pipe', style: { height: '18px', background: o.margin_paise >= 0 ? 'var(--up)' : 'var(--down)' } }),
        h('b', {}, f.qty(o.qty_g)), h('span', { class: 'muted' }, '→'), h('b', {}, o.customer_name),
        h('span', { class: 'dim num' }, `@ ${f.rate(o.sale_rate_paise)} · ${o.sale_ref} · ${f.date(o.sale_date)}`),
        h('span', { class: 'grow' }),
        h('b', { class: 'num ' + pnlClass(o.margin_paise) }, f.inr(o.margin_paise, { sign: true })))),
      ...l.moves.map(m => h('div', { class: 'lin' },
        h('i', { class: 'pipe', style: { height: '18px', background: 'var(--line-2)' } }),
        h('b', {}, f.qty(Math.abs(m.qty_g))),
        h('span', { class: 'muted' }, m.kind === 'transfer' ? '⇄' : '±'),
        h('b', {}, m.kind === 'transfer' ? `moved to ${m.to_warehouse}` : (m.qty_g < 0 ? 'written off' : 'found')),
        h('span', { class: 'dim num' }, `${f.date(m.move_date)}${m.reason ? ' · ' + m.reason : ''}`)))) : null);
}
