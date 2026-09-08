"""Realistic demo book. `python -m backend.seed --reset`

Note the two PVC S65 lines from different manufacturers: same material, same
grade, different maker, and therefore different stock at different prices.
That is the case the whole material -> grade -> manufacturer tree exists for.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

from . import db
from .money import parse_qty, parse_rate
from .services import deals

#     days, supplier,           material, grade,   manufacturer,       qty,     rate,      transporter
BUYS = [
    (-14, "Labdhi Exim",      "PVC",  "HS1000", "Chemplast Sanmar", "20 MT", "98.25+",  "Ekta"),
    (-12, "Shreeji Polymers", "PVC",  "HS1000", "Chemplast Sanmar", "15 MT", "99.10+",  "Ekta"),
    (-11, "Rajdhani Traders", "PVC",  "HS1000", "Chemplast Sanmar", "18 MT", "100.00+", "Sharma Transport"),
    (-10, "Vora Polychem",    "PVC",  "S65",    "Reliance",         "25 MT", "94.50+",  "Ekta"),
    (-8,  "Gokul Plastics",   "PVC",  "S65",    "DCW",              "12 MT", "95.75+",  None),
    (-7,  "Labdhi Exim",      "PP",   "H030SG", "Reliance",         "22 MT", "104.00+", "Sharma Transport"),
    (-5,  "Shreeji Polymers", "PVC",  "HS1000", "Chemplast Sanmar", "10 MT", "97.40+",  None),
    (-3,  "Gokul Plastics",   "HDPE", "50MA180", "GAIL",            "16 MT", "112.25+", "Ekta"),
    (-3,  "Vora Polychem",    "PVC",  "HS1000", "Chemplast Sanmar", "12 MT", "99.10+",  "Ekta"),
    (-2,  "Gokul Plastics",   "PVC",  "HS1000", "Chemplast Sanmar", "9 MT",  "101.50+", None),
]

SELLS = [
    (-9, "Krishna Dehgam",    "PVC",  "HS1000", "Chemplast Sanmar", "12 MT", "102.00", "fifo"),
    (-6, "Mahavir Pipes",     "PVC",  "S65",    "Reliance",         "20 MT", "97.25",  "cheapest"),
    (-4, "Krishna Dehgam",    "PVC",  "HS1000", "Chemplast Sanmar", "16 MT", "101.50", "fifo"),
    (-2, "Ambika Industries", "PP",   "H030SG", "Reliance",         "10 MT", "107.75", "fifo"),
    (-1, "Mahavir Pipes",     "HDPE", "50MA180", "GAIL",            "6 MT",  "115.00", "lifo"),
    (0,  "Shivam Polymers",   "PVC",  "HS1000", "Chemplast Sanmar", "8 MT",  "103.25", "costliest"),
]


def day(offset: int) -> str:
    return (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")


def run(reset: bool = False) -> None:
    if reset:
        db.reset()
    db.init_db()

    rows = [("buy",) + r for r in BUYS] + [("sell",) + r for r in SELLS]
    rows.sort(key=lambda r: r[1])

    for side, off, party, material, grade, maker, qty, rate, extra in rows:
        rate_paise, plus = parse_rate(rate)
        deals.create_deal({
            "side": side,
            "party_name": party,
            "material": material,
            "grade": grade,
            "manufacturer": maker,
            "qty_g": parse_qty(qty),
            "rate_paise": rate_paise,
            "plus_gst": plus,
            "deal_date": day(off),
            "transporter": extra if side == "buy" else None,
            "policy": extra if side == "sell" else None,
            "freight_by": "buyer" if side == "buy" else "seller",
            "delivery_by": "buyer",
            "payment_terms": "30 days" if side == "sell" else "against delivery",
            "eway": "%s to buyer" % party if side == "buy" else None,
            "confirm": True,
        })
    print("Seeded %d deals into %s" % (len(rows), "Postgres" if db.IS_PG else db.DB_PATH))


if __name__ == "__main__":
    run(reset="--reset" in sys.argv)
