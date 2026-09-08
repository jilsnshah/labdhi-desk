// Shell: state, routing, keyboard. Every screen is a full page — nothing in
// this app opens in a dialog, because a trade deserves the whole window.
import { h, mount, $, toast } from './ui.js';
import { api, setTokenPrompt, onSlow } from './api.js';
import { renderDesk, renderTicker } from './desk.js';
import { renderFlow } from './flow.js';
import { renderTape, renderPosition } from './tape.js';
import { startTrade, renderTrade } from './trade.js';
import { renderSetup } from './setup.js';
import { mountShell, renderMobileDesk, startTicket, syncTabs, paintHeader } from './mobile.js';

// A phone is a different product, not a narrower window: it gets its own shell
// and its own buy/sell flow. The breakpoint is watched rather than read once,
// so rotating a tablet swaps cleanly instead of stranding a half-built screen.
const phone = window.matchMedia('(max-width: 760px)');
const isPhone = () => phone.matches;

const ctx = {
  boot: null, desk: null, route: 'desk', param: null, skuFilter: null,
  go, refresh, openPosition, openDeal, sell
};

const main = $('#main'), tickerEl = $('#ticker');

async function boot() {
  document.body.classList.toggle('phone', isPhone());
  if (isPhone()) mountShell(ctx);
  ctx.boot = await api.bootstrap();
  ctx.desk = ctx.boot.desk;
  $('#company').textContent = ctx.boot.settings.company_name || 'Trading Desk';
  renderTicker(tickerEl, ctx.desk);
  if (isPhone()) paintHeader(ctx.desk);
  paint();
}

phone.addEventListener('change', () => {
  document.body.classList.toggle('phone', isPhone());
  if (isPhone() && !document.querySelector('.mhead')) mountShell(ctx);
  if (isPhone() && ctx.desk) paintHeader(ctx.desk);
  paint();
});

async function refresh() {
  ctx.boot = await api.bootstrap();
  ctx.desk = ctx.boot.desk;
  renderTicker(tickerEl, ctx.desk);
  if (isPhone()) paintHeader(ctx.desk);
  if (ctx.route !== 'trade') paint();
}

function go(route, param = null) {
  ctx.route = route; ctx.param = param;
  // On a phone the bottom nav and the Book bar would fight for the same strip,
  // so the trade screen owns it alone.
  document.body.classList.toggle('trading', route === 'trade');
  for (const b of document.querySelectorAll('.nav button')) b.classList.toggle('on', b.dataset.route === route);
  if (isPhone()) syncTabs();
  main.scrollTop = 0;
  paint();
}

function paint() {
  if (ctx.route === 'desk') {
    if (isPhone()) renderMobileDesk(main, ctx.desk);
    else renderDesk(main, ctx.desk, ctx);
  }
  else if (ctx.route === 'flow') renderFlow(main, ctx);
  else if (ctx.route === 'tape') renderTape(main, ctx);
  else if (ctx.route === 'position') renderPosition(main, ctx.param, ctx);
  else if (ctx.route === 'trade') renderTrade(main);
  else if (ctx.route === 'setup') renderSetup(main, ctx);
}

function trade(side, opts = {}) {
  if (isPhone()) {
    startTicket(side, opts.sku ? { sku_id: opts.sku.id, material: opts.sku.display } : {});
    return;
  }
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

ctx.trade = trade;

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
// A deployed desk is behind a shared token. Asking for it through a browser
// prompt works but looks like a phishing box, so it gets a real screen.
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
