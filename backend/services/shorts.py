"""Selling short: a sale for more than the warehouse holds.

A short is not a negative lot. It is the part of a booked sale that no lot
covers yet - `deals.uncovered_g` - so every gram a sale draws on still traces
to a real purchase, and lots never go below zero.

One rule keeps the book exact:

    In any warehouse, for any product, there is never free stock and an
    open short at the same time.

So a sale may go short only after it has taken everything the warehouse
holds, and the moment stock of that product arrives in that warehouse -
bought, transferred in, found, or handed back by a cancelled or reduced
sale - it covers the oldest open short first, oldest stock first.
`cover()` runs at the end of every change to the book and restores the rule.

Stock shown for a warehouse is then free stock minus open shorts: +7 MT, or
-3 MT, never both. A short's margin is not known until it is covered: only the
covered part counts as realised, and the short part is valued at the mark in
open P&L (sale rate - mark, per kg).
"""
from __future__ import annotations

from typing import Any, Dict

from .. import db
from ..money import fmt_qty
from . import stock

# Every open short, one row per sale. Shared by every figure that shows stock.
OPEN = ("SELECT d.id, d.product_id, d.warehouse_id, d.uncovered_g, d.rate_paise FROM deals d "
        "WHERE d.side = 'sell' AND d.status = 'booked' AND d.uncovered_g > 0")


def add_allocation(conn, sale_id: int, sale_rate: int, lot: Dict[str, Any], qty_g: int, method: str) -> None:
    """Put `qty_g` of a lot on a sale, at the lot's cost, merging with a row it already has on that lot."""
    same = db.q1("SELECT id FROM allocations WHERE sale_deal_id=? AND lot_id=? AND active=1 "
                 "AND cost_paise=? AND sale_rate_paise=?", (sale_id, lot["id"], lot["rate_paise"], sale_rate))
    if same:
        conn.execute("UPDATE allocations SET qty_g = qty_g + ? WHERE id=?", (qty_g, same["id"]))
    else:
        conn.execute("""INSERT INTO allocations(sale_deal_id,lot_id,qty_g,cost_paise,sale_rate_paise,
                                                method,created_at,active) VALUES (?,?,?,?,?,?,?,1)""",
                     (sale_id, lot["id"], qty_g, lot["rate_paise"], sale_rate, method, db.now()))
    conn.execute("UPDATE lots SET qty_allocated_g = qty_allocated_g + ? WHERE id=?", (qty_g, lot["id"]))


def cover(conn) -> None:
    """Cover open shorts from free stock in the same warehouse: oldest short
    first, oldest stock first. A no-op when the book already obeys the rule."""
    cells = db.q("SELECT DISTINCT product_id, warehouse_id FROM (%s) x ORDER BY product_id, warehouse_id" % OPEN)
    for cell in cells:
        lots = stock.lots_for(cell["product_id"], cell["warehouse_id"])
        if not lots:
            continue
        sales = db.dicts(db.q(
            "SELECT * FROM deals WHERE side='sell' AND status='booked' AND uncovered_g > 0 "
            "AND product_id=? AND warehouse_id=? ORDER BY deal_date, id",
            (cell["product_id"], cell["warehouse_id"])))
        for sale in sales:
            need, took = sale["uncovered_g"], []
            for lot in lots:
                if need <= 0:
                    break
                take = min(lot["available_g"], need)
                if take <= 0:
                    continue
                add_allocation(conn, sale["id"], sale["rate_paise"], lot, take, "covered")
                lot["available_g"] -= take
                need -= take
                took.append("%s from %s" % (fmt_qty(take), lot["deal_ref"]))
            if not took:
                break                                   # the warehouse is empty again
            conn.execute("UPDATE deals SET uncovered_g=? WHERE id=?", (need, sale["id"]))
            db.log(conn, "deal", sale["id"], "cover",
                   "Covered %s of short %s: %s" % (fmt_qty(sale["uncovered_g"] - need), sale["ref"], ", ".join(took)),
                   {"ref": sale["ref"], "covered_g": sale["uncovered_g"] - need, "left_g": need})
    stock.refresh_lot_status(conn)


def short_in(product_id: int, warehouse_id: int) -> int:
    return db.scalar("SELECT COALESCE(SUM(uncovered_g),0) FROM (%s) x WHERE product_id=? AND warehouse_id=?"
                     % OPEN, (int(product_id), int(warehouse_id)))


def open_pnl_gp(product_id=None, warehouse_id=None) -> int:
    """Open P&L of short sales, in gram-paise: (sale rate - mark) on what is still short.
    A product with no mark contributes nothing, as unmarked stock does."""
    where, args = [], []
    if product_id:
        where.append("x.product_id = ?"); args.append(int(product_id))
    if warehouse_id:
        where.append("x.warehouse_id = ?"); args.append(int(warehouse_id))
    return db.scalar("SELECT COALESCE(SUM(x.uncovered_g * (x.rate_paise - m.rate_paise)),0) FROM (%s) x "
                     "JOIN marks m ON m.product_id = x.product_id %s"
                     % (OPEN, ("WHERE " + " AND ".join(where)) if where else ""), args)
