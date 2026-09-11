"""Clear the trading book, keep the master records.

    python -m backend.reset_trading          # show what would go
    python -m backend.reset_trading --yes    # do it

Removes every deal, lot, allocation, transfer/adjustment and price mark, and
the audit entries that belong to them - so stock is zero everywhere and the
Sauda series starts again at 0001. Parties, warehouses, products, materials,
grades, manufacturers, states and settings are left exactly as they are.

One transaction: it all goes, or nothing does.
"""
from __future__ import annotations

import sys

from . import db

# children before parents
TRADING = ["stock_moves", "allocations", "lots", "marks", "deals"]
KEPT = ["parties", "warehouses", "products", "materials", "grades", "manufacturers", "states"]
# audit rows about the book itself; party/product/warehouse history stays
EVENTS = "entity IN ('deal','move') OR action = 'mark'"


def counts():
    out = {t: db.scalar("SELECT COUNT(*) FROM %s" % t) for t in TRADING + KEPT}
    out["events (trading)"] = db.scalar("SELECT COUNT(*) FROM events WHERE " + EVENTS)
    return out


def run(yes: bool = False) -> None:
    db.init_db()
    before = counts()
    print("before:", before)
    if not yes:
        print("nothing changed - add --yes to clear %s and trading events" % ", ".join(TRADING))
        return
    with db.tx() as conn:
        for table in TRADING:
            conn.execute("DELETE FROM %s" % table)
        conn.execute("DELETE FROM events WHERE " + EVENTS)
        db.log(conn, "system", None, "reset", "Trading book cleared: deals and stock start from zero",
               {k: v for k, v in before.items() if k in TRADING})
    after = counts()
    print("after: ", after)
    for t in KEPT:
        assert after[t] == before[t], "a master table changed: %s" % t


if __name__ == "__main__":
    run(yes="--yes" in sys.argv)
