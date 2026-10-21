// How each list can be ordered. The names match the server's own lists, which
// ignore anything they do not know, so a stale link can never reorder a query
// in a way the book did not intend.
export const SORTS = {
  stock: [
    ['stock', 'Most stock'], ['stock_low', 'Least stock'], ['idle', 'Idle longest'],
    ['oldest', 'Oldest first'], ['newest', 'Newest first'], ['value', 'Highest value'],
    ['cost', 'Dearest'], ['cost_low', 'Cheapest'], ['product', 'Product A–Z'], ['warehouse', 'Warehouse A–Z']
  ],
  deals: [
    ['newest', 'Newest first'], ['oldest', 'Oldest first'], ['value', 'Biggest value'],
    ['qty', 'Biggest quantity'], ['rate', 'Highest rate'], ['margin', 'Best margin'],
    ['margin_low', 'Worst margin'], ['due', 'Payment due first'],
    ['party', 'Party A–Z'], ['product', 'Product A–Z']
  ],
  positions: [
    ['stock', 'Most stock'], ['stock_low', 'Least stock'], ['lots', 'Most lots'],
    ['mark', 'Highest mark'], ['product', 'Product A–Z']
  ]
};

// A filter-bar field, so sorting sits with the filters instead of beside them.
export const sortField = (which, value = '') => ({
  key: 'sort', label: 'Sort by', value,
  options: SORTS[which].map(([v, label]) => ({ value: v, label })),
  any: SORTS[which][0][1]
});
