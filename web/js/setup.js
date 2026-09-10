// Setup: the master tree of material -> grade -> manufacturer.
//
// This exists because "manufacturer" is a word people type differently every
// time. Registered once here, it becomes a closed list in the ticket, so the
// same maker cannot arrive as "Reliance", "reliance " and "RIL" and split one
// position into three.
import { h, mount, toast, searchBar } from './ui.js';
import * as f from './fmt.js';
import { api } from './api.js';

let openMaterial = null, openGrade = null, filter = '';

export async function renderSetup(root, ctx) {
  const [{ tree: all }, { parties }, { warehouses }] = await Promise.all(
    [api.catalogTree(), api.partyList(), api.warehouses()]);
  const term = filter.toLowerCase();
  const hit = (...parts) => !term || parts.some(x => (x || '').toLowerCase().includes(term));
  const tree = !term ? all : all
    .map(m => {
      const grades = m.grades.filter(g =>
        hit(m.material, g.grade) || g.manufacturers.some(k => hit(k.manufacturer)));
      return hit(m.material) || grades.length ? { ...m, grades } : null;
    })
    .filter(Boolean);
  const view = h('div', { class: 'view' });
  mount(root, view);

  const refresh = () => renderSetup(root, ctx);
  const add = async (body, what) => {
    try { await api.addCatalog(body); toast(`Added ${what}`); refresh(); }
    catch (err) { toast(err.message, { kind: 'err', ms: 8000 }); }
  };
  const remove = async (body, what) => {
    if (!window.confirm(`Remove ${what}?`)) return;
    try { await api.removeCatalog(body); toast(`Removed ${what}`); refresh(); }
    catch (err) { toast(err.message, { kind: 'err', ms: 8000 }); }
  };

  const saveParty = async (p, form) => {
    try {
      await api.partySave({ ...form, id: p ? p.id : undefined });
      toast(p ? 'Saved' : `Added ${form.name}`);
      refresh();
    } catch (err) { toast(err.message, { kind: 'err', ms: 8000 }); }
  };
  const dropParty = async p => {
    if (!window.confirm(`Remove ${p.name}?`)) return;
    try { await api.partyRemove(p.id); toast(`Removed ${p.name}`); refresh(); }
    catch (err) { toast(err.message, { kind: 'err', ms: 8000 }); }
  };

  // A warehouse is a name and a location. Renaming one is carried through every
  // lot and deal that already records it, on the server, in one transaction.
  function whRow(w) {
    const box = (value, placeholder, width) => h('input', {
      class: 'ghost-input', value: value || '', placeholder,
      style: { textAlign: 'left', width, fontFamily: 'var(--sans)' }
    });
    const name = box(w && w.name, 'Warehouse name, e.g. Mundra', '240px');
    const location = box(w && w.location, 'Location / address', '340px');
    const save = async () => {
      const body = { name: name.value.trim(), location: location.value.trim() };
      if (!body.name) { toast('Name is required', { kind: 'err' }); return; }
      if (w) body.old_name = w.name;
      try {
        await api.saveWarehouse(body);
        toast(w ? (w.name !== body.name ? `Renamed to ${body.name}` : 'Saved') : `Added ${body.name}`);
        refresh();
      } catch (err) { toast(err.message, { kind: 'err', ms: 8000 }); }
    };
    return h('div', { class: 'party-row' }, name, location,
      w ? h('span', { class: 'dim', style: { fontSize: '13px' } },
            w.stock_g ? `${f.qty(w.stock_g)} in stock` : 'empty') : null,
      h('button', { class: 'chip on', style: { marginLeft: 'auto' }, onclick: save }, w ? 'Save' : 'Add'),
      w ? h('button', { class: 'setup-x', onclick: () => dropWh(w.name) }, '×') : null);
  }
  async function dropWh(name) {
    if (!window.confirm(`Remove warehouse ${name}?`)) return;
    try { await api.removeWarehouse(name); toast(`Removed ${name}`); refresh(); }
    catch (err) { toast(err.message, { kind: 'err', ms: 8000 }); }
  }

  // A party is a name, a phone, an address, a GSTIN and its PAN. There is no
  // buyer/seller split - the same firm sits on either side of a deal. PAN is
  // read off the GSTIN; it only takes typing when there is no GSTIN at all.
  function partyRow(p) {
    const box = (value, placeholder, width, mono) => h('input', {
      class: 'ghost-input', value: value || '', placeholder,
      style: { textAlign: 'left', width, fontFamily: mono ? 'var(--mono)' : 'var(--sans)' }
    });
    const name = box(p && p.name, 'Party name', '230px');
    const gstin = box(p && p.gstin, 'GSTIN', '180px', true);
    const pan = box(p && p.pan, 'PAN', '130px', true);
    const phone = box(p && p.phone, 'Phone', '160px');
    const address = box(p && (p.address || p.city), 'Address', '360px');
    const syncPan = () => {
      const g = gstin.value.replace(/\s+/g, '').toUpperCase();
      pan.readOnly = g.length >= 12;
      if (pan.readOnly) pan.value = g.slice(2, 12);
      pan.title = pan.readOnly ? 'Taken from the GSTIN' : '';
    };
    gstin.addEventListener('input', syncPan);
    syncPan();
    const collect = () => ({
      name: name.value.trim(), gstin: gstin.value.trim(), pan: pan.value.trim(),
      phone: phone.value.trim(), address: address.value.trim()
    });
    return h('div', { class: 'party-row' }, name, gstin, pan, phone, address,
      p && p.state ? h('span', { class: 'dim', style: { fontSize: '13px' } }, p.state) : null,
      h('span', { class: 'dim', style: { marginLeft: 'auto', fontSize: '13px' } },
        p ? (p.deal_count ? `${p.deal_count} deals` : 'unused') : ''),
      h('button', { class: 'chip on', onclick: () => {
        const form = collect();
        if (!form.name) { toast('Name is required', { kind: 'err' }); return; }
        saveParty(p, form);
      } }, p ? 'Save' : 'Add'),
      p ? h('button', { class: 'setup-x', onclick: () => dropParty(p) }, '×') : null);
  }

  // Hundreds of parties: render the ones that match, not all of them.
  const plist = h('div', { class: 'party-list' });
  const pcount = h('span', { class: 'dim' }, '');
  const paintParties = term => {
    const t = (term || '').trim().toLowerCase();
    const hits = !t ? parties : parties.filter(p =>
      [p.name, p.gstin, p.address, p.phone].some(x => (x || '').toLowerCase().includes(t)));
    pcount.textContent = hits.length > 60
      ? `showing 60 of ${hits.length} — search to narrow`
      : `${hits.length} part${hits.length === 1 ? 'y' : 'ies'}`;
    mount(plist, ...hits.slice(0, 60).map(partyRow));
  };
  paintParties('');

  mount(view,
    h('div', { class: 'section-head' },
      h('h2', {}, 'Parties'), h('i', { class: 'rule' }), pcount),
    h('div', { class: 'party-list' }, partyRow(null)),
    h('div', { class: 'searchbar', style: { maxWidth: 'none', margin: '10px 0' } },
      h('span', { class: 'dim' }, '⌕'),
      h('input', {
        placeholder: 'Search name, GSTIN, address or phone',
        oninput: e => paintParties(e.target.value)
      })),
    plist,

    h('div', { class: 'section-head', style: { marginTop: '34px' } },
      h('h2', {}, 'Warehouses'), h('i', { class: 'rule' }),
      h('span', { class: 'dim' }, 'where stock physically sits')),
    h('div', { class: 'party-list' },
      whRow(null),
      ...warehouses.map(whRow)),

    h('div', { class: 'section-head', style: { marginTop: '34px' } },
      h('h2', {}, 'Materials, grades and manufacturers'), h('i', { class: 'rule' }),
      h('span', { class: 'dim' }, 'a manufacturer is who made the resin, not who you trade with')),

    searchBar('Search material, grade or manufacturer…',
      t => { filter = t; if (t) { openMaterial = null; openGrade = null; } renderSetup(root, ctx); },
      { value: filter }),

    h('div', { class: 'setup-add' },
      h('input', {
        class: 'ghost-input', id: 'new-material', placeholder: 'New material, e.g. LLDPE',
        style: { width: '280px', textAlign: 'left' },
        onkeydown: e => { if (e.key === 'Enter') addMaterial(); }
      }),
      h('button', { class: 'chip on', onclick: addMaterial }, '+ Add material')),

    tree.length
      ? h('div', { class: 'setup-tree' }, ...tree.map(m => materialCard(m)))
      : h('div', { class: 'empty' },
          h('h3', {}, filter ? `Nothing matches "${filter}"` : 'Nothing set up yet'),
          h('div', {}, filter ? 'Try another name.' : 'Add your first material above.')));

  function addMaterial() {
    const el = document.getElementById('new-material');
    if (el && el.value.trim()) add({ material: el.value.trim() }, el.value.trim());
  }

  function materialCard(m) {
    const isOpen = openMaterial === m.material || (!!term && m.grades.length > 0);
    return h('div', { class: 'setup-node' + (isOpen ? ' open' : '') },
      h('div', {
        class: 'setup-row lvl-1',
        onclick: () => { openMaterial = isOpen ? null : m.material; openGrade = null; refresh(); }
      },
        h('span', { class: 'twist' }, isOpen ? '▾' : '▸'),
        h('b', {}, m.material),
        h('span', { class: 'setup-meta' },
          `${m.grades.length} grade${m.grades.length === 1 ? '' : 's'}`),
        h('span', { class: 'grow' }),
        m.stock_g ? h('span', { class: 'tag up' }, f.qty(m.stock_g)) : null,
        h('button', {
          class: 'setup-x',
          onclick: e => { e.stopPropagation(); remove({ material: m.material }, m.material); }
        }, '×')),

      isOpen ? h('div', { class: 'setup-children' },
        ...m.grades.map(g => gradeCard(m, g)),
        h('div', { class: 'setup-inline' },
          h('input', {
            class: 'ghost-input', placeholder: 'New grade, e.g. HS1000',
            style: { width: '220px', textAlign: 'left' },
            onkeydown: e => {
              if (e.key === 'Enter' && e.target.value.trim()) {
                add({ material: m.material, grade: e.target.value.trim() }, e.target.value.trim());
              }
            }
          }),
          h('span', { class: 'dim' }, 'press Enter to add'))) : null);
  }

  function gradeCard(m, g) {
    const key = m.material + '/' + g.grade;
    const isOpen = openGrade === key || (!!term && g.manufacturers.some(k => hit(k.manufacturer)));
    return h('div', { class: 'setup-node' + (isOpen ? ' open' : '') },
      h('div', {
        class: 'setup-row lvl-2',
        onclick: () => { openGrade = isOpen ? null : key; refresh(); }
      },
        h('span', { class: 'twist' }, isOpen ? '▾' : '▸'),
        h('b', {}, g.grade),
        h('span', { class: 'setup-meta' },
          g.manufacturers.length
            ? g.manufacturers.map(k => k.manufacturer).join(' · ')
            : 'no manufacturer yet'),
        h('span', { class: 'grow' }),
        g.stock_g ? h('span', { class: 'tag up' }, f.qty(g.stock_g)) : null,
        h('button', {
          class: 'setup-x',
          onclick: e => {
            e.stopPropagation();
            remove({ material: m.material, grade: g.grade }, `${m.material} ${g.grade}`);
          }
        }, '×')),

      isOpen ? h('div', { class: 'setup-children' },
        ...g.manufacturers.map(k => h('div', { class: 'setup-row lvl-3' },
          h('span', { class: 'twist' }, '·'),
          h('b', {}, k.manufacturer),
          h('span', { class: 'setup-meta' },
            k.deals ? `${k.deals} deal${k.deals === 1 ? '' : 's'}` : 'never traded'),
          h('span', { class: 'grow' }),
          k.stock_g ? h('span', { class: 'tag up' }, f.qty(k.stock_g)) : null,
          h('button', {
            class: 'setup-x',
            onclick: () => remove({ material: m.material, grade: g.grade, manufacturer: k.manufacturer },
              `${k.manufacturer} for ${m.material} ${g.grade}`)
          }, '×'))),
        h('div', { class: 'setup-inline' },
          h('input', {
            class: 'ghost-input', placeholder: 'New manufacturer, e.g. Reliance',
            style: { width: '260px', textAlign: 'left' },
            onkeydown: e => {
              if (e.key === 'Enter' && e.target.value.trim()) {
                add({ material: m.material, grade: g.grade, manufacturer: e.target.value.trim() },
                  e.target.value.trim());
              }
            }
          }),
          h('span', { class: 'dim' }, 'press Enter to add'))) : null);
  }

  return view;
}
