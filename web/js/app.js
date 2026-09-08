// Shell: state, routing, keyboard. Every screen is a full page — nothing in
// this app opens in a dialog, because a trade deserves the whole window.
import { h, mount, $, toast } from './ui.js';
import { api } from './api.js';
import { renderDesk, renderTicker } from './desk.js';
import { renderFlow } from './flow.js';
import { renderTape, renderPosition } from './tape.js';
import { startTrade, renderTrade } from './trade.js';
import { renderSetup } from './setup.js';

const ctx = {
  boot: null, desk: null, route: 'desk', param: null, skuFilter: null,
  go, refresh, openPosition, openDeal, sell
};

const main = $('#main'), tickerEl = $('#ticker');

async function boot() {
  ctx.boot = await api.bootstrap();
  ctx.desk = ctx.boot.desk;
  $('#company').textContent = ctx.boot.settings.company_name || 'Trading Desk';
  renderTicker(tickerEl, ctx.desk);
  paint();
}

async function refresh() {
  ctx.boot = await api.bootstrap();
  ctx.desk = ctx.boot.desk;
  renderTicker(tickerEl, ctx.desk);
  if (ctx.route !== 'trade') paint();
}

function go(route, param = null) {
  ctx.route = route; ctx.param = param;
  // On a phone the bottom nav and the Book bar would fight for the same strip,
  // so the trade screen owns it alone.
  document.body.classList.toggle('trading', route === 'trade');
  for (const b of document.querySelectorAll('.nav button')) b.classList.toggle('on', b.dataset.route === route);
  main.scrollTop = 0;
  paint();
}

function paint() {
  if (ctx.route === 'desk') renderDesk(main, ctx.desk, ctx);
  else if (ctx.route === 'flow') renderFlow(main, ctx);
  else if (ctx.route === 'tape') renderTape(main, ctx);
  else if (ctx.route === 'position') renderPosition(main, ctx.param, ctx);
  else if (ctx.route === 'trade') renderTrade(main);
  else if (ctx.route === 'setup') renderSetup(main, ctx);
}

function trade(side, opts = {}) {
  startTrade(side, opts, ctx);
  go('trade');
}

function openPosition(skuId) { go('position', skuId); }

async function openDeal(dealId) {
  const d = await api.deal(dealId);
  go('tape');
  toast(`${d.ref} · ${d.side.toUpperCase()} ${d.material} with ${d.party_name}`);
}

function sell(pos) {
  trade('sell', { sku: { id: pos.sku_id, display: pos.material } });
}

// ---------------------------------------------------------------- keyboard
window.addEventListener('keydown', e => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if (k === 'b') { e.preventDefault(); trade('buy'); }
  else if (k === 's') { e.preventDefault(); trade('sell'); }
  else if (k === 'escape' && ctx.route === 'trade') go('desk');
  else if (k === '1') go('desk');
  else if (k === '2') go('flow');
  else if (k === '3') go('tape');
  else if (k === '4') go('setup');
  else if (k === 'u') {
    api.undo().then(r => { toast('Reversed ' + (r.deal ? r.deal.ref : '')); refresh(); })
      .catch(err => toast(err.message, { kind: 'err' }));
  }
});

document.querySelectorAll('.nav button').forEach(b => b.onclick = () => go(b.dataset.route));
$('#btn-buy').onclick = () => trade('buy');
$('#btn-sell').onclick = () => trade('sell');

boot().catch(err => mount(main, h('div', { class: 'empty' },
  h('h3', {}, 'Could not reach the desk'), h('div', {}, err.message))));
