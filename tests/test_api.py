"""Every endpoint, executed once, against whatever backend is configured.

The engine tests cover the arithmetic. They do not execute most of the SQL the
screens actually run, which is where dialect differences hide: an alias in
HAVING, an alias inside an ORDER BY expression, a 32-bit overflow. Each of those
once shipped to production and broke a page while every unit test stayed green.

Run it against Postgres before deploying:

    DATABASE_URL=postgres://... python -m tests.test_api
"""
import unittest

from tests.common import MT, fresh                              # noqa: F401
from backend import api, db                                    # noqa: E402
from backend.seed import run as seed_run                       # noqa: E402

PAGE_KEYS = {"items", "total", "limit", "offset", "has_more"}


class TestEveryEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.reset()
        seed_run()
        cls.product_id = db.q1("SELECT product_id FROM deals WHERE side='sell' LIMIT 1")["product_id"]
        cls.sale_id = db.q1("SELECT id FROM deals WHERE side='sell' LIMIT 1")["id"]
        cls.wh = {r["name"]: r["id"] for r in db.q("SELECT id, name FROM warehouses")}

    def page(self, result, what):
        self.assertEqual(set(result) & PAGE_KEYS, PAGE_KEYS, "%s is not a page" % what)
        self.assertLessEqual(len(result["items"]), result["limit"])
        return result

    def test_every_list_is_a_page_and_pages_do_not_overlap(self):
        lists = {
            "parties": lambda **k: api.list_parties(**k),
            "states": lambda **k: api.list_states(**k),
            "warehouses": lambda **k: api.list_warehouses(**k),
            "materials": lambda **k: api.list_materials(**k),
            "grades": lambda **k: api.list_grades(**k),
            "manufacturers": lambda **k: api.list_manufacturers(**k),
            "products": lambda **k: api.list_products(**k),
            "positions": lambda **k: api.list_positions(**k),
            "stock": lambda **k: api.list_stock(**k),
            "moves": lambda **k: api.stock_moves(**k),
            "deals": lambda **k: api.list_deals(**k),
            "counterparties": lambda **k: api.counterparties(**k),
            "events": lambda **k: api.events(**k),
        }
        key = {"positions": "product_id", "stock": ("product_id", "warehouse_id"),
               "moves": ("kind", "lot_id", "move_id", "deal_id"), "states": "code"}
        for name, fn in lists.items():
            first = self.page(fn(limit=2, offset=0), name)
            self.assertGreater(first["total"], 0, "%s is empty in the demo book" % name)
            second = self.page(fn(limit=2, offset=2), name)
            k = key.get(name, "id")
            ident = (lambda r: tuple(r[x] for x in k)) if isinstance(k, tuple) else (lambda r: r[k])
            self.assertFalse({ident(r) for r in first["items"]} & {ident(r) for r in second["items"]},
                             "%s repeated a row across pages" % name)
            self.assertEqual(first["has_more"], first["total"] > 2)

    def test_filters_and_search(self):
        self.assertTrue(api.list_positions(q="pvc")["items"])
        self.assertTrue(api.list_positions(warehouse_id=self.wh["Mundra"])["items"])
        mat = api.list_materials(q="pvc")["items"][0]
        self.assertTrue(api.list_positions(material_id=mat["id"])["items"])
        self.assertTrue(api.list_grades(material_id=mat["id"], in_stock=True)["items"])
        self.assertTrue(api.list_products(warehouse_id=self.wh["Aslali"], in_stock=True)["items"])
        self.assertTrue(api.list_deals(side="sell", status="booked", warehouse_id=self.wh["Mundra"])["items"])
        self.assertIsNotNone(api.list_deals(q="krishna", date_from="2000-01-01", date_to="2099-01-01"))
        self.assertTrue(api.list_parties(holding=True)["items"])
        self.assertTrue(api.list_warehouses(product_id=self.product_id, in_stock=True)["items"])

    def test_tape_rows_carry_every_column(self):
        row = api.list_deals(limit=1)["items"][0]
        for col in ("ref", "deal_date", "side", "party_name", "product", "grade", "qty_g",
                    "rate_paise", "warehouse", "status", "delivery_by", "margin_paise"):
            self.assertIn(col, row)

    def test_details(self):
        detail = api.get_position(self.product_id)
        for key in ("lots", "by_supplier", "warehouses", "cost_paise"):
            self.assertIn(key, detail)
        self.assertTrue(api.get_deal(self.sale_id)["allocations"])
        self.assertIsNotNone(api.trace("sale", self.sale_id))
        self.assertIsNotNone(api.graph())
        self.assertIsNotNone(api.graph(product_id=self.product_id, warehouse_id=self.wh["Mundra"]))
        self.assertTrue(api.stock_lots(self.product_id, self.wh["Mundra"])["items"])
        self.assertIn("summary", api.bootstrap())
        self.assertTrue(api.get_product(self.product_id)["warehouses"])

    def test_master_records_round_trip(self):
        p = api.save_party(api.PartyIn(name="Test Counterparty", gstin="24AHZPG5607M1ZS",
                                       phone="+91 90000 00000", address="Tajpur"))
        self.assertEqual((p["pan"], p["state"]), ("AHZPG5607M", "Gujarat"))
        self.assertEqual(api.check_gstin("24AHZPG5607M1ZS")["party"]["id"], p["id"])
        with self.assertRaises(ValueError):
            api.save_party(api.PartyIn(name="Someone", gstin="24AHZPG5607M1ZS"))
        api.remove_party(p["id"])

        w = api.save_warehouse(api.WarehouseIn(name="Vapi", address="GIDC"))
        w = api.save_warehouse(api.WarehouseIn(id=w["id"], name="Vapi Shed", address="GIDC"))
        self.assertEqual(api.get_warehouse(w["id"])["name"], "Vapi Shed")
        api.remove_warehouse(w["id"])

        made = api.save_product(api.ProductIn(material="lldpe", grade="f19010", manufacturer="Reliance"))
        self.assertEqual((made["display"], made["existed"]), ("LLDPE F19010 · Reliance", False))
        again = api.save_product(api.ProductIn(material="LLDPE", grade="F19010", manufacturer="reliance"))
        self.assertEqual((again["id"], again["existed"]), (made["id"], True))
        api.remove_product(made["id"])

        used = db.q1("SELECT party_id FROM deals LIMIT 1")["party_id"]
        with self.assertRaises(ValueError):
            api.remove_party(used)

    def test_a_deal_by_ids_and_its_undo(self):
        pid = db.q1("SELECT id FROM parties WHERE name='Krishna Dehgam'")["id"]
        lots = api.stock_lots(self.product_id, self.wh["Mundra"])["items"]
        body = api.DealIn(side="sell", party_id=pid, product_id=self.product_id,
                          warehouse_id=self.wh["Mundra"], qty_g=MT, rate_paise=12000,
                          pins=[{"lot_id": l["id"], "qty_g": MT if i == 0 else 0}
                                for i, l in enumerate(lots)])
        deal = api.create_deal(body)
        self.assertEqual((deal["warehouse"], deal["allocations"][0]["lot_id"]), ("Mundra", lots[0]["id"]))
        self.assertEqual(api.undo()["deal"]["status"], "cancelled")

    def test_transfer_adjust_and_undo_over_http(self):
        lot = api.stock_lots(self.product_id, self.wh["Mundra"])["items"][0]
        mv = api.transfer(api.TransferIn(lot_id=lot["id"], to_warehouse_id=self.wh["Aslali"], qty_g=500_000))
        self.assertEqual(mv["to_warehouse"], "Aslali")
        api.cancel_move(mv["id"])
        adj = api.adjust(api.AdjustIn(lot_id=lot["id"], qty_g=-100_000, reason="damage"))
        self.assertEqual(api.undo()["move"]["status"], "cancelled")
        self.assertEqual(adj["qty_g"], -100_000)

    def test_sell_preview(self):
        plan = api.preview_sell(api.PreviewIn(product_id=self.product_id,
                                              warehouse_id=self.wh["Mundra"], qty_g=MT, rate_paise=12000))
        self.assertIn("picks", plan)
        self.assertTrue(all(l["warehouse_id"] == self.wh["Mundra"] for l in plan["lots"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
