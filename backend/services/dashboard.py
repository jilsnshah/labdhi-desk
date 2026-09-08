"""The desk view. Answers, in order: what do I hold, what did I make today,
what needs me right now."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

from .. import db
from ..money import fmt_qty, value_paise
from . import inventory


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


POSITION_PAGE = 12


def summary(q: str = "", limit: int = POSITION_PAGE, offset: int = 0,
            **filters) -> Dict[str, Any]:
    today = db.today()
    # Totals cover the whole book and come from SQL; the list is one filtered page.
    tot = inventory.totals()
    pos = inventory.positions(q=q, limit=limit, offset=offset, **filters)
    matched = inventory.count_positions(q=q, **filters)

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
        "materials": tot["materials"],
        "open_lots": tot["open_lots"],
        "positions": pos,
        "positions_matched": matched,
        "positions_shown": len(pos),
        "has_more": offset + len(pos) < matched,
        "query": q,
        "attention": attention(),
        "policy": db.settings().get("alloc_policy", "fifo"),
    }


def attention() -> List[Dict[str, Any]]:
    """Only things that are actionable. An empty list is a good day."""
    out: List[Dict[str, Any]] = []

    for r in db.q(
        """SELECT d.id, d.ref, d.uncovered_g, s.display AS material, p.name AS party
           FROM deals d JOIN skus s ON s.id=d.sku_id JOIN parties p ON p.id=d.party_id
           WHERE d.side='sell' AND d.status='booked' AND d.uncovered_g > 0
           ORDER BY d.uncovered_g DESC"""
    ):
        out.append({
            "level": "danger", "kind": "short", "deal_id": r["id"],
            "title": "%s is short %s" % (r["ref"], fmt_qty(r["uncovered_g"])),
            "detail": "%s sold to %s is not covered by stock. Buy to cover."
                      % (r["material"], r["party"]),
        })

    for r in db.q(
        """SELECT d.id, d.ref, p.name AS party, s.display AS material,
                  SUM(a.qty_g * (a.sale_rate_paise - a.cost_paise)) AS m
           FROM deals d
           JOIN allocations a ON a.sale_deal_id=d.id AND a.active=1
           JOIN parties p ON p.id=d.party_id JOIN skus s ON s.id=d.sku_id
           WHERE d.side='sell' AND d.status='booked'
           GROUP BY d.id HAVING m < 0 ORDER BY m ASC LIMIT 5"""
    ):
        out.append({
            "level": "warn", "kind": "loss", "deal_id": r["id"],
            "title": "%s booked at a loss" % r["ref"],
            "detail": "%s to %s" % (r["material"], r["party"]),
        })

    for r in db.q(
        """SELECT d.id, d.ref, d.side, p.name AS party, s.display AS material
           FROM deals d JOIN parties p ON p.id=d.party_id JOIN skus s ON s.id=d.sku_id
           WHERE d.status='draft' ORDER BY d.id DESC LIMIT 5"""
    ):
        out.append({
            "level": "info", "kind": "draft", "deal_id": r["id"],
            "title": "%s still a draft" % r["ref"],
            "detail": "%s %s with %s - confirm or discard"
                      % (r["side"].upper(), r["material"], r["party"]),
        })

    stale = _since(30)
    for r in db.q(
        """SELECT l.id, l.label, s.display AS material, d.deal_date,
                  (l.qty_g - l.qty_allocated_g) AS available_g
           FROM lots l JOIN deals d ON d.id=l.deal_id JOIN skus s ON s.id=l.sku_id
           WHERE l.status='open' AND l.qty_g > l.qty_allocated_g AND d.deal_date < ?
           ORDER BY d.deal_date LIMIT 5""", (stale,)
    ):
        out.append({
            "level": "info", "kind": "stale", "lot_id": r["id"],
            "title": "%s sitting since %s" % (fmt_qty(r["available_g"]), r["deal_date"]),
            "detail": "%s from %s" % (r["material"], r["label"]),
        })
    return out


def tape(limit: int = 40, offset: int = 0, q: str = "", **filters) -> Dict[str, Any]:
    """The trade tape - one chronological stream, one filtered page at a time."""
    from . import deals as deals_svc
    rows = deals_svc.list_deals(limit=limit, offset=offset, q=q, **filters)
    total = deals_svc.count_deals(q=q, **filters)
    return {"deals": rows, "matched": total, "has_more": offset + len(rows) < total}


def counterparties(limit: int = 12) -> List[Dict[str, Any]]:
    rows = db.q(
        """SELECT p.id, p.name, p.is_supplier, p.is_customer,
                  COALESCE(SUM(CASE WHEN d.side='buy'  AND d.status='booked' THEN d.qty_g END),0) AS bought_g,
                  COALESCE(SUM(CASE WHEN d.side='sell' AND d.status='booked' THEN d.qty_g END),0) AS sold_g,
                  COUNT(d.id) AS deals, MAX(d.deal_date) AS last_deal
           FROM parties p LEFT JOIN deals d ON d.party_id=p.id
           GROUP BY p.id HAVING deals > 0
           ORDER BY last_deal DESC, deals DESC LIMIT ?""", (limit,))
    out = []
    for r in rows:
        c = dict(r)
        gram_paise = db.scalar(
            """SELECT COALESCE(SUM(a.qty_g*(a.sale_rate_paise-a.cost_paise)),0)
               FROM allocations a JOIN deals d ON d.id=a.sale_deal_id
               WHERE a.active=1 AND d.party_id=? AND d.status='booked'""", (c["id"],))
        c["margin_paise"] = inventory._round_div(gram_paise)
        out.append(c)
    return out
