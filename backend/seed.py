"""Realistic demo book. `python -m backend.seed --reset`

Everything is created the way the app creates it: parties, warehouses and
products as records first, then deals that point at them by id.

Note the two PVC S65 products from different manufacturers: same material,
same grade, different maker, and therefore different stock at different
prices. And note HS1000 held in two warehouses: a sale ships from one of them
and can only draw on the lots sitting there.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

from . import db
from .money import parse_qty, parse_rate
from .services import deals, parties, products, stock, warehouses

WAREHOUSES = [("Mundra", "Plot 12, Mundra Port, Kutch"), ("Aslali", "Aslali GIDC, Ahmedabad")]
TRANSPORTERS = ["Ekta Roadways", "Sharma Transport"]

#     days, supplier,           material, grade,    manufacturer,       qty,     rate,      warehouse, transporter
BUYS = [
    (-14, "Labdhi Exim",      "PVC",  "HS1000",  "Chemplast Sanmar", "20 MT", "98.25+",  "Mundra", "Ekta Roadways"),
    (-12, "Shreeji Polymers", "PVC",  "HS1000",  "Chemplast Sanmar", "15 MT", "99.10+",  "Mundra", "Ekta Roadways"),
    (-11, "Rajdhani Traders", "PVC",  "HS1000",  "Chemplast Sanmar", "18 MT", "100.00+", "Aslali", "Sharma Transport"),
    (-10, "Vora Polychem",    "PVC",  "S65",     "Reliance",         "25 MT", "94.50+",  "Mundra", "Ekta Roadways"),
    (-8,  "Gokul Plastics",   "PVC",  "S65",     "DCW",              "12 MT", "95.75+",  "Aslali", None),
    (-7,  "Labdhi Exim",      "PP",   "H030SG",  "Reliance",         "22 MT", "104.00+", "Aslali", "Sharma Transport"),
    (-5,  "Shreeji Polymers", "PVC",  "HS1000",  "Chemplast Sanmar", "10 MT", "97.40+",  "Mundra", None),
    (-3,  "Gokul Plastics",   "HDPE", "50MA180", "GAIL",             "16 MT", "112.25+", "Mundra", "Ekta Roadways"),
    (-3,  "Vora Polychem",    "PVC",  "HS1000",  "Chemplast Sanmar", "12 MT", "99.10+",  "Aslali", "Ekta Roadways"),
    (-2,  "Gokul Plastics",   "PVC",  "HS1000",  "Chemplast Sanmar", "9 MT",  "101.50+", "Aslali", None),
]

#     days, buyer,              material, grade,    manufacturer,       qty,     rate,     warehouse, policy
SELLS = [
    (-9, "Krishna Dehgam",    "PVC",  "HS1000",  "Chemplast Sanmar", "12 MT", "102.00", "Mundra", "fifo"),
    (-6, "Mahavir Pipes",     "PVC",  "S65",     "Reliance",         "20 MT", "97.25",  "Mundra", "cheapest"),
    (-4, "Krishna Dehgam",    "PVC",  "HS1000",  "Chemplast Sanmar", "16 MT", "101.50", "Aslali", "fifo"),
    (-2, "Ambika Industries", "PP",   "H030SG",  "Reliance",         "10 MT", "107.75", "Aslali", "fifo"),
    (-1, "Mahavir Pipes",     "HDPE", "50MA180", "GAIL",             "6 MT",  "115.00", "Mundra", "lifo"),
    (0,  "Shivam Polymers",   "PVC",  "HS1000",  "Chemplast Sanmar", "8 MT",  "103.25", "Mundra", "costliest"),
]


def day(offset: int) -> str:
    return (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")


def run(reset: bool = False) -> None:
    if reset:
        db.reset()
    db.init_db()

    ids = {"party": {}, "wh": {}, "product": {}}
    with db.tx() as conn:
        for name, address in WAREHOUSES:
            ids["wh"][name] = warehouses.save_warehouse(conn, name, address)
        names = {r[1] for r in BUYS + SELLS} | set(TRANSPORTERS)
        for name in sorted(names):
            ids["party"][name] = parties.save_party(conn, name, merge=True, quiet=True)
        for r in BUYS + SELLS:
            key = (r[2], r[3], r[4])
            if key not in ids["product"]:
                ids["product"][key] = products.save_product(conn, *key)["id"]

    rows = [("buy",) + r for r in BUYS] + [("sell",) + r for r in SELLS]
    rows.sort(key=lambda r: r[1])
    for side, off, party, material, grade, maker, qty, rate, wh, extra in rows:
        rate_paise, plus = parse_rate(rate)
        deals.create_deal({
            "side": side,
            "party_id": ids["party"][party],
            "product_id": ids["product"][(material, grade, maker)],
            "warehouse_id": ids["wh"][wh],
            "qty_g": parse_qty(qty),
            "rate_paise": rate_paise,
            "plus_gst": plus,
            "deal_date": day(off),
            "transporter_id": ids["party"][extra] if side == "buy" and extra else None,
            "policy": extra if side == "sell" else None,
            "freight_by": "Buyer" if side == "buy" else "Seller",
            "delivery_by": "Buyer",
            "payment_terms": "30 days" if side == "sell" else "against delivery",
            "confirm": True,
        })

    # One transfer, so the movement ledger has something beyond buys and sells.
    lot = stock.lots_for(ids["product"][("PVC", "HS1000", "Chemplast Sanmar")], ids["wh"]["Mundra"])[-1]
    stock.transfer(lot["id"], ids["wh"]["Aslali"], min(4 * 10**6, lot["available_g"]), day(-1),
                   "Balancing godowns")
    print("Seeded %d deals into %s" % (len(rows), "Postgres" if db.IS_PG else db.DB_PATH))


if __name__ == "__main__":
    run(reset="--reset" in sys.argv)
