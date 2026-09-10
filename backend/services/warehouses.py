"""Warehouses: the company's own stock locations.

Every lot sits in exactly one warehouse and every deal names the warehouse it
received into or dispatched from - by id. Renaming a warehouse is therefore one
UPDATE on one row; nothing else holds its name.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .. import db
from .stock import AVAILABLE


def list_warehouses(q: str = "", product_id: Optional[int] = None, in_stock: bool = False,
                    limit: Optional[int] = None, offset: int = 0) -> Dict[str, Any]:
    """One page of warehouses with what each holds.

    With `product_id`, each row also says how much of that product is there,
    and the list is led by where most of it sits - the order a sell ticket
    wants. `in_stock` then keeps only warehouses actually holding it.
    """
    limit, offset = db.page_args(limit, offset)
    where, args = [], []
    like = db.like(q)
    if like:
        where.append("(LOWER(w.name) LIKE ? OR LOWER(COALESCE(w.address,'')) LIKE ?)")
        args += [like, like]
    pstock = "0"
    pargs = []
    if product_id:
        pstock = ("COALESCE((SELECT SUM(%s) FROM lots l WHERE l.warehouse_id = w.id "
                  "AND l.status = 'open' AND l.product_id = ?), 0)" % AVAILABLE)
        pargs = [int(product_id)]
        if in_stock:
            where.append("EXISTS (SELECT 1 FROM lots l WHERE l.warehouse_id = w.id AND "
                         "l.status = 'open' AND l.product_id = ? AND %s > 0)" % AVAILABLE)
            args.append(int(product_id))
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.scalar("SELECT COUNT(*) FROM warehouses w " + clause, args)
    rows = db.q(
        """SELECT * FROM (
             SELECT w.*,
                    COALESCE((SELECT SUM({a}) FROM lots l WHERE l.warehouse_id = w.id
                              AND l.status = 'open'), 0) AS stock_g,
                    COALESCE((SELECT SUM({a} * l.rate_paise) FROM lots l WHERE l.warehouse_id = w.id
                              AND l.status = 'open'), 0) AS cost_gp,
                    (SELECT COUNT(*) FROM lots l WHERE l.warehouse_id = w.id
                     AND l.status = 'open' AND {a} > 0) AS lots,
                    (SELECT COUNT(DISTINCT l.product_id) FROM lots l WHERE l.warehouse_id = w.id
                     AND l.status = 'open' AND {a} > 0) AS products,
                    {p} AS product_stock_g
             FROM warehouses w {clause}
           ) x
           ORDER BY {order} LIMIT ? OFFSET ?""".format(
            a=AVAILABLE, p=pstock, clause=clause,
            order="product_stock_g DESC, name" if product_id else "name"),
        pargs + args + [limit, offset])
    items = []
    for r in rows:
        w = dict(r)
        w["stock_value_paise"] = _paise(w.pop("cost_gp"))
        items.append(w)
    return db.page(items, total, limit, offset)


def _paise(gram_paise: int) -> int:
    q, r = divmod(abs(gram_paise or 0), 1000)
    if r * 2 >= 1000:
        q += 1
    return -q if (gram_paise or 0) < 0 else q


def get_warehouse(warehouse_id: int) -> Optional[Dict[str, Any]]:
    return db.row_to_dict(db.q1("SELECT * FROM warehouses WHERE id=?", (int(warehouse_id),)))


def require(warehouse_id) -> Dict[str, Any]:
    wh = get_warehouse(int(warehouse_id)) if warehouse_id else None
    if wh is None:
        raise ValueError("Choose a warehouse from the list, or add a new one")
    return wh


def save_warehouse(conn, name: str, address: Optional[str] = None,
                   warehouse_id: Optional[int] = None) -> int:
    name = db.clean(name)
    if not name:
        raise ValueError("Warehouse name is required")
    address = (address or "").strip() or None
    clash = db.q1("SELECT id, name FROM warehouses WHERE LOWER(name) = LOWER(?) AND id != ?",
                  (name, int(warehouse_id or 0)))
    if clash:
        raise ValueError("A warehouse called %s already exists" % clash["name"])
    if warehouse_id:
        cur = require(warehouse_id)
        conn.execute("UPDATE warehouses SET name=?, address=? WHERE id=?", (name, address, cur["id"]))
        db.log(conn, "warehouse", cur["id"], "update",
               ("Renamed %s to %s" % (cur["name"], name)) if cur["name"] != name else "Updated %s" % name,
               {"name": name})
        return int(cur["id"])
    wid = conn.insert("INSERT INTO warehouses(name,address,created_at) VALUES (?,?,?)",
                      (name, address, db.now()))
    db.log(conn, "warehouse", wid, "create", "Added warehouse %s" % name, {"name": name})
    return wid


def remove_warehouse(conn, warehouse_id: int) -> None:
    wh = require(warehouse_id)
    lots = db.scalar("SELECT COUNT(*) FROM lots WHERE warehouse_id=?", (wh["id"],))
    deals = db.scalar("SELECT COUNT(*) FROM deals WHERE warehouse_id=?", (wh["id"],))
    if lots or deals:
        raise ValueError("Cannot remove %s - %d deal%s already record it"
                         % (wh["name"], max(lots, deals), "" if max(lots, deals) == 1 else "s"))
    conn.execute("DELETE FROM warehouses WHERE id=?", (wh["id"],))
    db.log(conn, "warehouse", wh["id"], "remove", "Removed warehouse %s" % wh["name"], {})
