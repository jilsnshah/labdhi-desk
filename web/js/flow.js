// Flow: the lineage graph. Every kilo bought is a band on the left; every
// kilo sold is a band on the right; the ribbon between them is the actual
// allocation, coloured by the margin it earned. What is still in stock stays
// as an unconnected stub — you can see idle money.

import { h, mount, svg, costTint, pnlClass, searchBar } from './ui.js';
import * as f from './fmt.js';
import { api } from './api.js';

const W = 1040, LOT_X = 250, LOT_W = 26, SALE_X = 764, GAP = 9, MIN_H = 16;

// A lineage graph that covers the whole book becomes unreadable within a few
// hundred trades, so the window is a date range - anchored on sales, with the
// lots that fed them pulled in whatever their own date.
function shiftDays(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

const RANGES = [
  ['7 days', () => [shiftDays(-7), shiftDays(0)]],
  ['30 days', () => [shiftDays(-30), shiftDays(0)]],
  ['90 days', () => [shiftDays(-90), shiftDays(0)]],
  ['All time', () => ['', '']]
];

export async function renderFlow(root, ctx) {
  if (ctx.flowFrom === undefined) { ctx.flowFrom = shiftDays(-30); ctx.flowTo = shiftDays(0); }

  const [posPage, graph] = await Promise.all([
    api.positions({ limit: 40, q: ctx.flowQuery || '' }),
    api.graph({
      sku_id: ctx.skuFilter || undefined,
      date_from: ctx.flowFrom || undefined,
      date_to: ctx.flowTo || undefined,
      limit: 60
    })
  ]);
  const positions = posPage.positions;
  const view = h('div', { class: 'view' });
  mount(root, view);

  const reload = () => renderFlow(root, ctx);
  const setRange = (from, to) => { ctx.flowFrom = from; ctx.flowTo = to; reload(); };

  mount(view,
    h('div', { class: 'section-head' },
      h('h2', {}, 'Flow of material'), h('i', { class: 'rule' }),
      h('span', { class: 'dim', style: { fontSize: '14px' } },
        graph.truncated
          ? `newest ${graph.sales_shown} of ${graph.sales_total} sales`
          : `${graph.sales_shown} sale${graph.sales_shown === 1 ? '' : 's'} in range`)),

    h('div', { class: 'range' },
      h('input', {
        type: 'date', value: ctx.flowFrom,
        oninput: e => setRange(e.target.value, ctx.flowTo)
      }),
      h('span', { class: 'to' }, 'to'),
      h('input', {
        type: 'date', value: ctx.flowTo,
        oninput: e => setRange(ctx.flowFrom, e.target.value)
      }),
      ...RANGES.map(([label, fn]) => {
        const [from, to] = fn();
        const on = ctx.flowFrom === from && ctx.flowTo === to;
        return h('button', { class: 'chip' + (on ? ' on' : ''), onclick: () => setRange(from, to) }, label);
      }),
      graph.truncated
        ? h('span', { class: 'range-note' }, 'narrow the dates to see the rest')
        : null),

    searchBar('Filter the material list…', term => { ctx.flowQuery = term; reload(); },
      { value: ctx.flowQuery || '' }),

    h('div', { class: 'chips', style: { marginBottom: '16px' } },
      h('button', {
        class: 'chip' + (ctx.skuFilter ? '' : ' on'),
        onclick: () => { ctx.skuFilter = null; reload(); }
      }, 'All materials'),
      ...positions.map(p => h('button', {
        class: 'chip' + (ctx.skuFilter === p.sku_id ? ' on' : ''),
        onclick: () => { ctx.skuFilter = p.sku_id; reload(); }
      }, p.material, h('small', {}, f.qty(p.stock_g, { short: true })))),
      posPage.matched > positions.length
        ? h('span', { class: 'dim', style: { alignSelf: 'center', fontSize: '14px' } },
            `+${posPage.matched - positions.length} more — use the filter`)
        : null),

    graph.nodes.length
      ? h('div', { class: 'flow-wrap' }, diagram(graph, ctx),
          h('div', { class: 'flow-legend' },
            h('span', {}, '◧ left = purchase lots · right = sales'),
            h('span', { class: 'up' }, '▬ profitable allocation'),
            h('span', { class: 'down' }, '▬ loss-making allocation'),
            h('span', { class: 'dim' }, '▨ still in stock')))
      : h('div', { class: 'empty' },
          h('h3', {}, 'Nothing in this window'),
          h('div', {}, 'Widen the dates, or pick All time.')),
    h('div', { id: 'flow-detail' }));
  return view;
}

function diagram(graph, ctx) {
  const lots = graph.nodes.filter(n => n.kind === 'lot');
  const sales = graph.nodes.filter(n => n.kind === 'sale');
  const totalLeft = lots.reduce((s, n) => s + n.qty_g, 0);
  const totalRight = sales.reduce((s, n) => s + n.qty_g, 0);
  const biggest = Math.max(totalLeft, totalRight, 1);

  const rows = Math.max(lots.length, sales.length);
  const H = Math.max(360, Math.min(1500, rows * 62));
  const usable = H - 40 - GAP * Math.max(0, rows - 1);
  const scale = usable / biggest;
  const hOf = q => Math.max(MIN_H, q * scale);

  const place = list => {
    let y = 20;
    const map = new Map();
    for (const n of list) {
      const height = hOf(n.qty_g);
      map.set(n.id, { node: n, y, h: height, cursor: y });
      y += height + GAP;
    }
    return map;
  };
  const L = place(lots), R = place(sales);

  // Colour cost relative to its OWN material - a PVC lot and an HDPE lot are
  // not expensive or cheap against each other.
  const band = new Map();
  for (const n of lots) {
    const b = band.get(n.material) || { low: Infinity, high: -Infinity };
    b.low = Math.min(b.low, n.rate_paise); b.high = Math.max(b.high, n.rate_paise);
    band.set(n.material, b);
  }
  const tintOf = n => {
    const b = band.get(n.material) || { low: 0, high: 0 };
    return costTint(n.rate_paise, b.low, b.high);
  };

  const ribbons = [], nodesG = [];

  for (const e of graph.edges) {
    const a = L.get(e.source), b = R.get(e.target);
    if (!a || !b) continue;
    const ah = Math.max(1.5, e.qty_g * scale), bh = Math.max(1.5, e.qty_g * scale);
    const y0 = a.cursor, y1 = b.cursor;
    a.cursor += ah; b.cursor += bh;
    const x0 = LOT_X + LOT_W, x1 = SALE_X;
    const cx = (x0 + x1) / 2;
    const d = `M${x0},${y0} C${cx},${y0} ${cx},${y1} ${x1},${y1}
               L${x1},${y1 + bh} C${cx},${y1 + bh} ${cx},${y0 + ah} ${x0},${y0 + ah} Z`;
    const good = e.margin_paise >= 0;
    ribbons.push(svg('path', {
      class: 'ribbon', d,
      fill: good ? 'var(--up)' : 'var(--down)', 'fill-opacity': .34,
      onclick: () => showEdge(e, a.node, b.node)
    }, svg('title', {},
      `${a.node.party} → ${b.node.party}\n${f.qty(e.qty_g)} · cost ${f.rate(e.cost_paise)} → sold ${f.rate(e.sale_rate_paise)}\nmargin ${f.inr(e.margin_paise, { sign: true })}`)));
  }

  for (const { node, y, h: hh, cursor } of L.values()) {
    const soldH = cursor - y;
    nodesG.push(svg('g', { class: 'gnode', onclick: () => showNode('lot', node.lot_id) },
      svg('rect', { x: LOT_X, y, width: LOT_W, height: hh, rx: 5,
        fill: tintOf(node), 'fill-opacity': .95 }),
      soldH < hh - .5 ? svg('rect', {
        x: LOT_X, y: y + soldH, width: LOT_W, height: hh - soldH, rx: 5,
        fill: 'url(#hatch)', stroke: 'var(--line-2)'
      }) : null,
      svg('text', { class: 't1', x: LOT_X - 12, y: y + 13, 'text-anchor': 'end' }, node.party),
      svg('text', { x: LOT_X - 12, y: y + 27, 'text-anchor': 'end' },
        `${f.qty(node.qty_g)} @ ${f.rate(node.rate_paise)}`),
      hh > 42 ? svg('text', { x: LOT_X - 12, y: y + 40, 'text-anchor': 'end', fill: 'var(--ink-3)' },
        `${node.deal_ref} · ${f.date(node.date)}${node.remaining_g ? ' · ' + f.qty(node.remaining_g) + ' left' : ''}`) : null,
      svg('title', {}, `${node.deal_ref} · ${node.material}`)));
  }

  for (const { node, y, h: hh, cursor } of R.values()) {
    const covered = cursor - y;
    nodesG.push(svg('g', { class: 'gnode', onclick: () => showNode('sale', node.deal_id) },
      svg('rect', { x: SALE_X, y, width: LOT_W, height: hh, rx: 5, fill: 'var(--ink)', 'fill-opacity': .82 }),
      covered < hh - .5 ? svg('rect', {
        x: SALE_X, y: y + covered, width: LOT_W, height: hh - covered, rx: 5,
        fill: 'var(--down)', 'fill-opacity': .35, stroke: 'var(--down)'
      }) : null,
      svg('text', { class: 't1', x: SALE_X + LOT_W + 12, y: y + 13 }, node.party),
      svg('text', { x: SALE_X + LOT_W + 12, y: y + 27 },
        `${f.qty(node.qty_g)} @ ${f.rate(node.rate_paise)}`),
      hh > 42 ? svg('text', { x: SALE_X + LOT_W + 12, y: y + 40, fill: 'var(--ink-3)' },
        `${node.deal_ref} · ${f.date(node.date)}`) : null));
  }

  return svg('svg', { class: 'flow-svg', viewBox: `0 0 ${W} ${H}`, style: `min-height:${Math.min(H, 900)}px` },
    svg('defs', {}, svg('pattern', {
      id: 'hatch', width: 6, height: 6, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)'
    }, svg('rect', { width: 6, height: 6, fill: 'var(--bg)' }),
       svg('line', { x1: 0, y1: 0, x2: 0, y2: 6, stroke: 'var(--line-2)', 'stroke-width': 3 }))),
    ...ribbons, ...nodesG);
}

function showEdge(e, lot, sale) {
  mount(document.getElementById('flow-detail'),
    h('div', { class: 'detail' },
      h('div', { style: { fontWeight: 650, marginBottom: '8px' } },
        `${f.qty(e.qty_g)} · ${lot.party} → ${sale.party}`),
      h('div', { class: 'chipline' },
        h('span', { class: 'tag' }, `bought ${f.rate(e.cost_paise)} · ${lot.deal_ref}`),
        h('span', { class: 'tag' }, `sold ${f.rate(e.sale_rate_paise)} · ${sale.deal_ref}`),
        h('span', { class: 'tag ' + pnlClass(e.margin_paise) },
          `${f.rateDelta(e.margin_rate_paise)}/MT = ${f.inr(e.margin_paise, { sign: true })}`),
        h('span', { class: 'tag' }, `picked by ${e.method}`))));
}

export async function showNode(kind, id) {
  const data = await api.trace(kind, id);
  const box = document.getElementById('flow-detail');
  if (!box || !data.node) return;
  const n = data.node;
  box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  if (kind === 'lot') {
    mount(box, h('div', { class: 'detail' },
      h('div', { style: { fontWeight: 650, marginBottom: '4px' } },
        `${n.material} · ${f.qty(n.qty_g)} from ${n.supplier_name} @ ${f.rate(n.rate_paise)}`),
      h('div', { class: 'muted', style: { fontSize: '12px', marginBottom: '10px' } },
        `${n.deal_ref} · ${f.date(n.deal_date)} · ${f.qty(n.available_g)} still in stock · earned ${f.inr(n.margin_paise, { sign: true })}`),
      h('div', { class: 'lineage' },
        ...n.outflows.map(o => h('div', { class: 'lin' },
          h('i', { class: 'pipe', style: { background: o.margin_paise >= 0 ? 'var(--up)' : 'var(--down)' } }),
          h('b', {}, f.qty(o.qty_g)), h('span', { class: 'muted' }, '→'),
          h('b', {}, o.customer_name),
          h('span', { class: 'dim num' }, `@ ${f.rate(o.sale_rate_paise)} · ${o.sale_ref} · ${f.date(o.sale_date)}`),
          h('span', { class: 'grow' }),
          h('b', { class: 'num ' + pnlClass(o.margin_paise) }, f.inr(o.margin_paise, { sign: true })))),
        n.available_g > 0 ? h('div', { class: 'lin' },
          h('i', { class: 'pipe', style: { background: 'var(--line-2)' } }),
          h('b', {}, f.qty(n.available_g)), h('span', { class: 'muted' }, 'still unsold')) : null)));
  } else {
    mount(box, h('div', { class: 'detail' },
      h('div', { style: { fontWeight: 650, marginBottom: '4px' } },
        `${n.ref} · sold ${f.qty(n.qty_g)} ${n.material} to ${n.party_name} @ ${f.rate(n.rate_paise)}`),
      h('div', { class: 'muted', style: { fontSize: '12px', marginBottom: '10px' } },
        `${f.date(n.deal_date)} · avg cost ${f.rate(n.cost_paise)} · margin ${f.inr(n.margin_paise, { sign: true })}`),
      h('div', { class: 'lineage' },
        ...n.allocations.map(a => h('div', { class: 'lin' },
          h('i', { class: 'pipe', style: { background: a.margin_paise >= 0 ? 'var(--up)' : 'var(--down)' } }),
          h('b', {}, f.qty(a.qty_g)), h('span', { class: 'muted' }, '←'),
          h('b', {}, a.supplier_name),
          h('span', { class: 'dim num' }, `@ ${f.rate(a.cost_paise)} · ${a.buy_ref} · ${f.date(a.buy_date)}`),
          h('span', { class: 'grow' }),
          h('b', { class: 'num ' + pnlClass(a.margin_paise) }, f.inr(a.margin_paise, { sign: true })))),
        n.uncovered_g > 0 ? h('div', { class: 'lin down' },
          h('i', { class: 'pipe', style: { background: 'var(--down)' } }),
          h('b', {}, f.qty(n.uncovered_g)), h('span', {}, 'uncovered — short position')) : null)));
  }
}
