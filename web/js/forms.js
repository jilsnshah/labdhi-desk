// Forms and pickers: one implementation for every master record, shared by
// the desktop and the phone. A form is a centred card on a wide screen and the
// same markup becomes a bottom sheet on a phone - CSS only.
//
// Every "Add new ..." in the app opens one of the forms below and resolves
// with the saved record, so a ticket can select what was just created without
// the trader retyping it anywhere. That is what keeps one record per party,
// warehouse and product.
import { h, mount, toast, debounce } from './ui.js';
import { api } from './api.js';
import * as f from './fmt.js';
import { pagedList } from './lists.js';

// ================================================================ the engine
export function openForm({ title, sub, fields, submit, submitLabel = 'Save', onChange, onOpen }) {
  return new Promise(resolve => {
    const values = {};
    const refs = {};
    const err = h('div', { class: 'form-err' });
    let busy = false;

    const form = {
      values,
      set(k, v) { values[k] = v; if (refs[k] && refs[k].set) refs[k].set(v); },
      hint(k, text, tone) {
        const r = refs[k]; if (!r) return;
        r.hint.textContent = text === undefined ? (r.base || '') : text;
        r.hint.className = 'fld-hint' + (tone ? ' ' + tone : '');
      },
      action(k, node) { if (refs[k]) mount(refs[k].action, node); },
      disable(k, on) {
        const r = refs[k]; if (!r) return;
        r.wrap.classList.toggle('off', !!on);
        for (const el of r.wrap.querySelectorAll('input,select,textarea,button')) el.disabled = !!on;
      },
      error(text) { err.textContent = text || ''; },
      close
    };

    const changed = (k, v) => { values[k] = v; if (onChange) onChange(k, v, form); };

    const body = fields.filter(Boolean).map(fld => {
      values[fld.key] = fld.value === undefined ? '' : fld.value;
      const hint = h('div', { class: 'fld-hint' }, fld.hint || '');
      const action = h('div', { class: 'fld-action' });
      const control = build(fld, values, changed, form);
      const wrap = h('div', { class: 'fld' + (fld.half ? ' half' : '') + (fld.type === 'custom' ? ' custom' : '') },
        fld.label ? h('label', { class: 'fld-label' }, fld.label, fld.required ? h('i', {}, ' *') : null) : null,
        control.el, hint, action);
      refs[fld.key] = { wrap, hint, action, set: control.set, base: fld.hint || '' };
      return wrap;
    });

    const saveBtn = h('button', { class: 'form-save', type: 'submit' }, submitLabel);
    const card = h('form', {
      class: 'modal-card', novalidate: true,
      onsubmit: e => { e.preventDefault(); save(); }
    },
      h('div', { class: 'modal-head' },
        h('div', {}, h('div', { class: 'modal-title' }, title), sub ? h('div', { class: 'modal-sub' }, sub) : null),
        h('button', { type: 'button', class: 'modal-x', onclick: () => close(null) }, '×')),
      h('div', { class: 'form-grid' }, ...body),
      err,
      h('div', { class: 'modal-foot' },
        h('button', { type: 'button', class: 'form-cancel', onclick: () => close(null) }, 'Cancel'),
        saveBtn));
    const overlay = h('div', { class: 'modal', onmousedown: e => { if (e.target === overlay) close(null); } }, card);
    const onKey = e => { if (e.key === 'Escape') close(null); };
    document.addEventListener('keydown', onKey);
    document.body.appendChild(overlay);
    const first = card.querySelector('[data-autofocus]') || card.querySelector('input:not([disabled]),textarea');
    if (first && !matchMedia('(max-width: 760px)').matches) setTimeout(() => first.focus(), 40);
    if (onOpen) onOpen(form);

    async function save() {
      if (busy) return;
      for (const fld of fields.filter(Boolean)) {
        const v = values[fld.key];
        if (fld.required && (v === undefined || v === null || String(v.id !== undefined ? v.id : v).trim() === '')) {
          err.textContent = `${fld.label} is required`;
          return;
        }
      }
      busy = true; saveBtn.disabled = true; err.textContent = '';
      try {
        const result = await submit({ ...values }, form);
        if (result !== undefined) close(result);
      } catch (e) {
        err.textContent = e.message;
      } finally {
        busy = false; saveBtn.disabled = false;
      }
    }

    function close(result) {
      overlay.remove();
      document.removeEventListener('keydown', onKey);
      resolve(result === undefined ? null : result);
    }
  });
}

function build(fld, values, changed, form) {
  const type = fld.type || 'text';
  if (type === 'custom') {
    const el = fld.render(form);
    return { el, set: () => {} };
  }
  if (type === 'choice') {
    const btns = fld.options.map(opt => h('button', {
      type: 'button', class: 'seg-btn' + (values[fld.key] === opt ? ' on' : ''),
      onclick: () => {
        const v = values[fld.key] === opt && !fld.always ? '' : opt;
        paint(v); changed(fld.key, v);
      }
    }, opt));
    const paint = v => btns.forEach(b => b.classList.toggle('on', b.textContent === v));
    return { el: h('div', { class: 'seg-row' }, ...btns), set: paint };
  }
  if (type === 'select') {
    const sel = h('select', { class: 'fld-input', onchange: e => changed(fld.key, e.target.value) });
    const fill = opts => {
      mount(sel, ...opts.map(o => h('option', { value: o.value }, o.label)));
      sel.value = values[fld.key] ?? '';
    };
    if (typeof fld.options === 'function') fld.options().then(fill); else fill(fld.options || []);
    return { el: sel, set: v => { sel.value = v ?? ''; } };
  }
  if (type === 'picker') {
    // A record chosen from its own list - never typed as free text.
    const label = h('span', {});
    const clear = h('button', { type: 'button', class: 'pick-clear', title: 'Clear',
      onclick: e => { e.stopPropagation(); changed(fld.key, null); paint(null); } }, '×');
    const btn = h('button', {
      type: 'button', class: 'pick-btn',
      onclick: async () => { const v = await fld.pick(values); if (v) { changed(fld.key, v); paint(v); } }
    }, label, clear);
    const paint = v => {
      label.textContent = v ? v.label : (fld.placeholder || 'Choose…');
      btn.classList.toggle('empty', !v); clear.hidden = !v;
    };
    paint(values[fld.key]);
    return { el: btn, set: paint };
  }

  const isArea = type === 'textarea';
  const input = h(isArea ? 'textarea' : 'input', {
    class: 'fld-input' + (fld.mono ? ' mono' : ''),
    type: isArea ? undefined : (type === 'suggest' ? 'text' : type),
    rows: isArea ? 2 : undefined,
    placeholder: fld.placeholder || '', autocomplete: 'off', spellcheck: 'false',
    inputmode: fld.inputmode, data: fld.autofocus ? { autofocus: '1' } : undefined,
    oninput: e => {
      let v = e.target.value;
      if (fld.upper) { const pos = e.target.selectionStart; v = v.toUpperCase(); e.target.value = v; e.target.setSelectionRange(pos, pos); }
      changed(fld.key, v);
      if (type === 'suggest') suggest(v);
    },
    onfocus: () => { if (type === 'suggest') suggest(input.value); },
    onblur: () => { if (type === 'suggest') setTimeout(() => { list.hidden = true; }, 160); }
  });
  input.value = values[fld.key] ?? '';

  let list = null;
  const suggest = type === 'suggest' ? debounce(async q => {
    const names = await fld.suggest(q, values).catch(() => []);
    mount(list, ...names.filter(n => n !== input.value).slice(0, 8).map(n => h('button', {
      type: 'button', class: 'sugg-item',
      onmousedown: e => e.preventDefault(),
      onclick: () => { input.value = n; changed(fld.key, n); list.hidden = true; }
    }, n)));
    list.hidden = !list.childNodes.length || document.activeElement !== input;
  }, 150) : null;
  if (type === 'suggest') list = h('div', { class: 'sugg', hidden: true });

  const chips = fld.chips ? h('div', { class: 'fld-chips' }, ...fld.chips.map(([label, fn]) => h('button', {
    type: 'button', class: 'chip small',
    onclick: () => { const v = fn(values); input.value = v; changed(fld.key, v); }
  }, label))) : null;

  return {
    el: h('div', { class: 'fld-box' }, input, list, chips),
    set: v => { if (document.activeElement !== input) input.value = v ?? ''; }
  };
}

// ================================================================ picker
// A full list of records to choose from: searchable, paged, and able to add a
// new record on the spot. The desktop terms and filters and the phone's
// transporter field all choose records through this.
export function pickEntity({ title, load, row, addLabel, onAdd, placeholder = 'Search…', pageSize = 20 }) {
  return new Promise(resolve => {
    let query = '';
    const list = pagedList({
      pageSize,
      load: p => load({ ...p, q: query }),
      row: item => {
        const r = row(item);
        return h('button', { type: 'button', class: 'pick-row', onclick: () => close(item) },
          h('div', { class: 'pick-main' }, h('b', {}, r.title), r.sub ? h('span', {}, r.sub) : null),
          r.tag ? h('span', { class: 'pick-tag' }, r.tag) : null);
      },
      empty: () => h('div', { class: 'empty small' }, query ? `Nothing matches "${query}"` : 'Nothing here yet')
    });
    const search = h('input', {
      class: 'fld-input', type: 'search', placeholder,
      oninput: debounce(e => { query = e.target.value.trim(); list.reload(); }, 200)
    });
    const card = h('div', { class: 'modal-card pick' },
      h('div', { class: 'modal-head' },
        h('div', { class: 'modal-title' }, title),
        h('button', { type: 'button', class: 'modal-x', onclick: () => close(null) }, '×')),
      search,
      onAdd ? h('button', {
        type: 'button', class: 'pick-add',
        onclick: async () => { const made = await onAdd(query); if (made) close(made); }
      }, addLabel || '+ Add new') : null,
      h('div', { class: 'pick-list' }, list.el));
    const overlay = h('div', { class: 'modal', onmousedown: e => { if (e.target === overlay) close(null); } }, card);
    const onKey = e => { if (e.key === 'Escape') close(null); };
    document.addEventListener('keydown', onKey);
    document.body.appendChild(overlay);
    if (!matchMedia('(max-width: 760px)').matches) setTimeout(() => search.focus(), 40);
    list.reload({});

    function close(v) { overlay.remove(); document.removeEventListener('keydown', onKey); resolve(v); }
  });
}

// ================================================================ records
const partySub = p => [p.gstin, p.state, p.phone].filter(Boolean).join(' · ') || 'no GSTIN';

function gstinWatcher(existingId) {
  const lookup = debounce(async (g, form) => {
    const r = await api.gstin(g).catch(() => null);
    if (!r || !r.party || r.party.id === existingId) return;
    form.hint('gstin', `Already saved as ${r.party.name}`, 'bad');
    form.action('gstin', h('button', {
      type: 'button', class: 'chip small on',
      onclick: async () => form.close(await api.party(r.party.id))
    }, `Use ${r.party.name}`));
  }, 250);

  return (raw, form) => {
    const g = String(raw || '').replace(/\s+/g, '').toUpperCase();
    form.action('gstin', null);
    if (!g) {
      form.disable('pan', false); form.disable('state_code', false); form.hint('gstin');
      return;
    }
    if (g.length < 15) {
      form.hint('gstin', `${15 - g.length} more character${15 - g.length === 1 ? '' : 's'}`);
      return;
    }
    const problem = f.gstinProblem(g);
    if (problem) { form.hint('gstin', 'This GSTIN ' + problem, 'bad'); return; }
    form.set('pan', g.slice(2, 12)); form.disable('pan', true);
    form.set('state_code', g.slice(0, 2)); form.disable('state_code', true);
    form.hint('gstin', 'Valid — PAN and state are read from it', 'good');
    lookup(g, form);
  };
}

let statesCache = null;
async function stateOptions() {
  if (!statesCache) statesCache = (await api.states({ limit: 100 })).items;
  return [{ value: '', label: 'Choose state' },
          ...statesCache.map(s => ({ value: s.code, label: `${s.name} (${s.code})` }))];
}

export async function partyForm(existing = null, prefill = {}) {
  const p = existing || {};
  const watch = gstinWatcher(p.id);
  const saved = await openForm({
    title: existing ? 'Edit party' : 'Add new party',
    sub: existing ? 'Changes show on every deal with this party.' : 'Saved once, then chosen from the list in every ticket.',
    submitLabel: existing ? 'Save' : 'Add party',
    fields: [
      { key: 'name', label: 'Party name', value: p.name || prefill.name || '', required: true,
        placeholder: 'e.g. Krishna Polymers', autofocus: true },
      { key: 'gstin', label: 'GSTIN', value: p.gstin || '', placeholder: '24ABCDE1234F1Z5', upper: true,
        mono: true, hint: 'Leave empty if the party has none' },
      { key: 'pan', label: 'PAN', value: p.pan || '', placeholder: 'ABCDE1234F', upper: true, mono: true, half: true },
      { key: 'state_code', label: 'State', type: 'select', value: p.state_code || '', options: stateOptions, half: true },
      { key: 'phone', label: 'Phone', type: 'tel', value: p.phone || '', placeholder: '+91 98250 00000', inputmode: 'tel' },
      { key: 'address', label: 'Address', type: 'textarea', value: p.address || '', placeholder: 'Full address' }
    ],
    onChange: (k, v, form) => { if (k === 'gstin') watch(v, form); },
    onOpen: form => watch(p.gstin || '', form),
    submit: async (v, form) => {
      try {
        return await api.partySave({ id: p.id, name: v.name, gstin: v.gstin, pan: v.pan,
                                     state_code: v.state_code, phone: v.phone, address: v.address });
      } catch (err) {
        // Refused as a duplicate: offer the record that already exists.
        const name = String(v.name || '').trim().toLowerCase();
        const hit = (await api.parties({ q: v.name, limit: 10 }).catch(() => ({ items: [] }))).items
          .find(x => x.name.toLowerCase() === name && x.id !== p.id);
        if (hit) form.action('name', h('button', {
          type: 'button', class: 'chip small on', onclick: () => form.close(hit)
        }, `Use ${hit.name}`));
        throw err;
      }
    }
  });
  if (saved && !existing) toast(`${saved.name} added`);
  return saved;
}

export async function warehouseForm(existing = null, prefill = {}) {
  const w = existing || {};
  const saved = await openForm({
    title: existing ? 'Edit warehouse' : 'Add new warehouse',
    sub: existing ? 'A rename shows on every lot and deal at once.' : 'Stock is kept warehouse by warehouse.',
    submitLabel: existing ? 'Save' : 'Add warehouse',
    fields: [
      { key: 'name', label: 'Warehouse name', value: w.name || prefill.name || '', required: true,
        placeholder: 'e.g. Mundra', autofocus: true },
      { key: 'address', label: 'Address', type: 'textarea', value: w.address || '', placeholder: 'Plot, area, city' }
    ],
    submit: v => api.warehouseSave({ id: w.id, name: v.name, address: v.address })
  });
  if (saved && !existing) toast(`${saved.name} added`);
  return saved;
}

export async function productForm(existing = null, prefill = {}) {
  const p = existing || {};
  const fixed = !!(existing && existing.deal_count);
  const preview = h('div', { class: 'prod-preview' }, '');
  const check = debounce(async v => {
    const name = [v.material, v.grade].map(x => (x || '').trim().toUpperCase()).filter(Boolean).join(' ');
    const maker = (v.manufacturer || '').trim();
    preview.textContent = name ? `${name}${maker ? ' · ' + maker : ''}` : '';
    preview.className = 'prod-preview';
    if (!name || !maker || existing) return;
    const hit = (await api.products({ q: maker, limit: 50 }).catch(() => ({ items: [] }))).items
      .find(x => x.display.toLowerCase() === `${name} · ${maker}`.toLowerCase());
    if (hit) { preview.textContent = `${hit.display} is already in your products — it will be selected`; preview.className = 'prod-preview known'; }
  }, 200);

  const materialId = async name => {
    const hit = (await api.materials({ q: name, limit: 20 })).items
      .find(m => m.name === String(name || '').trim().toUpperCase());
    return hit ? hit.id : null;
  };

  const saved = await openForm({
    title: existing ? 'Edit product' : 'Add new product',
    sub: fixed ? 'Traded already, so only packing can change here.' : 'Material, grade and manufacturer make one stock item.',
    submitLabel: existing ? 'Save' : 'Add product',
    fields: [
      { key: 'material', label: 'Material', value: p.material || prefill.material || '', required: true,
        type: 'suggest', placeholder: 'e.g. PVC', upper: true, autofocus: !prefill.material,
        suggest: async q => (await api.materials({ q, limit: 8 })).items.map(m => m.name) },
      { key: 'grade', label: 'Grade', value: p.grade || prefill.grade || '', required: true,
        type: 'suggest', placeholder: 'e.g. S65', upper: true, half: true,
        suggest: async (q, v) => {
          const mid = await materialId(v.material);
          return mid ? (await api.grades({ material_id: mid, q, limit: 8 })).items.map(g => g.name) : [];
        } },
      { key: 'manufacturer', label: 'Manufacturer', value: p.manufacturer || prefill.manufacturer || '',
        required: true, type: 'suggest', placeholder: 'e.g. Reliance', half: true,
        hint: 'Who made it — not who you trade with',
        suggest: async q => (await api.manufacturers({ q, limit: 8 })).items.map(k => k.name) },
      { key: 'packing', label: 'Packing', value: p.packing || '', placeholder: 'e.g. 25 kg bag (optional)' },
      { key: 'preview', type: 'custom', render: () => preview }
    ],
    onChange: (_k, _v, form) => check(form.values),
    onOpen: form => {
      check(form.values);
      if (fixed) for (const k of ['material', 'grade', 'manufacturer']) form.disable(k, true);
    },
    submit: v => api.productSave({ id: p.id, material: v.material, grade: v.grade,
                                    manufacturer: v.manufacturer, packing: v.packing })
  });
  if (saved) toast(saved.existed ? `${saved.display} was already there — selected it` : `${saved.display} ${existing ? 'saved' : 'added'}`);
  return saved;
}

// ---------------------------------------------------------------- stock moves
export async function transferForm(lot) {
  const others = (await api.warehouses({ limit: 200 })).items.filter(w => w.id !== lot.warehouse_id);
  if (!others.length) {
    const made = await warehouseForm();
    if (!made) return null;
    others.push(made);
  }
  return openForm({
    title: 'Move to another warehouse',
    sub: `${lot.product || ''} · ${f.qty(lot.available_g)} in ${lot.warehouse} · from ${lot.supplier_name} @ ${f.rate(lot.rate_paise)}`,
    submitLabel: 'Move stock',
    fields: [
      { key: 'qty', label: 'Quantity (MT)', value: f.mt(lot.available_g), required: true, inputmode: 'decimal',
        hint: `Up to ${f.qty(lot.available_g)}`, half: true },
      { key: 'to', label: 'To warehouse', type: 'select', required: true, half: true,
        value: String(others[0].id), options: others.map(w => ({ value: String(w.id), label: w.name })) },
      { key: 'date', label: 'Date', type: 'date', value: f.today(), half: true },
      { key: 'reason', label: 'Note', value: '', placeholder: 'e.g. balancing godowns', half: true }
    ],
    submit: v => {
      const g = f.fromMt(v.qty);
      if (!g) throw new Error('Enter how many MT are moving');
      return api.transfer({ lot_id: lot.id, to_warehouse_id: +v.to, qty_g: g, move_date: v.date, reason: v.reason });
    }
  });
}

export async function adjustForm(lot) {
  return openForm({
    title: 'Adjust stock',
    sub: `${lot.product || ''} · ${f.qty(lot.available_g)} in ${lot.warehouse} · from ${lot.supplier_name}`,
    submitLabel: 'Record adjustment',
    fields: [
      { key: 'dir', label: 'What happened', type: 'choice', options: ['Write off', 'Found more'],
        value: 'Write off', always: true },
      { key: 'qty', label: 'Quantity (MT)', value: '', required: true, inputmode: 'decimal', placeholder: '0.25', half: true },
      { key: 'date', label: 'Date', type: 'date', value: f.today(), half: true },
      { key: 'why', label: 'Reason', type: 'choice', options: ['Shortage', 'Damage', 'Weighment', 'Other'], value: 'Shortage' },
      { key: 'note', label: 'Note', value: '', placeholder: 'optional' }
    ],
    submit: v => {
      const g = f.fromMt(v.qty);
      if (!g) throw new Error('Enter the quantity in MT');
      return api.adjust({ lot_id: lot.id, qty_g: v.dir === 'Found more' ? g : -g, move_date: v.date,
                          reason: [v.why, v.note].filter(Boolean).join(' — ') });
    }
  });
}

// ---------------------------------------------------------------- pickers
export const pickParty = (title = 'Choose party') => pickEntity({
  title, placeholder: 'Name, GSTIN or phone',
  load: p => api.parties(p),
  row: p => ({ title: p.name, sub: partySub(p), tag: p.deal_count ? `${p.deal_count} deals` : '' }),
  addLabel: '+ Add new party', onAdd: q => partyForm(null, { name: q })
});

export const pickWarehouse = (title = 'Choose warehouse') => pickEntity({
  title, placeholder: 'Warehouse name',
  load: p => api.warehouses(p),
  row: w => ({ title: w.name, sub: w.address || '', tag: w.stock_g ? f.qty(w.stock_g) : 'empty' }),
  addLabel: '+ Add new warehouse', onAdd: q => warehouseForm(null, { name: q })
});

export { partySub };
