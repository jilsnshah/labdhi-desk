// Tiny DOM layer. No framework: the whole app is four screens and staying
// close to the metal keeps every interaction under one frame.

export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') setStyle(el, v);
    else if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'data') for (const [dk, dv] of Object.entries(v)) el.dataset[dk] = dv;
    else el.setAttribute(k, v === true ? '' : v);
  }
  add(el, children);
  return el;
}

// Object.assign silently drops custom properties (--tint, --tone), so they
// have to go through setProperty.
function setStyle(el, style) {
  for (const [k, v] of Object.entries(style)) {
    if (v === null || v === undefined) continue;
    if (k.startsWith('--')) el.style.setProperty(k, v);
    else el.style[k] = v;
  }
}

function add(el, children) {
  for (const c of children.flat(9)) {
    if (c === null || c === undefined || c === false) continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export const svg = (tag, props = {}, ...children) => {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2).toLowerCase(), v);
    else el.setAttribute(k, v);
  }
  for (const c of children.flat(9)) if (c) el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
};

export const mount = (node, ...children) => { node.textContent = ''; add(node, children); return node; };
export const $ = sel => document.querySelector(sel);

// ---------------------------------------------------------------- toast
let toastTimer = null;
export function toast(message, { kind = 'ok', action = null, actionLabel = 'Undo', ms = 6000 } = {}) {
  const host = $('#toasts');
  const el = h('div', { class: `toast toast-${kind}` },
    h('span', { class: 'toast-msg' }, message),
    action && h('button', {
      class: 'toast-action',
      onclick: async () => { el.remove(); await action(); }
    }, actionLabel)
  );
  host.appendChild(el);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), ms);
  return el;
}

// ---------------------------------------------------------------- feedback
export function celebrate(text, sub, tone = 'buy') {
  const el = h('div', { class: `flash flash-${tone}` },
    h('div', { class: 'flash-text' }, text),
    sub && h('div', { class: 'flash-sub' }, sub));
  document.body.appendChild(el);
  setTimeout(() => el.classList.add('out'), 700);
  setTimeout(() => el.remove(), 1200);
}

export function countUp(el, to, format, ms = 500) {
  const from = Number(el.dataset.value || 0);
  if (from === to) { el.textContent = format(to); return; }
  const t0 = performance.now();
  const step = now => {
    const p = Math.min(1, (now - t0) / ms);
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = format(Math.round(from + (to - from) * eased));
    if (p < 1) requestAnimationFrame(step);
    else el.dataset.value = to;
  };
  requestAnimationFrame(step);
}

// A cost scale inside one material: cheap stock reads green, dear stock reads
// amber-red. Tuned for a light ground, so every step stays legible on white.
export function costTint(rate, low, high) {
  if (!isFinite(low) || !isFinite(high) || high === low) return 'hsl(152 62% 32%)';
  const t = Math.max(0, Math.min(1, (rate - low) / (high - low)));
  return `hsl(${152 - t * 137} ${62 + t * 6}% ${32 + t * 12}%)`;
}

export const pnlClass = v => (v > 0 ? 'up' : v < 0 ? 'down' : 'flat');

// ---------------------------------------------------------------- search
// Built once and never re-created, so typing in it can refresh a list below
// without the box losing focus or a character.
export function searchBar(placeholder, onChange, { value = '', delay = 200 } = {}) {
  let timer = null;
  const input = h('input', {
    type: 'search', placeholder, value,
    oninput: e => {
      clearTimeout(timer);
      const term = e.target.value;
      timer = setTimeout(() => onChange(term.trim()), delay);
    },
    onkeydown: e => {
      if (e.key === 'Escape') { e.target.value = ''; clearTimeout(timer); onChange(''); }
      if (e.key === 'Enter') { clearTimeout(timer); onChange(e.target.value.trim()); }
    }
  });
  const bar = h('div', { class: 'searchbar' }, h('span', { class: 'dim' }, '⌕'), input);
  bar.input = input;
  return bar;
}

// A "Load more" footer that knows how much is left.
export function moreBar(onMore) {
  const count = h('span', { class: 'more-count' }, '');
  const btn = h('button', { class: 'more-btn', onclick: onMore }, 'Load more');
  const bar = h('div', { class: 'morebar' }, count, btn);
  bar.update = (shown, matched) => {
    count.textContent = matched ? `Showing ${shown} of ${matched}` : '';
    btn.hidden = shown >= matched;
    bar.hidden = !matched || matched <= shown;
  };
  bar.busy = on => { btn.disabled = on; btn.textContent = on ? 'Loading…' : 'Load more'; };
  return bar;
}
