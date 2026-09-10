"""The desk view. Answers, in order: what do I hold, what did I make today,
what needs me right now. The positions themselves are paged separately."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .. import db
from ..money import fmt_qty, value_paise
from . import inventory
from .stock import AVAILABLE


def _since(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def realised_between(start: str, end: str) -> int:
    rows = db.q(
        """SELECT a.qty_g, a.cost_paise, a.sale_rate_paise
           FROM allocations a JOIN deals d ON d.id=a.sale_deal_id
           WHERE a.active=1 AND d.status='booked' AND d.deal_date BETWEEN ? AND ?""",
        (start, end),
    )
    return sum(value_paise(r["qty_g"], r["sale_rate_paise"] - r["cost_paise"]) for r in rows)


def summary() -> Dict[str, Any]:
    today = db.today()
    tot = inventory.totals()
    bought_today = db.q1(
        "SELECT COALESCE(SUM(qty_g),0) q, COUNT(*) n FROM deals "
        "WHERE side='buy' AND status='booked' AND deal_date=?", (today,))
    sold_today = db.q1(
        "SELECT COALESCE(SUM(qty_g),0) q, COUNT(*) n FROM deals "
        "WHERE side='sell' AND status='booked' AND deal_date=?", (today,))
    return {
        "company": db.settings().get("company_name", "Trading Desk"),
        "date": today,
        "stock_g": tot["stock_g"],
        "stock_value_paise": tot["stock_value_paise"],
        "unrealised_paise": tot["unrealised_paise"],
        "realised_paise": tot["realised_paise"],
        "realised_today_paise": realised_between(today, today),
        "realised_week_paise": realised_between(_since(7), today),
        "realised_month_paise": realised_between(_since(30), today),
        "bought_today_g": bought_today["q"], "buys_today": bought_today["n"],
        "sold_today_g": sold_today["q"], "sells_today": sold_today["n"],
        "products": tot["products"],
        "open_lots": tot["open_lots"],
        "warehouses": tot["warehouses"],
        "attention": attention(),
    }


def attention() -> List[Dict[str, Any]]:
    """Only things that are actionable. An empty list is a good day."""
    out: List[Dict[str, Any]] = []

    for r in db.q(
        """SELECT d.id, d.ref, d.uncovered_g, s.display AS product, p.name AS party
           FROM deals d JOIN v_products s ON s.id=d.product_id JOIN parties p ON p.id=d.party_id
           WHERE d.side='sell' AND d.status='booked' AND d.uncovered_g > 0
           ORDER BY d.uncovered_g DESC LIMIT 5"""
    ):
        out.append({
            "level": "danger", "kind": "short", "deal_id": r["id"],
            "title": "%s is short %s" % (r["ref"], fmt_qty(r["uncovered_g"])),
            "detail": "%s sold to %s is not covered by stock. Buy to cover."
                      % (r["product"], r["party"]),
        })

    for r in db.q(
        """SELECT d.id, d.ref, p.name AS party, s.display AS product,
                  SUM(a.qty_g * (a.sale_rate_paise - a.cost_paise)) AS m
           FROM deals d
           JOIN allocations a ON a.sale_deal_id=d.id AND a.active=1
           JOIN parties p ON p.id=d.party_id JOIN v_products s ON s.id=d.product_id
           WHERE d.side='sell' AND d.status='booked'
           GROUP BY d.id, d.ref, p.name, s.display
           HAVING SUM(a.qty_g * (a.sale_rate_paise - a.cost_paise)) < 0
           ORDER BY SUM(a.qty_g * (a.sale_rate_paise - a.cost_paise)) ASC LIMIT 5"""
    ):
        out.append({
            "level": "warn", "kind": "loss", "deal_id": r["id"],
            "title": "%s booked at a loss" % r["ref"],
            "detail": "%s to %s" % (r["product"], r["party"]),
        })

    for r in db.q(
        """SELECT d.id, d.ref, d.side, p.name AS party, s.display AS product
           FROM deals d JOIN parties p ON p.id=d.party_id JOIN v_products s ON s.id=d.product_id
           WHERE d.status='draft' ORDER BY d.id DESC LIMIT 5"""
    ):
        out.append({
            "level": "info", "kind": "draft", "deal_id": r["id"],
            "title": "%s still a draft" % r["ref"],
            "detail": "%s %s with %s - confirm or discard"
                      % (r["side"].upper(), r["product"], r["party"]),
        })

    stale = _since(30)
    for r in db.q(
        """SELECT l.id, l.label, s.display AS product, w.name AS warehouse, d.deal_date,
                  {a} AS available_g
           FROM lots l JOIN deals d ON d.id=l.deal_id JOIN v_products s ON s.id=l.product_id
           JOIN warehouses w ON w.id = l.warehouse_id
           WHERE l.status='open' AND {a} > 0 AND d.deal_date < ?
           ORDER BY d.deal_date LIMIT 5""".format(a=AVAILABLE), (stale,)
    ):
        out.append({
            "level": "info", "kind": "stale", "lot_id": r["id"],
            "title": "%s sitting in %s since %s" % (fmt_qty(r["available_g"]), r["warehouse"], r["deal_date"]),
            "detail": "%s from %s" % (r["product"], r["label"]),
        })
    return out


def counterparties(limit: Optional[int] = None, offset: int = 0) -> Dict[str, Any]:
    """Who the desk trades with, most recent first, with the margin each has earned."""
    limit, offset = db.page_args(limit, offset, default=12)
    total = db.scalar("SELECT COUNT(DISTINCT party_id) FROM deals")
    rows = db.q(
        """SELECT * FROM (
             SELECT p.id, p.name,
                    COALESCE(SUM(CASE WHEN d.side='buy'  AND d.status='booked' THEN d.qty_g END),0) AS bought_g,
                    COALESCE(SUM(CASE WHEN d.side='sell' AND d.status='booked' THEN d.qty_g END),0) AS sold_g,
                    COUNT(d.id) AS deals, MAX(d.deal_date) AS last_deal
             FROM parties p JOIN deals d ON d.party_id=p.id
             GROUP BY p.id, p.name) x
           ORDER BY last_deal DESC, deals DESC LIMIT ? OFFSET ?""", (limit, offset))
    out = []
    for r in rows:
        c = dict(r)
        gram_paise = db.scalar(
            """SELECT COALESCE(SUM(a.qty_g*(a.sale_rate_paise-a.cost_paise)),0)
               FROM allocations a JOIN deals d ON d.id=a.sale_deal_id
               WHERE a.active=1 AND d.party_id=? AND d.status='booked'""", (c["id"],))
        c["margin_paise"] = inventory._round_div(gram_paise)
        out.append(c)
    return db.page(out, total, limit, offset)
