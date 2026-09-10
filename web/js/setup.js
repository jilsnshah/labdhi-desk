// Setup: the master records. Each tab is one kind of record - searchable,
// paged, added and edited through the same form a ticket's "Add new" opens.
import { h, mount, toast, searchBar } from './ui.js';
import * as f from './fmt.js';
import { api } from './api.js';
import { pagedList, counter } from './lists.js';
import { openForm, partyForm, productForm, warehouseForm } from './forms.js';

export const TABS = [
  ['parties', 'Parties'], ['products', 'Products'], ['warehouses', 'Warehouses'],
  ['materials', 'Materials & grades'], ['manufacturers', 'Manufacturers'], ['states', 'States']
];

export function renderSetup(root, ctx) {
  const tab = ctx.setupTab || 'parties';
  const body = h('div', {});
  mount(root, h('div', { class: 'view' },
    h('div', { class: 'tabs' }, ...TABS.map(([key, label]) => h('button', {
      class: 'tab' + (tab === key ? ' on' : ''),
      onclick: () => { ctx.setupTab = key; ctx.setupFilter = null; renderSetup(root, ctx); }
    }, label))),
    body));
  SECTIONS[tab](body, ctx, () => renderSetup(root, ctx));
}

// Removing is refused by the server while anything still refers to a record;
// the reason comes back and is shown as it is.
export async function removeRecord(label, call, after) {
  if (!window.confirm(`Remove ${label}?`)) return;
  try { await call(); toast(`Removed ${label}`); after(); }
  catch (err) { toast(err.message, { kind: 'err', ms: 9000 }); }
}

function section(body, { title, hint, addLabel, onAdd, placeholder, head, load, row, filters, pageSize = 25 }) {
  let query = '', extra = {};
  const count = counter();
  const list = pagedList({ pageSize, head, load, row: item => row(item, () => list.reload()), onPage: count.update });
  const reload = () => list.reload({ q: query, ...extra });
  mount(body,
    h('div', { class: 'section-head' }, h('h2', {}, title), h('i', { class: 'rule' }), count,
      onAdd ? h('button', { class: 'chip on small', onclick: async () => { if (await onAdd()) reload(); } }, addLabel) : null),
    hint ? h('div', { class: 'section-hint' }, hint) : null,
    h('div', { class: 'setup-tools' },
      searchBar(placeholder, t => { query = t; reload(); }),
      filters ? filters(v => { extra = v; reload(); }) : null),
    list.el);
  return { reload, setExtra: v => { extra = v; } };
}

const acts = (...btns) => h('td', { class: 'acts' }, ...btns);
const act = (label, fn, cls = '') => h('button', { class: 'row-act ' + cls, onclick: e => { e.stopPropagation(); fn(); } }, label);

const SECTIONS = {
  parties(body, ctx) {
    const s = section(body, {
      title: 'Parties', addLabel: '+ Add party', onAdd: () => partyForm(),
      hint: 'One record per firm — buyer, seller or transporter. Identity is the GSTIN.',
      placeholder: 'Search name, GSTIN, phone or address…',
      head: ['Party', 'GSTIN', 'PAN', 'State', 'Phone', 'Address', { label: 'Deals', cls: 'r' }, ''],
      load: p => api.parties(p),
      row: (p, again) => h('tr', {},
        h('td', { class: 'strong' }, p.name),
        h('td', { class: 'mono' }, p.gstin || h('span', { class: 'dim' }, '—')),
        h('td', { class: 'mono' }, p.pan || ''),
        h('td', {}, p.state || ''),
        h('td', { class: 'nowrap' }, p.phone || ''),
        h('td', { class: 'addr', title: p.address || '' }, p.address || ''),
        h('td', { class: 'r mono' }, p.deal_count || ''),
        acts(act('Edit', async () => { if (await partyForm(p)) again(); }),
             act('×', () => removeRecord(p.name, () => api.partyRemove(p.id), again), 'x'))),
      filters: set => {
        const sel = h('select', { class: 'setup-select', onchange: e => set(e.target.value ? { state_code: e.target.value } : {}) },
          h('option', { value: '' }, 'All states'));
        api.states({ limit: 100 }).then(r => {
          for (const st of r.items.filter(x => x.parties)) sel.appendChild(h('option', { value: st.code }, `${st.name} (${st.parties})`));
          if (ctx.setupFilter) { sel.value = ctx.setupFilter; set({ state_code: ctx.setupFilter }); }
        });
        return sel;
      }
    });
    if (ctx.setupFilter) s.setExtra({ state_code: ctx.setupFilter });
    s.reload();
  },

  products(body) {
    section(body, {
      title: 'Products', addLabel: '+ Add product', onAdd: () => productForm(),
      hint: 'A product is a material, a grade and the manufacturer who made it. Stock is kept per product, per warehouse.',
      placeholder: 'Search material, grade or manufacturer…',
      head: ['Product', 'Material', 'Grade', 'Manufacturer', 'Packing', { label: 'In stock (MT)', cls: 'r' }, { label: 'Deals', cls: 'r' }, ''],
      load: p => api.products(p),
      row: (p, again) => h('tr', {},
        h('td', { class: 'strong' }, p.display),
        h('td', {}, p.material), h('td', {}, p.grade), h('td', {}, p.manufacturer),
        h('td', {}, p.packing || ''),
        h('td', { class: 'r mono' }, p.stock_g ? f.mt(p.stock_g) : ''),
        h('td', { class: 'r mono' }, p.deal_count || ''),
        acts(act('Edit', async () => { if (await productForm(await api.product(p.id))) again(); }),
             act('×', () => removeRecord(p.display, () => api.productRemove(p.id), again), 'x'))),
      filters: set => {
        const sel = h('select', { class: 'setup-select', onchange: e => set(e.target.value ? { material_id: e.target.value } : {}) },
          h('option', { value: '' }, 'All materials'));
        api.materials({ limit: 200 }).then(r => r.items.forEach(m => sel.appendChild(h('option', { value: m.id }, m.name))));
        return sel;
      }
    }).reload();
  },

  warehouses(body) {
    section(body, {
      title: 'Warehouses', addLabel: '+ Add warehouse', onAdd: () => warehouseForm(),
      hint: 'Your own stock locations. Every lot sits in one; a sale ships from one.',
      placeholder: 'Search name or address…',
      head: ['Warehouse', 'Address', { label: 'In stock (MT)', cls: 'r' }, { label: 'Value', cls: 'r' },
             { label: 'Products', cls: 'r' }, { label: 'Lots', cls: 'r' }, ''],
      load: p => api.warehouses(p),
      row: (w, again) => h('tr', {},
        h('td', { class: 'strong' }, w.name),
        h('td', { class: 'addr' }, w.address || ''),
        h('td', { class: 'r mono' }, f.mt(w.stock_g)),
        h('td', { class: 'r mono' }, f.inr(w.stock_value_paise, { compact: true })),
        h('td', { class: 'r mono' }, w.products),
        h('td', { class: 'r mono' }, w.lots),
        acts(act('Edit', async () => { if (await warehouseForm(w)) again(); }),
             act('×', () => removeRecord(w.name, () => api.warehouseRemove(w.id), again), 'x')))
    }).reload();
  },

  materials(body) {
    section(body, {
      title: 'Materials', addLabel: '+ Add material',
      onAdd: () => nameForm('Add material', '', 'e.g. LLDPE', v => api.materialSave({ name: v })),
      hint: 'Open a material to manage its grades.',
      placeholder: 'Search material…',
      head: ['Material', { label: 'Grades', cls: 'r' }, { label: 'Products', cls: 'r' }, { label: 'In stock (MT)', cls: 'r' }, ''],
      load: p => api.materials(p),
      row: (m, again) => {
        let open = null;
        const tr = h('tr', { class: 'click' },
          h('td', { class: 'strong' }, '▸ ', m.name),
          h('td', { class: 'r mono' }, m.grades), h('td', { class: 'r mono' }, m.products),
          h('td', { class: 'r mono' }, m.stock_g ? f.mt(m.stock_g) : ''),
          acts(act('Rename', async () => {
            if (await nameForm('Rename material', m.name, '', v => api.materialSave({ id: m.id, name: v }))) again();
          }), act('×', () => removeRecord(m.name, () => api.materialRemove(m.id), again), 'x')));
        tr.addEventListener('click', () => {
          if (open) { open.remove(); open = null; return; }
          const inner = h('div', { class: 'detail' });
          grades(inner, m);
          open = h('tr', { class: 'deal-detail' }, h('td', { colspan: 5 }, inner));
          tr.after(open);
        });
        return tr;
      }
    }).reload();
  },

  manufacturers(body) {
    section(body, {
      title: 'Manufacturers', addLabel: '+ Add manufacturer',
      onAdd: () => nameForm('Add manufacturer', '', 'e.g. Reliance', v => api.manufacturerSave({ name: v })),
      hint: 'Who made the resin — never the party you trade with. A rename fixes every product at once.',
      placeholder: 'Search manufacturer…',
      head: ['Manufacturer', { label: 'Products', cls: 'r' }, { label: 'In stock (MT)', cls: 'r' }, ''],
      load: p => api.manufacturers(p),
      row: (k, again) => h('tr', {},
        h('td', { class: 'strong' }, k.name),
        h('td', { class: 'r mono' }, k.products),
        h('td', { class: 'r mono' }, k.stock_g ? f.mt(k.stock_g) : ''),
        acts(act('Rename', async () => {
          if (await nameForm('Rename manufacturer', k.name, '', v => api.manufacturerSave({ id: k.id, name: v }))) again();
        }), act('×', () => removeRecord(k.name, () => api.manufacturerRemove(k.id), again), 'x')))
    }).reload();
  },

  states(body, ctx, rerender) {
    section(body, {
      title: 'States', pageSize: 50,
      hint: 'GST state codes. A party with a GSTIN takes its state from it; open one to see its parties.',
      placeholder: 'Search state or code…',
      head: ['Code', 'State', { label: 'Parties', cls: 'r' }],
      load: p => api.states(p),
      row: st => h('tr', { class: st.parties ? 'click' : '', onclick: () => {
        if (!st.parties) return;
        ctx.setupTab = 'parties'; ctx.setupFilter = st.code; rerender();
      } },
        h('td', { class: 'mono' }, st.code), h('td', {}, st.name),
        h('td', { class: 'r mono' }, st.parties || ''))
    }).reload();
  }
};

function grades(inner, m) {
  const list = pagedList({
    pageSize: 25, head: ['Grade', { label: 'Products', cls: 'r' }, { label: 'In stock (MT)', cls: 'r' }, ''],
    load: p => api.grades({ ...p, material_id: m.id }),
    row: g => h('tr', {},
      h('td', { class: 'strong' }, `${m.name} ${g.name}`),
      h('td', { class: 'r mono' }, g.products),
      h('td', { class: 'r mono' }, g.stock_g ? f.mt(g.stock_g) : ''),
      acts(act('Rename', async () => {
        if (await nameForm('Rename grade', g.name, '', v => api.gradeSave({ id: g.id, material_id: m.id, name: v }))) list.reload();
      }), act('×', () => removeRecord(`${m.name} ${g.name}`, () => api.gradeRemove(g.id), () => list.reload()), 'x')))
  });
  mount(inner,
    h('div', { class: 'chips', style: { marginBottom: '10px' } },
      h('button', { class: 'chip small on', onclick: async () => {
        if (await nameForm(`Add grade to ${m.name}`, '', 'e.g. S65', v => api.gradeSave({ material_id: m.id, name: v }))) list.reload();
      } }, `+ Add ${m.name} grade`)),
    list.el);
  list.reload({});
}

export function nameForm(title, value, placeholder, save) {
  return openForm({
    title, submitLabel: 'Save',
    fields: [{ key: 'name', label: 'Name', value, placeholder, required: true, autofocus: true }],
    submit: v => save(v.name.trim())
  });
}
