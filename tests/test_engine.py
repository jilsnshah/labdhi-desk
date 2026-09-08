"""Invariants that must never break. Run: .venv/bin/python -m tests.test_engine"""
import os
import tempfile
import unittest

os.environ["LABDHI_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

from backend import db                                   # noqa: E402
from backend.money import parse_qty, parse_rate, value_paise, weighted_rate  # noqa: E402
from backend.services import allocation, catalog, deals, inventory  # noqa: E402

MT = 1_000_000


# Inventory is kept per material x grade x manufacturer, so every helper names
# all three. MAKER defaults to one manufacturer; the tree tests vary it.
LINE = dict(material="PVC", grade="HS1000", manufacturer="Chemplast Sanmar")


def buy(party, qty_g, rate_paise, date, **line):
    return deals.create_deal(dict(LINE, side="buy", party_name=party, qty_g=qty_g,
                                  rate_paise=rate_paise, deal_date=date, **line))


def sell(party, qty_g, rate_paise, date, policy="fifo", allow_short=False, pins=None, **line):
    return deals.create_deal(dict(LINE, side="sell", party_name=party, qty_g=qty_g,
                                  rate_paise=rate_paise, deal_date=date, policy=policy,
                                  allow_short=allow_short, pins=pins, **line))


class Base(unittest.TestCase):
    def setUp(self):
        for suffix in ("", "-wal", "-shm"):
            p = db.DB_PATH + suffix
            if os.path.exists(p):
                os.remove(p)
        db._local.__dict__.clear()
        db.init_db()

    def sku_id(self, material="PVC", grade="HS1000", manufacturer="Chemplast Sanmar"):
        return db.q1("SELECT id FROM skus WHERE material=? AND grade=? AND manufacturer=?",
                     (material, grade, manufacturer))["id"]

    def assert_conserved(self, sku_id):
        """Every gram bought is either still in stock or allocated to a sale."""
        bought = db.scalar("SELECT COALESCE(SUM(qty_g),0) FROM lots WHERE sku_id=? AND status!='cancelled'", (sku_id,))
        allocated = db.scalar(
            "SELECT COALESCE(SUM(a.qty_g),0) FROM allocations a JOIN lots l ON l.id=a.lot_id "
            "WHERE l.sku_id=? AND a.active=1", (sku_id,))
        held = db.scalar("SELECT COALESCE(SUM(qty_allocated_g),0) FROM lots WHERE sku_id=? AND status!='cancelled'", (sku_id,))
        stock = sum(l["available_g"] for l in inventory.open_lots(sku_id))
        self.assertEqual(allocated, held, "lot.qty_allocated_g out of sync with allocations")
        self.assertEqual(bought, allocated + stock, "grams vanished or appeared")



class TestMoney(unittest.TestCase):
    def test_qty_units(self):
        self.assertEqual(parse_qty("20,000 kg"), 20 * MT)
        self.assertEqual(parse_qty("20 MT"), 20 * MT)
        self.assertEqual(parse_qty("20t"), 20 * MT)
        self.assertEqual(parse_qty("40 bags"), 1_000_000)

    def test_rate_plus_means_ex_gst(self):
        self.assertEqual(parse_rate("98.25+"), (9825, True))
        self.assertEqual(parse_rate("102"), (10200, False))

    def test_value_is_exact(self):
        self.assertEqual(value_paise(20 * MT, 9825), 196_500_000)  # 20,000 kg x Rs 98.25 = Rs 19,65,000
        self.assertEqual(value_paise(1, 1), 0)          # half-up on a sub-paise crumb
        self.assertEqual(value_paise(500, 1), 1)

    def test_weighted_average(self):
        self.assertEqual(weighted_rate([(5 * MT, 9800), (5 * MT, 9900)]), 9850)


class TestAllocation(Base):
    def stock_book(self):
        buy("Supplier A", 5 * MT, 9800, "2026-01-01")
        buy("Supplier B", 8 * MT, 9900, "2026-01-02")
        buy("Supplier C", 7 * MT, 10000, "2026-01-03")
        return self.sku_id()

    def test_fifo_consumes_oldest_first(self):
        sku = self.stock_book()
        plan = allocation.suggest(sku, 10 * MT, "fifo")
        self.assertEqual([(p["supplier_name"], p["qty_g"]) for p in plan["picks"]],
                         [("Supplier A", 5 * MT), ("Supplier B", 5 * MT)])
        self.assertEqual(plan["uncovered_g"], 0)
        self.assertEqual(plan["avg_cost_paise"], 9850)

    def test_cheapest_and_costliest_differ(self):
        sku = self.stock_book()
        cheap = allocation.preview(sku, 10 * MT, 10200, "cheapest")
        dear = allocation.preview(sku, 10 * MT, 10200, "costliest")
        self.assertGreater(cheap["margin_paise"], dear["margin_paise"])
        self.assertEqual(cheap["picks"][0]["cost_paise"], 9800)
        self.assertEqual(dear["picks"][0]["cost_paise"], 10000)

    def test_spread_touches_every_lot(self):
        sku = self.stock_book()
        plan = allocation.suggest(sku, 10 * MT, "spread")
        self.assertEqual(len(plan["picks"]), 3)
        self.assertEqual(plan["covered_g"], 10 * MT)

    def test_pins_win_then_engine_fills(self):
        sku = self.stock_book()
        lots = {l["supplier_name"]: l["id"] for l in allocation.available_lots(sku)}
        plan = allocation.suggest(sku, 10 * MT, "fifo",
                                  pins=[{"lot_id": lots["Supplier C"], "qty_g": 7 * MT}])
        first = plan["picks"][0]
        self.assertEqual(first["supplier_name"], "Supplier C")
        self.assertEqual(first["qty_g"], 7 * MT)
        self.assertEqual(plan["covered_g"], 10 * MT)

    def test_a_chosen_quantity_is_never_topped_up(self):
        # He asks for exactly 2 MT out of Supplier A. The engine must not put
        # A's other 3 MT back into the sale just because it needs the grams.
        sku = self.stock_book()
        lots = {l["supplier_name"]: l["id"] for l in allocation.available_lots(sku)}
        plan = allocation.suggest(sku, 20 * MT, "fifo",
                                  pins=[{"lot_id": lots["Supplier A"], "qty_g": 2 * MT}])
        taken = {p["supplier_name"]: p["qty_g"] for p in plan["picks"]}
        self.assertEqual(taken["Supplier A"], 2 * MT)
        self.assertEqual(taken["Supplier B"], 8 * MT)
        self.assertEqual(taken["Supplier C"], 7 * MT)
        self.assertEqual(plan["uncovered_g"], 3 * MT)   # surfaced, not hidden

    def test_choosing_zero_excludes_a_lot(self):
        sku = self.stock_book()
        lots = {l["supplier_name"]: l["id"] for l in allocation.available_lots(sku)}
        plan = allocation.suggest(sku, 10 * MT, "fifo",
                                  pins=[{"lot_id": lots["Supplier A"], "qty_g": 0}])
        self.assertNotIn("Supplier A", [p["supplier_name"] for p in plan["picks"]])
        self.assertEqual(plan["covered_g"], 10 * MT)

    def test_sale_drains_lots_and_conserves(self):
        sku = self.stock_book()
        sale = sell("Krishna", 10 * MT, 10200, "2026-01-04")
        self.assertEqual(sale["margin_paise"], value_paise(5 * MT, 400) + value_paise(5 * MT, 300))
        left = {l["supplier_name"]: l["available_g"] for l in inventory.open_lots(sku)}
        self.assertEqual(left, {"Supplier B": 3 * MT, "Supplier C": 7 * MT})
        self.assert_conserved(sku)

    def test_short_sale_blocked_then_allowed_and_flagged(self):
        buy("Supplier A", 5 * MT, 9800, "2026-01-01")
        with db.tx() as conn:
            db.set_setting(conn, "allow_short_sales", "0")
        with self.assertRaises(deals.DealError):
            sell("Krishna", 9 * MT, 10200, "2026-01-02")
        sale = sell("Krishna", 9 * MT, 10200, "2026-01-02", allow_short=True)
        self.assertEqual(sale["uncovered_g"], 4 * MT)
        self.assertEqual(sum(a["qty_g"] for a in sale["allocations"]), 5 * MT)

    def test_cannot_cancel_a_purchase_already_sold(self):
        sku = self.stock_book()
        sell("Krishna", 6 * MT, 10200, "2026-01-04")
        buy_deal = db.q1("SELECT id FROM deals WHERE side='buy' ORDER BY id")["id"]
        with self.assertRaises(deals.DealError):
            deals.cancel_deal(buy_deal)
        self.assert_conserved(sku)

    def test_cancelling_a_sale_returns_the_stock(self):
        sku = self.stock_book()
        sale = sell("Krishna", 10 * MT, 10200, "2026-01-04")
        deals.cancel_deal(sale["id"])
        self.assertEqual(sum(l["available_g"] for l in inventory.open_lots(sku)), 20 * MT)
        self.assert_conserved(sku)

    def test_reallocate_keeps_totals_and_history(self):
        sku = self.stock_book()
        sale = sell("Krishna", 10 * MT, 10200, "2026-01-04", policy="fifo")
        lots = {l["supplier_name"]: l["id"] for l in allocation.available_lots(sku)}
        moved = deals.reallocate(sale["id"], pins=[{"lot_id": lots["Supplier C"], "qty_g": 7 * MT}])
        self.assertEqual(sum(a["qty_g"] for a in moved["allocations"]), 10 * MT)
        self.assertEqual(moved["allocations"][0]["supplier_name"], "Supplier C")
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM allocations WHERE active=0"), 2)
        self.assert_conserved(sku)

    def test_undo_reverses_the_last_booking(self):
        sku = self.stock_book()
        sell("Krishna", 10 * MT, 10200, "2026-01-04")
        ev = deals.last_undoable()
        deals.undo_event(int(ev["id"]))
        self.assertEqual(sum(l["available_g"] for l in inventory.open_lots(sku)), 20 * MT)
        self.assert_conserved(sku)

    def test_lineage_is_traceable_both_ways(self):
        sku = self.stock_book()
        sale = sell("Krishna", 10 * MT, 10200, "2026-01-04")
        lot_id = sale["allocations"][0]["lot_id"]
        forward = inventory.trace("lot", lot_id)["node"]
        self.assertEqual(forward["outflows"][0]["customer_name"], "Krishna")
        back = inventory.trace("sale", sale["id"])["node"]
        self.assertEqual({a["supplier_name"] for a in back["allocations"]},
                         {"Supplier A", "Supplier B"})
        g = inventory.graph(sku)
        self.assertEqual(len(g["nodes"]), 4)
        self.assertEqual(sum(e["qty_g"] for e in g["edges"]), 10 * MT)

    def test_margin_equals_sale_minus_cost_across_lots(self):
        sku = self.stock_book()
        sale = sell("Krishna", 12 * MT, 10150, "2026-01-04")
        expected = sum(value_paise(a["qty_g"], 10150 - a["cost_paise"]) for a in sale["allocations"])
        self.assertEqual(sale["margin_paise"], expected)
        self.assertEqual(inventory.realised_for_sku(sku), expected)


    def test_cancelled_sale_stops_pricing_the_book(self):
        sku = self.stock_book()
        first = sell("Krishna", 4 * MT, 10100, "2026-01-04")
        second = sell("Mahavir", 4 * MT, 10500, "2026-01-05")
        self.assertEqual(db.q1("SELECT rate_paise FROM marks WHERE sku_id=?", (sku,))["rate_paise"], 10500)
        deals.cancel_deal(second["id"])
        self.assertEqual(db.q1("SELECT rate_paise FROM marks WHERE sku_id=?", (sku,))["rate_paise"], 10100)
        deals.cancel_deal(first["id"])
        self.assertIsNone(db.q1("SELECT rate_paise FROM marks WHERE sku_id=?", (sku,)))

    def test_same_grade_from_two_makers_is_two_positions(self):
        # PVC S65 from Reliance and from DCW are different stock at different
        # prices. They must never pool into one position.
        buy("Vora", 10 * MT, 9450, "2026-01-01", grade="S65", manufacturer="Reliance")
        buy("Gokul", 6 * MT, 9575, "2026-01-02", grade="S65", manufacturer="DCW")
        positions = {p["material"]: p for p in inventory.positions()}
        self.assertIn("PVC S65 \u00b7 Reliance", positions)
        self.assertIn("PVC S65 \u00b7 DCW", positions)
        self.assertEqual(positions["PVC S65 \u00b7 Reliance"]["stock_g"], 10 * MT)
        self.assertEqual(positions["PVC S65 \u00b7 DCW"]["stock_g"], 6 * MT)

        # Selling the Reliance line must not touch a single DCW kilo.
        sold = sell("Mahavir", 8 * MT, 9800, "2026-01-03", grade="S65", manufacturer="Reliance")
        self.assertEqual([a["supplier_name"] for a in sold["allocations"]], ["Vora"])
        self.assert_conserved(self.sku_id(grade="S65", manufacturer="DCW"))
        self.assert_conserved(self.sku_id(grade="S65", manufacturer="Reliance"))

    def test_catalogue_tree_and_removal_guard(self):
        buy("Vora", 5 * MT, 9800, "2026-01-01")
        tree = {m["material"]: m for m in catalog.tree()}
        self.assertIn("PVC", tree)
        grades = {g["grade"]: g for g in tree["PVC"]["grades"]}
        self.assertIn("HS1000", grades)
        self.assertEqual([k["manufacturer"] for k in grades["HS1000"]["manufacturers"]],
                         ["Chemplast Sanmar"])

        with db.tx() as conn:
            catalog.add_maker(conn, "PVC", "HS1000", "Finolex")   # never traded
        with db.tx() as conn:
            catalog.remove(conn, "PVC", "HS1000", "Finolex")      # so it can go

        with self.assertRaises(ValueError):                        # this one cannot
            with db.tx() as conn:
                catalog.remove(conn, "PVC", "HS1000", "Chemplast Sanmar")


if __name__ == "__main__":
    unittest.main(verbosity=2)
