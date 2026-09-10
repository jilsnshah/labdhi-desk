# How Labdhi Desk fits together

One rule runs through the whole system:

> **Master data exists once, has an id, and is edited in one place.
> Transactions point at master data by id and never copy it.**

A deal does not say "Krishna Dehgam", "Mundra" or "PVC S65 · Reliance" as text. It stores
three ids. Rename the party, fix a typo in a manufacturer, move a warehouse to a new address,
and every deal, lot, report and list shows the change at once, because nothing else held the
old value.

## Master data

| Record | Table | What makes it unique | Created / edited in | Referenced by |
|---|---|---|---|---|
| State | `states` | GST state code (`24`) | seeded, read-only | `parties.state_code` |
| Party | `parties` | GSTIN; without one, the name | Setup › Parties, or **Add new party** in any ticket or picker | `deals.party_id`, `deals.transporter_id`, `lots.supplier_id` |
| Warehouse | `warehouses` | name (case-insensitive) | Setup › Warehouses, Stock, or **Add new warehouse** in a buy ticket | `deals.warehouse_id`, `lots.warehouse_id` |
| Material | `materials` | name (`PVC`) | Setup › Materials & grades, or the product form | `grades.material_id` |
| Grade | `grades` | material + name (`PVC S65`) | Setup › Materials & grades, or the product form | `products.grade_id` |
| Manufacturer | `manufacturers` | name (`Reliance`) | Setup › Manufacturers, or the product form | `products.manufacturer_id` |
| **Product** (stock item) | `products` | grade + manufacturer | Setup › Products, or **Add new product** in a buy ticket | `deals.product_id`, `lots.product_id`, `marks.product_id` |

Notes:

- **There is no buyer/seller split.** The same firm buys one week and sells the next. A
  transporter is a party as well, picked from the same list.
- **A manufacturer is who made the resin.** It is never the party you trade with.
- **PAN and state are read off the GSTIN** and cannot disagree with it. They are typed only
  for a party without a GSTIN.
- **A product is what stock is kept against.** PVC S65 from Reliance and PVC S65 from DCW are
  two products and never pool.
- **A traded product keeps its identity.** You can't relabel it, because that would rewrite
  past deals. To correct a spelling, rename the manufacturer or grade itself.
- **Duplicates are refused, not merged silently.** The party form names the existing record
  ("Already saved as …") and offers **Use it**. The product form selects the existing product.
  Only the Tally importer merges automatically.
- **Removal is refused while anything refers to a record**, and the reason is shown.

Deliberately *not* entities: freight-by, delivery-by, payment terms, e-way, Ex-Place and notes.
They are per-deal paperwork, recorded as typed and never reused. The Sauda No. is the deal's
own identity.

## Transactions

```
                         ┌───────────── parties ─────────────┐
                         │ party_id        transporter_id    │ supplier_id
   products ── product_id┤                                   │
   warehouses ─ warehouse_id                                 │
                         ▼                                   ▼
                       deals ──(BUY books one)──────────►  lots ◄── parent_lot_id (transfers, gains)
                         ▲                                   │
                         │ sale_deal_id                      │ lot_id
                         └──────────── allocations ◄─────────┘
                                                     stock_moves (transfer / adjust) ── lot_id, to_lot_id
```

- **deal**: one sauda. BUY stock is *received into* `warehouse_id`; SELL stock is *dispatched
  from* `warehouse_id`.
- **lot**: the stock record. A quantity of one product, from one purchase, at one cost, sitting
  in one warehouse.
- **allocation**: which lot's grams a sale consumed, at what cost. This is the lineage from
  purchase to sale.
- **stock move**: a transfer between warehouses, or an adjustment (write-off or found stock).

## Stock, warehouse by warehouse

Stock is never stored as a running total that could drift. It is always the sum of open lots:

```
stock(product, warehouse) = Σ (qty_g − qty_allocated_g − qty_out_g)
                            over that product's open lots in that warehouse
```

| Movement | What happens |
|---|---|
| Purchase | A booked BUY creates a lot in its receiving warehouse. |
| Sale | Allocations take grams from lots, **only lots in the sale's warehouse**. The ticket shows only those lots and caps the quantity at what sits there. The server refuses a pin on a lot elsewhere and refuses any short. |
| Transfer | Grams leave a lot (`qty_out_g`) and arrive as a new lot in the other warehouse. The new lot keeps the parent, supplier, cost and purchase, so a sale from it still traces to the original buy. |
| Write-off | Grams leave a lot (`qty_out_g`). |
| Found stock | A new lot at the same cost, recorded against the lot it was found with. |

Invariants, enforced by tests on SQLite and Postgres:

- Bought + found − written off = in stock + sold.
- The movement ledger walks back from today's balance to zero.
- Stock by warehouse, stock by product, and warehouse totals are sums over the same lots, so they
  cannot disagree.

Every booking, transfer and adjustment can be undone while nothing depends on it:

- A purchase that was sold or moved can't be cancelled.
- A transfer whose stock was sold can't be undone.

## Lists and pagination

Every list the app shows (orders, parties, states, warehouses, materials, grades, manufacturers,
products, positions, stock, movements, counterparties, events) is served one way:

```
GET /api/<things>?q=&limit=&offset=&<filters>
→ { items, total, limit, offset, has_more }        (limit ≤ 200, default 25)
```

The screens use one list component (`web/js/lists.js`). It shows a page and the total, fetches
the next page on **Load more**, and drops a reply that arrives after a newer search, so fast
typing never paints stale rows. No screen ever loads a whole table.

The only unpaged list is the lots of one product in one warehouse. That is exactly the set a
sale is split across, and the split must see all of it to add up.

## Records in the API

```
POST /api/<things>                  create (no id) or edit (with id); returns the saved record
POST /api/<things>/<id>/remove      refused while anything refers to it
```

Every **Add new …** button in the app opens the matching form (`web/js/forms.js`). The form
returns the saved record, so the ticket that opened it continues with that record selected.
The same forms appear as a centred card on desktop and a bottom sheet on the phone.

## Screens

| Screen | Shows |
|---|---|
| Desk | Positions per product with per-warehouse breakdown, P&L, alerts. Filters: warehouse, material, grade, manufacturer, bought-from. |
| Stock | Warehouse cards; stock per product × warehouse; each row opens its lots (Move / Adjust) and its ledger with running balance; all movements. |
| Flow | Lineage graph, purchases → sales. |
| Tape | One row per sauda: Sauda No., Date, Type, Party, Product / Grade, Qty (MT), Rate (₹/MT), Warehouse, Status, Delivery, Margin. A row opens every recorded field and the lineage. |
| Setup | Parties, Products, Warehouses, Materials & grades, Manufacturers, States. |
| Buy / Sell ticket | Party → product → warehouse → quantity → rate → (sale: which lots) → paperwork. |

## Upgrading an older book

`backend/migrate.py` runs on every boot and does nothing once a book is current. A version-1 book
(identified by its old `skus` table) is rebuilt in one transaction:

- Every table keeps its ids.
- Every warehouse name that version 1 ever wrote down becomes a warehouse record.
- Stock booked before warehouses existed goes into a warehouse called **Main**, which you can
  rename in Setup.
- A version-1 sale drawn from two warehouses keeps no single warehouse, rather than a made-up one.
- The old catalogue and stock lines become materials, grades, manufacturers and products.
- A party's old city becomes its address.
- Each party's state is read from its GSTIN.

`tests/test_migrate.py` rebuilds both version-1 shapes (production's and the local desk's) from
frozen schemas and checks nothing is lost.
