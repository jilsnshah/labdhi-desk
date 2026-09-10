// The phone app's screens (the ticket lives in mticket.js).
//
// Not the desktop at a narrower width: every list is a column of cards sized
// for a thumb, and every list is paged from the server exactly as on the
// desktop. Records are added and edited through the same forms, which a phone
// shows as bottom sheets.

import { h, mount, svg, toast, debounce } from './ui.js';
import * as f from './fmt.js';
import { api } from './api.js';
import { pagedList } from './lists.js';
import { partyForm, productForm, warehouseForm, transferForm, adjustForm, partySub } from './forms.js';
import { removeRecord, nameForm } from './setup.js';
import { MOVE_LABEL } from './stock.js';

let ctx = null;

// ==================================================================== shell
export function mountShell(appCtx) {
  ctx = appCtx;
  const head = h('header', { class: 'mhead' },
    h('div', { class: 'mhead-top' },
      h('i', { class: 'mhead-dot' }),
      h('div', { class: 'mhead-name', id: 'm-company' }, 'Labdhi Desk'),
      h('div', { class: 'mhead-stat' }, h('b', { id: 'm-pnl', class: 'num' }, '—'), h('span', {}, 'open p&l'))),
    h('div', { class: 'mhead-strip', id: 'm-strip' }));

  const dock = h('div', { class: 'dock' },
    h('div', { class: 'dock-actions' },
      h('button', { class: 'dock-btn buy', onclick: () => ctx.trade('buy') }, '↓ Buy'),
      h('button', { class: 'dock-btn sell', onclick: () => ctx.trade('sell') }, '↑ Sell')),
    h('div', { class: 'dock-tabs' },
      ...[['desk', '▦', 'Desk'], ['stock', '▤', 'Stock'], ['flow', '⤳', 'Flow'], ['tape', '≡', 'Tape'], ['setup', '⚙', 'Setup']]
        .map(([route, icon, label]) => h('button', {
          class: 'dock-tab', data: { route }, onclick: () => ctx.go(route)
        }, h('i', {}, icon), label))));

  document.getElementById('app').prepend(head);
  document.body.appendChild(dock);
  syncTabs();
}

export function syncTabs() {
  for (const b of document.querySelectorAll('.dock-tab')) b.classList.toggle('on', b.dataset.route === ctx.route);
}

export function paintHeader(s) {
  const company = document.getElementById('m-company');
  if (company) company.textContent = s.company || 'Labdhi Desk';
  const pnl = document.getElementById('m-pnl');
  if (pnl) {
    pnl.textContent = f.inr(s.unrealised_paise, { sign: true, compact: true });
    pnl.className = 'num ' + (s.unrealised_paise >= 0 ? 'up' : 'down');
  }
  const strip = document.getElementById('m-strip');
  if (strip) {
    mount(strip,
      h('div', {}, 'Stock ', h('b', {}, f.qty(s.stock_g))),
      h('div', {}, 'Today ', h('b', { class: s.realised_today_paise >= 0 ? 'up' : 'down' },
        f.inr(s.realised_today_paise, { sign: true, compact: true }))),
      h('div', {}, 'Value ', h('b', {}, f.inr(s.stock_value_paise, { compact: true }))));
  }
}

const label = (text, count) => h('div', { class: 'mlabel' }, text, count || null);
const countEl = () => { const el = h('span', {}); el.update = st => { el.textContent = st.total ? `${st.items.length} of ${st.total}` : ''; }; return el; };
const msearch = (placeholder, onChange, value = '') => h('div', { class: 'msearch' },
  h('span', { class: 'dim' }, '⌕'),
  h('input', { type: 'search', placeholder, value, oninput: debounce(e => onChange(e.target.value.trim()), 250) }));

// ===================================================================== desk
export function renderMobileDesk(root, s, appCtx) {
  ctx = appCtx || ctx;
  paintHeader(s);
  const count = countEl();
  const list = pagedList({
    pageSize: 15,
    load: p => api.positions(p),
    onPage: count.update,
    row: p => h('div', { class: 'mpos' },
      h('div', { class: 'mpos-main', onclick: () => ctx.go('position', p.product_id) },
        h('div', { class: 'mpos-name' }, p.product),
        h('div', { class: 'mpos-sub' }, p.warehouses.map(w => `${w.name} ${f.qty(w.stock_g, { short: true })}`).join(' · '))),
      h('div', { class: 'mpos-qty', onclick: () => ctx.go('position', p.product_id) },
        h('b', { class: 'num' }, f.qty(p.stock_g)),
        h('span', { class: 'num ' + (p.unrealised_paise >= 0 ? 'up' : 'down') },
          f.inr(p.unrealised_paise, { sign: true, compact: true }))),
      h('button', { class: 'mpos-go', title: 'Sell',
        onclick: () => ctx.trade('sell', { product: { id: p.product_id, display: p.product } }) }, '↑')),
    empty: () => h('div', { class: 'empty' }, h('h3', {}, 'No stock yet'), h('div', {}, 'Tap Buy to record your first purchase.'))
  });
  mount(root, h('div', { class: 'view' },
    s.attention && s.attention.length
      ? h('div', { class: 'mflow', style: { paddingTop: '14px' } },
          ...s.attention.slice(0, 3).map(a => h('div', { class: 'alert ' + a.level },
            h('i', { class: 'bar' }), h('div', {}, h('b', {}, a.title), h('div', {}, h('span', {}, a.detail))))))
      : null,
    h('div', { class: 'mdesk' }, label('Positions', count), list.el)));
  list.reload({});
}

// ==================================================================== stock
export async function renderMobileStock(root, appCtx) {
  ctx = appCtx || ctx;
  const st = ctx.mstock || (ctx.mstock = { warehouse_id: '', q: '', detail: null });
  if (st.detail) return stockItem(root, st);

  const whs = await api.warehouses({ limit: 50 });
  const count = countEl();
  const list = pagedList({
    pageSize: 20, load: p => api.stock(p), onPage: count.update,
    row: r => h('button', { class: 'mflow-card mcard-btn', onclick: () => { st.detail = r; renderMobileStock(root, ctx); } },
      h('div', { class: 'mflow-head' },
        h('div', { class: 'grow' }, h('b', {}, `${r.material} ${r.grade} · ${r.manufacturer}`),
          h('span', {}, `${r.warehouse} · ${r.lots} lot${r.lots === 1 ? '' : 's'} · cost ${f.rate(r.cost_paise)}`)),
        h('div', { class: 'mflow-money' }, h('b', { class: 'num' }, f.qty(r.stock_g)),
          h('span', {}, f.inr(r.stock_value_paise, { compact: true }))))),
    empty: () => h('div', { class: 'empty' }, h('h3', {}, 'Nothing in stock here'))
  });
  const reload = () => list.reload({ q: st.q, warehouse_id: st.warehouse_id });

  mount(root, h('div', { class: 'view' },
    h('div', { class: 'mflow', style: { paddingTop: '14px' } },
      h('div', { class: 'mwh-row' },
        h('button', { class: 'mwh' + (st.warehouse_id ? '' : ' on'), onclick: () => { st.warehouse_id = ''; renderMobileStock(root, ctx); } },
          h('b', {}, 'All'), h('span', {}, 'warehouses')),
        ...whs.items.map(w => h('button', {
          class: 'mwh' + (String(st.warehouse_id) === String(w.id) ? ' on' : ''),
          onclick: () => { st.warehouse_id = w.id; renderMobileStock(root, ctx); }
        }, h('b', {}, w.name), h('span', {}, f.qty(w.stock_g)))),
        h('button', { class: 'mwh add', onclick: async () => { if (await warehouseForm()) renderMobileStock(root, ctx); } },
          h('b', {}, '+'), h('span', {}, 'warehouse'))),
      msearch('Product, grade or maker', q => { st.q = q; reload(); }, st.q)),
    h('div', { class: 'mflow' }, label('Stock by warehouse', count), list.el)));
  reload();
}

// One product in one warehouse: its lots, each movable, and its ledger.
async function stockItem(root, st) {
  const r = st.detail;
  const { items: lots } = await api.lots(r.product_id, r.warehouse_id);
  const after = () => { ctx.refresh(); stockItem(root, st); };
  const ledger = pagedList({
    pageSize: 15, load: p => api.moves({ ...p, product_id: r.product_id, warehouse_id: r.warehouse_id }),
    row: m => ledgerCard(m)
  });
  const here = lots.reduce((s, l) => s + l.available_g, 0);
  mount(root, h('div', { class: 'view' },
    h('div', { class: 'mflow', style: { paddingTop: '14px' } },
      h('button', { class: 'mmore', style: { marginBottom: '12px' }, onclick: () => { st.detail = null; renderMobileStock(root, ctx); } },
        '‹ All stock'),
      h('div', { class: 'mpos-hero' },
        h('b', {}, r.product),
        h('div', { class: 'mpos-hero-row' },
          heroStat('Warehouse', r.warehouse), heroStat('In stock', f.qty(here)),
          heroStat('Avg cost', f.rate(r.cost_paise)), heroStat('Value', f.inr(r.stock_value_paise, { compact: true }))))),
    h('div', { class: 'mflow' }, label('Lots here'),
      ...lots.map(l => lotCard({ ...l, product: r.product }, after)),
      label('Ledger'), ledger.el)));
  ledger.reload({});
}

function lotCard(l, after) {
  return h('div', { class: 'mflow-card' },
    h('div', { class: 'mflow-head' },
      h('span', { class: 'mflow-side lot' }, f.rate(l.rate_paise)),
      h('div', { class: 'grow' }, h('b', {}, l.supplier_name),
        h('span', {}, `${l.deal_ref} · ${f.date(l.deal_date)} · ${l.warehouse}${l.parent_lot_id ? ' · moved in' : ''}`)),
      h('div', { class: 'mflow-money' }, h('b', { class: 'num' }, f.qty(l.available_g)), h('span', { class: 'num' }, `of ${f.qty(l.qty_g)}`))),
    l.available_g > 0 ? h('div', { class: 'mcard-acts' },
      h('button', { class: 'mchip', onclick: async () => {
        const mv = await transferForm(l);
        if (mv) { toast(`Moved ${f.qty(mv.qty_g)} to ${mv.to_warehouse}`); after(); }
      } }, '⇄ Move'),
      h('button', { class: 'mchip', onclick: async () => {
        const mv = await adjustForm(l);
        if (mv) { toast('Adjustment recorded'); after(); }
      } }, '± Adjust')) : null);
}

function ledgerCard(m) {
  const into = m.qty_g > 0;
  const who = m.kind === 'transfer_in' ? `from ${m.counterparty}` : m.kind === 'transfer_out' ? `to ${m.counterparty}`
    : (m.counterparty || m.note || '');
  return h('div', { class: 'mledger' },
    h('span', { class: 'mv-tag ' + m.kind }, MOVE_LABEL[m.kind]),
    h('div', { class: 'grow' }, h('b', {}, who || '—'), h('span', {}, `${f.date(m.date)}${m.ref ? ' · ' + m.ref : ''}`)),
    h('div', { class: 'mflow-money' },
      h('b', { class: 'num ' + (into ? 'up' : 'down') }, (into ? '+' : '−') + f.mt(Math.abs(m.qty_g))),
      m.balance_g !== undefined ? h('span', { class: 'num' }, `bal ${f.mt(m.balance_g)}`) : null));
}

// ================================================================ position
export async function renderMobilePosition(root, productId, appCtx) {
  ctx = appCtx || ctx;
  const p = await api.position(productId);
  const open = p.lots.filter(l => l.available_g > 0);
  const after = () => { ctx.refresh(); renderMobilePosition(root, productId, ctx); };
  mount(root, h('div', { class: 'view' },
    h('div', { class: 'mflow', style: { paddingTop: '14px' } },
      h('button', { class: 'mmore', style: { marginBottom: '12px' }, onclick: () => ctx.go('desk') }, '‹ Back to desk'),
      h('div', { class: 'mpos-hero' },
        h('b', {}, p.product.display),
        h('div', { class: 'mpos-hero-row' },
          heroStat('In stock', f.qty(p.stock_g)), heroStat('Avg cost', f.rate(p.cost_paise)),
          heroStat('Mark', p.mark_paise ? f.rate(p.mark_paise) : '—'),
          heroStat('Open P&L', f.inr(p.unrealised_paise, { sign: true, compact: true }), p.unrealised_paise >= 0 ? 'up' : 'down')),
        p.warehouses.length ? h('div', { class: 'mdeal-meta' },
          ...p.warehouses.map(w => h('span', {}, `${w.name} ${f.qty(w.stock_g)}`))) : null),
      open.length ? h('button', { class: 'dock-btn sell', style: { marginTop: '12px', width: '100%' },
        onclick: () => ctx.trade('sell', { product: { id: productId, display: p.product.display } }) }, '↑ Sell this product') : null),
    h('div', { class: 'mflow' }, label('Lots', h('span', {}, `${open.length} open`)),
      ...p.lots.map(l => {
        const card = lotCard({ ...l, product: p.product.display }, after);
        if (l.outflows && l.outflows.length) {
          card.appendChild(h('div', { class: 'mflow-links' }, ...l.outflows.map(o => h('div', { class: 'mflow-link' },
            h('i', { style: { background: o.margin_paise >= 0 ? 'var(--up)' : 'var(--down)' } }),
            h('span', { class: 'qty' }, f.qty(o.qty_g)),
            h('span', { class: 'who' }, `to ${o.customer_name} @ ${f.rate(o.sale_rate_paise)}`),
            h('span', { class: 'pl ' + (o.margin_paise >= 0 ? 'up' : 'down') }, f.inr(o.margin_paise, { sign: true, compact: true }))))));
        }
        return card;
      }))));
}

function heroStat(labelText, value, tone) {
  return h('div', {}, h('span', {}, labelText), h('b', { class: 'num ' + (tone || '') }, value));
}

// ===================================================================== tape
// Every card carries the tape's columns: Sauda No., date, type, party,
// product/grade, quantity, rate, warehouse, status and delivery.
export async function renderMobileTape(root, appCtx) {
  ctx = appCtx || ctx;
  const st = { q: '', side: '', warehouse_id: '' };
  const whs = await api.warehouses({ limit: 100 });
  const count = countEl();
  const list = pagedList({ pageSize: 20, load: p => api.deals(p), onPage: count.update, row: d => dealCard(d, () => reload()),
    empty: () => h('div', { class: 'empty' }, h('h3', {}, 'No deals')) });
  const reload = () => list.reload({ q: st.q, side: st.side, warehouse_id: st.warehouse_id });

  const sideChips = h('div', { class: 'mchips g3 filter', style: { padding: '0 0 8px' } });
  const paintChips = () => mount(sideChips, ...[['', 'All'], ['buy', 'Bought'], ['sell', 'Sold']].map(([v, text]) => h('button', {
    class: 'mchip' + (st.side === v ? ' on' : ''), onclick: () => { st.side = v; paintChips(); reload(); }
  }, text)));
  paintChips();

  mount(root, h('div', { class: 'view' },
    h('div', { class: 'mflow', style: { paddingTop: '14px' } },
      msearch('Sauda No., party, product, warehouse', q => { st.q = q; reload(); }),
      sideChips,
      h('select', { class: 'mselect', onchange: e => { st.warehouse_id = e.target.value; reload(); } },
        h('option', { value: '' }, 'All warehouses'),
        ...whs.items.map(w => h('option', { value: w.id }, w.name)))),
    h('div', { class: 'mflow' }, label('Trades', count), list.el)));
  reload();
}

function dealCard(d, reload) {
  const sell = d.side === 'sell';
  const cell = (k, v) => h('div', {}, h('span', {}, k), h('b', {}, v || '—'));
  const card = h('div', { class: 'mdeal ' + d.status },
    h('div', { class: 'mdeal-top' },
      h('span', { class: 'mono' }, d.ref),
      h('span', { class: 'dim' }, f.date(d.deal_date)),
      h('span', { class: 'side-tag ' + d.side }, f.sideLabel(d.side)),
      h('span', { class: 'grow' }),
      h('span', { class: 'pill ' + d.status }, f.statusLabel(d.status))),
    h('div', { class: 'mdeal-party' }, d.party_name),
    h('div', { class: 'mdeal-product' }, `${d.material} ${d.grade}`, h('small', {}, ` · ${d.manufacturer}`)),
    h('div', { class: 'mdeal-grid' },
      cell('Qty (MT)', f.mt(d.qty_g)),
      cell('Rate (₹/MT)', f.perMt(d.rate_paise).toLocaleString('en-IN')),
      cell('Warehouse', d.warehouse),
      cell('Delivery', d.delivery_by)),
    sell ? h('div', { class: 'mdeal-margin ' + (d.margin_paise >= 0 ? 'up' : 'down') },
      `margin ${f.inr(d.margin_paise, { sign: true, compact: true })}`) : null);

  let open = null;
  card.onclick = async e => {
    if (e.target.closest('button')) return;
    if (open) { open.remove(); open = null; return; }
    const full = await api.deal(d.id);
    const lines = sell ? full.allocations : full.sold;
    const costs = lines.map(a => a.cost_paise);
    const lo = Math.min(...costs), hi = Math.max(...costs);
    open = h('div', { class: 'mdeal-more' },
      h('div', { class: 'mdeal-grid wide' },
        cell('GST', full.plus_gst ? 'Extra' : 'Included'),
        cell('Value', f.inr(full.value_paise, { compact: true })),
        cell('Payment due', full.payment_due ? f.date(full.payment_due) : ''),
        cell('Ex-Place', full.ex_place),
        cell('Transporter', full.transporter_name),
        cell('Freight by', full.freight_by),
        cell('Payment terms', full.payment_terms),
        cell('E-way bill', full.eway),
        full.remarks ? cell('Note', full.remarks) : null,
        full.party_gstin ? cell('GSTIN', full.party_gstin) : null),
      lines.length ? fanDiagram(
        lines.map(a => ({
          qty: a.qty_g, color: sell ? costShade(a.cost_paise, lo, hi) : 'hsl(214 60% 55%)',
          good: a.margin_paise >= 0, short: f.qty(a.qty_g, { short: true }),
          label: sell ? `${a.supplier_name} @ ${f.rate(a.cost_paise)} · ${a.warehouse}` : `${a.customer_name} @ ${f.rate(a.sale_rate_paise)}`
        })),
        { qty: full.qty_g, short: `${full.party_name} · ${f.qty(full.qty_g)}` },
        { targetColor: sell ? 'var(--up)' : 'var(--accent)' }) : h('div', { class: 'mflow-empty' },
          sell ? 'Nothing allocated.' : 'None of this purchase sold yet.'),
      full.status === 'booked' ? h('button', {
        class: 'mmore', style: { marginTop: '10px' },
        onclick: async () => {
          if (!window.confirm(`Cancel ${full.ref}?`)) return;
          try { await api.cancel(full.id); toast('Cancelled ' + full.ref); reload(); ctx.refresh(); }
          catch (err) { toast(err.message, { kind: 'err', ms: 8000 }); }
        }
      }, 'Cancel this deal') : null);
    card.appendChild(open);
  };
  return card;
}

// ==================================================================== setup
const SETUP_TABS = [['parties', 'Parties'], ['products', 'Products'], ['warehouses', 'Warehouses'],
                    ['materials', 'Materials'], ['manufacturers', 'Makers'], ['states', 'States']];

export function renderMobileSetup(root, appCtx) {
  ctx = appCtx || ctx;
  const tab = ctx.setupTab || 'parties';
  const again = () => renderMobileSetup(root, ctx);
  const cfg = MSETUP[tab];
  let query = '';
  const count = countEl();
  const list = pagedList({ pageSize: 20, load: p => cfg.load({ ...p, ...(cfg.extra ? cfg.extra() : {}) }), onPage: count.update,
    row: item => cfg.card(item, () => list.reload()),
    empty: () => h('div', { class: 'empty small' }, query ? `Nothing matches "${query}"` : 'Nothing here yet') });

  mount(root, h('div', { class: 'view' },
    h('div', { class: 'mflow', style: { paddingTop: '14px' } },
      h('div', { class: 'mchips g3 filter', style: { padding: '0 0 10px' } },
        ...SETUP_TABS.map(([key, text]) => h('button', {
          class: 'mchip' + (tab === key ? ' on' : ''), onclick: () => { ctx.setupTab = key; ctx.setupFilter = null; again(); }
        }, text))),
      cfg.add ? h('button', { class: 'mmore', style: { marginBottom: '10px' },
        onclick: async () => { if (await cfg.add()) list.reload(); } }, cfg.addLabel) : null,
      msearch(cfg.placeholder, q => { query = q; list.reload({ q }); }),
      ctx.setupFilter && tab === 'parties'
        ? h('button', { class: 'mmore', style: { marginBottom: '10px' }, onclick: () => { ctx.setupFilter = null; again(); } },
            `State ${ctx.setupFilter} only · show all`) : null),
    h('div', { class: 'mflow' }, label(cfg.title, count), list.el)));
  list.reload({});
}

const x = (labelText, call, again) => h('button', { class: 'msetup-x', onclick: e => { e.stopPropagation(); removeRecord(labelText, call, again); } }, '×');
const card = (onTap, title, sub, right, extra, xBtn) => h('div', { class: 'mflow-card' },
  h('div', { class: 'mflow-head' },
    h('div', { class: 'grow', onclick: onTap }, h('b', {}, title), sub ? h('span', {}, sub) : null),
    right ? h('div', { class: 'mflow-money' }, right) : null,
    xBtn || null),
  extra || null);

const MSETUP = {
  parties: {
    title: 'Parties', addLabel: '+ Add party', add: () => partyForm(), placeholder: 'Name, GSTIN, phone',
    load: p => api.parties(p), extra: () => (ctx.setupFilter ? { state_code: ctx.setupFilter } : {}),
    card: (p, again) => card(async () => { if (await partyForm(p)) again(); }, p.name, partySub(p),
      h('b', { class: 'num' }, p.deal_count || '—'),
      p.address ? h('div', { class: 'mparty-addr' }, p.address) : null,
      x(p.name, () => api.partyRemove(p.id), again))
  },
  products: {
    title: 'Products', addLabel: '+ Add product', add: () => productForm(), placeholder: 'Material, grade, maker',
    load: p => api.products(p),
    card: (p, again) => card(async () => { if (await productForm(await api.product(p.id))) again(); },
      p.display, p.packing || (p.deal_count ? `${p.deal_count} deals` : 'never traded'),
      h('b', { class: 'num' }, p.stock_g ? f.qty(p.stock_g) : '—'), null,
      x(p.display, () => api.productRemove(p.id), again))
  },
  warehouses: {
    title: 'Warehouses', addLabel: '+ Add warehouse', add: () => warehouseForm(), placeholder: 'Name or address',
    load: p => api.warehouses(p),
    card: (w, again) => card(async () => { if (await warehouseForm(w)) again(); }, w.name, w.address || 'no address',
      h('b', { class: 'num' }, f.qty(w.stock_g)), null, x(w.name, () => api.warehouseRemove(w.id), again))
  },
  materials: {
    title: 'Materials', addLabel: '+ Add material', placeholder: 'Material',
    add: () => nameForm('Add material', '', 'e.g. LLDPE', v => api.materialSave({ name: v })),
    load: p => api.materials(p),
    card: (m, again) => {
      const box = h('div', {});
      const c = card(() => {
        if (box.childNodes.length) { mount(box); return; }
        const grades = pagedList({ pageSize: 20, load: p => api.grades({ ...p, material_id: m.id }),
          row: g => h('div', { class: 'mflow-link' },
            h('i', { style: { background: 'var(--accent)' } }),
            h('span', { class: 'who', onclick: async () => {
              if (await nameForm('Rename grade', g.name, '', v => api.gradeSave({ id: g.id, material_id: m.id, name: v }))) grades.reload();
            } }, g.name),
            h('span', { class: 'qty' }, g.stock_g ? f.qty(g.stock_g) : ''),
            x(`${m.name} ${g.name}`, () => api.gradeRemove(g.id), () => grades.reload())) });
        mount(box, h('div', { class: 'mflow-links' },
          h('button', { class: 'mmore', onclick: async () => {
            if (await nameForm(`Add grade to ${m.name}`, '', 'e.g. S65', v => api.gradeSave({ material_id: m.id, name: v }))) grades.reload();
          } }, `+ ${m.name} grade`), grades.el));
        grades.reload({});
      }, m.name, `${m.grades} grade${m.grades === 1 ? '' : 's'} · ${m.products} product${m.products === 1 ? '' : 's'} · tap to open`,
        h('b', { class: 'num' }, m.stock_g ? f.qty(m.stock_g) : '—'), box, x(m.name, () => api.materialRemove(m.id), again));
      return c;
    }
  },
  manufacturers: {
    title: 'Manufacturers', addLabel: '+ Add manufacturer', placeholder: 'Manufacturer',
    add: () => nameForm('Add manufacturer', '', 'e.g. Reliance', v => api.manufacturerSave({ name: v })),
    load: p => api.manufacturers(p),
    card: (k, again) => card(async () => {
      if (await nameForm('Rename manufacturer', k.name, '', v => api.manufacturerSave({ id: k.id, name: v }))) again();
    }, k.name, `${k.products} product${k.products === 1 ? '' : 's'} · tap to rename`,
      h('b', { class: 'num' }, k.stock_g ? f.qty(k.stock_g) : '—'), null, x(k.name, () => api.manufacturerRemove(k.id), again))
  },
  states: {
    title: 'States', placeholder: 'State or code', load: p => api.states({ ...p, limit: 50 }),
    card: s => card(() => { if (s.parties) { ctx.setupTab = 'parties'; ctx.setupFilter = s.code; ctx.go('setup'); } },
      s.name, `code ${s.code}`, h('b', { class: 'num' }, s.parties ? `${s.parties}` : '—'))
  }
};

// ============================================================= visualisation
// Cost is shaded along one hue, light for cheap through to dark for dear: red
// and green mean loss and profit everywhere else in the app.
function costShade(rate, low, high) {
  if (!isFinite(low) || !isFinite(high) || high === low) return 'hsl(199 42% 46%)';
  const t = Math.max(0, Math.min(1, (rate - low) / (high - low)));
  return `hsl(${202 - t * 10} ${38 + t * 16}% ${62 - t * 28}%)`;
}

function proportionBar(parts, opts = {}) {
  const total = parts.reduce((s, p) => s + p.qty, 0) || 1;
  return h('div', { class: 'mbar' + (opts.thin ? ' thin' : '') },
    ...parts.map(p => h('i', { style: { width: (p.qty / total * 100) + '%', background: p.color }, title: p.label })));
}

// One trade, its sources fanning into it, drawn top-to-bottom.
function fanDiagram(sources, target, opts = {}) {
  const W = 340, H = 186, BAND = 30, TOP = 10, BOT = H - BAND - 26;
  const total = sources.reduce((s, x2) => s + x2.qty, 0) || 1;
  const targetQty = Math.max(total, target.qty || 0);
  const gap = sources.length > 1 ? 5 : 0;
  const usable = W - gap * (sources.length - 1);
  let xx = 0, tx = 0;
  const bands = [], ribbons = [], labels = [];
  for (const src of sources) {
    const w = Math.max(9, (src.qty / total) * usable);
    const tw = (src.qty / targetQty) * W;
    bands.push(svg('rect', { x: xx, y: TOP, width: w, height: BAND, rx: 6, fill: src.color, 'fill-opacity': .95 }));
    const x0 = xx, x1 = xx + w, t0 = tx, t1 = tx + tw, my = (TOP + BAND + BOT) / 2;
    ribbons.push(svg('path', {
      d: `M${x0},${TOP + BAND} C${x0},${my} ${t0},${my} ${t0},${BOT} L${t1},${BOT} C${t1},${my} ${x1},${my} ${x1},${TOP + BAND} Z`,
      fill: src.good === false ? 'var(--down)' : 'var(--up)', 'fill-opacity': .22
    }));
    if (w > 46) labels.push(svg('text', { x: xx + w / 2, y: TOP + BAND / 2 + 4, 'text-anchor': 'middle',
      'font-size': 11, 'font-weight': 700, fill: '#fff' }, src.short));
    xx += w + gap; tx += tw;
  }
  const shortfall = targetQty > total ? ((targetQty - total) / targetQty) * W : 0;
  return h('div', { class: 'mfan' },
    svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'mfan-svg' },
      ...ribbons, ...bands, ...labels,
      svg('rect', { x: 0, y: BOT, width: W - shortfall, height: BAND, rx: 6, fill: opts.targetColor || 'var(--ink)', 'fill-opacity': .88 }),
      shortfall ? svg('rect', { x: W - shortfall, y: BOT, width: shortfall, height: BAND, rx: 6, fill: 'var(--down)', 'fill-opacity': .35 }) : null,
      svg('text', { x: W / 2, y: BOT + BAND / 2 + 4, 'text-anchor': 'middle', 'font-size': 11.5, 'font-weight': 700, fill: '#fff' }, target.short)),
    h('div', { class: 'mfan-key' },
      ...sources.map(src => h('span', {}, h('i', { style: { background: src.color } }), src.label)),
      sources.length > 1 ? h('span', { class: 'mfan-note' }, 'lighter = cheaper stock') : null));
}

// The whole window as one diagram: purchases along the top, sales along the
// bottom, a ribbon per allocation. The desktop sankey turned ninety degrees.
function overviewDiagram(graph, onPick) {
  const W = 358, BAND = 30, H = 300, TOP = 4, BOT = H - BAND - 4;
  const lots = graph.nodes.filter(n => n.kind === 'lot');
  const sales = graph.nodes.filter(n => n.kind === 'sale');
  if (!lots.length && !sales.length) return null;
  const rates = lots.map(n => n.rate_paise);
  const low = Math.min(...rates), high = Math.max(...rates);
  const lotTotal = lots.reduce((s, n) => s + n.qty_g, 0) || 1;
  const saleTotal = sales.reduce((s, n) => s + n.qty_g, 0) || 1;
  const scale = Math.max(lotTotal, saleTotal);
  const place = (nodes, total) => {
    const MIN = 7, gap = 2;
    const room = (total / scale) * W - gap * Math.max(0, nodes.length - 1);
    const raw = nodes.map(n => (n.qty_g / total) * room);
    const lift = raw.reduce((s, w) => s + Math.max(0, MIN - w), 0);
    const shrinkable = raw.reduce((s, w) => s + Math.max(0, w - MIN), 0) || 1;
    let xx = 0;
    const map = new Map();
    nodes.forEach((n, i) => {
      const w = raw[i] < MIN ? MIN : raw[i] - (raw[i] - MIN) * (lift / shrinkable);
      map.set(n.id, { node: n, x: xx, w, cursor: xx });
      xx += w + gap;
    });
    return map;
  };
  const L = place(lots, lotTotal), S = place(sales, saleTotal);
  const soldPct = Math.round((saleTotal / lotTotal) * 100);
  const ribbons = [], bands = [];
  for (const e of graph.edges) {
    const a = L.get(e.source), b = S.get(e.target);
    if (!a || !b) continue;
    const aw = (e.qty_g / a.node.qty_g) * a.w, bw = (e.qty_g / b.node.qty_g) * b.w;
    const x0 = a.cursor, x1 = a.cursor + aw, t0 = b.cursor, t1 = b.cursor + bw;
    a.cursor += aw; b.cursor += bw;
    const my = (TOP + BAND + BOT) / 2;
    ribbons.push(svg('path', {
      d: `M${x0},${TOP + BAND} C${x0},${my} ${t0},${my} ${t0},${BOT} L${t1},${BOT} C${t1},${my} ${x1},${my} ${x1},${TOP + BAND} Z`,
      fill: e.margin_paise >= 0 ? 'var(--up)' : 'var(--down)', 'fill-opacity': .2
    }));
  }
  for (const { node, x: bx, w, cursor } of L.values()) {
    const soldW = cursor - bx;
    bands.push(svg('rect', { class: 'moband', x: bx, y: TOP, width: w, height: BAND, rx: 5,
      fill: costShade(node.rate_paise, low, high), onclick: () => onPick({ kind: 'lot', node }) }));
    if (soldW < w - 0.5) bands.push(svg('rect', { x: bx + soldW, y: TOP, width: w - soldW, height: BAND, rx: 5,
      fill: 'var(--bg)', 'fill-opacity': .62, 'pointer-events': 'none' }));
  }
  for (const { node, x: bx, w, cursor } of S.values()) {
    const covered = cursor - bx;
    bands.push(svg('rect', { class: 'moband', x: bx, y: BOT, width: w, height: BAND, rx: 5,
      fill: 'var(--up)', 'fill-opacity': .85, onclick: () => onPick({ kind: 'sale', node }) }));
    if (covered < w - 0.5) bands.push(svg('rect', { x: bx + covered, y: BOT, width: w - covered, height: BAND, rx: 5,
      fill: 'var(--down)', 'fill-opacity': .4, 'pointer-events': 'none' }));
  }
  return h('div', { class: 'moview' },
    h('div', { class: 'moview-side' }, h('span', {}, 'BOUGHT'), h('b', { class: 'num' }, f.qty(lotTotal))),
    svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'moview-svg' }, ...ribbons, ...bands),
    h('div', { class: 'moview-side bottom' },
      h('span', {}, 'SOLD'), h('b', { class: 'num' }, f.qty(saleTotal)), h('em', {}, `${soldPct}% of what was bought`)));
}

// ===================================================================== flow
const RANGES = [['7d', 7], ['30d', 30], ['90d', 90], ['All', 0]];
function daysAgo(days) {
  const d = new Date(); d.setDate(d.getDate() - days);
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export async function renderMobileFlow(root, appCtx) {
  ctx = appCtx || ctx;
  if (ctx.flowDays === undefined) ctx.flowDays = 30;
  const from = ctx.flowDays ? daysAgo(ctx.flowDays) : undefined;
  const [graph, posPage] = await Promise.all([
    api.graph({ product_id: ctx.productFilter || undefined, date_from: from, limit: 40 }),
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
  const idle = graph.nodes.filter(n => n.kind === 'lot' && n.remaining_g > 0).sort((a, b) => b.remaining_g - a.remaining_g);

  mount(root, h('div', { class: 'view' },
    h('div', { class: 'mflow', style: { paddingTop: '14px' } },
      h('div', { class: 'mchips g4 filter', style: { padding: '0 0 10px' } },
        ...RANGES.map(([text, days]) => h('button', {
          class: 'mchip' + (ctx.flowDays === days ? ' on' : ''), onclick: () => { ctx.flowDays = days; renderMobileFlow(root, ctx); }
        }, text))),
      h('select', { class: 'mselect', onchange: e => { ctx.productFilter = e.target.value ? +e.target.value : null; renderMobileFlow(root, ctx); } },
        h('option', { value: '' }, 'All products'),
        ...posPage.items.map(p => h('option', { value: p.product_id, selected: ctx.productFilter === p.product_id || undefined },
          `${p.product} — ${f.qty(p.stock_g)}`)))),
    h('div', { class: 'mflow' },
      overviewDiagram(graph, sel => {
        const box = document.getElementById('mo-detail');
        if (!box) return;
        const n = sel.node;
        mount(box, h('div', { class: 'mo-detail' },
          h('b', {}, `${n.party} · ${f.qty(n.qty_g)} @ ${f.rate(n.rate_paise)}`),
          h('span', {}, `${n.product} · ${n.deal_ref} · ${f.date(n.date)}${n.warehouse ? ' · ' + n.warehouse : ''}` +
            (sel.kind === 'lot' ? ` · ${f.qty(n.remaining_g)} still in stock` : ''))));
      }),
      h('div', { id: 'mo-detail' }, h('div', { class: 'mo-detail hint' }, h('span', {}, 'Each block is a trade, sized by quantity. Tap one to name it.'))),
      h('div', { class: 'mo-key' },
        h('span', {}, h('i', { style: { background: 'hsl(202 38% 62%)' } }), 'cheap stock'),
        h('span', {}, h('i', { style: { background: 'hsl(192 54% 34%)' } }), 'dear stock'),
        h('span', {}, h('i', { style: { background: 'var(--up)', opacity: .3 } }), 'profitable flow'),
        h('span', {}, h('i', { style: { background: 'var(--bg)', border: '1px solid var(--line-2)' } }), 'unsold'))),
    label('Sale by sale', h('span', {}, graph.truncated ? `newest ${sales.length} of ${graph.sales_total}` : `${sales.length} in range`)),
    h('div', { class: 'mflow' },
      ...sales.map(sale => {
        const sources = bySale.get(sale.id) || [];
        const margin = sources.reduce((s, e) => s + e.margin_paise, 0);
        const costs = sources.map(e => e.cost_paise);
        const low = Math.min(...costs), high = Math.max(...costs);
        const parts = sources.map(e => ({ qty: e.qty_g, color: costShade(e.cost_paise, low, high), label: (lots.get(e.source) || {}).party || '' }));
        if (sale.uncovered_g) parts.push({ qty: sale.uncovered_g, color: 'var(--down)', label: 'uncovered' });
        const c = h('div', { class: 'mflow-card' },
          h('div', { class: 'mflow-head' },
            h('span', { class: 'mflow-side sell' }, 'SOLD'),
            h('div', { class: 'grow' }, h('b', {}, sale.party), h('span', {}, `${f.date(sale.date)} · ${sale.product}${sale.warehouse ? ' · ' + sale.warehouse : ''}`)),
            h('div', { class: 'mflow-money' },
              h('b', { class: 'num ' + (margin >= 0 ? 'up' : 'down') }, f.inr(margin, { sign: true, compact: true })),
              h('span', { class: 'num' }, `${f.qty(sale.qty_g)} @ ${f.rate(sale.rate_paise)}`))),
          h('div', { class: 'mflow-links' },
            ...sources.map(e => {
              const lot = lots.get(e.source) || {};
              return h('div', { class: 'mflow-link' },
                h('i', { style: { background: e.margin_paise >= 0 ? 'var(--up)' : 'var(--down)' } }),
                h('span', { class: 'qty' }, f.qty(e.qty_g)),
                h('span', { class: 'who' }, `from ${lot.party || '—'} @ ${f.rate(e.cost_paise)}`),
                h('span', { class: 'pl ' + (e.margin_paise >= 0 ? 'up' : 'down') }, f.rateDelta(e.margin_rate_paise)));
            }),
            sources.length ? null : h('div', { class: 'mflow-empty' }, 'No stock allocated.')));
        if (parts.length > 1) c.insertBefore(proportionBar(parts), c.querySelector('.mflow-links'));
        if (parts.length) {
          c.onclick = () => {
            const open = c.querySelector('.mfan');
            if (open) { open.remove(); return; }
            c.appendChild(fanDiagram(sources.map(e => {
              const lot = lots.get(e.source) || {};
              return { qty: e.qty_g, color: costShade(e.cost_paise, low, high), good: e.margin_paise >= 0,
                       short: f.qty(e.qty_g, { short: true }), label: `${lot.party || '—'} @ ${f.rate(e.cost_paise)}` };
            }), { qty: sale.qty_g, short: `${sale.party} · ${f.qty(sale.qty_g)} @ ${f.rate(sale.rate_paise)}` }, { targetColor: 'var(--up)' }));
          };
        }
        return c;
      }),
      sales.length ? null : h('div', { class: 'empty' }, h('h3', {}, 'No sales in this window'), h('div', {}, 'Try a longer range.'))),
    idle.length ? [
      label('Still in stock'),
      h('div', { class: 'mflow' }, ...idle.slice(0, 20).map(lot => {
        const gone = byLot.get(lot.id) || [];
        const goneParts = gone.map(e => ({ qty: e.qty_g, color: e.margin_paise >= 0 ? 'var(--up)' : 'var(--down)', label: 'sold' }));
        goneParts.push({ qty: lot.remaining_g, color: 'var(--line-2)', label: 'in stock' });
        return h('div', { class: 'mflow-card' },
          h('div', { class: 'mflow-head' },
            h('span', { class: 'mflow-side lot' }, 'HELD'),
            h('div', { class: 'grow' }, h('b', {}, lot.party), h('span', {}, `${f.date(lot.date)} · ${lot.product}`)),
            h('div', { class: 'mflow-money' }, h('b', { class: 'num' }, f.qty(lot.remaining_g)),
              h('span', { class: 'num' }, `of ${f.qty(lot.qty_g)} @ ${f.rate(lot.rate_paise)}`))),
          goneParts.length > 1 ? proportionBar(goneParts, { thin: true }) : null);
      }))
    ] : null));
}
