# Labdhi Desk — PVC / polymer trading book

A trading surface, not an ERP. Five screens, two hotkeys, and a lot-level
accounting engine underneath that can tell you where every single kilo came
from, which warehouse it sat in, and where it went.

**How the pieces connect — entities, stock, pagination, migrations — is in
[ARCHITECTURE.md](ARCHITECTURE.md).**

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m backend.seed --reset     # optional demo book
.venv/bin/python run.py                      # http://127.0.0.1:8420
.venv/bin/python -m tests.test_engine        # engine invariants, records by id
.venv/bin/python -m tests.test_stock         # warehouse-wise stock, transfers, adjustments, ledger
.venv/bin/python -m tests.test_fields        # Sauda No., warehouse, GST, payment due
.venv/bin/python -m tests.test_parties       # party identity + importer
.venv/bin/python -m tests.test_migrate       # a v1 book upgrades without losing a row
.venv/bin/python -m tests.test_api           # every endpoint, every list is a page
```

Every test module also runs against Postgres: `DATABASE_URL=postgres://... python -m tests.<module>`.

---

## What I changed from the brief, and why

**1. Party, warehouse and product are records, not fields.** A ticket never
takes a typed name. You pick the party, the product and the warehouse from
their lists. One that isn't there yet is added through its form (**Add new
party / product / warehouse**) without leaving the ticket, and comes straight
back selected. The same forms edit the same records in **Setup**, so a firm, a
godown or a grade exists exactly once, and correcting it corrects every deal.

**2. Suppliers and customers are one table.** In this trade the same firm sells
you HS1000 in March and buys it back in May. Two tables would have meant two
records for one relationship and a broken counterparty P&L. A transporter is a
party too.

**2a. Supplier and manufacturer are never the same field.** The supplier is the
party you bought from; the manufacturer is who made the resin. You buy
Chemplast HS1000 *from* Shreeji Polymers, and next week the same grade from
Rajdhani. Confusing the two would make cost history meaningless.

**3. Stock is kept warehouse by warehouse.** A purchase is received into one
warehouse; a sale is dispatched from one warehouse and can only draw on lots
sitting there. Transfers and adjustments move stock between and within
warehouses without losing which purchase it came from.

**4. `98.25+` means "basic rate, GST extra"** and is stored as a flag.
Margins are computed on basic rates throughout, because GST is pass-through.
Freight, delivery, transport, payment and e-way are **recorded but not
costed** — they sit on the deal as text.

**5. Short selling is off.** A sale is either fully covered by real lots in its
warehouse or it does not book. `allow_short_sales` stays `0`.

**6. Everything is reversible.** Booking, transferring and adjusting each write
an undoable event; `U` or the toast reverses the last one. Cancelling a sale
hands the exact grams back to the exact lots they came from. Cancelling a
*purchase* whose stock is already sold or moved is refused, and the error names
what blocks it.

---

## The interaction model

Booking a sale:

```
S                  → sell screen opens
Mahavir Pipes      → search or tap from the party list  (+ Add new party)
PVC → S65 → Reliance   material, grade, manufacturer — only products you hold
Mundra  12T        → dispatch from: only warehouses holding it (skipped if one)
50% / All          → quantity, capped at what Mundra holds
₹97,250            → rate per MT; the margin moves as you move it
which lots         → type how much comes out of each lot in Mundra
Book sale          → booked
```

A purchase is the same with **Receive into** for the warehouse, and **Add new
product / warehouse** available at each step.

The hard part of a sale is deciding how much comes out of each lot you are
sitting on, so the split is plain:

```
 WHICH STOCK GOES OUT OF MUNDRA                    ┌──────────────────┐
 ₹97,400  Shreeji Polymers  10 MT   +₹5,850/MT     │ LEFT TO ASSIGN   │
                                    [  5000 ] kg   │     12 MT        │
 ₹99,100  Vora Polychem     12 MT   +₹4,150/MT     │    of 24 MT      │
                                    [  7000 ] kg   │ ▓▓▓▓▓▓▓░░░░░░░░  │
 ₹1,00,000 Rajdhani Traders 18 MT   +₹3,250/MT     │  12 MT assigned  │
                                    [     0 ] kg   │    Clear all     │
                                       fill rest   └──────────────────┘
```

Every lot starts at **zero**. He types into the ones he wants; `fill rest`
drops the outstanding balance into a row. **Left to assign** is sticky and has
exactly one target: zero.

### No short, no over — enforced in three places

* The **quantity** cannot exceed what the dispatching warehouse holds.
* The **book button stays locked** unless assigned equals the sale exactly.
* The **server refuses anyway**: a sale that cannot be covered from real lots in
  its warehouse is rejected with the shortfall and the warehouse's stock named,
  and a pin on a lot in another warehouse is refused.

### Screens

| | |
|---|---|
| **Desk** `1` | Open P&L, realised today, and one card per product with its per-warehouse stock and the *lot ladder* — every lot as a segment, coloured cheap-green to dear-amber. |
| **Stock** `2` | Warehouse cards; stock per product per warehouse; open a row for its lots (**Move**, **Adjust**) and its ledger with a running balance; every movement below. |
| **Flow** `3` | The lineage graph. Purchases left, sales right, ribbons sized by quantity and coloured by margin. |
| **Tape** `4` | Every sauda as a table row: Sauda No., Date, Type, Party, Product / Grade, Qty (MT), Rate (₹/MT), Warehouse, Status, Delivery, Margin. Open a row for every recorded field and the lineage. |
| **Setup** `5` | Parties, Products, Warehouses, Materials & grades, Manufacturers, States — each searchable and paged. |
| **Position** | Click a card: the ladder full-width, per-warehouse stock, then every lot with where it went. |

Hotkeys: `B` buy · `S` sell · `U` undo last action · `1`–`5` screens · `Esc` close.

The phone gets its own app on the same records: one decision per screen, a
numeric pad, Buy and Sell under the thumb, forms as bottom sheets.

---

## Data model

The short version (full map in [ARCHITECTURE.md](ARCHITECTURE.md)):

```
states ── parties ──┐                      ┌── warehouses
                    ├── deals ── lots ─────┤   (a BUY books a lot in its warehouse;
materials ─ grades ─┤    │        │        │    a transfer makes a child lot elsewhere)
manufacturers ──────┴ products    │        │
                         └──── allocations ┘   ← the edge of the lineage graph
                                  stock_moves  ← transfers and adjustments
```

**Allocations carry snapshots of cost and sale rate, not references.** History
must never change under you because a lot was edited later.

### Numbers are integers, always

`backend/money.py` is the only place arithmetic happens.

| stored as | unit | example |
|---|---|---|
| quantity | grams | 20,000 kg → `20_000_000` |
| rate | paise per kg | ₹98,250/MT → `9825` |
| value | paise | ₹19,65,000 → `196_500_000` |

No float ever reaches the database. Rates are shown per MT; one paisa per kg is
₹10 per MT, so a per-MT figure that is not a multiple of ₹10 is refused rather
than rounded.

### The invariant the tests defend

> Every gram bought (plus found, less written off) is either still in stock or
> allocated to exactly one sale.

`conserved()` in `tests/common.py` checks it after every mutating test — sale,
cancel, re-allocate, transfer, adjustment, undo, migration.

---

## The allocation engine

`backend/services/allocation.py` is pure: it reads lots, returns a proposal,
writes nothing. The pool is the product's lots **in the sale's warehouse**.

1. **The trader's own picks first**, honoured exactly and clamped to what the lot
   holds. A chosen lot leaves the auto-fill pool, so typing `0` excludes it.
2. **Then oldest stock first** for anything unspecified — only callers that leave
   lots out (the seed, re-planning) ever reach this; the ticket sends every lot.
3. **Whatever is left over is a short**, reported rather than rounded away, and
   refused at booking.

---

## Layout

```
backend/
  schema.sql          every table and the v_products view, commented
  migrate.py          v1 → v2 upgrade, runs on boot, no-op once current
  gst.py              GSTIN check digit, PAN, state codes
  money.py            integer money + unit parsing
  db.py               connections, transactions, paging, audit log
  api.py              HTTP surface: one list contract, one record contract
  seed.py             demo book
  import_parties.py   Tally ledger import
  services/
    parties.py        parties + states
    warehouses.py     warehouses
    products.py       materials, grades, manufacturers, products
    stock.py          lots, stock by warehouse, ledger, transfers, adjustments
    allocation.py     the engine
    deals.py          lifecycle: draft → booked → cancelled
    inventory.py      positions, lineage graph
    dashboard.py      summary, attention, counterparties
web/js/
  lists.js            the one paged list
  forms.js            the one form engine + every record form and picker
  trade.js / mticket.js   desktop / phone ticket
  desk, stock, flow, tape, setup .js   desktop screens;  mobile.js  phone screens
tests/                engine, stock, fields, parties, migrate, api (+ fixtures/)
```

No ORM: the SQL is explicit because this is money. No frontend framework and no
build step.

---

## Built for a book that keeps growing

**Every list is paged the same way** — `?q=&limit=&offset=` in, `{items, total,
has_more}` out — and every screen shows `25 of 1021` and a *Load more*. Search
runs server-side, so filtering never depends on having loaded the rows first.

**Totals are computed in SQL, not by summing the page.**

**The lineage graph is a window, not the whole book** — a date range anchored on
sales, with the purchases that fed them pulled in whatever their date.

## Deal paperwork

| Field | Rule |
|---|---|
| **Sauda No.** | `LE/26-27/0001` — one series for buys and sells, restarting each 1 April. The next number is one past the highest used that year. Editable; must be unique. The prefix is the `sauda_prefix` setting. |
| **Warehouse** | A warehouse record. Buy: where it is received. Sell: where it is dispatched from, and the only place its lots can come from. |
| **Rate** | Entered and shown **per MT**, stored as paise per kg; exact in ₹10 steps. |
| **GST extra** | Checkbox, on by default. Recorded only. |
| **Payment due** | A calendar date, with Today / +7 / +15 / +30 / +45 day shortcuts. |
| **Ex-Place** | Free text — pricing basis, e.g. Mundra. |
| **Transporter** | A party record, picked from the same list. |
| **Freight / Delivery by** | Buyer or Seller. *Delivery* is the tape's Delivery column. |

## Parties

A party is **name, GSTIN, PAN, state, phone, address** — no buyer/seller split.

* **Picked, never typed, in a ticket.** **Add new party** opens the form.
* **Unique by GSTIN.** The form checks as you type and names the party that
  already holds a GSTIN, with a **Use it** button. The same goes for a name.
* **PAN and state come from the GSTIN** and cannot disagree with it. Without a
  GSTIN they are entered; state is chosen from the GST state list.
* **GSTINs are check-digit validated** when typed.
* **Branches of one firm are separate parties** — two GSTINs sharing a PAN.

### Importing parties from Tally

```bash
.venv/bin/pip install -r requirements-dev.txt          # openpyxl, import only
.venv/bin/python -m backend.import_parties --dry-run "labdhi exim ledger address.xlsx" "om ledger address.xlsx"
.venv/bin/python -m backend.import_parties           "labdhi exim ledger address.xlsx" "om ledger address.xlsx"
```

The import reads Tally's *Updation of Party GSTIN/UIN* export:

- Numbered rows are parties.
- `DELIVERY` rows are ship-to addresses and are skipped.
- *Not Applicable* rows are accounting ledgers and are excluded.
- Duplicates collapse on GSTIN across both files.

Re-running is safe. The importer is the one place repeats merge automatically.

> **The ledgers are private.** They hold real customer names, addresses and
> GSTINs, and this repository is public. `*.xlsx` is gitignored — never commit
> them.

## Schema changes on a live database

`backend/migrate.py` runs on every boot. A version-1 book is rebuilt in one
transaction:

- Ids are kept.
- Text warehouses become warehouse records.
- Stock with no warehouse goes into **Main**.
- The catalogue becomes products.

`tests/test_migrate.py` builds both historic shapes from `tests/fixtures/` on
SQLite or Postgres and proves nothing is lost. Run it against a copy of
production before deploying.

## Known edges

* Single-user. SQLite in WAL mode with `BEGIN IMMEDIATE` around every write is
  correct for one desk; a second trader needs Postgres and a lock strategy.
* Search is `LIKE '%term%'`. Fine to five figures of deals; past that it wants
  FTS5 or a trigram index.
* Payments and physical deliveries against a sauda are not tracked yet — the
  deal records the terms. Both are additive tables against `deals`.
