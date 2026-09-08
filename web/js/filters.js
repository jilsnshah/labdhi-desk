// A filter bar that stays on screen.
//
// Hiding filters behind a button hides what is *active*, which is how people
// end up staring at a list that is quietly excluding half the book. Here the
// controls are always visible and every applied filter also appears as a chip
// you can strike off, so the state of the list is never a mystery.
import { h, mount } from './ui.js';

export function makeFilters({ fields, onChange }) {
  const state = {};
  const options = {};
  for (const fld of fields) state[fld.key] = fld.value || '';

  const el = h('div', { class: 'filterbar' });

  function set(key, value) {
    state[key] = value || '';
    // Choosing higher up the material tree invalidates the rungs below it.
    const fld = fields.find(f => f.key === key);
    if (fld && fld.clears) for (const k of fld.clears) state[k] = '';
    paint();
    onChange(active());
  }

  function active() {
    const out = {};
    for (const [k, v] of Object.entries(state)) if (v) out[k] = v;
    return out;
  }

  function labelFor(fld, value) {
    const opt = (options[fld.key] || []).find(o => String(o.value) === String(value));
    return opt ? opt.label : value;
  }

  function control(fld) {
    if (fld.type === 'chips') {
      return h('div', { class: 'filt-chips' },
        ...fld.chips.map(c => h('button', {
          class: 'filt-chip' + (String(state[fld.key]) === String(c.value) ? ' on' : ''),
          onclick: () => set(fld.key, c.value)
        }, c.label)));
    }
    if (fld.type === 'date') {
      return h('label', { class: 'filt' },
        h('span', {}, fld.label),
        h('input', {
          type: 'date', value: state[fld.key] || '',
          oninput: e => set(fld.key, e.target.value)
        }));
    }
    const opts = options[fld.key] || [];
    const disabled = fld.needs && !state[fld.needs];
    return h('label', { class: 'filt' + (disabled ? ' off' : '') },
      h('span', {}, fld.label),
      h('select', {
        data: { k: fld.key }, disabled: disabled || undefined,
        onchange: e => set(fld.key, e.target.value)
      },
        h('option', { value: '' }, disabled ? `pick a ${fld.needs} first` : fld.any || 'Any'),
        ...opts.map(o => h('option', {
          value: o.value, selected: String(state[o.key || fld.key] ?? state[fld.key]) === String(o.value) || undefined
        }, o.label))));
  }

  function paint() {
    const chosen = fields.filter(f => state[f.key] && f.type !== 'chips');
    mount(el,
      h('div', { class: 'filt-row' }, ...fields.map(control)),
      chosen.length
        ? h('div', { class: 'filt-active' },
            ...chosen.map(f => h('button', {
              class: 'filt-tag', onclick: () => set(f.key, '')
            }, `${f.label}: ${labelFor(f, state[f.key])}`, h('span', {}, '×'))),
            h('button', {
              class: 'filt-clear',
              onclick: () => { for (const f of fields) state[f.key] = f.value || ''; paint(); onChange(active()); }
            }, 'Clear all'))
        : null);
    // <select> value must be set as a property; the attribute only seeds it.
    for (const fld of fields) {
      if (fld.type === 'chips' || fld.type === 'date') continue;
      const sel = el.querySelector(`select[data-k="${fld.key}"]`);
      if (sel) sel.value = state[fld.key] || '';
    }
  }

  paint();
  return {
    el, state,
    values: active,
    setOptions(key, list) { options[key] = list; paint(); },
    get(key) { return state[key]; }
  };
}
