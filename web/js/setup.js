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
  const { tree: all } = await api.catalogTree();
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

  mount(view,
    h('div', { class: 'section-head' },
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
