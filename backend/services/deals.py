"""Deal lifecycle: draft -> booked -> (cancelled).

Booking a BUY mints a lot. Booking a SELL consumes lots through allocations.
Both are single transactions with an audit event, and both are reversible
while nothing downstream depends on them.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .. import db
from ..money import fmt_money, fmt_qty, value_paise, weighted_rate
from . import allocation, catalog


class DealError(Exception):
    """User-facing, expected failure. The API turns this into a 400."""


# ------------------------------------------------------------------ refs
def _next_ref(conn, side: str) -> str:
    prefix = "B" if side == "buy" else "S"
    n = db.scalar("SELECT COUNT(*) FROM deals WHERE side=?", (side,)) + 1
    while db.q1("SELECT 1 FROM deals WHERE ref=?", ("%s-%04d" % (prefix, n),)):
        n += 1
    return "%s-%04d" % (prefix, n)


TERM_FIELDS = ("freight_by", "delivery_by", "payment_terms", "eway", "remarks")


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

    party_name = (payload.get("party_name") or "").strip()
    confirm = bool(payload.get("confirm", True))

    with db.tx() as conn:
        if payload.get("party_id"):
            party_id = int(payload["party_id"])
            catalog.upsert_party(conn, _party_name(party_id),
                                 "supplier" if side == "buy" else "customer")
        elif party_name:
            party_id = catalog.upsert_party(conn, party_name,
                                            "supplier" if side == "buy" else "customer")
        else:
            raise DealError("Who is the deal with?")

        # The party is who you trade with; the manufacturer is who made the
        # resin. Both are asked for, and they are never the same field.
        if payload.get("sku_id"):
            sku_id = int(payload["sku_id"])
        else:
            try:
                sku_id = catalog.upsert_sku(
                    conn,
                    material=payload.get("material") or "",
                    grade=payload.get("grade") or "",
                    manufacturer=payload.get("manufacturer") or "",
                    packing=payload.get("packing"),
                )
            except ValueError as exc:
                raise DealError(str(exc))

        transporter_id = None
        if payload.get("transporter"):
            transporter_id = catalog.upsert_party(conn, payload["transporter"], "transporter")

        ref = _next_ref(conn, side)
        cur = conn.execute(
            """INSERT INTO deals
               (ref,side,status,party_id,sku_id,qty_g,rate_paise,plus_gst,deal_date,
                transporter_id,freight_by,delivery_by,payment_terms,eway,remarks,
                alloc_policy,uncovered_g,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)""",
            (ref, side, "draft", party_id, sku_id, qty_g, rate_paise,
             1 if payload.get("plus_gst", True) else 0,
             payload.get("deal_date") or db.today(), transporter_id,
             payload.get("freight_by"), payload.get("delivery_by"),
             payload.get("payment_terms"), payload.get("eway"), payload.get("remarks"),
             payload.get("policy") or db.settings().get("alloc_policy", "fifo"), db.now()),
        )
        deal_id = int(cur.lastrowid)
        db.log(conn, "deal", deal_id, "create", "Drafted %s" % ref, {"side": side, "ref": ref})

        if confirm:
            _book(conn, deal_id,
                  pins=payload.get("pins"),
                  policy=payload.get("policy"),
                  allow_short=bool(payload.get("allow_short", False)))

    return get_deal(deal_id)


def _party_name(party_id: int) -> str:
    row = db.q1("SELECT name FROM parties WHERE id=?", (party_id,))
    if not row:
        raise DealError("Unknown party")
    return row["name"]


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

    if deal["side"] == "buy":
        _book_buy(conn, deal)
    else:
        _book_sell(conn, deal, pins=pins, policy=policy, allow_short=allow_short)

    conn.execute("UPDATE deals SET status='booked', booked_at=? WHERE id=?", (db.now(), deal_id))
    _remark_mark(conn, deal)


def _book_buy(conn, deal) -> None:
    label = "%s / %s" % (deal["ref"], _party_name(deal["party_id"]))
    cur = conn.execute(
        """INSERT INTO lots(label,deal_id,sku_id,supplier_id,rate_paise,qty_g,
                            qty_allocated_g,status,booked_at)
           VALUES (?,?,?,?,?,?,0,'open',?)""",
        (label, deal["id"], deal["sku_id"], deal["party_id"],
         deal["rate_paise"], deal["qty_g"], db.now()),
    )
    lot_id = int(cur.lastrowid)
    db.log(conn, "deal", deal["id"], "book",
           "Bought %s %s @ %s" % (fmt_qty(deal["qty_g"]), _sku_name(deal["sku_id"]),
                                  fmt_money(deal["rate_paise"])),
           {"lot_id": lot_id, "ref": deal["ref"]}, undoable=True)


def _book_sell(conn, deal, pins=None, policy=None, allow_short: bool = False) -> None:
    policy = policy or deal["alloc_policy"] or allocation.DEFAULT_POLICY
    plan = allocation.suggest(deal["sku_id"], deal["qty_g"], policy, pins=pins)

    if plan["uncovered_g"] > 0 and not (allow_short or db.settings().get("allow_short_sales") == "1"):
        raise DealError(
            "Short by %s - you do not hold enough %s"
            % (fmt_qty(plan["uncovered_g"]), _sku_name(deal["sku_id"]))
        )

    _write_allocations(conn, deal, plan, policy)
    margin = _margin_of(deal["id"])
    db.log(conn, "deal", deal["id"], "book",
           "Sold %s %s @ %s  (margin %s)"
           % (fmt_qty(deal["qty_g"]), _sku_name(deal["sku_id"]),
              fmt_money(deal["rate_paise"]), fmt_money(margin)),
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
    _refresh_lot_status(conn)


def _release_allocations(conn, sale_deal_id: int) -> None:
    for r in db.q("SELECT lot_id, qty_g FROM allocations WHERE sale_deal_id=? AND active=1",
                  (sale_deal_id,)):
        conn.execute("UPDATE lots SET qty_allocated_g = qty_allocated_g - ? WHERE id=?",
                     (r["qty_g"], r["lot_id"]))
    conn.execute("UPDATE allocations SET active=0 WHERE sale_deal_id=? AND active=1", (sale_deal_id,))
    _refresh_lot_status(conn)


def _refresh_lot_status(conn) -> None:
    conn.execute("UPDATE lots SET status='exhausted' "
                 "WHERE status='open' AND qty_allocated_g >= qty_g")
    conn.execute("UPDATE lots SET status='open' "
                 "WHERE status='exhausted' AND qty_allocated_g < qty_g")


# ------------------------------------------------------------------ re-plan
def reallocate(sale_deal_id: int, pins=None, policy=None) -> Dict[str, Any]:
    """Change which lots a booked sale eats, after the fact. Used by the
    drag-to-adjust strip. History is preserved: old rows go inactive."""
    with db.tx() as conn:
        deal = db.q1("SELECT * FROM deals WHERE id=?", (sale_deal_id,))
        if deal is None or deal["side"] != "sell":
            raise DealError("Not a sale")
        if deal["status"] != "booked":
            raise DealError("Only a booked sale can be re-allocated")
        policy = policy or deal["alloc_policy"]
        _release_allocations(conn, sale_deal_id)
        plan = allocation.suggest(deal["sku_id"], deal["qty_g"], policy, pins=pins)
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
                """SELECT d.ref, a.qty_g FROM allocations a
                   JOIN lots  l ON l.id = a.lot_id
                   JOIN deals d ON d.id = a.sale_deal_id
                   WHERE l.deal_id=? AND a.active=1""",
                (deal_id,),
            )
            if sold:
                refs = ", ".join(sorted({r["ref"] for r in sold}))
                raise DealError(
                    "Cannot cancel %s - its material is already sold on %s. Cancel those first."
                    % (deal["ref"], refs)
                )
            conn.execute("UPDATE lots SET status='cancelled' WHERE deal_id=?", (deal_id,))

        if deal["side"] == "sell" and deal["status"] == "booked":
            _release_allocations(conn, deal_id)
            _rollback_mark(conn, deal)

        conn.execute("UPDATE deals SET status='cancelled', cancelled_at=? WHERE id=?",
                     (db.now(), deal_id))
        db.log(conn, "deal", deal_id, "cancel", "Cancelled %s" % deal["ref"],
               {"reason": reason, "ref": deal["ref"]})
    return get_deal(deal_id)


# ------------------------------------------------------------------ reading
def _sku_name(sku_id: int) -> str:
    row = db.q1("SELECT display FROM skus WHERE id=?", (sku_id,))
    return row["display"] if row else "?"


def _margin_of(sale_deal_id: int) -> int:
    rows = db.q("SELECT qty_g, cost_paise, sale_rate_paise FROM allocations "
                "WHERE sale_deal_id=? AND active=1", (sale_deal_id,))
    return sum(value_paise(r["qty_g"], r["sale_rate_paise"] - r["cost_paise"]) for r in rows)


def _rollback_mark(conn, deal) -> None:
    """A cancelled sale must not keep pricing the book. Fall back to the most
    recent surviving sale, or drop the mark entirely."""
    mark = db.q1("SELECT * FROM marks WHERE sku_id=?", (deal["sku_id"],))
    if mark is None or mark["source"] != "sale %s" % deal["ref"]:
        return
    prev = db.q1(
        "SELECT ref, rate_paise FROM deals WHERE sku_id=? AND side='sell' "
        "AND status='booked' AND id != ? ORDER BY deal_date DESC, id DESC LIMIT 1",
        (deal["sku_id"], deal["id"]),
    )
    if prev:
        conn.execute("UPDATE marks SET rate_paise=?, source=?, updated_at=? WHERE sku_id=?",
                     (prev["rate_paise"], "sale %s" % prev["ref"], db.now(), deal["sku_id"]))
    else:
        conn.execute("DELETE FROM marks WHERE sku_id=?", (deal["sku_id"],))


def _remark_mark(conn, deal) -> None:
    """A fresh sale is the best price signal we have for unrealised P&L."""
    if deal["side"] != "sell":
        return
    conn.execute(
        "INSERT INTO marks(sku_id,rate_paise,source,updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(sku_id) DO UPDATE SET rate_paise=excluded.rate_paise, "
        "source=excluded.source, updated_at=excluded.updated_at",
        (deal["sku_id"], deal["rate_paise"], "sale %s" % deal["ref"], db.now()),
    )


DEAL_SELECT = """
    SELECT d.*, p.name AS party_name, s.display AS material,
           s.material AS material_name, s.grade, s.manufacturer,
           t.name AS transporter_name
    FROM deals d
    JOIN parties p ON p.id = d.party_id
    JOIN skus    s ON s.id = d.sku_id
    LEFT JOIN parties t ON t.id = d.transporter_id
"""


def get_deal(deal_id: int) -> Optional[Dict[str, Any]]:
    row = db.q1(DEAL_SELECT + " WHERE d.id=?", (deal_id,))
    if row is None:
        return None
    deal = dict(row)
    deal["value_paise"] = value_paise(deal["qty_g"], deal["rate_paise"])
    if deal["side"] == "sell":
        deal["allocations"] = allocations_of(deal_id)
        deal["margin_paise"] = sum(a["margin_paise"] for a in deal["allocations"])
        deal["cost_paise"] = weighted_rate([(a["qty_g"], a["cost_paise"]) for a in deal["allocations"]])
        deal["margin_rate_paise"] = (deal["rate_paise"] - deal["cost_paise"]) if deal["cost_paise"] else 0
    else:
        deal["lot"] = db.row_to_dict(db.q1("SELECT * FROM lots WHERE deal_id=?", (deal_id,)))
        deal["sold"] = sold_from_deal(deal_id)
        deal["sold_g"] = sum(s["qty_g"] for s in deal["sold"])
        deal["margin_paise"] = sum(s["margin_paise"] for s in deal["sold"])
    deal["events"] = [dict(r) for r in db.q(
        "SELECT * FROM events WHERE entity='deal' AND entity_id=? ORDER BY id", (deal_id,))]
    return deal


def allocations_of(sale_deal_id: int) -> List[Dict[str, Any]]:
    rows = db.q(
        """SELECT a.*, l.label AS lot_label, l.rate_paise AS lot_rate,
                  l.qty_g AS lot_qty_g, p.name AS supplier_name, p.id AS supplier_id,
                  bd.ref AS buy_ref, bd.deal_date AS buy_date
           FROM allocations a
           JOIN lots l    ON l.id = a.lot_id
           JOIN parties p ON p.id = l.supplier_id
           JOIN deals bd  ON bd.id = l.deal_id
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
                  cp.name AS customer_name, cp.id AS customer_id
           FROM allocations a
           JOIN lots l   ON l.id = a.lot_id
           JOIN deals sd ON sd.id = a.sale_deal_id
           JOIN parties cp ON cp.id = sd.party_id
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


DEAL_SEARCH = """
    (LOWER(d.ref) LIKE ? OR LOWER(p.name) LIKE ? OR LOWER(s.display) LIKE ?
     OR LOWER(s.material) LIKE ? OR LOWER(s.grade) LIKE ? OR LOWER(s.manufacturer) LIKE ?
     OR LOWER(COALESCE(t.name,'')) LIKE ?)
"""


def count_deals(side=None, status=None, sku_id=None, party_id=None, q=None, **extra) -> int:
    where, args = _deal_filters(side, status, sku_id, party_id, q, **extra)
    sql = ("SELECT COUNT(*) FROM deals d JOIN parties p ON p.id=d.party_id "
           "JOIN skus s ON s.id=d.sku_id LEFT JOIN parties t ON t.id=d.transporter_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    return db.scalar(sql, args)


def _deal_filters(side, status, sku_id, party_id, q, date_from=None, date_to=None,
                  material=None, grade=None, manufacturer=None):
    where, args = [], []
    if side:
        where.append("d.side=?"); args.append(side)
    if status:
        where.append("d.status=?"); args.append(status)
    if sku_id:
        where.append("d.sku_id=?"); args.append(sku_id)
    if party_id:
        where.append("d.party_id=?"); args.append(party_id)
    if date_from:
        where.append("d.deal_date >= ?"); args.append(date_from)
    if date_to:
        where.append("d.deal_date <= ?"); args.append(date_to)
    if material:
        where.append("s.material = ?"); args.append(material.strip().upper())
    if grade:
        where.append("s.grade = ?"); args.append(grade.strip().upper())
    if manufacturer:
        where.append("s.manufacturer = ?"); args.append(manufacturer.strip())
    term = (q or "").strip().lower()
    if term:
        where.append(DEAL_SEARCH)
        args += ["%%%s%%" % term] * 7
    return where, args


def list_deals(side: Optional[str] = None, status: Optional[str] = None,
               sku_id: Optional[int] = None, party_id: Optional[int] = None,
               limit: int = 60, offset: int = 0, q: Optional[str] = None,
               **extra) -> List[Dict[str, Any]]:
    where, args = _deal_filters(side, status, sku_id, party_id, q, **extra)
    sql = DEAL_SELECT + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY d.deal_date DESC, d.id DESC LIMIT ? OFFSET ?"
    out = []
    for r in db.q(sql, args + [limit, offset]):
        d = dict(r)
        d["value_paise"] = value_paise(d["qty_g"], d["rate_paise"])
        if d["side"] == "sell":
            d["margin_paise"] = _margin_of(d["id"])
            d["margin_rate_paise"] = (
                d["margin_paise"] * 1000 // d["qty_g"] if d["qty_g"] else 0)
        else:
            d["sold_g"] = db.scalar(
                "SELECT COALESCE(SUM(a.qty_g),0) FROM allocations a JOIN lots l ON l.id=a.lot_id "
                "WHERE l.deal_id=? AND a.active=1", (d["id"],))
        out.append(d)
    return out


# ------------------------------------------------------------------ undo
def undo_event(event_id: int) -> Dict[str, Any]:
    ev = db.q1("SELECT * FROM events WHERE id=?", (event_id,))
    if ev is None:
        raise DealError("Nothing to undo")
    if ev["undone"]:
        raise DealError("Already undone")
    if not ev["undoable"]:
        raise DealError("That action cannot be undone")
    if ev["entity"] != "deal" or ev["action"] != "book":
        raise DealError("That action cannot be undone")

    deal_id = int(ev["entity_id"])
    result = cancel_deal(deal_id, reason="undo")
    with db.tx() as conn:
        conn.execute("UPDATE events SET undone=1 WHERE id=?", (event_id,))
    return result


def last_undoable() -> Optional[Dict[str, Any]]:
    return db.row_to_dict(db.q1(
        "SELECT * FROM events WHERE undoable=1 AND undone=0 ORDER BY id DESC LIMIT 1"))
