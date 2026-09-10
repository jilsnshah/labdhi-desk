// One list component for every list in the app.
//
// The server answers every list with one page and the size of the whole match,
// so no screen ever holds a whole table: it shows a page, says how many there
// are, and fetches the next page when asked. A reply that arrives after a newer
// search has started is dropped, so fast typing can never paint stale rows.
import { h, mount, moreBar } from './ui.js';

export function pagedList({ load, row, empty, pageSize = 25, head = null, className = '', onPage = null }) {
  const st = { params: {}, items: [], total: 0, seq: 0, loaded: false };
  const body = head ? h('tbody') : h('div', { class: 'plist-body ' + className });
  const table = head
    ? h('div', { class: 'tbl-wrap' },
        h('table', { class: 'tbl ' + className },
          h('thead', {}, h('tr', {}, ...head.map(c => typeof c === 'string'
            ? h('th', {}, c) : h('th', { class: c.cls || '' }, c.label)))),
          body))
    : body;
  const status = h('div', {});
  const foot = moreBar(loadMore);
  const el = h('div', { class: 'plist' }, table, status, foot);

  async function fetchPage(offset) {
    const seq = ++st.seq;
    const page = await load({ ...st.params, limit: pageSize, offset });
    return seq === st.seq ? page : null;
  }

  async function reload(params) {
    if (params !== undefined) st.params = params;
    const page = await fetchPage(0);
    if (page) { st.items = page.items; st.total = page.total; st.loaded = true; paint(); }
    return st;
  }

  async function loadMore() {
    foot.busy(true);
    try {
      const page = await fetchPage(st.items.length);
      if (page) { st.items = st.items.concat(page.items); st.total = page.total; paint(); }
    } finally {
      foot.busy(false);
    }
  }

  function paint() {
    mount(body, ...st.items.map((item, i) => row(item, i)));
    const none = st.loaded && !st.items.length;
    table.hidden = none;
    mount(status, none
      ? (empty ? empty(st.params) : h('div', { class: 'empty' }, h('h3', {}, 'Nothing here yet')))
      : null);
    foot.update(st.items.length, st.total);
    if (onPage) onPage(st);
  }

  return { el, reload, state: st, repaint: paint };
}

// "12 of 340" beside a heading, kept in step with a list.
export function counter() {
  const el = h('span', { class: 'count' }, '');
  el.update = st => { el.textContent = st.total ? `${st.items.length} of ${st.total}` : ''; };
  return el;
}
