// Tape = the chronological book. Position = one material, drilled all the way
// down to which kilos of which lot went to which buyer.
import { h, mount, costTint, pnlClass, toast, searchBar, moreBar } from './ui.js';
import * as f from './fmt.js';
import { api } from './api.js';
import { ladder } from './desk.js';
import { makeFilters } from './filters.js';
import { showNode } from './flow.js';

let tapeState = { query: '', filters: {}, rows: [], matched: 0, ctx: null };

export async function renderTape(root, ctx) {
  const page = await api.tape({ limit: 30 });
  tapeState = { query: '', filters: {}, rows: page.deals, matched: page.matched, ctx };

  const list = h('div', { class: 'tape' });
  const more = moreBar(loadMoreTape);
  const counter = h('span', { class: 'dim', style: { fontSize: '14px' } }, '');
  const search = searchBar('Search deal ref, party, material, grade, manufacturer…', searchTape);
  const filters = makeFilters({
    fields: [
      { key: 'side', type: 'chips', label: 'Side',
        chips: [{ value: '', label: 'All' }, { value: 'buy', label: 'Buy' }, { value: 'sell', label: 'Sell' }] },
      { key: 'status', type: 'chips', label: 'Status',
        chips: [{ value: '', label: 'Any' }, { value: 'booked', label: 'Booked' },
                { value: 'cancelled', label: 'Cancelled' }] },
      { key: 'date_from', type: 'date', label: 'From' },
      { key: 'date_to', type: 'date', label: 'To' },
      { key: 'material', label: 'Material', clears: ['grade', 'manufacturer'] },
      { key: 'grade', label: 'Grade', needs: 'material', clears: ['manufacturer'] },
      { key: 'manufacturer', label: 'Manufacturer', needs: 'grade' }
    ],
    onChange: applyTapeFilters
  });
  tapeState.list = list; tapeState.more = more; tapeState.counter = counter;
  tapeState.filters_ui = filters;
  loadTapeOptions();

  const view = h('div', { class: 'view' },
    h('div', { class: 'section-head' },
      h('h2', {}, 'Trade tape'), h('i', { class: 'rule' }), counter),
    search, filters.el, list, more,
    (page.counterparties || []).length
      ? [h('div', { class: 'section-head', style: { marginTop: '32px' } },
            h('h2', {}, 'Counterparties'), h('i', { class: 'rule' })),
         h('div', { class: 'grid' }, ...page.counterparties.map(c =>
           h('div', { class: 'pos', style: { cursor: 'default' } },
             h('div', { class: 'pos-head' },
               h('div', {}, h('div', { class: 'pos-name' }, c.name),
                 h('div', { class: 'pos-meta' }, `${c.deals} deals · last ${f.ago(c.last_deal)}`)),
               h('div', { class: 'pos-qty' },
                 h('b', { class: 'num ' + pnlClass(c.margin_paise) },
                   f.inr(c.margin_paise, { sign: true, compact: true })),
                 h('span', {}, 'margin earned'))),
             h('div', { class: 'chipline' },
               c.bought_g ? h('span', { class: 'tag' }, `bought ${f.qty(c.bought_g)}`) : null,
               c.sold_g ? h('span', { class: 'tag' }, `sold ${f.qty(c.sold_g)}`) : null))))]
      : null);

  mount(root, view);
  paintTape();
  return view;
}

function paintTape() {
  const { rows, list, more, matched, counter, query, ctx } = tapeState;
  mount(list, ...rows.map(d => tradeRow(d, ctx)));
  if (!rows.length) {
    mount(list, h('div', { class: 'empty' },
      h('h3', {}, query ? `No deals match "${query}"` : 'No deals yet')));
  }
  counter.textContent = `${rows.length} of ${matched} shown`;
  more.update(rows.length, matched);
}

async function loadTapeOptions() {
  const ui = tapeState.filters_ui;
  const { material, grade } = ui.state;
  const mats = await api.catalog('material');
  ui.setOptions('material', mats.options.map(o => ({ value: o.value, label: o.value })));
  ui.setOptions('grade', material
    ? (await api.catalog('grade', material)).options.map(o => ({ value: o.value, label: o.value }))
    : []);
  ui.setOptions('manufacturer', material && grade
    ? (await api.catalog('manufacturer', material, grade)).options.map(o => ({ value: o.value, label: o.value }))
    : []);
}

function applyTapeFilters(values) {
  tapeState.filters = values;
  loadTapeOptions();
  return reloadTape();
}

async function reloadTape() {
  const page = await api.tape({ limit: 30, q: tapeState.query, ...tapeState.filters });
  tapeState.rows = page.deals;
  tapeState.matched = page.matched;
  paintTape();
}

function searchTape(term) {
  tapeState.query = term;
  return reloadTape();
}

async function loadMoreTape() {
  const { more, rows, query, filters } = tapeState;
  more.busy(true);
  try {
    const page = await api.tape({ limit: 30, offset: rows.length, q: query, ...filters });
    tapeState.rows = rows.concat(page.deals);
    tapeState.matched = page.matched;
    paintTape();
  } finally {
    more.busy(false);
  }
}

export function tradeRow(d, ctx) {
  const sell = d.side === 'sell';
  const row = h('div', { class: 'trade ' + (d.status === 'cancelled' ? 'cancelled' : '') },
    h('div', { class: 'side ' + d.side }, sell ? 'SELL' : 'BUY'),
    h('div', { class: 'when' }, f.date(d.deal_date)),
    h('div', { class: 'what' },
      h('b', {}, `${d.material} · ${f.qty(d.qty_g)}`),
      h('div', {}, `${sell ? 'to' : 'from'} ${d.party_name} · ${d.ref}` +
        (d.transporter_name ? ` · ${d.transporter_name}` : '') +
        (d.uncovered_g ? ` · SHORT ${f.qty(d.uncovered_g)}` : ''))),
    h('div', { class: 'nums' },
      h('b', { class: 'num' }, f.rate(d.rate_paise)),
      h('span', { class: 'num' }, f.inr(d.value_paise, { compact: true }))),
    h('div', { class: 'nums' },
      sell
        ? [h('b', { class: 'num ' + pnlClass(d.margin_paise) }, f.inr(d.margin_paise, { sign: true, compact: true })),
           h('span', { class: 'num' }, f.rateDelta(d.margin_rate_paise || 0) + '/kg')]
        : [h('b', { class: 'num muted' }, d.sold_g ? f.qty(d.sold_g) : '—'),
           h('span', {}, 'sold on')]));

  let open = null;
  row.addEventListener('click', async () => {
    if (open) { open.remove(); open = null; return; }
    const deal = await api.deal(d.id);
    open = dealDetail(deal, ctx, () => { open.remove(); open = null; });
    row.after(open);
  });
  return row;
}

function dealDetail(deal, ctx, closeSelf) {
  const sell = deal.side === 'sell';
  const lines = sell ? deal.allocations : deal.sold;
  return h('div', { class: 'detail' },
    h('div', { class: 'chipline', style: { marginBottom: '10px' } },
      h('span', { class: 'tag' }, deal.ref),
      h('span', { class: 'tag' }, deal.status),
      deal.plus_gst ? h('span', { class: 'tag' }, 'rate is basic, GST extra') : null,
      deal.freight_by ? h('span', { class: 'tag' }, `freight: ${deal.freight_by}`) : null,
      deal.delivery_by ? h('span', { class: 'tag' }, `delivery: ${deal.delivery_by}`) : null,
      deal.payment_terms ? h('span', { class: 'tag' }, `pay: ${deal.payment_terms}`) : null,
      deal.eway ? h('span', { class: 'tag' }, `e-way: ${deal.eway}`) : null,
      deal.remarks ? h('span', { class: 'tag' }, deal.remarks) : null),

    h('div', { class: 'lineage' },
      ...lines.map(a => h('div', { class: 'lin' },
        h('i', { class: 'pipe', style: { background: a.margin_paise >= 0 ? 'var(--up)' : 'var(--down)' } }),
        h('b', {}, f.qty(a.qty_g)),
        h('span', { class: 'muted' }, sell ? '←' : '→'),
        h('b', {}, sell ? a.supplier_name : a.customer_name),
        h('span', { class: 'dim num' }, sell
          ? `cost ${f.rate(a.cost_paise)} · ${a.buy_ref}`
          : `sold ${f.rate(a.sale_rate_paise)} · ${a.sale_ref}`),
        h('span', { class: 'grow' }),
        h('b', { class: 'num ' + pnlClass(a.margin_paise) }, f.inr(a.margin_paise, { sign: true })))),
      !lines.length ? h('div', { class: 'dim' },
        sell ? 'No stock was allocated — fully short.' : 'None of this lot has been sold yet.') : null,
      deal.uncovered_g ? h('div', { class: 'lin' },
        h('i', { class: 'pipe', style: { background: 'var(--down)' } }),
        h('b', { class: 'down' }, f.qty(deal.uncovered_g)), h('span', { class: 'down' }, 'uncovered')) : null),

    h('div', { class: 'chips', style: { marginTop: '12px' } },
      sell && deal.status === 'booked' ? h('button', {
        class: 'chip', onclick: async e => {
          e.stopPropagation();
          await api.reallocate(deal.id, {});
          toast('Allocation reset for ' + deal.ref); closeSelf(); ctx.refresh();
        }
      }, '↻ Reset allocation') : null,
      deal.status === 'booked' ? h('button', {
        class: 'chip', onclick: async e => {
          e.stopPropagation();
          if (!window.confirm(`Cancel ${deal.ref}? Stock goes back the way it was.`)) return;
          try { await api.cancel(deal.id); toast('Cancelled ' + deal.ref); closeSelf(); ctx.refresh(); }
          catch (err) { toast(err.message, { kind: 'err', ms: 9000 }); }
        }
      }, '✕ Cancel deal') : null,
      h('button', {
        class: 'chip',
        onclick: e => { e.stopPropagation(); ctx.go('flow'); setTimeout(() => showNode(sell ? 'sale' : 'lot', sell ? deal.id : (deal.lot && deal.lot.id)), 260); }
      }, '⇄ See in flow')));
}

// ---------------------------------------------------------------- position
export async function renderPosition(root, skuId, ctx) {
  const p = await api.position(skuId);
  const view = h('div', { class: 'view' });
  mount(root, view);
  const pos = {
    lots: p.lots.filter(l => l.available_g > 0),
    cost_low_paise: Math.min(...p.lots.map(l => l.rate_paise)),
    cost_high_paise: Math.max(...p.lots.map(l => l.rate_paise))
  };

  mount(view,
    h('button', { class: 'chip', style: { marginBottom: '14px' }, onclick: () => ctx.go('desk') }, '← Desk'),
    h('div', { class: 'pnl-row' },
      stat('In stock', f.qty(p.stock_g), `${pos.lots.length} open lots`, 'var(--ink)'),
      stat('Weighted cost', f.rate(p.cost_paise), f.inr(p.stock_value_paise, { compact: true }) + ' tied up', 'var(--ink)'),
      stat('Mark', p.mark_paise ? f.rate(p.mark_paise) : '—', p.mark_source || 'tap to set', 'var(--accent)', async () => {
        const v = prompt('Current market rate ₹/kg', p.mark_paise ? (p.mark_paise / 100).toFixed(2) : '');
        if (v) { await api.setMark(skuId, v); ctx.go('position', skuId); }
      }),
      stat('Open P&L', f.inr(p.unrealised_paise, { sign: true, compact: true }),
        `realised ${f.inr(p.realised_paise, { compact: true })}`,
        p.unrealised_paise >= 0 ? 'var(--up)' : 'var(--down)')),

    h('div', { class: 'section-head' }, h('h2', {}, p.sku.display + ' — cost ladder'), h('i', { class: 'rule' })),
    pos.lots.length ? ladder(pos, { height: 46 }) : h('div', { class: 'dim' }, 'Flat — nothing in stock.'),

    h('div', { class: 'section-head', style: { marginTop: '22px' } },
      h('h2', {}, 'Every lot, and where it went'), h('i', { class: 'rule' })),
    h('div', { class: 'tape' }, ...p.lots.map(l => lotRow(l, pos, ctx))));
  return view;
}

function stat(label, value, sub, tone, onclick) {
  return h('div', { class: 'pnl', style: { '--tone': tone, cursor: onclick ? 'pointer' : 'default' }, onclick },
    h('div', { class: 'label' }, label),
    h('div', { class: 'value num', style: { color: tone } }, value),
    h('div', { class: 'sub' }, sub));
}

function lotRow(l, pos, ctx) {
  const sold = l.qty_allocated_g, pct = l.qty_g ? (sold / l.qty_g) * 100 : 0;
  const row = h('div', { class: 'trade', style: { flexDirection: 'column', alignItems: 'stretch', gap: '9px' } },
    h('div', { style: { display: 'flex', alignItems: 'center', gap: '13px' } },
      h('i', { class: 'swatch', style: { width: '9px', height: '30px', borderRadius: '3px', background: costTint(l.rate_paise, pos.cost_low_paise, pos.cost_high_paise) } }),
      h('div', { class: 'what' },
        h('b', {}, `${l.supplier_name} · ${f.qty(l.qty_g)} @ ${f.rate(l.rate_paise)}`),
        h('div', {}, `${l.deal_ref} · ${f.date(l.deal_date)} · ${f.qty(l.available_g)} left`)),
      h('div', { class: 'nums' },
        h('b', { class: 'num ' + pnlClass(l.margin_paise) }, f.inr(l.margin_paise, { sign: true, compact: true })),
        h('span', {}, `${pct.toFixed(0)}% sold`))),
    h('div', { style: { display: 'flex', height: '8px', borderRadius: '5px', overflow: 'hidden', background: 'var(--line)' } },
      h('i', { style: { width: pct + '%', background: costTint(l.rate_paise, pos.cost_low_paise, pos.cost_high_paise) } }),
      h('i', { style: { width: (100 - pct) + '%', background: 'transparent' } })),
    l.outflows.length ? h('div', { class: 'lineage' },
      ...l.outflows.map(o => h('div', { class: 'lin' },
        h('i', { class: 'pipe', style: { height: '18px', background: o.margin_paise >= 0 ? 'var(--up)' : 'var(--down)' } }),
        h('b', {}, f.qty(o.qty_g)), h('span', { class: 'muted' }, '→'), h('b', {}, o.customer_name),
        h('span', { class: 'dim num' }, `@ ${f.rate(o.sale_rate_paise)} · ${o.sale_ref} · ${f.date(o.sale_date)}`),
        h('span', { class: 'grow' }),
        h('b', { class: 'num ' + pnlClass(o.margin_paise) }, f.inr(o.margin_paise, { sign: true }))))) : null);
  return row;
}
