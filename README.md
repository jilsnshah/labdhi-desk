# Labdhi Desk — PVC / polymer trading book

A trading surface, not an ERP. Four screens, two hotkeys, and a lot-level
accounting engine underneath that can tell you where every single kilo came
from and where it went.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m backend.seed --reset     # optional demo book
.venv/bin/python run.py                      # http://127.0.0.1:8420
.venv/bin/python -m tests.test_engine        # 17 invariant tests
```

---

## What I changed from the brief, and why

**1. Parties need no setup; the material tree does.** A supplier or customer
exists the moment you name it in a deal — a misspelling there costs you one
duplicate row. A *manufacturer* is different: typed free-hand it arrives as
"Reliance", "reliance " and "RIL", and one position silently becomes three. So
materials, grades and manufacturers are a maintained tree on the **Setup**
screen, and the ticket offers them as a closed list. New entries can still be
added from inside the ticket; they write to the same tree.

**2. Suppliers and customers are one table.** In this trade the same firm sells
you HS1000 in March and buys it back in May. Two tables would have meant two
records for one relationship and a broken counterparty P&L.

**2a. Supplier and manufacturer are never the same field.** The supplier is the
party you bought from; the manufacturer is who made the resin. You buy
Chemplast HS1000 *from* Shreeji Polymers, and next week the same grade from
Rajdhani. Confusing the two would make cost history meaningless.

**3. `98.25+` is parsed as "basic rate, GST extra"** and stored as a flag.
Margins are computed on basic rates throughout, because GST is pass-through.
Per your instruction freight, delivery, transport, payment and e-way are
**recorded but not costed** — they sit on the deal as text. When you want them
in the landed cost later, the change is one column on `lots` and one line in
the margin function; nothing else moves.

**4. Short selling is off.** The engine can represent an uncovered sale — the
column and the alert exist, because a book can end up short through a cancelled
purchase — but the interface will not create one. A sale is either fully
covered by real lots or it does not book. `allow_short_sales` stays `0`.

**5. Everything is reversible.** Booking writes an undoable event; `U` or the
toast reverses the last one. Cancelling a sale hands the exact grams back to
the exact lots they came from. Cancelling a *purchase* whose material is
already sold is refused, and the error names the sales blocking it.

---

## The interaction model

Booking a sale is four taps and zero navigation:

```
S                  → sell screen opens
Mahavir Pipes      → tap a chip (recent counterparties first)
PVC                → material
S65                → grade, narrowed to grades of PVC
Reliance  5T       → manufacturer, narrowed again, with stock on the chip
50% / All          → tap a quantity chip, or ± by the tonne
₹97.25             → ± by 25 paise; the margin moves as you move it
⌘↵                 → booked
```

The three material levels cascade: choosing PVC narrows the grade list, and
choosing S65 narrows the manufacturer list to just the makers you hold that
grade from. On a sale only lines with stock are offered, so it is impossible to
walk the tree into a dead end.

Only the slot you are on is bright; finished slots collapse to a chip you can
tap to change. Logistics live behind one collapsed `details`. Nothing is typed
that can be tapped.

The hard part of a sell is not the buyer or the rate — it is deciding how much
of it comes out of each lot you are sitting on. Two earlier versions of this
screen tried to be clever about that and both failed. Auto-allocating and then
letting him nudge it meant editing one row silently rewrote the others. Making
him tap lots in a priority order removed the arithmetic but also removed the
thing he actually wants to say: *three tonnes out of this one*.

So the split is now plain, and the plainness is the point:

```
 WHICH STOCK GOES OUT                              ┌──────────────────┐
 ₹97.40   Shreeji Polymers  10 MT   +₹5.85/kg      │ LEFT TO ASSIGN   │
                                    [  5000 ] kg   │     12 MT        │
 ₹99.10   Shreeji Polymers   7 MT   +₹4.15/kg      │    of 24 MT      │
                                    [     0 ] kg   │ ▓▓▓▓▓▓▓░░░░░░░░  │
 ₹99.10   Vora Polychem     12 MT   +₹4.15/kg      │  12 MT assigned  │
                                    [  7000 ] kg   │  cost ₹98.11     │
 ₹100.00  Rajdhani Traders  18 MT   +₹3.25/kg      │    Clear all     │
                                    [     0 ] kg   └──────────────────┘
                                       fill rest      ↑ sticky, always in view
```

Every lot starts at **zero**. He types into the ones he wants. Rows are ordered
cheapest first, and two lots at the same rate stay tellable apart by supplier,
purchase reference, date and remaining stock. `fill rest` on any row drops the
outstanding balance into it, so the last row never needs mental arithmetic.

**Left to assign** sits beside the rows and is `position: sticky`, so it never
scrolls away while he works down a long list. It has exactly one target: zero.
Type more than a lot holds and the box stops at what it holds, immediately —
not on blur, so the figure on screen is always the figure that will be sold.

### No short, no over — enforced in three places

* The **quantity** field cannot exceed what he actually holds, so a sale can
  never be written for stock that does not exist.
* The **book button stays locked** unless assigned equals the sale exactly.
  Under, and it says how much is still to assign; over, and it says how much to
  take back out.
* The **server refuses anyway**: `allow_short_sales` is off, so a sale that
  cannot be covered from real lots is rejected with the shortfall named, no
  matter what any client sends.

### Screens

| | |
|---|---|
| **Desk** `1` | Open P&L, realised today, and one card per position. Each card carries the *lot ladder* — every purchase lot as a segment, coloured cheap-green to dear-amber, so your cost structure is a picture, not a table. |
| **Flow** `2` | The lineage graph. Purchase lots on the left, sales on the right, ribbons in between sized by quantity and coloured by margin. Hatched blocks are stock still sitting. Click anything for the full trace. |
| **Search** | Every list has one. Positions match on material, grade, manufacturer *or* the supplier the stock came from — "the DCW S65" and "the stuff from Vora" both find it. The tape additionally matches deal reference, counterparty and transporter. |
| **Setup** `4` | The master tree: materials, the grades under each, and the manufacturers who make each grade. Stock and deal counts show against every node, and anything already traded refuses to be deleted. |
| **Tape** `3` | Every deal, newest first. Click one to unfold its lineage — a sale shows the lots it came from, a purchase shows the buyers it went to. Reset an allocation or cancel from there. |
| **Position** | Click a card: the ladder full-width, then every lot with a fill bar and the exact list of who bought which kilos at what rate for what margin. |

Hotkeys: `B` buy · `S` sell · `U` undo last booking · `1/2/3` screens · `Esc`
close · `⌘↵` confirm.

---

## Data model

```
parties ──┬── deals ──┬── lots ────┐
          │  (buy)    │            │  (a booked BUY mints exactly one lot)
          │           └── rate, qty, qty_allocated
          │                        │
          └── deals ──── allocations ───┘   ← the edge of the lineage graph
             (sell)      qty_g, cost_paise, sale_rate_paise  (SNAPSHOTS)
```

* `skus` — **material x grade x manufacturer**, the tradeable atom and the unit
  inventory is kept in. PVC S65 from Reliance and PVC S65 from DCW are separate
  positions with separate costs, enforced by `UNIQUE (material, grade, manufacturer)`
* `catalog_materials` / `catalog_grades` / `catalog_makers` — the master tree
  behind the Setup screen. Entries can exist with no stock and no history; ones
  that have been traded cannot be deleted
* `marks` — current market rate per material, set by your last sale or by hand;
  drives unrealised P&L, and rolls back if that sale is cancelled
* `events` — append-only audit of every mutation, some flagged undoable

**Allocations carry snapshots of cost and sale rate, not references.** History
must never change under you because a lot was edited later.

### Numbers are integers, always

`backend/money.py` is the only place arithmetic happens.

| stored as | unit | example |
|---|---|---|
| quantity | grams | 20,000 kg → `20_000_000` |
| rate | paise per kg | ₹98.25 → `9825` |
| value | paise | ₹19,65,000 → `196_500_000` |

No float ever reaches the database, so 5,000 kg + 8,000 kg + 7,000 kg is
20,000 kg forever. Rounding is half-up in one function. Input parsing accepts
`20,000 kg`, `20 MT`, `20t`, `40 bags`, `₹98.25+`, `Rs 102`.

### The invariant the tests defend

> Every gram bought is either still in stock or allocated to exactly one sale.

`assert_conserved()` in `tests/test_engine.py` checks it after every mutating
test — sale, cancel, re-allocate, undo. If that identity ever breaks, the
margins are fiction.

---

## The allocation engine

`backend/services/allocation.py` is pure: it reads lots, returns a proposal,
writes nothing. `deals.book_sell()` is the only thing that commits.

1. **The trader's own picks first**, honoured exactly and clamped only to what
   the lot actually holds. A chosen lot is then removed from the auto-fill
   pool, so his number is the final word on that lot — typing `0` is how you
   exclude a lot entirely.
2. **Then the fill order takes the rest** — oldest stock first. This only runs
   for the opening proposal; once the trader has touched the split, the client
   sends every lot explicitly and no auto-fill happens at all. The other orders
   (newest, cheapest, dearest, pro-rata) exist in the engine and are used by the
   seed script, but nothing in the interface exposes them.
3. **Whatever is left over is a short**, reported as `uncovered_g` rather than
   quietly rounded away.

`preview()` prices a plan without touching the database — that is what makes
the margin move live while you drag the rate. `reallocate()` re-runs it on a
booked sale: old allocation rows go `active=0` rather than being deleted, so
the audit trail survives the change.

---

## Layout

```
backend/
  schema.sql          every table, commented
  money.py            integer money + unit parsing
  db.py               connections, transactions, audit log
  api.py              HTTP surface
  seed.py             demo book
  services/
    catalog.py        parties + skus, find-or-create
    allocation.py     the engine
    deals.py          lifecycle: draft → booked → cancelled
    inventory.py      positions, lot ladder, lineage graph
    dashboard.py      desk summary + attention
web/                  vanilla ES modules, no build step
tests/test_engine.py  17 invariant tests
```

No ORM: the SQL is explicit because this is money. No frontend framework: four
screens do not need one, and every interaction lands inside a frame.

---

## Built for a book that keeps growing

Three things would have fallen over at a few thousand trades, so none of them
loads everything:

**Lists are paged.** Positions and the tape fetch one page and append on
*Load more*, with `Showing 60 of 176` under them. Search runs server-side, so
filtering does not depend on having loaded the rows first.

**Totals are computed in SQL, not by summing the page.** `inventory.totals()`
returns stock, stock value, open and realised P&L across the whole book in two
queries. Summing a paginated list would have quietly under-reported the moment
the second page existed — the sort of bug that looks like a rounding error and
is not.

**The lineage graph is a window, not the whole book.** It takes a date range
(7 / 30 / 90 days, All time, or explicit dates) and is anchored on *sales* in
that period. The purchase lots feeding those sales are pulled in whatever their
own date, because a sale whose source is off-screen is not a lineage, it is a
dangling arrow. Purchases made inside the window show too, so material bought
and not yet sold still appears as idle stock. Beyond 60 sales it renders the
newest and says so rather than drawing an unreadable mat.

## Known edges

* Single-user. SQLite in WAL mode with `BEGIN IMMEDIATE` around every write is
  correct for one desk; a second trader needs Postgres and a lock strategy.
* Search is `LIKE '%term%'`, which cannot use an index. Fine to five figures of
  deals on SQLite; past that it wants FTS5 or a trigram index.
* Payments and deliveries are not tracked yet — the deal records the terms as
  text. Both are additive: a `payments` table against `deals`, a `movements`
  table against `lots`.
* Re-allocating a booked sale from the Tape only resets it to the automatic
  fill. Re-opening the price rack on a booked sale is the better interaction
  and is not built yet.
