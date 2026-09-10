// Shell: state, routing, keyboard. Every screen is a full page — a trade
// deserves the whole window. The only overlays are the record forms and
// pickers, which return to exactly where the trader was.
import { h, mount, $, toast } from './ui.js';
import { api, setTokenPrompt, onSlow } from './api.js';
import { renderDesk, renderTicker } from './desk.js';
import { renderFlow } from './flow.js';
import { renderTape, renderPosition } from './tape.js';
import { renderStock } from './stock.js';
import { startTrade, renderTrade } from './trade.js';
import { renderSetup } from './setup.js';
import {
  mountShell, renderMobileDesk, renderMobileFlow, renderMobileTape, renderMobileStock,
  renderMobilePosition, renderMobileSetup, syncTabs, paintHeader
} from './mobile.js';
import { startTicket } from './mticket.js';

// A phone is a different product, not a narrower window: it gets its own shell
// and its own buy/sell flow. The breakpoint is watched rather than read once.
const phone = window.matchMedia('(max-width: 760px)');
const isPhone = () => phone.matches;

const ctx = {
  boot: null, summary: null, route: 'desk', param: null, productFilter: null,
  go, refresh, openPosition, openDeal, sell
};

const main = $('#main'), tickerEl = $('#ticker');

async function boot() {
  document.body.classList.toggle('phone', isPhone());
  if (isPhone()) mountShell(ctx);
  ctx.boot = await api.bootstrap();
  ctx.summary = ctx.boot.summary;
  $('#company').textContent = ctx.boot.settings.company_name || 'Trading Desk';
  renderTicker(tickerEl, ctx.summary);
  if (isPhone()) paintHeader(ctx.summary);
  paint();
}

phone.addEventListener('change', () => {
  document.body.classList.toggle('phone', isPhone());
  if (isPhone() && !document.querySelector('.mhead')) mountShell(ctx);
  if (isPhone() && ctx.summary) paintHeader(ctx.summary);
  paint();
});

async function refresh() {
  ctx.summary = await api.summary();
  renderTicker(tickerEl, ctx.summary);
  if (isPhone()) paintHeader(ctx.summary);
  if (ctx.route === 'desk') paint();
}

function go(route, param = null) {
  ctx.route = route; ctx.param = param;
  document.body.classList.toggle('trading', route === 'trade');
  for (const b of document.querySelectorAll('.nav button')) b.classList.toggle('on', b.dataset.route === route);
  if (isPhone()) syncTabs();
  main.scrollTop = 0;
  paint();
}

function paint() {
  document.body.classList.toggle('on-desk', ctx.route === 'desk');
  const P = isPhone();
  const r = ctx.route;
  if (r === 'desk') (P ? renderMobileDesk : renderDesk)(main, ctx.summary, ctx);
  else if (r === 'stock') (P ? renderMobileStock : renderStock)(main, ctx);
  else if (r === 'flow') (P ? renderMobileFlow : renderFlow)(main, ctx);
  else if (r === 'tape') (P ? renderMobileTape : renderTape)(main, ctx);
  else if (r === 'position') (P ? renderMobilePosition : renderPosition)(main, ctx.param, ctx);
  else if (r === 'trade') renderTrade(main);
  else if (r === 'setup') (P ? renderMobileSetup : renderSetup)(main, ctx);
}

// opts.product: {id, display} to open the ticket on a product already chosen
function trade(side, opts = {}) {
  if (isPhone()) { startTicket(side, opts, ctx); return; }
  startTrade(side, opts, ctx);
  go('trade');
}

function openPosition(productId) { go('position', productId); }

async function openDeal(dealId) {
  const d = await api.deal(dealId);
  go('tape');
  toast(`${d.ref} · ${d.side.toUpperCase()} ${d.product} with ${d.party_name}`);
}

function sell(pos) {
  trade('sell', { product: { id: pos.product_id, display: pos.product } });
}

ctx.trade = trade;

// ---------------------------------------------------------------- keyboard
window.addEventListener('keydown', e => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
  if (typing || e.metaKey || e.ctrlKey || e.altKey || document.querySelector('.modal')) return;
  const k = e.key.toLowerCase();
  if (k === 'b') { e.preventDefault(); trade('buy'); }
  else if (k === 's') { e.preventDefault(); trade('sell'); }
  else if (k === 'escape' && ctx.route === 'trade') go('desk');
  else if (k === '1') go('desk');
  else if (k === '2') go('stock');
  else if (k === '3') go('flow');
  else if (k === '4') go('tape');
  else if (k === '5') go('setup');
  else if (k === 'u') {
    api.undo().then(() => { toast('Reversed the last action'); refresh(); if (ctx.route !== 'trade') paint(); })
      .catch(err => toast(err.message, { kind: 'err' }));
  }
});

// The server is asleep more often than it is broken. Say so.
const waking = h('div', { class: 'waking', hidden: true },
  h('div', { class: 'waking-card' },
    h('div', { class: 'waking-spin' }),
    h('div', {},
      h('b', {}, 'Waking the server'),
      h('span', {}, 'It sleeps when idle to stay free. This takes about a minute, once.'))));
document.body.appendChild(waking);
onSlow(on => { waking.hidden = !on; });

// ---------------------------------------------------------------- unlock
setTokenPrompt(() => new Promise(resolve => {
  const input = h('input', {
    type: 'password', placeholder: 'Password', autocomplete: 'current-password',
    onkeydown: e => { if (e.key === 'Enter') submit(); }
  });
  const card = h('div', { class: 'unlock-card' },
    h('div', { class: 'unlock-title' }, 'Labdhi Desk'),
    h('div', { class: 'unlock-sub' }, 'Enter the password to open the book.'),
    input,
    h('button', { class: 'confirm buy', onclick: () => submit() }, 'Unlock'));
  const screen = h('div', { class: 'unlock' }, card);
  document.body.appendChild(screen);
  setTimeout(() => input.focus(), 50);

  function submit() {
    const value = input.value.trim();
    if (!value) { input.focus(); return; }
    screen.remove();
    resolve(value);
  }
}));

document.querySelectorAll('.nav button').forEach(b => b.onclick = () => go(b.dataset.route));
$('#btn-buy').onclick = () => trade('buy');
$('#btn-sell').onclick = () => trade('sell');

boot().catch(err => mount(main, h('div', { class: 'empty' },
  h('h3', {}, 'Could not reach the desk'), h('div', {}, err.message))));
