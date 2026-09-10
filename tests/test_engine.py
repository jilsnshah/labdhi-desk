"""Invariants that must never break. Run: .venv/bin/python -m tests.test_engine"""
import unittest

from tests.common import MT, buy, conserved, fresh, product, sell, warehouse
from backend import db                                                    # noqa: E402
from backend.money import parse_qty, parse_rate, value_paise, weighted_rate  # noqa: E402
from backend.services import allocation, deals, inventory, products     # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        fresh()


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
        self.assertEqual(value_paise(20 * MT, 9825), 196_500_000)
        self.assertEqual(value_paise(1, 1), 0)
        self.assertEqual(value_paise(500, 1), 1)

    def test_weighted_average(self):
        self.assertEqual(weighted_rate([(5 * MT, 9800), (5 * MT, 9900)]), 9850)


class TestAllocation(Base):
    def stock_book(self):
        buy("Supplier A", 5 * MT, 9800, "2026-01-01")
        buy("Supplier B", 8 * MT, 9900, "2026-01-02")
        buy("Supplier C", 7 * MT, 10000, "2026-01-03")
        return product()

    def lots(self, pid):
        return {l["supplier_name"]: l["id"] for l in inventory.open_lots(pid)}

    def test_fifo_consumes_oldest_first(self):
        pid = self.stock_book()
        plan = allocation.suggest(pid, 10 * MT, "fifo")
        self.assertEqual([(p["supplier_name"], p["qty_g"]) for p in plan["picks"]],
                         [("Supplier A", 5 * MT), ("Supplier B", 5 * MT)])
        self.assertEqual(plan["uncovered_g"], 0)
        self.assertEqual(plan["avg_cost_paise"], 9850)

    def test_cheapest_and_costliest_differ(self):
        pid = self.stock_book()
        cheap = allocation.preview(pid, 10 * MT, 10200, "cheapest")
        dear = allocation.preview(pid, 10 * MT, 10200, "costliest")
        self.assertGreater(cheap["margin_paise"], dear["margin_paise"])

    def test_spread_touches_every_lot(self):
        pid = self.stock_book()
        plan = allocation.suggest(pid, 10 * MT, "spread")
        self.assertEqual(len(plan["picks"]), 3)
        self.assertEqual(plan["covered_g"], 10 * MT)

    def test_pins_win_then_engine_fills(self):
        pid = self.stock_book()
        plan = allocation.suggest(pid, 10 * MT, "fifo",
                                  pins=[{"lot_id": self.lots(pid)["Supplier C"], "qty_g": 7 * MT}])
        self.assertEqual((plan["picks"][0]["supplier_name"], plan["picks"][0]["qty_g"]),
                         ("Supplier C", 7 * MT))
        self.assertEqual(plan["covered_g"], 10 * MT)

    def test_a_chosen_quantity_is_never_topped_up(self):
        pid = self.stock_book()
        plan = allocation.suggest(pid, 20 * MT, "fifo",
                                  pins=[{"lot_id": self.lots(pid)["Supplier A"], "qty_g": 2 * MT}])
        taken = {p["supplier_name"]: p["qty_g"] for p in plan["picks"]}
        self.assertEqual(taken, {"Supplier A": 2 * MT, "Supplier B": 8 * MT, "Supplier C": 7 * MT})
        self.assertEqual(plan["uncovered_g"], 3 * MT)

    def test_choosing_zero_excludes_a_lot(self):
        pid = self.stock_book()
        plan = allocation.suggest(pid, 10 * MT, "fifo",
                                  pins=[{"lot_id": self.lots(pid)["Supplier A"], "qty_g": 0}])
        self.assertNotIn("Supplier A", [p["supplier_name"] for p in plan["picks"]])

    def test_sale_drains_lots_and_conserves(self):
        pid = self.stock_book()
        sale = sell("Krishna", 10 * MT, 10200, "2026-01-04")
        self.assertEqual(sale["margin_paise"], value_paise(5 * MT, 400) + value_paise(5 * MT, 300))
        left = {l["supplier_name"]: l["available_g"] for l in inventory.open_lots(pid)}
        self.assertEqual(left, {"Supplier B": 3 * MT, "Supplier C": 7 * MT})
        conserved(self, pid)

    def test_short_sale_is_refused(self):
        buy("Supplier A", 5 * MT, 9800, "2026-01-01")
        with self.assertRaises(deals.DealError) as err:
            sell("Krishna", 9 * MT, 10200, "2026-01-02")
        self.assertIn("Mundra holds 5 MT", str(err.exception))

    def test_cannot_cancel_a_purchase_already_sold(self):
        pid = self.stock_book()
        sell("Krishna", 6 * MT, 10200, "2026-01-04")
        buy_deal = db.q1("SELECT id FROM deals WHERE side='buy' ORDER BY id")["id"]
        with self.assertRaises(deals.DealError):
            deals.cancel_deal(buy_deal)
        conserved(self, pid)

    def test_cancelling_a_sale_returns_the_stock(self):
        pid = self.stock_book()
        sale = sell("Krishna", 10 * MT, 10200, "2026-01-04")
        deals.cancel_deal(sale["id"])
        self.assertEqual(sum(l["available_g"] for l in inventory.open_lots(pid)), 20 * MT)
        conserved(self, pid)

    def test_reallocate_keeps_totals_and_history(self):
        pid = self.stock_book()
        sale = sell("Krishna", 10 * MT, 10200, "2026-01-04")
        moved = deals.reallocate(sale["id"], pins=[{"lot_id": self.lots(pid)["Supplier C"], "qty_g": 7 * MT}])
        self.assertEqual(sum(a["qty_g"] for a in moved["allocations"]), 10 * MT)
        self.assertEqual(moved["allocations"][0]["supplier_name"], "Supplier C")
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM allocations WHERE active=0"), 2)
        conserved(self, pid)

    def test_undo_reverses_the_last_booking(self):
        pid = self.stock_book()
        sell("Krishna", 10 * MT, 10200, "2026-01-04")
        deals.undo_event(int(deals.last_undoable()["id"]))
        self.assertEqual(sum(l["available_g"] for l in inventory.open_lots(pid)), 20 * MT)
        conserved(self, pid)

    def test_lineage_is_traceable_both_ways(self):
        pid = self.stock_book()
        sale = sell("Krishna", 10 * MT, 10200, "2026-01-04")
        forward = inventory.trace("lot", sale["allocations"][0]["lot_id"])["node"]
        self.assertEqual(forward["outflows"][0]["customer_name"], "Krishna")
        back = inventory.trace("sale", sale["id"])["node"]
        self.assertEqual({a["supplier_name"] for a in back["allocations"]}, {"Supplier A", "Supplier B"})
        g = inventory.graph(pid)
        self.assertEqual(len(g["nodes"]), 4)
        self.assertEqual(sum(e["qty_g"] for e in g["edges"]), 10 * MT)

    def test_margin_equals_sale_minus_cost_across_lots(self):
        pid = self.stock_book()
        sale = sell("Krishna", 12 * MT, 10150, "2026-01-04")
        expected = sum(value_paise(a["qty_g"], 10150 - a["cost_paise"]) for a in sale["allocations"])
        self.assertEqual(sale["margin_paise"], expected)
        self.assertEqual(inventory.realised_for_product(pid), expected)

    def test_cancelled_sale_stops_pricing_the_book(self):
        pid = self.stock_book()
        first = sell("Krishna", 4 * MT, 10100, "2026-01-04")
        second = sell("Mahavir", 4 * MT, 10500, "2026-01-05")
        mark = lambda: db.q1("SELECT rate_paise FROM marks WHERE product_id=?", (pid,))   # noqa: E731
        self.assertEqual(mark()["rate_paise"], 10500)
        deals.cancel_deal(second["id"])
        self.assertEqual(mark()["rate_paise"], 10100)
        deals.cancel_deal(first["id"])
        self.assertIsNone(mark())

    def test_same_grade_from_two_makers_is_two_products(self):
        buy("Vora", 10 * MT, 9450, "2026-01-01", grade="S65", manufacturer="Reliance")
        buy("Gokul", 6 * MT, 9575, "2026-01-02", grade="S65", manufacturer="DCW")
        pos = {p["product"]: p for p in inventory.positions()["items"]}
        self.assertEqual(pos["PVC S65 · Reliance"]["stock_g"], 10 * MT)
        self.assertEqual(pos["PVC S65 · DCW"]["stock_g"], 6 * MT)
        sold = sell("Mahavir", 8 * MT, 9800, "2026-01-03", grade="S65", manufacturer="Reliance")
        self.assertEqual([a["supplier_name"] for a in sold["allocations"]], ["Vora"])
        conserved(self, product(grade="S65", manufacturer="DCW"))
        conserved(self, product(grade="S65", manufacturer="Reliance"))


class TestEntities(Base):
    """Deals point at records; nothing about a record is copied into a deal."""

    def test_a_deal_needs_real_records(self):
        with self.assertRaises(deals.DealError):
            deals.create_deal(dict(side="buy", party_id=999, product_id=product(),
                                   warehouse_id=warehouse("Mundra"), qty_g=MT, rate_paise=9500))
        with self.assertRaises(deals.DealError):
            deals.create_deal(dict(side="buy", party_name="Typed Name", product_id=product(),
                                   warehouse_id=warehouse("Mundra"), qty_g=MT, rate_paise=9500))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM parties"), 0, "a typed name made a party")

    def test_renaming_a_record_renames_it_everywhere(self):
        d = buy("Vora", 5 * MT, 9500, "2026-01-01", manufacturer="Relaince")
        with db.tx() as conn:
            products.save_manufacturer(conn, "Reliance", d["manufacturer_id"])
            from backend.services import warehouses
            warehouses.save_warehouse(conn, "Mundra Port", None, d["warehouse_id"])
        again = deals.get_deal(d["id"])
        self.assertEqual((again["manufacturer"], again["warehouse"]), ("Reliance", "Mundra Port"))
        self.assertEqual(again["lot"]["warehouse"], "Mundra Port")

    def test_one_product_per_material_grade_maker(self):
        a = product("pvc ", "hs1000", "Chemplast Sanmar")
        b = product("PVC", "HS1000", "chemplast sanmar")
        self.assertEqual(a, b)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM products"), 1)

    def test_traded_records_cannot_be_removed(self):
        d = buy("Vora", 5 * MT, 9500, "2026-01-01")
        from backend.services import parties, warehouses
        for fn, arg in ((parties.remove_party, d["party_id"]),
                        (warehouses.remove_warehouse, d["warehouse_id"]),
                        (products.remove_product, d["product_id"]),
                        (products.remove_manufacturer, d["manufacturer_id"]),
                        (products.remove_material, d["material_id"])):
            with self.assertRaises(ValueError):
                with db.tx() as conn:
                    fn(conn, arg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
