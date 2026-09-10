"""Stock: what sits where, and every movement that put it there.

Stock is never stored as a running number that could drift. It is the sum of
open lots, and every lot belongs to one product and one warehouse:

    stock(product, warehouse) = SUM(qty_g - qty_allocated_g - qty_out_g)
                                over that product's open lots in that warehouse

What changes it:
    purchase   a booked BUY creates a lot in the receiving warehouse
    sale       a booked SELL allocates grams out of lots in its warehouse
    transfer   grams leave a lot and arrive in a new lot in another warehouse
    adjustment grams written off a lot, or found against it (a new lot)

The movement ledger below is those four read back as one chronological list,
so a warehouse's stock can be audited line by line down to today's figure.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import db
from ..money import fmt_qty

AVAILABLE = "(l.qty_g - l.qty_allocated_g - l.qty_out_g)"


def refresh_lot_status(conn) -> None:
    conn.execute("UPDATE lots SET status='exhausted' "
                 "WHERE status='open' AND qty_allocated_g + qty_out_g >= qty_g")
    conn.execute("UPDATE lots SET status='open' "
                 "WHERE status='exhausted' AND qty_allocated_g + qty_out_g < qty_g")


def _paise(gram_paise) -> int:
    gp = gram_paise or 0
    q, r = divmod(abs(gp), 1000)
    if r * 2 >= 1000:
        q += 1
    return -q if gp < 0 else q


# ------------------------------------------------------------------ lots
LOT_SELECT = """
    SELECT l.*, p.name AS supplier_name, d.ref AS deal_ref, d.deal_date,
           w.name AS warehouse, s.display AS product, %s AS available_g
    FROM lots l
    JOIN parties p     ON p.id = l.supplier_id
    JOIN deals d       ON d.id = l.deal_id
    JOIN warehouses w  ON w.id = l.warehouse_id
    JOIN v_products s  ON s.id = l.product_id
""" % AVAILABLE


def lots_for(product_id: int, warehouse_id: Optional[int] = None,
             include_empty: bool = False) -> List[Dict[str, Any]]:
    """The open lots of one product, optionally in one warehouse, oldest first.

    Not paged: this is exactly the set a sale can be split across, and the
    split has to see all of it to add up.
    """
    where, args = ["l.product_id = ?", "l.status = 'open'"], [int(product_id)]
    if warehouse_id:
        where.append("l.warehouse_id = ?"); args.append(int(warehouse_id))
    rows = db.q(LOT_SELECT + " WHERE " + " AND ".join(where) + " ORDER BY d.deal_date, l.id", args)
    lots = db.dicts(rows)
    return lots if include_empty else [l for l in lots if l["available_g"] > 0]


def get_lot(lot_id: int) -> Optional[Dict[str, Any]]:
    return db.row_to_dict(db.q1(LOT_SELECT + " WHERE l.id = ?", (int(lot_id),)))


def available_in(product_id: int, warehouse_id: int) -> int:
    return db.scalar("SELECT COALESCE(SUM(%s),0) FROM lots l WHERE l.product_id=? AND "
                     "l.warehouse_id=? AND l.status='open'" % AVAILABLE,
                     (int(product_id), int(warehouse_id)))


# ------------------------------------------------------------------ stock by warehouse
_STOCK_SEARCH = ("(LOWER(s.display) LIKE ? OR LOWER(s.material) LIKE ? OR LOWER(s.grade) LIKE ? "
                 "OR LOWER(s.manufacturer) LIKE ? OR LOWER(w.name) LIKE ?)")


def stock_rows(q: str = "", product_id: Optional[int] = None, warehouse_id: Optional[int] = None,
               material_id: Optional[int] = None, grade_id: Optional[int] = None,
               manufacturer_id: Optional[int] = None, limit: Optional[int] = None,
               offset: int = 0) -> Dict[str, Any]:
    """One row per product per warehouse that holds any of it."""
    limit, offset = db.page_args(limit, offset)
    where, args = ["l.status = 'open'", AVAILABLE + " > 0"], []
    like = db.like(q)
    if like:
        where.append(_STOCK_SEARCH); args += [like] * 5
    for col, val in (("l.product_id", product_id), ("l.warehouse_id", warehouse_id),
                     ("s.material_id", material_id), ("s.grade_id", grade_id),
                     ("s.manufacturer_id", manufacturer_id)):
        if val:
            where.append("%s = ?" % col); args.append(int(val))
    base = """FROM lots l JOIN v_products s ON s.id = l.product_id
              JOIN warehouses w ON w.id = l.warehouse_id
              WHERE """ + " AND ".join(where)
    total = db.scalar("SELECT COUNT(*) FROM (SELECT l.product_id, l.warehouse_id %s "
                      "GROUP BY l.product_id, l.warehouse_id) x" % base, args)
    rows = db.q(
        """SELECT * FROM (
             SELECT l.product_id, l.warehouse_id, s.display AS product, s.material, s.grade,
                    s.manufacturer, w.name AS warehouse,
                    SUM({a}) AS stock_g, COUNT(*) AS lots, SUM({a} * l.rate_paise) AS cost_gp,
                    MIN(l.rate_paise) AS cost_low_paise, MAX(l.rate_paise) AS cost_high_paise
             {base}
             GROUP BY l.product_id, l.warehouse_id, s.display, s.material, s.grade,
                      s.manufacturer, w.name
           ) x ORDER BY stock_g DESC, product, warehouse LIMIT ? OFFSET ?""".format(a=AVAILABLE, base=base),
        args + [limit, offset])
    marks = {}
    items = []
    for r in rows:
        row = dict(r)
        gp = row.pop("cost_gp")
        row["cost_paise"] = round(gp / row["stock_g"]) if row["stock_g"] else 0
        row["stock_value_paise"] = _paise(gp)
        if row["product_id"] not in marks:
            m = db.q1("SELECT rate_paise FROM marks WHERE product_id=?", (row["product_id"],))
            marks[row["product_id"]] = m["rate_paise"] if m else None
        row["mark_paise"] = marks[row["product_id"]]
        items.append(row)
    return db.page(items, total, limit, offset)


# ------------------------------------------------------------------ the ledger
# Every NULL is typed: Postgres types a bare NULL in a UNION as text, and the
# first branch's text then refuses to meet a later branch's bigint.
_NO_ID = "CAST(NULL AS BIGINT)"
_NO_TEXT = "CAST(NULL AS TEXT)"
_LEDGER = """
    SELECT 'receipt' AS kind, d.deal_date AS date, l.booked_at AS ts, d.id AS deal_id,
           {i} AS move_id, d.ref AS ref, p.name AS counterparty, l.product_id AS product_id,
           l.warehouse_id AS warehouse_id, l.id AS lot_id, l.qty_g AS qty_g,
           l.rate_paise AS rate_paise, {t} AS note
    FROM lots l JOIN deals d ON d.id = l.deal_id JOIN parties p ON p.id = d.party_id
    WHERE l.parent_lot_id IS NULL AND l.status != 'cancelled'
  UNION ALL
    SELECT 'sale', sd.deal_date, a.created_at, sd.id, {i}, sd.ref, cp.name, l.product_id,
           l.warehouse_id, l.id, -a.qty_g, a.sale_rate_paise, {t}
    FROM allocations a JOIN lots l ON l.id = a.lot_id JOIN deals sd ON sd.id = a.sale_deal_id
    JOIN parties cp ON cp.id = sd.party_id
    WHERE a.active = 1 AND sd.status = 'booked'
  UNION ALL
    SELECT 'transfer_out', m.move_date, m.created_at, {i}, m.id, {t}, tw.name, l.product_id,
           l.warehouse_id, l.id, -m.qty_g, l.rate_paise, m.reason
    FROM stock_moves m JOIN lots l ON l.id = m.lot_id JOIN lots t ON t.id = m.to_lot_id
    JOIN warehouses tw ON tw.id = t.warehouse_id
    WHERE m.kind = 'transfer' AND m.status = 'done'
  UNION ALL
    SELECT 'transfer_in', m.move_date, m.created_at, {i}, m.id, {t}, fw.name, t.product_id,
           t.warehouse_id, t.id, m.qty_g, t.rate_paise, m.reason
    FROM stock_moves m JOIN lots l ON l.id = m.lot_id JOIN lots t ON t.id = m.to_lot_id
    JOIN warehouses fw ON fw.id = l.warehouse_id
    WHERE m.kind = 'transfer' AND m.status = 'done'
  UNION ALL
    SELECT 'loss', m.move_date, m.created_at, {i}, m.id, {t}, {t}, l.product_id,
           l.warehouse_id, l.id, m.qty_g, l.rate_paise, m.reason
    FROM stock_moves m JOIN lots l ON l.id = m.lot_id
    WHERE m.kind = 'adjust' AND m.qty_g < 0 AND m.status = 'done'
  UNION ALL
    SELECT 'gain', m.move_date, m.created_at, {i}, m.id, {t}, {t}, t.product_id,
           t.warehouse_id, t.id, m.qty_g, t.rate_paise, m.reason
    FROM stock_moves m JOIN lots t ON t.id = m.to_lot_id
    WHERE m.kind = 'adjust' AND m.qty_g > 0 AND m.status = 'done'
""".format(i=_NO_ID, t=_NO_TEXT)

_ORDER = " ORDER BY date DESC, ts DESC, kind, lot_id DESC"


def movements(product_id: Optional[int] = None, warehouse_id: Optional[int] = None,
              lot_id: Optional[int] = None, limit: Optional[int] = None,
              offset: int = 0) -> Dict[str, Any]:
    """Every movement in and out, newest first, each with the balance after it."""
    limit, offset = db.page_args(limit, offset)
    where, args = [], []
    for col, val in (("product_id", product_id), ("warehouse_id", warehouse_id), ("lot_id", lot_id)):
        if val:
            where.append("mv.%s = ?" % col); args.append(int(val))
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    inner = "SELECT * FROM (%s) mv %s" % (_LEDGER, clause)
    total = db.scalar("SELECT COUNT(*) FROM (%s) x" % inner, args)
    rows = db.dicts(db.q(inner + _ORDER + " LIMIT ? OFFSET ?", args + [limit, offset]))

    # The balance is known today; walk back from it through the newer rows.
    lwhere = ["l.status != 'cancelled'"]
    largs: List[Any] = []
    for col, val in (("l.product_id", product_id), ("l.warehouse_id", warehouse_id), ("l.id", lot_id)):
        if val:
            lwhere.append("%s = ?" % col); largs.append(int(val))
    balance = db.scalar("SELECT COALESCE(SUM(%s),0) FROM lots l WHERE %s"
                        % (AVAILABLE, " AND ".join(lwhere)), largs)
    if offset:
        balance -= db.scalar("SELECT COALESCE(SUM(qty_g),0) FROM (%s%s LIMIT ?) x"
                             % (inner, _ORDER), args + [offset])

    names = {}
    for r in rows:
        r["balance_g"] = balance
        balance -= r["qty_g"]
        if r["move_id"]:
            r["ref"] = ("TR-%d" if r["kind"].startswith("transfer") else "ADJ-%d") % r["move_id"]
        for key, table in (("product_id", "v_products"), ("warehouse_id", "warehouses")):
            k = (table, r[key])
            if k not in names:
                row = db.q1("SELECT %s AS n FROM %s WHERE id=?"
                            % ("display" if table == "v_products" else "name", table), (r[key],))
                names[k] = row["n"] if row else ""
        r["product"] = names[("v_products", r["product_id"])]
        r["warehouse"] = names[("warehouses", r["warehouse_id"])]
    return db.page(rows, total, limit, offset)


# ------------------------------------------------------------------ transfers & adjustments
def _open_lot(lot_id) -> Dict[str, Any]:
    lot = get_lot(int(lot_id or 0))
    if lot is None:
        raise ValueError("No such lot")
    if lot["status"] != "open" or lot["available_g"] <= 0:
        raise ValueError("Nothing left in that lot to move")
    return lot


def _child(conn, lot, warehouse_id: int, qty_g: int) -> int:
    return conn.insert(
        """INSERT INTO lots(label,deal_id,product_id,warehouse_id,supplier_id,parent_lot_id,
                            rate_paise,qty_g,qty_allocated_g,qty_out_g,status,booked_at)
           VALUES (?,?,?,?,?,?,?,?,0,0,'open',?)""",
        (lot["label"], lot["deal_id"], lot["product_id"], int(warehouse_id), lot["supplier_id"],
         lot["id"], lot["rate_paise"], int(qty_g), db.now()))


def transfer(lot_id: int, to_warehouse_id: int, qty_g: int, move_date: Optional[str] = None,
             reason: Optional[str] = None) -> Dict[str, Any]:
    """Move grams of one lot to another warehouse.

    The grams arrive as a new lot that remembers its parent, so the cost, the
    supplier and the purchase they trace back to travel with them.
    """
    from . import warehouses
    qty_g = int(qty_g or 0)
    with db.tx() as conn:
        lot = _open_lot(lot_id)
        dest = warehouses.require(to_warehouse_id)
        if int(dest["id"]) == int(lot["warehouse_id"]):
            raise ValueError("That lot is already in %s" % dest["name"])
        if qty_g <= 0:
            raise ValueError("How much is moving?")
        if qty_g > lot["available_g"]:
            raise ValueError("Only %s of that lot is left in %s"
                             % (fmt_qty(lot["available_g"]), lot["warehouse"]))
        child = _child(conn, lot, dest["id"], qty_g)
        conn.execute("UPDATE lots SET qty_out_g = qty_out_g + ? WHERE id=?", (qty_g, lot["id"]))
        refresh_lot_status(conn)
        move_id = conn.insert(
            "INSERT INTO stock_moves(kind,lot_id,to_lot_id,qty_g,reason,move_date,status,created_at) "
            "VALUES ('transfer',?,?,?,?,?,'done',?)",
            (lot["id"], child, qty_g, db.clean(reason) or None, move_date or db.today(), db.now()))
        db.log(conn, "move", move_id, "transfer",
               "Moved %s %s from %s to %s" % (fmt_qty(qty_g), lot["product"], lot["warehouse"], dest["name"]),
               {"lot_id": lot["id"], "to_lot_id": child}, undoable=True)
    return get_move(move_id)


def adjust(lot_id: int, qty_g: int, move_date: Optional[str] = None,
           reason: Optional[str] = None) -> Dict[str, Any]:
    """Correct a lot to what is physically there. Negative: written off.
    Positive: found - it arrives as a new lot at the same cost."""
    qty_g = int(qty_g or 0)
    if not qty_g:
        raise ValueError("Adjust by how much?")
    with db.tx() as conn:
        lot = get_lot(int(lot_id or 0))
        if lot is None or lot["status"] == "cancelled":
            raise ValueError("No such lot")
        to_lot = None
        if qty_g < 0:
            if -qty_g > lot["available_g"]:
                raise ValueError("Only %s of that lot is left to write off" % fmt_qty(lot["available_g"]))
            conn.execute("UPDATE lots SET qty_out_g = qty_out_g + ? WHERE id=?", (-qty_g, lot["id"]))
        else:
            to_lot = _child(conn, lot, lot["warehouse_id"], qty_g)
        refresh_lot_status(conn)
        move_id = conn.insert(
            "INSERT INTO stock_moves(kind,lot_id,to_lot_id,qty_g,reason,move_date,status,created_at) "
            "VALUES ('adjust',?,?,?,?,?,'done',?)",
            (lot["id"], to_lot, qty_g, db.clean(reason) or None, move_date or db.today(), db.now()))
        db.log(conn, "move", move_id, "adjust",
               "%s %s %s in %s" % ("Wrote off" if qty_g < 0 else "Found", fmt_qty(abs(qty_g)),
                                   lot["product"], lot["warehouse"]),
               {"lot_id": lot["id"], "to_lot_id": to_lot}, undoable=True)
    return get_move(move_id)


def get_move(move_id: int) -> Optional[Dict[str, Any]]:
    row = db.q1(
        """SELECT m.*, s.display AS product, fw.name AS from_warehouse, tw.name AS to_warehouse
           FROM stock_moves m JOIN lots l ON l.id = m.lot_id
           JOIN v_products s ON s.id = l.product_id
           JOIN warehouses fw ON fw.id = l.warehouse_id
           LEFT JOIN lots t ON t.id = m.to_lot_id
           LEFT JOIN warehouses tw ON tw.id = t.warehouse_id
           WHERE m.id = ?""", (int(move_id),))
    return db.row_to_dict(row)


def cancel_move(move_id: int) -> Dict[str, Any]:
    """Put a transfer or adjustment back - only while nothing depends on it."""
    with db.tx() as conn:
        m = db.q1("SELECT * FROM stock_moves WHERE id=?", (int(move_id),))
        if m is None:
            raise ValueError("No such movement")
        if m["status"] != "done":
            raise ValueError("Already cancelled")
        if m["to_lot_id"]:
            t = db.q1("SELECT * FROM lots WHERE id=?", (m["to_lot_id"],))
            if t["qty_allocated_g"] or t["qty_out_g"]:
                raise ValueError("Cannot undo - stock it brought in has since been sold or moved")
            conn.execute("UPDATE lots SET status='cancelled' WHERE id=?", (t["id"],))
        if m["kind"] == "transfer" or m["qty_g"] < 0:
            conn.execute("UPDATE lots SET qty_out_g = qty_out_g - ? WHERE id=?",
                         (abs(m["qty_g"]), m["lot_id"]))
        refresh_lot_status(conn)
        conn.execute("UPDATE stock_moves SET status='cancelled', cancelled_at=? WHERE id=?",
                     (db.now(), m["id"]))
        db.log(conn, "move", m["id"], "cancel", "Undid movement %d" % m["id"], {})
    return get_move(move_id)
