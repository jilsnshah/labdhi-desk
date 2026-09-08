"""The allocation engine - decides WHICH purchased kilos a sale consumes.

This is the only genuinely hard piece of logic in the system, and the whole
UX bet is that the trader never has to think about it. He names a buyer, a
material, a quantity and a rate; the engine proposes the split and shows the
margin. He accepts, or drags one slider.

Everything here is pure: it reads lots and returns a proposal. Nothing is
written until deals.book_sell() commits it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import db
from ..money import value_paise, weighted_rate

# Fill order for whatever the trader has not chosen himself. This is an
# internal default, never a question put to the user: oldest stock moves first.
POLICIES = {
    "fifo":      "Oldest stock first",
    "lifo":      "Newest stock first",
    "cheapest":  "Lowest cost first",
    "costliest": "Highest cost first",
    "spread":    "Pro-rata across every lot",
}
DEFAULT_POLICY = "fifo"


# ------------------------------------------------------------------ reading
def available_lots(sku_id: int, include_empty: bool = False) -> List[Dict[str, Any]]:
    rows = db.q(
        """
        SELECT l.*, p.name AS supplier_name, d.ref AS deal_ref, d.deal_date,
               (l.qty_g - l.qty_allocated_g) AS available_g
        FROM lots l
        JOIN parties p ON p.id = l.supplier_id
        JOIN deals   d ON d.id = l.deal_id
        WHERE l.sku_id = ? AND l.status = 'open'
        ORDER BY d.deal_date, l.id
        """,
        (sku_id,),
    )
    lots = [dict(r) for r in rows]
    if not include_empty:
        lots = [l for l in lots if l["available_g"] > 0]
    return lots


def _order(lots: List[Dict[str, Any]], policy: str, prefer_supplier: Optional[int]) -> List[Dict[str, Any]]:
    key = {
        "fifo":      lambda l: (l["deal_date"], l["id"]),
        "lifo":      lambda l: (_neg_date(l["deal_date"]), -l["id"]),
        "cheapest":  lambda l: (l["rate_paise"], l["deal_date"], l["id"]),
        "costliest": lambda l: (-l["rate_paise"], l["deal_date"], l["id"]),
        "spread":    lambda l: (l["deal_date"], l["id"]),
    }.get(policy, lambda l: (l["deal_date"], l["id"]))
    ordered = sorted(lots, key=key)
    if prefer_supplier:
        ordered.sort(key=lambda l: 0 if l["supplier_id"] == prefer_supplier else 1)
    return ordered


def _neg_date(d: str) -> str:
    # cheap descending sort key for ISO dates without parsing
    return "".join(chr(255 - ord(c)) if ord(c) < 255 else c for c in (d or ""))


# ------------------------------------------------------------------ the plan
def suggest(sku_id: int, qty_g: int, policy: str = DEFAULT_POLICY,
            pins: Optional[List[Dict[str, int]]] = None,
            exclude_lot_ids: Optional[List[int]] = None,
            prefer_supplier: Optional[int] = None,
            ignore_sale_id: Optional[int] = None) -> Dict[str, Any]:
    """Return picks covering `qty_g`, honouring pinned lots first.

    `ignore_sale_id` lets an already-booked sale be re-planned: its own
    allocations are handed back to the pool before the new split is computed.
    """
    lots = available_lots(sku_id)
    if ignore_sale_id:
        held = db.q(
            "SELECT lot_id, SUM(qty_g) AS q FROM allocations "
            "WHERE sale_deal_id=? AND active=1 GROUP BY lot_id",
            (ignore_sale_id,),
        )
        back = {int(r["lot_id"]): int(r["q"]) for r in held}
        by_id = {l["id"]: l for l in lots}
        for lot_id, qty in back.items():
            if lot_id in by_id:
                by_id[lot_id]["available_g"] += qty
            else:
                row = db.q1(
                    "SELECT l.*, p.name AS supplier_name, d.ref AS deal_ref, d.deal_date "
                    "FROM lots l JOIN parties p ON p.id=l.supplier_id "
                    "JOIN deals d ON d.id=l.deal_id WHERE l.id=?",
                    (lot_id,),
                )
                if row:
                    lot = dict(row)
                    lot["available_g"] = qty
                    lots.append(lot)

    excluded = set(exclude_lot_ids or [])
    pool = {l["id"]: l for l in lots if l["id"] not in excluded}

    picks: List[Dict[str, Any]] = []
    remaining = qty_g

    # 1. the trader's own choices, exactly as asked, clamped to what exists.
    #    A chosen quantity is a decision, not a hint - so a chosen lot is taken
    #    out of the auto-fill pool entirely. If that leaves the sale short, the
    #    shortfall is reported rather than quietly topped up from his pick.
    chosen = set()
    for pin in (pins or []):
        lot_id = int(pin["lot_id"])
        lot = pool.get(lot_id)
        if not lot:
            continue
        chosen.add(lot_id)
        if remaining <= 0:
            continue
        take = min(int(pin.get("qty_g") or 0), lot["available_g"], remaining)
        if take <= 0:
            continue
        picks.append(_pick(lot, take, "chosen"))
        lot["available_g"] -= take
        remaining -= take

    # 2. fill whatever is left from the lots he did not touch
    if remaining > 0:
        rest = [l for l in _order(list(pool.values()), policy, prefer_supplier)
                if l["available_g"] > 0 and l["id"] not in chosen]
        if policy == "spread" and rest:
            remaining = _spread(rest, remaining, picks)
        for lot in rest:
            if remaining <= 0:
                break
            take = min(lot["available_g"], remaining)
            if take <= 0:
                continue
            picks.append(_pick(lot, take, policy))
            lot["available_g"] -= take
            remaining -= take

    picks = _merge(picks)
    covered = sum(p["qty_g"] for p in picks)
    return {
        "picks": picks,
        "covered_g": covered,
        "uncovered_g": max(0, qty_g - covered),
        "policy": policy,
        "avg_cost_paise": weighted_rate([(p["qty_g"], p["cost_paise"]) for p in picks]),
    }


def _spread(rest: List[Dict[str, Any]], remaining: int, picks: List[Dict[str, Any]]) -> int:
    """Pro-rata pass. Leftover grams fall through to the sequential fill."""
    pool_total = sum(l["available_g"] for l in rest)
    if pool_total <= 0:
        return remaining
    target = min(remaining, pool_total)
    for lot in rest:
        if remaining <= 0:
            break
        share = min(lot["available_g"], (lot["available_g"] * target) // pool_total, remaining)
        if share <= 0:
            continue
        picks.append(_pick(lot, share, "spread"))
        lot["available_g"] -= share
        remaining -= share
    return remaining


def _pick(lot: Dict[str, Any], qty_g: int, method: str) -> Dict[str, Any]:
    return {
        "lot_id": lot["id"],
        "lot_label": lot["label"],
        "supplier_id": lot["supplier_id"],
        "supplier_name": lot["supplier_name"],
        "deal_ref": lot["deal_ref"],
        "deal_date": lot["deal_date"],
        "cost_paise": lot["rate_paise"],
        "qty_g": qty_g,
        "lot_qty_g": lot["qty_g"],
        "lot_available_g": lot["available_g"],
        "method": method,
    }


def _merge(picks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    for p in picks:
        cur = out.get(p["lot_id"])
        if cur:
            cur["qty_g"] += p["qty_g"]
        else:
            out[p["lot_id"]] = dict(p)
    return list(out.values())


# ------------------------------------------------------------------ margin
def price_plan(plan: Dict[str, Any], sale_rate_paise: int, qty_g: int) -> Dict[str, Any]:
    """Attach money to a plan. Uncovered kilos are a short position, priced at
    the last known market so the trader sees the risk instead of a blank."""
    lines = []
    for p in plan["picks"]:
        margin_rate = sale_rate_paise - p["cost_paise"]
        line = dict(p)
        line["margin_rate_paise"] = margin_rate
        line["margin_paise"] = value_paise(p["qty_g"], margin_rate)
        line["cost_value_paise"] = value_paise(p["qty_g"], p["cost_paise"])
        line["sale_value_paise"] = value_paise(p["qty_g"], sale_rate_paise)
        lines.append(line)

    covered = plan["covered_g"]
    margin_paise = sum(l["margin_paise"] for l in lines)
    return {
        "picks": lines,
        "policy": plan["policy"],
        "qty_g": qty_g,
        "covered_g": covered,
        "uncovered_g": plan["uncovered_g"],
        "avg_cost_paise": plan["avg_cost_paise"],
        "sale_rate_paise": sale_rate_paise,
        "margin_rate_paise": (sale_rate_paise - plan["avg_cost_paise"]) if covered else 0,
        "margin_paise": margin_paise,
        "sale_value_paise": value_paise(qty_g, sale_rate_paise),
        "cost_value_paise": sum(l["cost_value_paise"] for l in lines),
    }


def preview(sku_id: int, qty_g: int, sale_rate_paise: int, policy: str = DEFAULT_POLICY,
            pins=None, prefer_supplier=None, ignore_sale_id=None) -> Dict[str, Any]:
    plan = suggest(sku_id, qty_g, policy, pins=pins,
                   prefer_supplier=prefer_supplier, ignore_sale_id=ignore_sale_id)
    return price_plan(plan, sale_rate_paise, qty_g)
