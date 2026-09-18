"""Changing a booked sauda after the fact: edit it, or cancel a purchase whose
stock is already sold by moving those sales onto other stock.

Three rules keep the book exact through any change:

1. Everything happens in one transaction. An edit either lands whole or not
   at all - there is never a moment where a sale has been released but not
   re-allocated.
2. The book is changed the same way booking and cancelling change it: lot
   counters and allocations move together, so every gram bought is still
   either in stock or sold, and every sale is still covered exactly.
3. A preview is the real edit, run and then rolled back. What the screen
   shows before "Save" is computed by the same code that saves, so it cannot
   disagree with the result.

What each change does:

  any booked sauda   paperwork (GST flag, date, payment terms and due date,
                     Ex-Place, transporter, freight, transport, e-way, note):
                     written in place, nothing else moves.
  sale               party: in place.  rate: the sale's allocations take the
                     new rate, so its margin follows; stock does not move.
                     quantity / warehouse / product / which lots: the sale's
                     allocations are released and it is allocated again - the
                     same as cancelling and booking it anew, but keeping its
                     Sauda No., in one step.
  purchase           party: its lots name the new supplier.  rate: its lots
                     and the cost carried by every sale drawn on them change,
                     so those sales' margins follow.  quantity: may go down
                     only to what is sold and moved out of it; below that, the
                     sold part must be moved onto other stock (rehome).
                     product / warehouse: only while nothing of it is sold
                     (or with rehome, which moves what was sold first), and
                     never once part of it was transferred or adjusted.

Rehoming moves a sale's quantity off the lots of one purchase onto other lots
of the same product in the same warehouse, oldest first - the sale keeps its
Sauda No., buyer and rate; only where its material came from, and so its cost
and margin, change. If other stock cannot cover it, nothing changes.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .. import db
from ..money import fmt_qty
from . import allocation, parties, products, stock, warehouses
from .deals import (DealError, RehomeNeeded, _release_allocations, _require, _write_allocations,
                    get_deal)


PAPER = ("plus_gst", "deal_date", "payment_due", "ex_place", "transporter_id",
         "freight_by", "delivery_by", "payment_terms", "eway", "remarks")
CORE = ("party_id", "product_id", "warehouse_id", "qty_g", "rate_paise")
FIELDS = CORE + PAPER
TEXT = ("payment_due", "ex_place", "freight_by", "delivery_by", "payment_terms", "eway", "remarks")


# ------------------------------------------------------------------ dry run
class _Rollback(Exception):
    def __init__(self, payload):
        super().__init__("rolled back")
        self.payload = payload


def dry_run(fn):
    """Run fn inside a transaction, keep what it returns, then roll it all back."""
    try:
        with db.tx():
            raise _Rollback(fn())
    except _Rollback as done:
        return done.payload


# ------------------------------------------------------------------ what moved
def _book_state() -> Dict[str, Any]:
    sales = {r["id"]: dict(r) for r in db.q(
        """SELECT d.id, d.ref, p.name AS party, d.qty_g,
                  COALESCE(SUM(a.qty_g * (a.sale_rate_paise - a.cost_paise)), 0) AS margin_gp,
                  COALESCE(SUM(a.qty_g * a.cost_paise), 0) AS cost_gp,
                  COALESCE(SUM(a.qty_g), 0) AS alloc_g
           FROM deals d JOIN parties p ON p.id = d.party_id
           LEFT JOIN allocations a ON a.sale_deal_id = d.id AND a.active = 1
           WHERE d.side = 'sell' AND d.status = 'booked'
           GROUP BY d.id, d.ref, p.name, d.qty_g""")}
    cells = {(r["product"], r["warehouse"]): r["g"] for r in db.q(
        """SELECT s.display AS product, w.name AS warehouse, SUM(%s) AS g
           FROM lots l JOIN v_products s ON s.id = l.product_id JOIN warehouses w ON w.id = l.warehouse_id
           WHERE l.status = 'open' GROUP BY s.display, w.name""" % stock.AVAILABLE)}
    realised = db.scalar("""SELECT COALESCE(SUM(a.qty_g * (a.sale_rate_paise - a.cost_paise)), 0)
                            FROM allocations a JOIN deals d ON d.id = a.sale_deal_id
                            WHERE a.active = 1 AND d.status = 'booked'""")
    return {"sales": sales, "cells": cells, "realised_gp": realised}


def _gp(gp) -> int:
    return stock._paise(gp)


def _avg(row) -> Optional[int]:
    """Average cost per kg of what the sale draws on, to the nearest paisa."""
    if not row or not row["alloc_g"]:
        return None
    q, r = divmod(int(row["cost_gp"]), int(row["alloc_g"]))
    return q + (1 if r * 2 >= row["alloc_g"] else 0)


def _effects(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    sales = []
    for sid in sorted(set(before["sales"]) | set(after["sales"])):
        b, a = before["sales"].get(sid), after["sales"].get(sid)
        bm = _gp(b["margin_gp"]) if b else None
        am = _gp(a["margin_gp"]) if a else None
        if bm != am:
            ref = (a or b)["ref"]
            party = (a or b)["party"]
            qty = (a or b)["qty_g"]
            sales.append({"id": sid, "ref": ref, "party": party, "qty_g": qty,
                          "margin_before_paise": bm, "margin_after_paise": am,
                          "cost_before_paise": _avg(b), "cost_after_paise": _avg(a)})
    cells = []
    for key in sorted(set(before["cells"]) | set(after["cells"])):
        b, a = before["cells"].get(key, 0), after["cells"].get(key, 0)
        if b != a:
            cells.append({"product": key[0], "warehouse": key[1], "before_g": b, "after_g": a})
    return {"sales": sales, "stock": cells,
            "realised_before_paise": _gp(before["realised_gp"]), "realised_after_paise": _gp(after["realised_gp"])}


# ------------------------------------------------------------------ marks
def recompute_mark(conn, product_id: int, ref: str) -> None:
    """After sale `ref` changed, put the mark of one product where the rule
    says - whichever came last, the latest booked sale or a rate set by hand -
    but only if that sale set it or now sets it; any other mark is left alone."""
    import json
    current = db.q1("SELECT source FROM marks WHERE product_id=?", (product_id,))
    sale = db.q1(
        """SELECT d.ref, d.rate_paise, MAX(e.id) AS ev FROM deals d
           JOIN events e ON e.entity = 'deal' AND e.entity_id = d.id AND e.action = 'book'
           WHERE d.product_id = ? AND d.side = 'sell' AND d.status = 'booked'
           GROUP BY d.id, d.ref, d.rate_paise ORDER BY ev DESC LIMIT 1""", (product_id,))
    manual = db.q1("SELECT id AS ev, payload FROM events WHERE entity = 'product' AND entity_id = ? "
                   "AND action = 'mark' ORDER BY id DESC LIMIT 1", (product_id,))
    if manual and (sale is None or manual["ev"] > sale["ev"]):
        rate, source = int(json.loads(manual["payload"])["rate_paise"]), "manual"
    elif sale:
        rate, source = sale["rate_paise"], "sale %s" % sale["ref"]
    else:
        rate, source = None, None
    mine = "sale %s" % ref
    if source != mine and not (current and current["source"] == mine):
        return
    if source is None:
        conn.execute("DELETE FROM marks WHERE product_id=?", (product_id,))
        return
    conn.execute(
        "INSERT INTO marks(product_id,rate_paise,source,updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(product_id) DO UPDATE SET rate_paise=excluded.rate_paise, "
        "source=excluded.source, updated_at=excluded.updated_at",
        (product_id, rate, source, db.now()))


# ------------------------------------------------------------------ rehoming
def _add_allocation(conn, sale_id: int, sale_rate: int, lot: Dict[str, Any], qty_g: int, method: str) -> None:
    same = db.q1("SELECT id FROM allocations WHERE sale_deal_id=? AND lot_id=? AND active=1 "
                 "AND cost_paise=? AND sale_rate_paise=?", (sale_id, lot["id"], lot["rate_paise"], sale_rate))
    if same:
        conn.execute("UPDATE allocations SET qty_g = qty_g + ? WHERE id=?", (qty_g, same["id"]))
    else:
        conn.execute("""INSERT INTO allocations(sale_deal_id,lot_id,qty_g,cost_paise,sale_rate_paise,
                                                method,created_at,active) VALUES (?,?,?,?,?,?,?,1)""",
                     (sale_id, lot["id"], qty_g, lot["rate_paise"], sale_rate, method, db.now()))
    conn.execute("UPDATE lots SET qty_allocated_g = qty_allocated_g + ? WHERE id=?", (qty_g, lot["id"]))


def move_allocations(conn, pieces: List[Tuple[Dict[str, Any], int]], exclude_lot_ids: List[int]) -> None:
    """Move `qty` of each allocation off its lot onto other lots of the same
    product in the same warehouse, oldest first. Raises, changing nothing that
    survives the transaction, if other stock cannot cover it."""
    excluded = set(exclude_lot_ids)
    for alloc, qty in pieces:
        if qty <= 0:
            continue
        conn.execute("UPDATE allocations SET active=0 WHERE id=?", (alloc["id"],))
        if alloc["qty_g"] - qty > 0:
            conn.execute("""INSERT INTO allocations(sale_deal_id,lot_id,qty_g,cost_paise,sale_rate_paise,
                                                    method,created_at,active) VALUES (?,?,?,?,?,?,?,1)""",
                         (alloc["sale_deal_id"], alloc["lot_id"], alloc["qty_g"] - qty, alloc["cost_paise"],
                          alloc["sale_rate_paise"], alloc["method"], db.now()))
        conn.execute("UPDATE lots SET qty_allocated_g = qty_allocated_g - ? WHERE id=?", (qty, alloc["lot_id"]))
        src = db.q1("SELECT product_id, warehouse_id FROM lots WHERE id=?", (alloc["lot_id"],))
        need = qty
        for lot in stock.lots_for(src["product_id"], src["warehouse_id"]):
            if need <= 0:
                break
            if lot["id"] in excluded or lot["available_g"] <= 0:
                continue
            take = min(lot["available_g"], need)
            _add_allocation(conn, alloc["sale_deal_id"], alloc["sale_rate_paise"], lot, take, "rehomed")
            need -= take
        if need > 0:
            sale = db.q1("SELECT ref FROM deals WHERE id=?", (alloc["sale_deal_id"],))
            names = db.q1("""SELECT s.display AS product, w.name AS warehouse FROM lots l
                             JOIN v_products s ON s.id = l.product_id JOIN warehouses w ON w.id = l.warehouse_id
                             WHERE l.id = ?""", (alloc["lot_id"],))
            raise DealError("Not enough other %s in %s to move %s onto - %s more is needed. Nothing was changed."
                            % (names["product"], names["warehouse"], sale["ref"], fmt_qty(need)))
    stock.refresh_lot_status(conn)


def _allocations_on(lot_ids: List[int], newest_first: bool = False) -> List[Dict[str, Any]]:
    if not lot_ids:
        return []
    return db.dicts(db.q(
        """SELECT a.* FROM allocations a JOIN deals d ON d.id = a.sale_deal_id
           WHERE a.active = 1 AND a.lot_id IN (%s)
           ORDER BY d.deal_date %s, a.id %s""" % (",".join("?" * len(lot_ids)),
                                                  "DESC" if newest_first else "", "DESC" if newest_first else ""),
        lot_ids))


def rehome_purchase(conn, buy: Dict[str, Any]) -> None:
    """Every sale drawn on this purchase moves onto other stock (for cancelling it)."""
    lot_ids = [r["id"] for r in db.q("SELECT id FROM lots WHERE deal_id=?", (buy["id"],))]
    move_allocations(conn, [(a, a["qty_g"]) for a in _allocations_on(lot_ids)], lot_ids)


def lots_for_sale(product_id: int, warehouse_id: Optional[int], sale_id: Optional[int]) -> List[Dict[str, Any]]:
    """The lots a sale being edited can be split across: what is free, plus
    what the sale itself holds now (it gets that back when it is re-split)."""
    lots = stock.lots_for(product_id, warehouse_id)
    if not sale_id:
        return lots
    by_id = {l["id"]: l for l in lots}
    for r in db.q("""SELECT a.lot_id, SUM(a.qty_g) AS q FROM allocations a JOIN lots l ON l.id = a.lot_id
                     WHERE a.sale_deal_id=? AND a.active=1 AND l.product_id=? GROUP BY a.lot_id""",
                  (sale_id, product_id)):
        lot = by_id.get(r["lot_id"])
        if lot is None:
            lot = stock.get_lot(r["lot_id"])
            if lot is None or (warehouse_id and lot["warehouse_id"] != int(warehouse_id)):
                continue
            lot["available_g"] = 0
            lots.append(lot)
        lot["available_g"] += int(r["q"])
        lot["held_g"] = int(r["q"])
    lots.sort(key=lambda l: (l["deal_date"] or "", l["id"]))
    return lots


# ------------------------------------------------------------------ editing
def _clean(deal: Dict[str, Any], changes: Dict[str, Any]) -> Dict[str, Any]:
    new = dict(deal)
    side = deal["side"]
    for k, v in changes.items():
        if k not in FIELDS:
            continue
        if k in TEXT:
            v = (str(v).strip() if v is not None else "") or None
        elif k == "plus_gst":
            v = 1 if v else 0
        elif k == "deal_date":
            v = (v or "").strip() or deal["deal_date"]
            if len(v) != 10 or v[4] != "-" or v[7] != "-":
                raise DealError("Deal date must be a date")
        elif k in ("qty_g", "rate_paise"):
            v = int(v or 0)
            if v <= 0:
                raise DealError("%s must be greater than zero" % ("Quantity" if k == "qty_g" else "Rate"))
        elif k == "party_id":
            v = _require(parties.require, v, "supplier" if side == "buy" else "buyer")["id"]
        elif k == "product_id":
            v = _require(products.require, v)["id"]
        elif k == "warehouse_id":
            v = _require(warehouses.require, v)["id"]
        elif k == "transporter_id":
            v = _require(parties.require, v, "transporter")["id"] if v else None
        new[k] = v
    return new


def _write_fields(conn, deal_id: int, new: Dict[str, Any], changed: List[str]) -> None:
    if changed:
        conn.execute("UPDATE deals SET %s WHERE id=?" % ", ".join("%s=?" % k for k in changed),
                     [new[k] for k in changed] + [deal_id])


def _trim_sale(conn, sale_id: int, grams: int) -> None:
    """Give back `grams` of a sale, from the allocations made last."""
    rows = db.dicts(db.q("SELECT * FROM allocations WHERE sale_deal_id=? AND active=1 ORDER BY id DESC",
                         (sale_id,)))
    for a in rows:
        if grams <= 0:
            break
        take = min(a["qty_g"], grams)
        conn.execute("UPDATE allocations SET active=0 WHERE id=?", (a["id"],))
        if a["qty_g"] > take:
            conn.execute("""INSERT INTO allocations(sale_deal_id,lot_id,qty_g,cost_paise,sale_rate_paise,
                                                    method,created_at,active) VALUES (?,?,?,?,?,?,?,1)""",
                         (sale_id, a["lot_id"], a["qty_g"] - take, a["cost_paise"], a["sale_rate_paise"],
                          a["method"], db.now()))
        conn.execute("UPDATE lots SET qty_allocated_g = qty_allocated_g - ? WHERE id=?", (take, a["lot_id"]))
        grams -= take
    stock.refresh_lot_status(conn)


def _one_row_per_lot(conn, sale_id: int) -> None:
    """A sale draws on each lot once, as a fresh booking would record it: where
    an edit left two live rows on one lot, they become one (the old rows stay
    as history, inactive)."""
    for r in db.q("""SELECT lot_id, COUNT(*) AS n, SUM(qty_g) AS g FROM allocations
                     WHERE sale_deal_id=? AND active=1 GROUP BY lot_id""", (sale_id,)):
        if r["n"] < 2:
            continue
        first = db.q1("SELECT * FROM allocations WHERE sale_deal_id=? AND lot_id=? AND active=1 ORDER BY id",
                      (sale_id, r["lot_id"]))
        conn.execute("UPDATE allocations SET active=0 WHERE sale_deal_id=? AND lot_id=? AND active=1",
                     (sale_id, r["lot_id"]))
        conn.execute("""INSERT INTO allocations(sale_deal_id,lot_id,qty_g,cost_paise,sale_rate_paise,
                                                method,created_at,active) VALUES (?,?,?,?,?,?,?,1)""",
                     (sale_id, r["lot_id"], int(r["g"]), first["cost_paise"], first["sale_rate_paise"],
                      first["method"], db.now()))


def _edit_sale(conn, old, new, changed, pins) -> None:
    _write_fields(conn, old["id"], new, changed)
    policy = old["alloc_policy"] or allocation.DEFAULT_POLICY
    held = db.scalar("SELECT COALESCE(SUM(qty_g),0) FROM allocations WHERE sale_deal_id=? AND active=1",
                     (old["id"],))
    names = db.q1("SELECT s.display AS product, w.name AS warehouse FROM v_products s, warehouses w "
                  "WHERE s.id = ? AND w.id = ?", (new["product_id"], new["warehouse_id"]))
    moved = new["product_id"] != old["product_id"] or new["warehouse_id"] != old["warehouse_id"]

    if pins is not None or moved:
        # a new split: hand everything back, then allocate as a fresh booking would
        _release_allocations(conn, old["id"])
        plan = allocation.suggest(new["product_id"], new["qty_g"], policy, pins=pins,
                                  warehouse_id=new["warehouse_id"])
        if plan["rejected"]:
            raise DealError("Some of the chosen stock is not %s in %s - pick again"
                            % (names["product"], names["warehouse"]))
        _write_allocations(conn, new, plan, policy)
    elif new["qty_g"] < held:
        _trim_sale(conn, old["id"], held - new["qty_g"])
    elif new["qty_g"] > held:
        # keep what it holds; the extra comes from what is free, as a booking would take it
        plan = allocation.suggest(new["product_id"], new["qty_g"] - held, policy,
                                  warehouse_id=new["warehouse_id"])
        _write_allocations(conn, new, plan, policy)

    # every allocation of the sale carries its (possibly new) rate
    conn.execute("UPDATE allocations SET sale_rate_paise=? WHERE sale_deal_id=? AND active=1",
                 (new["rate_paise"], old["id"]))
    covered = db.scalar("SELECT COALESCE(SUM(qty_g),0) FROM allocations WHERE sale_deal_id=? AND active=1",
                        (old["id"],))
    short = max(0, new["qty_g"] - covered)
    if short > 0 and short > old["uncovered_g"] and db.settings().get("allow_short_sales") != "1":
        free = stock.available_in(new["product_id"], new["warehouse_id"])
        raise DealError("Short by %s - %s has only %s more of %s free for this sale. Nothing was changed."
                        % (fmt_qty(short), names["warehouse"], fmt_qty(free), names["product"]))
    conn.execute("UPDATE deals SET uncovered_g=? WHERE id=?", (short, old["id"]))
    _one_row_per_lot(conn, old["id"])
    recompute_mark(conn, old["product_id"], old["ref"])
    if new["product_id"] != old["product_id"]:
        recompute_mark(conn, new["product_id"], old["ref"])


def _edit_purchase(conn, old, new, changed, rehome: bool) -> None:
    lots = db.dicts(db.q("SELECT * FROM lots WHERE deal_id=? AND status != 'cancelled' ORDER BY id", (old["id"],)))
    root = next(l for l in lots if l["parent_lot_id"] is None)
    lot_ids = [l["id"] for l in lots]
    moved_out = sum(l["qty_out_g"] for l in lots) > 0 or len(lots) > 1
    party = db.q1("SELECT name FROM parties WHERE id=?", (new["party_id"],))["name"]

    if "product_id" in changed or "warehouse_id" in changed:
        if moved_out:
            raise DealError("%s was partly transferred or adjusted - undo those stock movements before "
                            "changing its product or warehouse" % old["ref"])
        if root["qty_allocated_g"] > 0:
            if not rehome:
                raise RehomeNeeded("%s of %s is already sold - those sales must move onto other stock "
                                   "before its product or warehouse can change"
                                   % (fmt_qty(root["qty_allocated_g"]), old["ref"]), root["qty_allocated_g"])
            move_allocations(conn, [(a, a["qty_g"]) for a in _allocations_on([root["id"]])], lot_ids)
        conn.execute("UPDATE lots SET product_id=?, warehouse_id=? WHERE id=?",
                     (new["product_id"], new["warehouse_id"], root["id"]))

    if "qty_g" in changed:
        root = db.q1("SELECT * FROM lots WHERE id=?", (root["id"],))
        need = root["qty_allocated_g"] + root["qty_out_g"] - new["qty_g"]
        if need > 0:
            if need > root["qty_allocated_g"]:
                raise DealError("%s of %s was transferred or written off - it cannot go below %s"
                                % (fmt_qty(root["qty_out_g"]), old["ref"], fmt_qty(root["qty_out_g"])))
            if not rehome:
                raise RehomeNeeded("%s of %s is already sold - lowering it to %s means moving %s of those "
                                   "sales onto other stock"
                                   % (fmt_qty(root["qty_allocated_g"]), old["ref"], fmt_qty(new["qty_g"]),
                                      fmt_qty(need)), need)
            pieces, left = [], need
            for a in _allocations_on([root["id"]], newest_first=True):
                if left <= 0:
                    break
                take = min(a["qty_g"], left)
                pieces.append((a, take))
                left -= take
            move_allocations(conn, pieces, lot_ids)
        conn.execute("UPDATE lots SET qty_g=? WHERE id=?", (new["qty_g"], root["id"]))

    if "rate_paise" in changed:
        conn.execute("UPDATE lots SET rate_paise=? WHERE deal_id=?", (new["rate_paise"], old["id"]))
        conn.execute("UPDATE allocations SET cost_paise=? WHERE active=1 AND lot_id IN (%s)"
                     % ",".join("?" * len(lot_ids)), [new["rate_paise"]] + lot_ids)

    if "party_id" in changed:
        conn.execute("UPDATE lots SET supplier_id=?, label=? WHERE deal_id=?",
                     (new["party_id"], "%s / %s" % (old["ref"], party), old["id"]))

    _write_fields(conn, old["id"], new, changed)
    stock.refresh_lot_status(conn)


def edit_deal(deal_id: int, changes: Dict[str, Any], rehome: bool = False) -> Dict[str, Any]:
    with db.tx() as conn:
        row = db.q1("SELECT * FROM deals WHERE id=?", (deal_id,))
        if row is None:
            raise DealError("Deal not found")
        old = dict(row)
        if old["status"] != "booked":
            raise DealError("%s is %s - only a booked sauda can be edited" % (old["ref"], old["status"]))
        new = _clean(old, changes)
        changed = [k for k in FIELDS if k in changes and new[k] != old[k]]
        pins = changes.get("pins")
        if old["side"] == "buy":
            pins = None
        if not changed and pins is None:
            return get_deal(deal_id)
        if old["side"] == "sell":
            _edit_sale(conn, old, new, changed, pins)
        else:
            _edit_purchase(conn, old, new, changed, rehome)
        db.log(conn, "deal", deal_id, "edit", "Edited %s: %s" % (old["ref"], ", ".join(changed) or "lots"),
               {"ref": old["ref"], "changes": {k: [old[k], new[k]] for k in changed},
                "pins": pins, "rehome": rehome})
    return get_deal(deal_id)


# ------------------------------------------------------------------ previews
def _labels(old: Dict[str, Any], new: Dict[str, Any], changed: List[str]) -> List[Dict[str, Any]]:
    def show(k, v):
        if v is None:
            return ""
        if k in ("party_id", "transporter_id"):
            return db.scalar("SELECT name FROM parties WHERE id=?", (v,), "")
        if k == "product_id":
            return db.scalar("SELECT display FROM v_products WHERE id=?", (v,), "")
        if k == "warehouse_id":
            return db.scalar("SELECT name FROM warehouses WHERE id=?", (v,), "")
        if k == "qty_g":
            return fmt_qty(v)
        if k == "rate_paise":
            return "₹%.2f/kg" % (v / 100)
        if k == "plus_gst":
            return "GST extra" if v else "GST included"
        return str(v)
    names = {"party_id": "Party", "product_id": "Product", "warehouse_id": "Warehouse", "qty_g": "Quantity",
             "rate_paise": "Rate", "plus_gst": "GST", "deal_date": "Date", "payment_due": "Payment due",
             "ex_place": "Ex-Place", "transporter_id": "Transporter", "freight_by": "Freight paid by",
             "delivery_by": "Transport arranged by", "payment_terms": "Payment terms", "eway": "E-way bill",
             "remarks": "Note"}
    return [{"field": k, "label": names[k], "before": show(k, old[k]), "after": show(k, new[k])} for k in changed]


def preview_edit(deal_id: int, changes: Dict[str, Any], rehome: bool = False) -> Dict[str, Any]:
    """What saving would do, computed by saving and rolling back."""
    row = db.q1("SELECT * FROM deals WHERE id=?", (deal_id,))
    if row is None:
        raise DealError("Deal not found")
    old = dict(row)

    def attempt(with_rehome):
        before = _book_state()
        new_deal = edit_deal(deal_id, changes, rehome=with_rehome)
        return {"deal": new_deal, "effects": _effects(before, _book_state())}

    out = {"needs_rehome": False, "rehome_grams": 0, "error": None}
    try:
        out.update(dry_run(lambda: attempt(rehome)))
    except RehomeNeeded as need:
        out.update({"needs_rehome": True, "rehome_grams": need.grams, "reason": str(need)})
        try:
            out.update(dry_run(lambda: attempt(True)))
        except DealError as exc:
            out["error"] = str(exc)
    except DealError as exc:
        out["error"] = str(exc)
    try:
        new = _clean(old, changes)
    except DealError:
        new = old
    changed = [k for k in FIELDS if k in changes and new.get(k) != old[k]]
    out["changes"] = _labels(old, new, changed)
    return out


def preview_cancel(deal_id: int) -> Dict[str, Any]:
    """Cancelling as it would happen; for a purchase whose stock is sold, what
    moving those sales onto other stock would do."""
    from . import deals as deals_svc
    row = db.q1("SELECT * FROM deals WHERE id=?", (deal_id,))
    if row is None:
        raise DealError("Deal not found")

    def attempt(rehome):
        before = _book_state()
        deals_svc.cancel_deal(deal_id, reason="preview", rehome=rehome)
        return {"effects": _effects(before, _book_state())}

    out = {"needs_rehome": False, "error": None}
    try:
        out.update(dry_run(lambda: attempt(False)))
    except RehomeNeeded as need:
        out.update({"needs_rehome": True, "rehome_grams": need.grams, "reason": str(need)})
        try:
            out.update(dry_run(lambda: attempt(True)))
        except DealError as exc:
            out["error"] = str(exc)
    except DealError as exc:
        out["error"] = str(exc)
    return out
