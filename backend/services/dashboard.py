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
    """Only things that need doing, one line per kind, however busy the book.

    A sale below the cost of the lots it used is not listed here: it is a
    trading outcome, not a task, and on a busy desk a line per such sale would
    bury everything else - often for a purchase that made money overall. Losses
    show where margins are read: red on the Tape and in the Excel reports.
    """
    out: List[Dict[str, Any]] = []

    short = db.q1("""SELECT COUNT(*) AS n, COALESCE(SUM(uncovered_g), 0) AS g, MIN(id) AS first_id
                     FROM deals WHERE side='sell' AND status='booked' AND uncovered_g > 0""")
    if short["n"]:
        out.append({
            "level": "danger", "kind": "short", "deal_id": short["first_id"] if short["n"] == 1 else None,
            "route": "tape",
            "title": "%d sale%s not covered by stock (%s short)" % (short["n"], "" if short["n"] == 1 else "s",
                                                                  fmt_qty(short["g"])),
            "detail": "Buy to cover, or cancel the sale.",
        })

    drafts = db.q1("SELECT COUNT(*) AS n, MIN(id) AS first_id FROM deals WHERE status='draft'")
    if drafts["n"]:
        out.append({
            "level": "info", "kind": "draft", "deal_id": drafts["first_id"] if drafts["n"] == 1 else None,
            "route": "tape",
            "title": "%d sauda%s not booked yet" % (drafts["n"], "" if drafts["n"] == 1 else "s"),
            "detail": "Confirm or discard.",
        })

    stale = db.q1(
        """SELECT COUNT(*) AS n, COALESCE(SUM({a}), 0) AS g, MIN(d.deal_date) AS oldest
           FROM lots l JOIN deals d ON d.id = l.deal_id
           WHERE l.status = 'open' AND {a} > 0 AND d.deal_date < ?""".format(a=AVAILABLE), (_since(30),))
    if stale["n"]:
        out.append({
            "level": "info", "kind": "stale", "route": "stock",
            "title": "%s idle for over 30 days" % fmt_qty(stale["g"]),
            "detail": "%d lot%s, the oldest bought %s. Open Stock to see them."
                      % (stale["n"], "" if stale["n"] == 1 else "s", stale["oldest"]),
        })
    return out


def counterparties(limit: Optional[int] = None, offset: int = 0) -> Dict[str, Any]:
    """Who the desk trades with, most recent first, with the margin each has earned."""
    limit, offset = db.page_args(limit, offset, default=12)
    # a cancelled deal is not trade: a party whose only deal was cancelled is not a counterparty
    total = db.scalar("SELECT COUNT(DISTINCT party_id) FROM deals WHERE status='booked'")
    rows = db.q(
        """SELECT * FROM (
             SELECT p.id, p.name,
                    COALESCE(SUM(CASE WHEN d.side='buy'  AND d.status='booked' THEN d.qty_g END),0) AS bought_g,
                    COALESCE(SUM(CASE WHEN d.side='sell' AND d.status='booked' THEN d.qty_g END),0) AS sold_g,
                    COUNT(d.id) AS deals, MAX(d.deal_date) AS last_deal
             FROM parties p JOIN deals d ON d.party_id=p.id AND d.status='booked'
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
