"""Positions, the lot ladder, and the lineage graph.

The lineage graph is the answer to the trader's real question: *where did
this kilo come from and where did it go*. Nodes are purchase lots and sales;
edges are allocations carrying quantity, cost and margin.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import db
from ..money import value_paise, weighted_rate


# ------------------------------------------------------------------ search
# One search box has to find a position by material, grade, manufacturer or by
# the supplier it came from, because that is how a trader remembers stock:
# "the DCW S65" or "the stuff from Vora".
def _like(term: Optional[str]) -> Optional[str]:
    term = (term or "").strip()
    return "%%%s%%" % term.lower() if term else None


_POS_SEARCH = """
    (LOWER(s.display) LIKE ? OR LOWER(s.material) LIKE ? OR LOWER(s.grade) LIKE ?
     OR LOWER(s.manufacturer) LIKE ?
     OR EXISTS (SELECT 1 FROM lots l2 JOIN parties p2 ON p2.id = l2.supplier_id
                WHERE l2.sku_id = s.id AND l2.status='open' AND LOWER(p2.name) LIKE ?))
"""


# ------------------------------------------------------------------ totals
def totals() -> Dict[str, Any]:
    """Book-wide figures, computed in SQL.

    These must cover every position, not the page being shown, so they can
    never be a sum over a paginated list.
    """
    row = db.q1(
        """
        SELECT COUNT(DISTINCT CASE WHEN st.stock_g > 0 THEN s.id END) AS materials,
               COALESCE(SUM(st.stock_g), 0) AS stock_g,
               COALESCE(SUM(st.cost_value), 0) AS cost_value,
               COALESCE(SUM(CASE WHEN m.rate_paise IS NOT NULL
                                 THEN st.stock_g * m.rate_paise ELSE 0 END), 0) AS mark_value,
               COALESCE(SUM(CASE WHEN m.rate_paise IS NOT NULL THEN st.cost_value ELSE 0 END), 0)
                   AS marked_cost_value,
               COALESCE(SUM(st.open_lots), 0) AS open_lots
        FROM skus s
        LEFT JOIN (SELECT sku_id,
                          SUM(qty_g - qty_allocated_g) AS stock_g,
                          SUM((qty_g - qty_allocated_g) * rate_paise) AS cost_value,
                          COUNT(*) AS open_lots
                   FROM lots WHERE status='open' AND qty_g > qty_allocated_g
                   GROUP BY sku_id) st ON st.sku_id = s.id
        LEFT JOIN marks m ON m.sku_id = s.id
        """
    )
    realised = db.scalar(
        """SELECT COALESCE(SUM(a.qty_g * (a.sale_rate_paise - a.cost_paise)), 0)
           FROM allocations a JOIN deals d ON d.id = a.sale_deal_id
           WHERE a.active = 1 AND d.status = 'booked'"""
    )
    # The sub-totals above are gram-paise; bring them down to paise once, here.
    return {
        "materials": row["materials"],
        "stock_g": row["stock_g"],
        "stock_value_paise": _round_div(row["cost_value"]),
        "unrealised_paise": _round_div(row["mark_value"] - row["marked_cost_value"]),
        "realised_paise": _round_div(realised),
        "open_lots": row["open_lots"],
    }


def _round_div(gram_paise: int, per: int = 1000) -> int:
    q, r = divmod(abs(gram_paise), per)
    if r * 2 >= per:
        q += 1
    return -q if gram_paise < 0 else q


def _pos_filters(q=None, include_flat=False, material=None, grade=None,
                 manufacturer=None, supplier_id=None):
    where = ["1=1"] if include_flat else ["st.stock_g > 0"]
    args: List[Any] = []
    like = _like(q)
    if like:
        where.append(_POS_SEARCH)
        args += [like] * 5
    if material:
        where.append("s.material = ?"); args.append(material.strip().upper())
    if grade:
        where.append("s.grade = ?"); args.append(grade.strip().upper())
    if manufacturer:
        where.append("s.manufacturer = ?"); args.append(manufacturer.strip())
    if supplier_id:
        # "positions holding stock I bought from this supplier"
        where.append("""EXISTS (SELECT 1 FROM lots l3 WHERE l3.sku_id = s.id
                        AND l3.status='open' AND l3.qty_g > l3.qty_allocated_g
                        AND l3.supplier_id = ?)""")
        args.append(int(supplier_id))
    return where, args


def count_positions(q: Optional[str] = None, include_flat: bool = False, **filters) -> int:
    where, args = _pos_filters(q, include_flat, **filters)
    return db.scalar(
        """SELECT COUNT(*) FROM skus s
           LEFT JOIN (SELECT sku_id, SUM(qty_g - qty_allocated_g) AS stock_g FROM lots
                      WHERE status='open' GROUP BY sku_id) st ON st.sku_id = s.id
           WHERE """ + " AND ".join(where), args)


# ------------------------------------------------------------------ positions
def positions(include_flat: bool = False, q: Optional[str] = None,
              limit: Optional[int] = None, offset: int = 0, **filters) -> List[Dict[str, Any]]:
    where, args = _pos_filters(q, include_flat, **filters)

    sql = """
        SELECT s.id AS sku_id, s.display AS material,
               s.material AS material_name, s.grade, s.manufacturer,
               COALESCE(st.stock_g, 0) AS stock_g,
               COALESCE(st.open_lots, 0) AS open_lots,
               m.rate_paise AS mark_paise, m.source AS mark_source
        FROM skus s
        LEFT JOIN (SELECT sku_id, SUM(qty_g - qty_allocated_g) AS stock_g, COUNT(*) AS open_lots
                   FROM lots WHERE status='open' AND qty_g > qty_allocated_g
                   GROUP BY sku_id) st ON st.sku_id = s.id
        LEFT JOIN marks m ON m.sku_id = s.id
        WHERE """ + " AND ".join(where) + """
        ORDER BY stock_g DESC, s.display
    """
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        args += [limit, offset]

    out = []
    for r in db.q(sql, args):
        pos = dict(r)
        lots = open_lots(pos["sku_id"])
        pos["lots"] = lots
        pos["cost_paise"] = weighted_rate([(l["available_g"], l["rate_paise"]) for l in lots])
        pos["stock_value_paise"] = value_paise(pos["stock_g"], pos["cost_paise"])
        mark = pos["mark_paise"]
        pos["unrealised_paise"] = value_paise(pos["stock_g"], mark - pos["cost_paise"]) if mark else 0
        pos["mark_value_paise"] = value_paise(pos["stock_g"], mark) if mark else pos["stock_value_paise"]
        pos["realised_paise"] = realised_for_sku(pos["sku_id"])
        pos["cost_low_paise"] = min([l["rate_paise"] for l in lots], default=0)
        pos["cost_high_paise"] = max([l["rate_paise"] for l in lots], default=0)
        pos["suppliers"] = sorted({l["supplier_name"] for l in lots})
        out.append(pos)
    return out


def open_lots(sku_id: int) -> List[Dict[str, Any]]:
    rows = db.q(
        """SELECT l.*, p.name AS supplier_name, d.ref AS deal_ref, d.deal_date,
                  (l.qty_g - l.qty_allocated_g) AS available_g
           FROM lots l
           JOIN parties p ON p.id = l.supplier_id
           JOIN deals   d ON d.id = l.deal_id
           WHERE l.sku_id=? AND l.status='open' AND l.qty_g > l.qty_allocated_g
           ORDER BY d.deal_date, l.id""",
        (sku_id,),
    )
    out = []
    for r in rows:
        lot = dict(r)
        lot["value_paise"] = value_paise(lot["available_g"], lot["rate_paise"])
        lot["pct_sold"] = round(100.0 * lot["qty_allocated_g"] / lot["qty_g"], 1) if lot["qty_g"] else 0
        out.append(lot)
    return out


def realised_for_sku(sku_id: int) -> int:
    rows = db.q(
        """SELECT a.qty_g, a.cost_paise, a.sale_rate_paise
           FROM allocations a JOIN deals d ON d.id = a.sale_deal_id
           WHERE d.sku_id=? AND a.active=1 AND d.status='booked'""",
        (sku_id,),
    )
    return sum(value_paise(r["qty_g"], r["sale_rate_paise"] - r["cost_paise"]) for r in rows)


def position_detail(sku_id: int) -> Optional[Dict[str, Any]]:
    sku = db.q1("SELECT * FROM skus WHERE id=?", (sku_id,))
    if sku is None:
        return None
    all_lots = db.q(
        """SELECT l.*, p.name AS supplier_name, d.ref AS deal_ref, d.deal_date,
                  (l.qty_g - l.qty_allocated_g) AS available_g
           FROM lots l
           JOIN parties p ON p.id=l.supplier_id
           JOIN deals   d ON d.id=l.deal_id
           WHERE l.sku_id=? AND l.status != 'cancelled'
           ORDER BY d.deal_date, l.id""",
        (sku_id,),
    )
    lots = []
    for r in all_lots:
        lot = dict(r)
        lot["value_paise"] = value_paise(lot["available_g"], lot["rate_paise"])
        lot["outflows"] = lot_outflows(lot["id"])
        lot["margin_paise"] = sum(o["margin_paise"] for o in lot["outflows"])
        lots.append(lot)

    stock_g = sum(l["available_g"] for l in lots)
    cost = weighted_rate([(l["available_g"], l["rate_paise"]) for l in lots if l["available_g"] > 0])
    mark = db.q1("SELECT * FROM marks WHERE sku_id=?", (sku_id,))
    return {
        "sku": dict(sku),
        "lots": lots,
        "stock_g": stock_g,
        "cost_paise": cost,
        "stock_value_paise": value_paise(stock_g, cost),
        "mark_paise": mark["rate_paise"] if mark else None,
        "mark_source": mark["source"] if mark else None,
        "unrealised_paise": value_paise(stock_g, mark["rate_paise"] - cost) if mark else 0,
        "realised_paise": realised_for_sku(sku_id),
        "by_supplier": by_supplier(sku_id),
        "graph": graph(sku_id),
    }


def by_supplier(sku_id: int) -> List[Dict[str, Any]]:
    rows = db.q(
        """SELECT p.id, p.name,
                  SUM(l.qty_g - l.qty_allocated_g) AS stock_g,
                  SUM(l.qty_g) AS bought_g
           FROM lots l JOIN parties p ON p.id=l.supplier_id
           WHERE l.sku_id=? AND l.status='open'
           GROUP BY p.id, p.name
           HAVING SUM(l.qty_g - l.qty_allocated_g) > 0
           ORDER BY stock_g DESC""",
        (sku_id,),
    )
    return [dict(r) for r in rows]


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


# ------------------------------------------------------------------ the graph
def graph(sku_id: Optional[int] = None, date_from: Optional[str] = None,
          date_to: Optional[str] = None, limit: int = 60) -> Dict[str, Any]:
    """Nodes = purchase lots + sales. Edges = allocations.

    The window is anchored on SALES in the period, and the purchase lots that
    fed them are pulled in whatever their own date, because a sale whose source
    is off-screen is not a lineage - it is a dangling arrow. Purchases made in
    the period are included too, so material bought and not yet sold still shows
    as idle stock. Without this the graph would grow with the book and become
    unreadable after a few thousand trades.
    """
    args: List[Any] = []
    sale_where = ["d.side='sell'", "d.status='booked'"]
    if sku_id:
        sale_where.append("d.sku_id = ?"); args.append(sku_id)
    if date_from:
        sale_where.append("d.deal_date >= ?"); args.append(date_from)
    if date_to:
        sale_where.append("d.deal_date <= ?"); args.append(date_to)

    sale_rows = db.q(
        """SELECT d.*, p.name AS party_name, s.display AS material
           FROM deals d JOIN parties p ON p.id=d.party_id JOIN skus s ON s.id=d.sku_id
           WHERE """ + " AND ".join(sale_where) +
        " ORDER BY d.deal_date DESC, d.id DESC LIMIT ?", args + [limit])
    sale_ids = [r["id"] for r in sale_rows]

    # Purchases made inside the window, whether or not anything has been sold.
    buy_args: List[Any] = []
    lot_where = ["l.status != 'cancelled'"]
    if sku_id:
        lot_where.append("l.sku_id = ?"); buy_args.append(sku_id)
    if date_from:
        lot_where.append("d.deal_date >= ?"); buy_args.append(date_from)
    if date_to:
        lot_where.append("d.deal_date <= ?"); buy_args.append(date_to)
    lot_rows = list(db.q(
        """SELECT l.*, p.name AS supplier_name, d.ref AS deal_ref, d.deal_date,
                  s.display AS material, (l.qty_g - l.qty_allocated_g) AS available_g
           FROM lots l JOIN parties p ON p.id=l.supplier_id JOIN deals d ON d.id=l.deal_id
           JOIN skus s ON s.id=l.sku_id
           WHERE """ + " AND ".join(lot_where) +
        " ORDER BY d.deal_date DESC, l.id DESC LIMIT ?", buy_args + [limit]))

    edge_rows = []
    if sale_ids:
        marks = ",".join("?" * len(sale_ids))
        edge_rows = list(db.q(
            "SELECT * FROM allocations WHERE active=1 AND sale_deal_id IN (%s) ORDER BY id" % marks,
            sale_ids))
        # Pull in any feeding lot the window would otherwise have cut off.
        have = {r["id"] for r in lot_rows}
        missing = sorted({r["lot_id"] for r in edge_rows} - have)
        if missing:
            marks = ",".join("?" * len(missing))
            lot_rows += list(db.q(
                """SELECT l.*, p.name AS supplier_name, d.ref AS deal_ref, d.deal_date,
                          s.display AS material, (l.qty_g - l.qty_allocated_g) AS available_g
                   FROM lots l JOIN parties p ON p.id=l.supplier_id JOIN deals d ON d.id=l.deal_id
                   JOIN skus s ON s.id=l.sku_id WHERE l.id IN (%s)""" % marks, missing))

    lot_rows.sort(key=lambda r: (r["deal_date"], r["id"]))
    sale_rows = sorted(sale_rows, key=lambda r: (r["deal_date"], r["id"]))

    nodes = []
    for r in lot_rows:
        nodes.append({
            "id": "lot:%d" % r["id"], "kind": "lot", "lot_id": r["id"],
            "deal_ref": r["deal_ref"], "date": r["deal_date"],
            "party": r["supplier_name"], "material": r["material"],
            "qty_g": r["qty_g"], "remaining_g": r["available_g"],
            "rate_paise": r["rate_paise"],
            "value_paise": value_paise(r["qty_g"], r["rate_paise"]),
        })
    for r in sale_rows:
        nodes.append({
            "id": "sale:%d" % r["id"], "kind": "sale", "deal_id": r["id"],
            "deal_ref": r["ref"], "date": r["deal_date"],
            "party": r["party_name"], "material": r["material"],
            "qty_g": r["qty_g"], "uncovered_g": r["uncovered_g"],
            "rate_paise": r["rate_paise"],
            "value_paise": value_paise(r["qty_g"], r["rate_paise"]),
        })

    edges = []
    lot_ids = {r["id"] for r in lot_rows}
    for r in edge_rows:
        if r["lot_id"] not in lot_ids:
            continue
        margin_rate = r["sale_rate_paise"] - r["cost_paise"]
        edges.append({
            "id": r["id"], "source": "lot:%d" % r["lot_id"], "target": "sale:%d" % r["sale_deal_id"],
            "qty_g": r["qty_g"], "cost_paise": r["cost_paise"],
            "sale_rate_paise": r["sale_rate_paise"], "margin_rate_paise": margin_rate,
            "margin_paise": value_paise(r["qty_g"], margin_rate), "method": r["method"],
        })

    total_sales = db.scalar(
        "SELECT COUNT(*) FROM deals d WHERE " + " AND ".join(sale_where), args)
    return {
        "nodes": nodes, "edges": edges,
        "sales_shown": len(sale_rows), "sales_total": total_sales,
        "truncated": total_sales > len(sale_rows),
        "from": date_from, "to": date_to,
    }


def trace(kind: str, entity_id: int) -> Dict[str, Any]:
    """Full provenance of one lot or one sale, both directions."""
    if kind == "lot":
        lot = db.q1(
            """SELECT l.*, p.name AS supplier_name, d.ref AS deal_ref, d.deal_date,
                      s.display AS material, (l.qty_g - l.qty_allocated_g) AS available_g
               FROM lots l JOIN parties p ON p.id=l.supplier_id
               JOIN deals d ON d.id=l.deal_id JOIN skus s ON s.id=l.sku_id
               WHERE l.id=?""", (entity_id,))
        if lot is None:
            return {}
        out = dict(lot)
        out["outflows"] = lot_outflows(entity_id)
        out["margin_paise"] = sum(o["margin_paise"] for o in out["outflows"])
        return {"kind": "lot", "node": out}

    from . import deals as deals_svc
    deal = deals_svc.get_deal(entity_id)
    return {"kind": "sale", "node": deal}
