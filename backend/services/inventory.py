"""Positions, the lot ladder, and the lineage graph.

A position is one product across every warehouse: the trader's exposure. The
same figures split by warehouse live in stock.stock_rows(); both are sums over
the same lots, so they can never disagree.

The lineage graph is the answer to the trader's real question: *where did
this kilo come from and where did it go*. Nodes are purchases and sales;
edges are allocations carrying quantity, cost and margin.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import db
from ..money import value_paise, weighted_rate
from .stock import AVAILABLE, LOT_SELECT, lots_for


def _round_div(gram_paise: int, per: int = 1000) -> int:
    q, r = divmod(abs(gram_paise or 0), per)
    if r * 2 >= per:
        q += 1
    return -q if (gram_paise or 0) < 0 else q


# ------------------------------------------------------------------ totals
def totals() -> Dict[str, Any]:
    """Book-wide figures, computed in SQL - never a sum over a page."""
    row = db.q1(
        """
        SELECT COUNT(DISTINCT CASE WHEN st.stock_g > 0 THEN s.id END) AS products,
               COALESCE(SUM(st.stock_g), 0) AS stock_g,
               COALESCE(SUM(st.cost_value), 0) AS cost_value,
               COALESCE(SUM(CASE WHEN m.rate_paise IS NOT NULL
                                 THEN st.stock_g * m.rate_paise ELSE 0 END), 0) AS mark_value,
               COALESCE(SUM(CASE WHEN m.rate_paise IS NOT NULL THEN st.cost_value ELSE 0 END), 0)
                   AS marked_cost_value,
               COALESCE(SUM(st.open_lots), 0) AS open_lots
        FROM products s
        LEFT JOIN (SELECT l.product_id,
                          SUM({a}) AS stock_g,
                          SUM({a} * l.rate_paise) AS cost_value,
                          COUNT(*) AS open_lots
                   FROM lots l WHERE l.status='open' AND {a} > 0
                   GROUP BY l.product_id) st ON st.product_id = s.id
        LEFT JOIN marks m ON m.product_id = s.id
        """.format(a=AVAILABLE))
    realised = db.scalar(
        """SELECT COALESCE(SUM(a.qty_g * (a.sale_rate_paise - a.cost_paise)), 0)
           FROM allocations a JOIN deals d ON d.id = a.sale_deal_id
           WHERE a.active = 1 AND d.status = 'booked'""")
    return {
        "products": row["products"],
        "stock_g": row["stock_g"],
        "stock_value_paise": _round_div(row["cost_value"]),
        "unrealised_paise": _round_div(row["mark_value"] - row["marked_cost_value"]),
        "realised_paise": _round_div(realised),
        "open_lots": row["open_lots"],
        "warehouses": db.scalar("SELECT COUNT(*) FROM warehouses"),
    }


# ------------------------------------------------------------------ positions
# One search box finds a position by material, grade, manufacturer, by the
# supplier it came from or by the warehouse it sits in - that is how a trader
# remembers stock: "the DCW S65", "the stuff from Vora", "what's in Mundra".
_POS_SEARCH = """
    (LOWER(s.display) LIKE ? OR LOWER(s.material) LIKE ? OR LOWER(s.grade) LIKE ?
     OR LOWER(s.manufacturer) LIKE ?
     OR EXISTS (SELECT 1 FROM lots l2 JOIN parties p2 ON p2.id = l2.supplier_id
                JOIN warehouses w2 ON w2.id = l2.warehouse_id
                WHERE l2.product_id = s.id AND l2.status='open'
                AND (LOWER(p2.name) LIKE ? OR LOWER(w2.name) LIKE ?)))
"""


def _stock_join(warehouse_id: Optional[int]) -> str:
    return """LEFT JOIN (SELECT l.product_id, SUM({a}) AS stock_g, COUNT(*) AS open_lots
                         FROM lots l WHERE l.status='open' AND {a} > 0 {w}
                         GROUP BY l.product_id) st ON st.product_id = s.id""".format(
        a=AVAILABLE, w="AND l.warehouse_id = ?" if warehouse_id else "")


def _pos_filters(q=None, include_flat=False, material_id=None, grade_id=None,
                 manufacturer_id=None, supplier_id=None, warehouse_id=None):
    where = ["1=1"] if include_flat else ["st.stock_g > 0"]
    args: List[Any] = []
    like = db.like(q)
    if like:
        where.append(_POS_SEARCH); args += [like] * 6
    for col, val in (("s.material_id", material_id), ("s.grade_id", grade_id),
                     ("s.manufacturer_id", manufacturer_id)):
        if val:
            where.append("%s = ?" % col); args.append(int(val))
    if supplier_id:
        where.append("""EXISTS (SELECT 1 FROM lots l3 WHERE l3.product_id = s.id
                        AND l3.status='open' AND l3.qty_g > l3.qty_allocated_g + l3.qty_out_g
                        AND l3.supplier_id = ?)""")
        args.append(int(supplier_id))
    return where, args


def positions(include_flat: bool = False, q: Optional[str] = None, limit: Optional[int] = None,
              offset: int = 0, warehouse_id: Optional[int] = None, **filters) -> Dict[str, Any]:
    limit, offset = db.page_args(limit, offset, default=12)
    where, args = _pos_filters(q, include_flat, warehouse_id=warehouse_id, **filters)
    join_args = [int(warehouse_id)] if warehouse_id else []
    base = " FROM v_products s " + _stock_join(warehouse_id) + \
           " LEFT JOIN marks m ON m.product_id = s.id WHERE " + " AND ".join(where)
    total = db.scalar("SELECT COUNT(*)" + base, join_args + args)
    rows = db.q(
        """SELECT s.id AS product_id, s.display AS product, s.material, s.grade, s.manufacturer,
                  s.material_id, s.grade_id, s.manufacturer_id,
                  COALESCE(st.stock_g, 0) AS stock_g, COALESCE(st.open_lots, 0) AS open_lots,
                  m.rate_paise AS mark_paise, m.source AS mark_source""" + base +
        " ORDER BY COALESCE(st.stock_g, 0) DESC, s.display LIMIT ? OFFSET ?",
        join_args + args + [limit, offset])

    out = []
    for r in rows:
        pos = dict(r)
        lots = open_lots(pos["product_id"], warehouse_id)
        pos["lots"] = lots
        pos["cost_paise"] = weighted_rate([(l["available_g"], l["rate_paise"]) for l in lots])
        pos["stock_value_paise"] = value_paise(pos["stock_g"], pos["cost_paise"])
        mark = pos["mark_paise"]
        pos["unrealised_paise"] = value_paise(pos["stock_g"], mark - pos["cost_paise"]) if mark else 0
        pos["realised_paise"] = realised_for_product(pos["product_id"])
        pos["cost_low_paise"] = min([l["rate_paise"] for l in lots], default=0)
        pos["cost_high_paise"] = max([l["rate_paise"] for l in lots], default=0)
        pos["suppliers"] = sorted({l["supplier_name"] for l in lots})
        pos["warehouses"] = by_warehouse(pos["product_id"])
        out.append(pos)
    return db.page(out, total, limit, offset)


def open_lots(product_id: int, warehouse_id: Optional[int] = None) -> List[Dict[str, Any]]:
    out = []
    for lot in lots_for(product_id, warehouse_id):
        lot["value_paise"] = value_paise(lot["available_g"], lot["rate_paise"])
        lot["pct_sold"] = round(100.0 * lot["qty_allocated_g"] / lot["qty_g"], 1) if lot["qty_g"] else 0
        out.append(lot)
    return out


def by_warehouse(product_id: int) -> List[Dict[str, Any]]:
    return db.dicts(db.q(
        """SELECT w.id, w.name, SUM({a}) AS stock_g, COUNT(*) AS lots
           FROM lots l JOIN warehouses w ON w.id = l.warehouse_id
           WHERE l.product_id = ? AND l.status = 'open' AND {a} > 0
           GROUP BY w.id, w.name ORDER BY SUM({a}) DESC, w.name""".format(a=AVAILABLE),
        (int(product_id),)))


def realised_for_product(product_id: int) -> int:
    rows = db.q(
        """SELECT a.qty_g, a.cost_paise, a.sale_rate_paise
           FROM allocations a JOIN deals d ON d.id = a.sale_deal_id
           WHERE d.product_id=? AND a.active=1 AND d.status='booked'""",
        (product_id,),
    )
    return sum(value_paise(r["qty_g"], r["sale_rate_paise"] - r["cost_paise"]) for r in rows)


def position_detail(product_id: int) -> Optional[Dict[str, Any]]:
    product = db.q1("SELECT * FROM v_products WHERE id=?", (product_id,))
    if product is None:
        return None
    lots = []
    for r in db.q(LOT_SELECT + " WHERE l.product_id=? AND l.status != 'cancelled' "
                  "ORDER BY d.deal_date, l.id", (product_id,)):
        lot = dict(r)
        lot["value_paise"] = value_paise(lot["available_g"], lot["rate_paise"])
        lot["outflows"] = lot_outflows(lot["id"])
        lot["moves"] = lot_moves(lot["id"])
        lot["margin_paise"] = sum(o["margin_paise"] for o in lot["outflows"])
        lots.append(lot)

    stock_g = sum(l["available_g"] for l in lots)
    cost = weighted_rate([(l["available_g"], l["rate_paise"]) for l in lots if l["available_g"] > 0])
    mark = db.q1("SELECT * FROM marks WHERE product_id=?", (product_id,))
    return {
        "product": dict(product),
        "lots": lots,
        "stock_g": stock_g,
        "cost_paise": cost,
        "stock_value_paise": value_paise(stock_g, cost),
        "mark_paise": mark["rate_paise"] if mark else None,
        "mark_source": mark["source"] if mark else None,
        "unrealised_paise": value_paise(stock_g, mark["rate_paise"] - cost) if mark else 0,
        "realised_paise": realised_for_product(product_id),
        "by_supplier": by_supplier(product_id),
        "warehouses": by_warehouse(product_id),
    }


def by_supplier(product_id: int) -> List[Dict[str, Any]]:
    rows = db.q(
        """SELECT p.id, p.name, SUM({a}) AS stock_g, SUM(l.qty_g) AS bought_g
           FROM lots l JOIN parties p ON p.id=l.supplier_id
           WHERE l.product_id=? AND l.status='open' AND l.parent_lot_id IS NULL
           GROUP BY p.id, p.name
           HAVING SUM({a}) > 0
           ORDER BY SUM({a}) DESC""".format(a=AVAILABLE),
        (product_id,),
    )
    return db.dicts(rows)


def lot_outflows(lot_id: int) -> List[Dict[str, Any]]:
    rows = db.q(
        """SELECT a.*, d.ref AS sale_ref, d.deal_date AS sale_date, d.status AS sale_status,
                  p.name AS customer_name, p.id AS customer_id
           FROM allocations a
           JOIN deals d   ON d.id = a.sale_deal_id
           JOIN parties p ON p.id = d.party_id
           WHERE a.lot_id=? AND a.active=1
           ORDER BY d.deal_date, a.id""",
        (lot_id,),
    )
    out = []
    for r in rows:
        o = dict(r)
        o["margin_rate_paise"] = o["sale_rate_paise"] - o["cost_paise"]
        o["margin_paise"] = value_paise(o["qty_g"], o["margin_rate_paise"])
        out.append(o)
    return out


def lot_moves(lot_id: int) -> List[Dict[str, Any]]:
    """Transfers and adjustments that took grams out of, or found them against, a lot."""
    return db.dicts(db.q(
        """SELECT m.*, tw.name AS to_warehouse
           FROM stock_moves m LEFT JOIN lots t ON t.id = m.to_lot_id
           LEFT JOIN warehouses tw ON tw.id = t.warehouse_id
           WHERE m.lot_id = ? AND m.status = 'done' ORDER BY m.id""", (lot_id,)))


# ------------------------------------------------------------------ the graph
def graph(product_id: Optional[int] = None, date_from: Optional[str] = None,
          date_to: Optional[str] = None, limit: int = 60,
          warehouse_id: Optional[int] = None) -> Dict[str, Any]:
    """Nodes = purchases + sales. Edges = allocations.

    The window is anchored on SALES in the period, and the purchases that fed
    them are pulled in whatever their own date, because a sale whose source is
    off-screen is not a lineage - it is a dangling arrow. Purchases made in the
    period are included too, so material bought and not yet sold still shows
    as idle stock.

    A purchase is one node even after part of it was transferred: grams sold
    out of the transferred lot are drawn from the purchase they came from.
    """
    args: List[Any] = []
    sale_where = ["d.side='sell'", "d.status='booked'"]
    for col, val in (("d.product_id", product_id), ("d.warehouse_id", warehouse_id)):
        if val:
            sale_where.append("%s = ?" % col); args.append(val)
    if date_from:
        sale_where.append("d.deal_date >= ?"); args.append(date_from)
    if date_to:
        sale_where.append("d.deal_date <= ?"); args.append(date_to)

    sale_rows = db.q(
        """SELECT d.*, p.name AS party_name, s.display AS product, w.name AS warehouse
           FROM deals d JOIN parties p ON p.id=d.party_id JOIN v_products s ON s.id=d.product_id
           LEFT JOIN warehouses w ON w.id = d.warehouse_id
           WHERE """ + " AND ".join(sale_where) +
        " ORDER BY d.deal_date DESC, d.id DESC LIMIT ?", args + [limit])
    sale_ids = [r["id"] for r in sale_rows]

    buy_where = ["d.side='buy'", "d.status='booked'"]
    buy_args: List[Any] = []
    for col, val in (("d.product_id", product_id), ("d.warehouse_id", warehouse_id)):
        if val:
            buy_where.append("%s = ?" % col); buy_args.append(val)
    if date_from:
        buy_where.append("d.deal_date >= ?"); buy_args.append(date_from)
    if date_to:
        buy_where.append("d.deal_date <= ?"); buy_args.append(date_to)
    buy_ids = [r["id"] for r in db.q(
        "SELECT d.id FROM deals d WHERE " + " AND ".join(buy_where) +
        " ORDER BY d.deal_date DESC, d.id DESC LIMIT ?", buy_args + [limit])]

    edge_rows = []
    if sale_ids:
        marks = ",".join("?" * len(sale_ids))
        edge_rows = db.dicts(db.q(
            "SELECT a.*, l.deal_id AS buy_deal_id FROM allocations a JOIN lots l ON l.id = a.lot_id "
            "WHERE a.active=1 AND a.sale_deal_id IN (%s) ORDER BY a.id" % marks, sale_ids))
        # Pull in any purchase the window would otherwise have cut off.
        buy_ids += sorted({r["buy_deal_id"] for r in edge_rows} - set(buy_ids))

    purchases = []
    if buy_ids:
        marks = ",".join("?" * len(buy_ids))
        purchases = db.dicts(db.q(
            """SELECT d.id AS deal_id, d.ref AS deal_ref, d.deal_date, d.qty_g, d.rate_paise,
                      p.name AS supplier_name, s.display AS product, w.name AS warehouse,
                      (SELECT MIN(r.id) FROM lots r WHERE r.deal_id = d.id
                       AND r.parent_lot_id IS NULL) AS lot_id,
                      (SELECT COALESCE(SUM(l.qty_g - l.qty_allocated_g - l.qty_out_g), 0)
                       FROM lots l WHERE l.deal_id = d.id AND l.status = 'open') AS available_g
               FROM deals d JOIN parties p ON p.id = d.party_id
               JOIN v_products s ON s.id = d.product_id
               LEFT JOIN warehouses w ON w.id = d.warehouse_id
               WHERE d.id IN (%s)""" % marks, buy_ids))

    purchases.sort(key=lambda r: (r["deal_date"], r["deal_id"]))
    sale_rows = sorted(sale_rows, key=lambda r: (r["deal_date"], r["id"]))
    root = {p["deal_id"]: p["lot_id"] for p in purchases}

    nodes = []
    for r in purchases:
        nodes.append({
            "id": "lot:%d" % r["lot_id"], "kind": "lot", "lot_id": r["lot_id"],
            "deal_id": r["deal_id"], "deal_ref": r["deal_ref"], "date": r["deal_date"],
            "party": r["supplier_name"], "material": r["product"], "product": r["product"],
            "warehouse": r["warehouse"],
            "qty_g": r["qty_g"], "remaining_g": r["available_g"],
            "rate_paise": r["rate_paise"],
            "value_paise": value_paise(r["qty_g"], r["rate_paise"]),
        })
    for r in sale_rows:
        nodes.append({
            "id": "sale:%d" % r["id"], "kind": "sale", "deal_id": r["id"],
            "deal_ref": r["ref"], "date": r["deal_date"],
            "party": r["party_name"], "material": r["product"], "product": r["product"],
            "warehouse": r["warehouse"],
            "qty_g": r["qty_g"], "uncovered_g": r["uncovered_g"],
            "rate_paise": r["rate_paise"],
            "value_paise": value_paise(r["qty_g"], r["rate_paise"]),
        })

    edges = []
    for r in edge_rows:
        lot_root = root.get(r["buy_deal_id"])
        if lot_root is None:
            continue
        margin_rate = r["sale_rate_paise"] - r["cost_paise"]
        edges.append({
            "id": r["id"], "source": "lot:%d" % lot_root, "target": "sale:%d" % r["sale_deal_id"],
            "qty_g": r["qty_g"], "cost_paise": r["cost_paise"],
            "sale_rate_paise": r["sale_rate_paise"], "margin_rate_paise": margin_rate,
            "margin_paise": value_paise(r["qty_g"], margin_rate), "method": r["method"],
        })

    total_sales = db.scalar("SELECT COUNT(*) FROM deals d WHERE " + " AND ".join(sale_where), args)
    return {
        "nodes": nodes, "edges": edges,
        "sales_shown": len(sale_rows), "sales_total": total_sales,
        "truncated": total_sales > len(sale_rows),
        "from": date_from, "to": date_to,
    }


def trace(kind: str, entity_id: int) -> Dict[str, Any]:
    """Full provenance of one lot (its whole purchase) or one sale."""
    if kind == "lot":
        lot = db.q1(LOT_SELECT + " WHERE l.id=?", (entity_id,))
        if lot is None:
            return {}
        out = dict(lot)
        family = [r["id"] for r in db.q("SELECT id FROM lots WHERE deal_id=? AND status != 'cancelled'",
                                        (out["deal_id"],))]
        out["outflows"] = [o for lid in family for o in lot_outflows(lid)]
        out["moves"] = [m for lid in family for m in lot_moves(lid)]
        out["available_g"] = db.scalar(
            "SELECT COALESCE(SUM(%s),0) FROM lots l WHERE l.deal_id=? AND l.status='open'" % AVAILABLE,
            (out["deal_id"],))
        out["margin_paise"] = sum(o["margin_paise"] for o in out["outflows"])
        return {"kind": "lot", "node": out}

    from . import deals as deals_svc
    return {"kind": "sale", "node": deals_svc.get_deal(entity_id)}
