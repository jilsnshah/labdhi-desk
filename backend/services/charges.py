"""What a sauda cost us to handle and move: labour and freight.

Both are always ours - the trader pays the hamali and the lorry whatever the
sauda says about who arranges them - so they are recorded on the deal as two
whole-deal amounts in paise and taken straight off profit.

They are counted on the deal's own date, the day the cost is incurred: a
purchase's labour and freight reduce profit when the material is bought, not
when it is later sold, and stock stays valued at the purchase rate. So:

    realised profit = margin of every sale  -  labour and freight of every
                      booked sauda, bought or sold

Nothing here touches stock: a kilo's cost is still the rate its purchase was
booked at, and these amounts never move a lot, an allocation or a margin.
"""
from __future__ import annotations

from typing import Optional

from .. import db

# On a deal row, what it cost us.
COST = "(d.labour_paise + d.freight_paise)"


def total(product_id: Optional[int] = None, party_id: Optional[int] = None,
          date_from: Optional[str] = None, date_to: Optional[str] = None,
          side: Optional[str] = None) -> int:
    """Labour and freight, in paise, over whichever slice of the book is asked for."""
    where, args = ["d.status = 'booked'"], []
    for column, value in (("d.product_id", product_id), ("d.party_id", party_id), ("d.side", side)):
        if value:
            where.append("%s = ?" % column); args.append(value)
    if date_from:
        where.append("d.deal_date >= ?"); args.append(date_from)
    if date_to:
        where.append("d.deal_date <= ?"); args.append(date_to)
    return db.scalar("SELECT COALESCE(SUM(%s), 0) FROM deals d WHERE %s" % (COST, " AND ".join(where)), args)


def of(deal) -> int:
    """What one deal cost, zero unless it is booked."""
    if deal is None or deal.get("status") != "booked":
        return 0
    return int(deal.get("labour_paise") or 0) + int(deal.get("freight_paise") or 0)
