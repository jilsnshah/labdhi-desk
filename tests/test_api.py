"""Every read path, executed once.

The engine tests cover the arithmetic. They do not execute most of the SQL that
the screens actually run, which is where dialect differences hide: an alias in
HAVING, an alias inside an ORDER BY expression, a 32-bit overflow. Each of those
shipped to production and broke a page while every unit test stayed green.

So this walks the whole API surface against whatever backend is configured.
Run it against Postgres before deploying:

    DATABASE_URL=postgres://... python -m tests.test_api
"""
import os
import tempfile
import unittest

if not os.environ.get("DATABASE_URL"):
    os.environ.setdefault("LABDHI_DB", os.path.join(tempfile.mkdtemp(), "api.db"))

from backend import api, db                                    # noqa: E402
from backend.seed import run as seed_run                       # noqa: E402


class TestEveryEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.reset()
        seed_run()
        cls.sku_id = db.q1("SELECT id FROM skus LIMIT 1")["id"]
        cls.deal_id = db.q1("SELECT id FROM deals WHERE side='sell' LIMIT 1")["id"]
        cls.party_id = db.q1("SELECT id FROM parties LIMIT 1")["id"]

    def test_bootstrap(self):
        b = api.bootstrap()
        for key in ("settings", "desk", "tape", "suppliers", "customers", "materials"):
            self.assertIn(key, b)
        self.assertTrue(b["suppliers"], "supplier chips would be empty")

    def test_desk_and_paging(self):
        first = api.desk(limit=2)
        self.assertLessEqual(len(first["positions"]), 2)
        self.assertGreater(first["positions_matched"], 0)
        second = api.desk(limit=2, offset=2)
        overlap = {p["sku_id"] for p in first["positions"]} & {p["sku_id"] for p in second["positions"]}
        self.assertFalse(overlap, "a page repeated rows")

    def test_desk_filters_and_search(self):
        self.assertTrue(api.desk(q="pvc")["positions"])
        self.assertTrue(api.desk(material="PVC")["positions"])
        self.assertTrue(api.desk(material="PVC", grade="S65")["positions"])
        self.assertIsNotNone(api.desk(supplier_id=self.party_id))

    def test_tape_filters(self):
        self.assertTrue(api.tape()["deals"])
        self.assertTrue(api.tape(side="sell")["deals"])
        self.assertIsNotNone(api.tape(status="booked", date_from="2000-01-01", date_to="2099-01-01"))
        self.assertIsNotNone(api.tape(q="krishna"))
        self.assertIsNotNone(api.tape(material="PVC", grade="HS1000"))

    def test_positions_and_detail(self):
        self.assertTrue(api.positions()["positions"])
        detail = api.position(self.sku_id)
        for key in ("lots", "by_supplier", "graph", "cost_paise"):
            self.assertIn(key, detail)

    def test_graph_windows(self):
        self.assertIsNotNone(api.graph())
        self.assertIsNotNone(api.graph(date_from="2000-01-01", date_to="2099-01-01"))
        self.assertIsNotNone(api.graph(sku_id=self.sku_id))

    def test_catalog_tree_and_options(self):
        self.assertTrue(api.catalog_tree()["tree"])
        mats = api.catalog_options("material")["options"]
        self.assertTrue(mats)
        grades = api.catalog_options("grade", material=mats[0]["value"])["options"]
        self.assertTrue(grades)
        self.assertIsNotNone(
            api.catalog_options("manufacturer", material=mats[0]["value"], grade=grades[0]["value"]))

    def test_searches_and_lots(self):
        self.assertTrue(api.search_parties(role="supplier")["results"])
        self.assertTrue(api.search_parties(role="customer")["results"])
        self.assertIsNotNone(api.search_materials(q="pvc"))
        self.assertIsNotNone(api.lots(self.sku_id))

    def test_deals_and_events(self):
        self.assertTrue(api.list_deals()["deals"])
        self.assertIsNotNone(api.get_deal(self.deal_id))
        self.assertIsNotNone(api.events())
        self.assertIsNotNone(api.trace("sale", self.deal_id))

    def test_sell_preview(self):
        plan = api.preview_sell(api.PreviewIn(sku_id=self.sku_id, qty="1 MT", rate="120"))
        self.assertIn("picks", plan)


if __name__ == "__main__":
    unittest.main(verbosity=2)
