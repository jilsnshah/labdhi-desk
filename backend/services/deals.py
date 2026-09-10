"""Deal lifecycle: draft -> booked -> (cancelled).

A deal references three records and copies none of them:
    party_id      who it is with            (parties)
    product_id    what is traded            (products)
    warehouse_id  BUY: where it is received (warehouses)
                  SELL: where it leaves from

Booking a BUY creates a lot in its warehouse. Booking a SELL consumes lots in
its warehouse through allocations. Both are single transactions with an audit
event, and both are reversible while nothing downstream depends on them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import db
from ..money import fmt_money, fmt_qty, value_paise, weighted_rate
from . import allocation, parties, products, stock, warehouses


class DealError(Exception):
    """User-facing, expected failure. The API turns this into a 400."""


# ------------------------------------------------------------------ refs
def financial_year(deal_date: Optional[str] = None) -> str:
    """Indian financial year, April to March: 10 Sep 2026 -> "26-27"."""
    d = deal_date or db.today()
    year, month = int(d[:4]), int(d[5:7])
    start = year if month >= 4 else year - 1
    return "%02d-%02d" % (start % 100, (start + 1) % 100)


def next_sauda_no(deal_date: Optional[str] = None) -> str:
    """LE/26-27/0001. One series for buys and sells, restarting each April.

    The next number is one past the highest already used in that year rather
    than a count of deals, so a cancelled deal or a hand-typed number can never
    cause the next auto-number to collide with an existing one.
    """
    prefix = (db.settings().get("sauda_prefix") or "LE").strip()
    stem = "%s/%s/" % (prefix, financial_year(deal_date))
    highest = 0
    for r in db.q("SELECT ref FROM deals WHERE ref LIKE ?", (stem + "%",)):
        tail = r["ref"][len(stem):]
        if tail.isdigit():
            highest = max(highest, int(tail))
    n = highest + 1
    while db.q1("SELECT 1 FROM deals WHERE ref=?", (stem + "%04d" % n,)):
        n += 1
    return stem + "%04d" % n


def _require(fn, *args):
    try:
        return fn(*args)
    except ValueError as exc:
        raise DealError(str(exc))


# ------------------------------------------------------------------ create
def create_deal(payload: Dict[str, Any]) -> Dict[str, Any]:
    side = payload.get("side")
    if side not in ("buy", "sell"):
        raise DealError("side must be buy or sell")

    qty_g = int(payload.get("qty_g") or 0)
    rate_paise = int(payload.get("rate_paise") or 0)
    if qty_g <= 0:
        raise DealError("Quantity must be greater than zero")
    if rate_paise <= 0:
        raise DealError("Rate must be greater than zero")

    party = _require(parties.require, payload.get("party_id"),
                     "supplier" if side == "buy" else "buyer")
    product = _require(products.require, payload.get("product_id"))
    wh = _require(warehouses.require, payload.get("warehouse_id"))
    transporter_id = None
    if payload.get("transporter_id"):
        transporter_id = _require(parties.require, payload["transporter_id"], "transporter")["id"]

    deal_date = payload.get("deal_date") or db.today()
    confirm = bool(payload.get("confirm", True))

    with db.tx() as conn:
        # A Sauda No. typed by the trader is kept as typed; it only has to be
        # unique. Otherwise the next number in this financial year is issued.
        ref = (payload.get("sauda_no") or "").strip()
        if ref:
            if db.q1("SELECT 1 FROM deals WHERE ref=?", (ref,)):
                raise DealError("Sauda No. %s is already used" % ref)
        else:
            ref = next_sauda_no(deal_date)

        deal_id = conn.insert(
            """INSERT INTO deals
               (ref,side,status,party_id,product_id,warehouse_id,qty_g,rate_paise,plus_gst,
                deal_date,payment_due,ex_place,transporter_id,freight_by,delivery_by,
                payment_terms,eway,remarks,alloc_policy,uncovered_g,created_at)
               VALUES (?,?,'draft',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)""",
            (ref, side, party["id"], product["id"], wh["id"], qty_g, rate_paise,
             1 if payload.get("plus_gst", True) else 0, deal_date,
             (payload.get("payment_due") or "").strip() or None,
             (payload.get("ex_place") or "").strip() or None,
             transporter_id, payload.get("freight_by"), payload.get("delivery_by"),
             payload.get("payment_terms"), payload.get("eway"), payload.get("remarks"),
             payload.get("policy") or db.settings().get("alloc_policy", "fifo"), db.now()),
        )
        db.log(conn, "deal", deal_id, "create", "Drafted %s" % ref, {"side": side, "ref": ref})

        if confirm:
            _book(conn, deal_id, pins=payload.get("pins"), policy=payload.get("policy"),
                  allow_short=bool(payload.get("allow_short", False)))

    return get_deal(deal_id)


# ------------------------------------------------------------------ booking
def book_deal(deal_id: int, pins=None, policy=None, allow_short: bool = False) -> Dict[str, Any]:
    with db.tx() as conn:
        _book(conn, deal_id, pins=pins, policy=policy, allow_short=allow_short)
    return get_deal(deal_id)


def _book(conn, deal_id: int, pins=None, policy=None, allow_short: bool = False) -> None:
    deal = db.q1("SELECT * FROM deals WHERE id=?", (deal_id,))
    if deal is None:
        raise DealError("Deal not found")
    if deal["status"] == "booked":
        raise DealError("%s is already booked" % deal["ref"])
    if deal["status"] == "cancelled":
        raise DealError("%s was cancelled" % deal["ref"])
    if not deal["warehouse_id"]:
        raise DealError("Choose the warehouse for %s first" % deal["ref"])

    if deal["side"] == "buy":
        _book_buy(conn, deal)
    else:
        _book_sell(conn, deal, pins=pins, policy=policy, allow_short=allow_short)

    conn.execute("UPDATE deals SET status='booked', booked_at=? WHERE id=?", (db.now(), deal_id))
    _remark_mark(conn, deal)


def _names(deal) -> Dict[str, str]:
    row = db.q1("""SELECT p.name AS party, s.display AS product, w.name AS warehouse
                   FROM deals d JOIN parties p ON p.id = d.party_id
                   JOIN v_products s ON s.id = d.product_id
                   LEFT JOIN warehouses w ON w.id = d.warehouse_id WHERE d.id = ?""", (deal["id"],))
    return dict(row)


def _book_buy(conn, deal) -> None:
    n = _names(deal)
    lot_id = conn.insert(
        """INSERT INTO lots(label,deal_id,product_id,warehouse_id,supplier_id,rate_paise,qty_g,
                            qty_allocated_g,qty_out_g,status,booked_at)
           VALUES (?,?,?,?,?,?,?,0,0,'open',?)""",
        ("%s / %s" % (deal["ref"], n["party"]), deal["id"], deal["product_id"], deal["warehouse_id"],
         deal["party_id"], deal["rate_paise"], deal["qty_g"], db.now()),
    )
    db.log(conn, "deal", deal["id"], "book",
           "Bought %s %s @ %s into %s" % (fmt_qty(deal["qty_g"]), n["product"],
                                          fmt_money(deal["rate_paise"]), n["warehouse"]),
           {"lot_id": lot_id, "ref": deal["ref"]}, undoable=True)


def _book_sell(conn, deal, pins=None, policy=None, allow_short: bool = False) -> None:
    n = _names(deal)
    policy = policy or deal["alloc_policy"] or allocation.DEFAULT_POLICY
    plan = allocation.suggest(deal["product_id"], deal["qty_g"], policy, pins=pins,
                              warehouse_id=deal["warehouse_id"])
    if plan["rejected"]:
        raise DealError("Some of the chosen stock is not %s in %s - pick again"
                        % (n["product"], n["warehouse"]))
    if plan["uncovered_g"] > 0 and not (allow_short or db.settings().get("allow_short_sales") == "1"):
        held = stock.available_in(deal["product_id"], deal["warehouse_id"])
        raise DealError("Short by %s - %s holds %s of %s"
                        % (fmt_qty(plan["uncovered_g"]), n["warehouse"], fmt_qty(held), n["product"]))

    _write_allocations(conn, deal, plan, policy)
    db.log(conn, "deal", deal["id"], "book",
           "Sold %s %s @ %s from %s  (margin %s)"
           % (fmt_qty(deal["qty_g"]), n["product"], fmt_money(deal["rate_paise"]),
              n["warehouse"], fmt_money(_margin_of(deal["id"]))),
           {"ref": deal["ref"], "picks": plan["picks"], "uncovered_g": plan["uncovered_g"]},
           undoable=True)


def _write_allocations(conn, deal, plan, policy) -> None:
    for p in plan["picks"]:
        conn.execute(
            """INSERT INTO allocations(sale_deal_id,lot_id,qty_g,cost_paise,
                                       sale_rate_paise,method,created_at,active)
               VALUES (?,?,?,?,?,?,?,1)""",
            (deal["id"], p["lot_id"], p["qty_g"], p["cost_paise"],
             deal["rate_paise"], p.get("method") or policy, db.now()),
        )
        conn.execute("UPDATE lots SET qty_allocated_g = qty_allocated_g + ? WHERE id=?",
                     (p["qty_g"], p["lot_id"]))
    conn.execute("UPDATE deals SET uncovered_g=?, alloc_policy=? WHERE id=?",
                 (plan["uncovered_g"], policy, deal["id"]))
    stock.refresh_lot_status(conn)


def _release_allocations(conn, sale_deal_id: int) -> None:
    for r in db.q("SELECT lot_id, qty_g FROM allocations WHERE sale_deal_id=? AND active=1",
                  (sale_deal_id,)):
        conn.execute("UPDATE lots SET qty_allocated_g = qty_allocated_g - ? WHERE id=?",
                     (r["qty_g"], r["lot_id"]))
    conn.execute("UPDATE allocations SET active=0 WHERE sale_deal_id=? AND active=1", (sale_deal_id,))
    stock.refresh_lot_status(conn)


# ------------------------------------------------------------------ re-plan
def reallocate(sale_deal_id: int, pins=None, policy=None) -> Dict[str, Any]:
    """Change which lots a booked sale eats, after the fact. History is kept:
    old rows go inactive. The warehouse it ships from does not change."""
    with db.tx() as conn:
        deal = db.q1("SELECT * FROM deals WHERE id=?", (sale_deal_id,))
        if deal is None or deal["side"] != "sell":
            raise DealError("Not a sale")
        if deal["status"] != "booked":
            raise DealError("Only a booked sale can be re-allocated")
        policy = policy or deal["alloc_policy"]
        _release_allocations(conn, sale_deal_id)
        plan = allocation.suggest(deal["product_id"], deal["qty_g"], policy, pins=pins,
                                  warehouse_id=deal["warehouse_id"])
        if plan["rejected"]:
            raise DealError("Some of the chosen stock is not in this sale's warehouse")
        _write_allocations(conn, deal, plan, policy)
        db.log(conn, "deal", sale_deal_id, "reallocate",
               "Re-allocated %s" % deal["ref"], {"picks": plan["picks"]}, undoable=False)
    return get_deal(sale_deal_id)


# ------------------------------------------------------------------ cancel
def cancel_deal(deal_id: int, reason: str = "") -> Dict[str, Any]:
    with db.tx() as conn:
        deal = db.q1("SELECT * FROM deals WHERE id=?", (deal_id,))
        if deal is None:
            raise DealError("Deal not found")
        if deal["status"] == "cancelled":
            raise DealError("Already cancelled")

        if deal["side"] == "buy" and deal["status"] == "booked":
            sold = db.q(
                """SELECT DISTINCT d.ref FROM allocations a
                   JOIN lots  l ON l.id = a.lot_id
                   JOIN deals d ON d.id = a.sale_deal_id
                   WHERE l.deal_id=? AND a.active=1""", (deal_id,))
            if sold:
                raise DealError(
                    "Cannot cancel %s - its material is already sold on %s. Cancel those first."
                    % (deal["ref"], ", ".join(sorted(r["ref"] for r in sold))))
            moved = db.scalar(
                """SELECT COUNT(*) FROM stock_moves m JOIN lots l ON l.id = m.lot_id
                   WHERE l.deal_id=? AND m.status='done'""", (deal_id,))
            if moved:
                raise DealError("Cannot cancel %s - some of it was transferred or adjusted. "
                                "Undo those movements first." % deal["ref"])
            conn.execute("UPDATE lots SET status='cancelled' WHERE deal_id=?", (deal_id,))

        if deal["side"] == "sell" and deal["status"] == "booked":
            _release_allocations(conn, deal_id)
            _rollback_mark(conn, deal)

        conn.execute("UPDATE deals SET status='cancelled', cancelled_at=? WHERE id=?",
                     (db.now(), deal_id))
        db.log(conn, "deal", deal_id, "cancel", "Cancelled %s" % deal["ref"],
               {"reason": reason, "ref": deal["ref"]})
    return get_deal(deal_id)


# ------------------------------------------------------------------ marks
def _margin_of(sale_deal_id: int) -> int:
    rows = db.q("SELECT qty_g, cost_paise, sale_rate_paise FROM allocations "
                "WHERE sale_deal_id=? AND active=1", (sale_deal_id,))
    return sum(value_paise(r["qty_g"], r["sale_rate_paise"] - r["cost_paise"]) for r in rows)


def _rollback_mark(conn, deal) -> None:
    """A cancelled sale must not keep pricing the book. Fall back to the most
    recent surviving sale, or drop the mark entirely."""
    mark = db.q1("SELECT * FROM marks WHERE product_id=?", (deal["product_id"],))
    if mark is None or mark["source"] != "sale %s" % deal["ref"]:
        return
    prev = db.q1(
        "SELECT ref, rate_paise FROM deals WHERE product_id=? AND side='sell' "
        "AND status='booked' AND id != ? ORDER BY deal_date DESC, id DESC LIMIT 1",
        (deal["product_id"], deal["id"]),
    )
    if prev:
        conn.execute("UPDATE marks SET rate_paise=?, source=?, updated_at=? WHERE product_id=?",
                     (prev["rate_paise"], "sale %s" % prev["ref"], db.now(), deal["product_id"]))
    else:
        conn.execute("DELETE FROM marks WHERE product_id=?", (deal["product_id"],))


def _remark_mark(conn, deal) -> None:
    """A fresh sale is the best price signal we have for unrealised P&L."""
    if deal["side"] != "sell":
        return
    conn.execute(
        "INSERT INTO marks(product_id,rate_paise,source,updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(product_id) DO UPDATE SET rate_paise=excluded.rate_paise, "
        "source=excluded.source, updated_at=excluded.updated_at",
        (deal["product_id"], deal["rate_paise"], "sale %s" % deal["ref"], db.now()),
    )


# ------------------------------------------------------------------ reading
# Names are joined in at read time from the one record that owns them.
DEAL_SELECT = """
    SELECT d.*, p.name AS party_name, p.gstin AS party_gstin,
           s.display AS product, s.material, s.grade, s.manufacturer,
           s.material_id, s.grade_id, s.manufacturer_id,
           w.name AS warehouse, t.name AS transporter_name,
           (SELECT COALESCE(SUM(a.qty_g * (a.sale_rate_paise - a.cost_paise)), 0)
              FROM allocations a WHERE a.sale_deal_id = d.id AND a.active = 1) AS margin_gp,
           (SELECT COALESCE(SUM(a.qty_g), 0) FROM allocations a JOIN lots l ON l.id = a.lot_id
              WHERE l.deal_id = d.id AND a.active = 1 AND d.side = 'buy') AS sold_g
    FROM deals d
    JOIN parties p     ON p.id = d.party_id
    JOIN v_products s  ON s.id = d.product_id
    LEFT JOIN warehouses w ON w.id = d.warehouse_id
    LEFT JOIN parties t    ON t.id = d.transporter_id
"""


def _dress(row) -> Dict[str, Any]:
    d = dict(row)
    gp = d.pop("margin_gp") or 0
    q, r = divmod(abs(gp), 1000)
    d["margin_paise"] = (q + (1 if r * 2 >= 1000 else 0)) * (-1 if gp < 0 else 1)
    d["value_paise"] = value_paise(d["qty_g"], d["rate_paise"])
    d["margin_rate_paise"] = (d["margin_paise"] * 1000 // d["qty_g"]) if d["side"] == "sell" and d["qty_g"] else 0
    return d


def get_deal(deal_id: int) -> Optional[Dict[str, Any]]:
    row = db.q1(DEAL_SELECT + " WHERE d.id=?", (deal_id,))
    if row is None:
        return None
    deal = _dress(row)
    if deal["side"] == "sell":
        deal["allocations"] = allocations_of(deal_id)
        deal["margin_paise"] = sum(a["margin_paise"] for a in deal["allocations"])
        deal["cost_paise"] = weighted_rate([(a["qty_g"], a["cost_paise"]) for a in deal["allocations"]])
        deal["margin_rate_paise"] = (deal["rate_paise"] - deal["cost_paise"]) if deal["cost_paise"] else 0
    else:
        lots = db.dicts(db.q(stock.LOT_SELECT + " WHERE l.deal_id=? ORDER BY l.id", (deal_id,)))
        deal["lot"] = next((l for l in lots if not l["parent_lot_id"]), None)
        deal["lots"] = lots
        deal["sold"] = sold_from_deal(deal_id)
        deal["sold_g"] = sum(s["qty_g"] for s in deal["sold"])
        deal["margin_paise"] = sum(s["margin_paise"] for s in deal["sold"])
    deal["events"] = db.dicts(db.q(
        "SELECT * FROM events WHERE entity='deal' AND entity_id=? ORDER BY id", (deal_id,)))
    return deal


def allocations_of(sale_deal_id: int) -> List[Dict[str, Any]]:
    rows = db.q(
        """SELECT a.*, l.label AS lot_label, l.rate_paise AS lot_rate,
                  l.qty_g AS lot_qty_g, p.name AS supplier_name, p.id AS supplier_id,
                  bd.ref AS buy_ref, bd.deal_date AS buy_date, w.name AS warehouse
           FROM allocations a
           JOIN lots l       ON l.id = a.lot_id
           JOIN parties p    ON p.id = l.supplier_id
           JOIN deals bd     ON bd.id = l.deal_id
           JOIN warehouses w ON w.id = l.warehouse_id
           WHERE a.sale_deal_id=? AND a.active=1
           ORDER BY a.id""",
        (sale_deal_id,),
    )
    out = []
    for r in rows:
        a = dict(r)
        a["margin_rate_paise"] = a["sale_rate_paise"] - a["cost_paise"]
        a["margin_paise"] = value_paise(a["qty_g"], a["margin_rate_paise"])
        out.append(a)
    return out


def sold_from_deal(buy_deal_id: int) -> List[Dict[str, Any]]:
    """Where did this purchase end up? The other half of the lineage."""
    rows = db.q(
        """SELECT a.*, sd.ref AS sale_ref, sd.deal_date AS sale_date,
                  cp.name AS customer_name, cp.id AS customer_id, w.name AS warehouse
           FROM allocations a
           JOIN lots l       ON l.id = a.lot_id
           JOIN deals sd     ON sd.id = a.sale_deal_id
           JOIN parties cp   ON cp.id = sd.party_id
           JOIN warehouses w ON w.id = l.warehouse_id
           WHERE l.deal_id=? AND a.active=1
           ORDER BY a.id""",
        (buy_deal_id,),
    )
    out = []
    for r in rows:
        a = dict(r)
        a["margin_rate_paise"] = a["sale_rate_paise"] - a["cost_paise"]
        a["margin_paise"] = value_paise(a["qty_g"], a["margin_rate_paise"])
        out.append(a)
    return out


# ------------------------------------------------------------------ the list
DEAL_SEARCH = """
    (LOWER(d.ref) LIKE ? OR LOWER(p.name) LIKE ? OR LOWER(s.display) LIKE ?
     OR LOWER(COALESCE(w.name,'')) LIKE ? OR LOWER(COALESCE(t.name,'')) LIKE ?
     OR LOWER(COALESCE(p.gstin,'')) LIKE ?)
"""


def _deal_filters(side=None, status=None, product_id=None, party_id=None, warehouse_id=None,
                  q=None, date_from=None, date_to=None, material_id=None, grade_id=None,
                  manufacturer_id=None):
    where, args = [], []
    for col, val in (("d.side", side), ("d.status", status), ("d.product_id", product_id),
                     ("d.party_id", party_id), ("d.warehouse_id", warehouse_id),
                     ("s.material_id", material_id), ("s.grade_id", grade_id),
                     ("s.manufacturer_id", manufacturer_id)):
        if val:
            where.append("%s = ?" % col); args.append(val)
    if date_from:
        where.append("d.deal_date >= ?"); args.append(date_from)
    if date_to:
        where.append("d.deal_date <= ?"); args.append(date_to)
    like = db.like(q)
    if like:
        where.append(DEAL_SEARCH); args += [like] * 6
    return where, args


def list_deals(limit: Optional[int] = None, offset: int = 0, **filters) -> Dict[str, Any]:
    """The book as one chronological stream, one filtered page at a time."""
    limit, offset = db.page_args(limit, offset, default=30)
    where, args = _deal_filters(**filters)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = db.scalar(
        "SELECT COUNT(*) FROM deals d JOIN parties p ON p.id=d.party_id "
        "JOIN v_products s ON s.id=d.product_id LEFT JOIN warehouses w ON w.id=d.warehouse_id "
        "LEFT JOIN parties t ON t.id=d.transporter_id" + clause, args)
    rows = db.q(DEAL_SELECT + clause + " ORDER BY d.deal_date DESC, d.id DESC LIMIT ? OFFSET ?",
                args + [limit, offset])
    return db.page([_dress(r) for r in rows], total, limit, offset)


# ------------------------------------------------------------------ undo
def undo_event(event_id: int) -> Dict[str, Any]:
    ev = db.q1("SELECT * FROM events WHERE id=?", (event_id,))
    if ev is None:
        raise DealError("Nothing to undo")
    if ev["undone"]:
        raise DealError("Already undone")
    if not ev["undoable"]:
        raise DealError("That action cannot be undone")

    if ev["entity"] == "deal" and ev["action"] == "book":
        result = {"deal": cancel_deal(int(ev["entity_id"]), reason="undo")}
    elif ev["entity"] == "move":
        try:
            result = {"move": stock.cancel_move(int(ev["entity_id"]))}
        except ValueError as exc:
            raise DealError(str(exc))
    else:
        raise DealError("That action cannot be undone")
    with db.tx() as conn:
        conn.execute("UPDATE events SET undone=1 WHERE id=?", (event_id,))
    return result


def last_undoable() -> Optional[Dict[str, Any]]:
    return db.row_to_dict(db.q1(
        "SELECT * FROM events WHERE undoable=1 AND undone=0 ORDER BY id DESC LIMIT 1"))
