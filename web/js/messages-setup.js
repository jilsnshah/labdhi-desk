// Setup > Messages: what the two WhatsApp messages say.
//
// The trader writes them himself. A {token} is filled in from the sauda when
// the message is sent, and the preview below shows the result against the last
// sauda on the book, so the effect of an edit is visible while typing.
//
// The same editor is used on the desktop and the phone - it is one column of
// cards, which is what the phone wants anyway.
import { h, mount, toast } from './ui.js';
import { api } from './api.js';
import { DEFAULTS, TOKENS, render, values } from './message.js';

const KEY = { party: 'sauda_template', internal: 'internal_template' };
const TITLE = {
  party: ['To the party', 'The confirmation a buyer or supplier receives. Keep cost and margin out of it.'],
  internal: ['Internal', 'The firm\'s own copy, pasted into your WhatsApp group. This is the one that may carry cost and margin.']
};

export async function renderMessages(root, ctx, { phone = false } = {}) {
  const settings = (ctx.boot && ctx.boot.settings) || {};
  const sample = await latestDeal();
  const draft = {
    party: settings[KEY.party] || DEFAULTS.party,
    internal: settings[KEY.internal] || DEFAULTS.internal
  };

  const view = kind => {
    const [name, why] = TITLE[kind];
    const preview = h('pre', { class: 'wa-msg msg-preview' }, '');
    const area = h('textarea', {
      class: 'fld-input msg-area', rows: phone ? 10 : 14, spellcheck: 'false',
      oninput: e => { draft[kind] = e.target.value; paint(); }
    });
    area.value = draft[kind];
    const paint = () => {
      preview.textContent = sample
        ? render(draft[kind], values(sample, settings, { stock_after: undefined }))
        : 'Book a sauda and the preview appears here.';
    };
    paint();

    const save = async () => {
      try {
        const saved = await api.settings({ [KEY[kind]]: draft[kind] });
        if (ctx.boot) ctx.boot.settings = saved;
        toast(`${name} message saved`);
      } catch (err) { toast(err.message, { kind: 'err', ms: 8000 }); }
    };
    const reset = () => {
      draft[kind] = DEFAULTS[kind];
      area.value = draft[kind];
      paint();
      toast('Back to the standard wording — save to keep it');
    };

    return h('div', { class: 'msg-card' },
      h('div', { class: 'section-head' }, h('h2', {}, name), h('i', { class: 'rule' })),
      h('div', { class: 'msg-why' }, why),
      area,
      h('div', { class: 'msg-actions' },
        h('button', { class: 'form-save', onclick: save }, 'Save'),
        h('button', { class: 'form-cancel', onclick: reset }, 'Use the standard wording')),
      h('div', { class: 'msg-label' }, sample ? `Preview · ${sample.ref}` : 'Preview'),
      preview);
  };

  mount(root, h('div', { class: 'view msg-view' },
    view('party'),
    view('internal'),
    h('div', { class: 'msg-card' },
      h('div', { class: 'section-head' }, h('h2', {}, 'What you can put in'), h('i', { class: 'rule' })),
      h('div', { class: 'msg-why' }, 'Tap a token to copy it, then paste it into either message.'),
      ...TOKENS.map(([group, list]) => h('div', { class: 'msg-group' },
        h('div', { class: 'msg-label' }, group),
        h('div', { class: 'chips' }, ...list.map(token => h('button', {
          class: 'chip mono', onclick: async () => {
            try { await navigator.clipboard.writeText(`{${token}}`); toast(`{${token}} copied`); }
            catch (_) { toast(`Type it as {${token}}`); }
          }
        }, `{${token}}`))))))));
}

async function latestDeal() {
  try {
    const list = await api.deals({ limit: 1, status: 'booked' });
    return list.items.length ? await api.deal(list.items[0].id) : null;
  } catch (_) { return null; }
}
