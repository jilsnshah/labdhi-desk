"""Shared test helpers. Every deal is made the way the app makes one: the party,
product and warehouse exist as records first and the deal points at their ids.

Import this only after the test module has set LABDHI_DB (or DATABASE_URL)."""
import os
import tempfile

if not os.environ.get("DATABASE_URL"):
    os.environ.setdefault("LABDHI_DB", os.path.join(tempfile.mkdtemp(), "test.db"))

from backend import db                                               # noqa: E402
from backend.services import deals, parties, products, warehouses    # noqa: E402

MT = 1_000_000
LINE = dict(material="PVC", grade="HS1000", manufacturer="Chemplast Sanmar")


def fresh():
    db.reset()
    db.init_db()


def party(name: str, **kw) -> int:
    with db.tx() as conn:
        return parties.save_party(conn, name, merge=True, quiet=True, **kw)


def warehouse(name: str) -> int:
    row = db.q1("SELECT id FROM warehouses WHERE name=?", (name,))
    if row:
        return int(row["id"])
    with db.tx() as conn:
        return warehouses.save_warehouse(conn, name)


def product(material="PVC", grade="HS1000", manufacturer="Chemplast Sanmar") -> int:
    with db.tx() as conn:
        return products.save_product(conn, material, grade, manufacturer)["id"]


def _deal(side, who, qty_g, rate, date, wh="Mundra", **extra):
    line = {k: extra.pop(k, LINE[k]) for k in LINE}
    return deals.create_deal(dict(side=side, party_id=party(who), product_id=product(**line),
                                  warehouse_id=warehouse(wh), qty_g=qty_g, rate_paise=rate,
                                  deal_date=date, **extra))


def buy(who, qty_g, rate, date, wh="Mundra", **extra):
    return _deal("buy", who, qty_g, rate, date, wh, **extra)


def sell(who, qty_g, rate, date, wh="Mundra", policy="fifo", **extra):
    return _deal("sell", who, qty_g, rate, date, wh, policy=policy, **extra)


def conserved(test, product_id):
    """Every gram bought (plus found, less written off) is in stock or sold."""
    bought = db.scalar("SELECT COALESCE(SUM(qty_g),0) FROM deals WHERE product_id=? AND side='buy' "
                       "AND status='booked'", (product_id,))
    moves = db.scalar("""SELECT COALESCE(SUM(m.qty_g),0) FROM stock_moves m JOIN lots l ON l.id=m.lot_id
                         WHERE l.product_id=? AND m.kind='adjust' AND m.status='done'""", (product_id,))
    sold = db.scalar("""SELECT COALESCE(SUM(a.qty_g),0) FROM allocations a JOIN lots l ON l.id=a.lot_id
                        WHERE l.product_id=? AND a.active=1""", (product_id,))
    held = db.scalar("SELECT COALESCE(SUM(qty_allocated_g),0) FROM lots WHERE product_id=? "
                     "AND status!='cancelled'", (product_id,))
    stock = db.scalar("SELECT COALESCE(SUM(qty_g-qty_allocated_g-qty_out_g),0) FROM lots "
                      "WHERE product_id=? AND status='open'", (product_id,))
    test.assertEqual(sold, held, "lot.qty_allocated_g out of sync with allocations")
    test.assertEqual(bought + moves, stock + sold, "grams vanished or appeared")
