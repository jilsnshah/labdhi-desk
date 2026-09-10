"""Sauda No., warehouse, GST flag and payment due date - and the migration that
adds them to a database created before they existed.

    python -m tests.test_fields
"""
import os
import sqlite3
import tempfile
import unittest

os.environ["LABDHI_DB"] = os.path.join(tempfile.mkdtemp(), "fields.db")

from backend import db                                     # noqa: E402
from backend.services import allocation, catalog, deals    # noqa: E402

MT = 1_000_000
LINE = dict(material="PVC", grade="S65", manufacturer="DCW")


def buy(party, qty_g, rate, date, **extra):
    return deals.create_deal(dict(LINE, side="buy", party_name=party, qty_g=qty_g,
                                  rate_paise=rate, deal_date=date, **extra))


def sell(party, qty_g, rate, date, **extra):
    return deals.create_deal(dict(LINE, side="sell", party_name=party, qty_g=qty_g,
                                  rate_paise=rate, deal_date=date, **extra))


class Fields(unittest.TestCase):
    def setUp(self):
        db.reset()
        db.init_db()

    # ------------------------------------------------------------ sauda no
    def test_financial_year_turns_over_in_april(self):
        self.assertEqual(deals.financial_year("2026-03-31"), "25-26")
        self.assertEqual(deals.financial_year("2026-04-01"), "26-27")
        self.assertEqual(deals.financial_year("2026-09-10"), "26-27")
        self.assertEqual(deals.financial_year("2099-12-31"), "99-00")

    def test_one_series_across_buys_and_sells_restarting_each_year(self):
        a = buy("A", 10 * MT, 9500, "2026-09-01")
        b = sell("B", 2 * MT, 9800, "2026-09-02")
        c = buy("C", 5 * MT, 9500, "2027-04-02")          # next financial year
        self.assertEqual((a["ref"], b["ref"], c["ref"]),
                         ("LE/26-27/0001", "LE/26-27/0002", "LE/27-28/0001"))

    def test_typed_number_is_kept_and_must_be_unique(self):
        a = buy("A", 10 * MT, 9500, "2026-09-01", sauda_no="LE/26-27/0100")
        self.assertEqual(a["ref"], "LE/26-27/0100")
        with self.assertRaises(deals.DealError):
            buy("B", 1 * MT, 9500, "2026-09-01", sauda_no="LE/26-27/0100")
        # the auto series continues past a hand-typed number, never onto it
        self.assertEqual(buy("C", 1 * MT, 9500, "2026-09-01")["ref"], "LE/26-27/0101")

    def test_cancelled_numbers_are_not_reissued(self):
        a = buy("A", 10 * MT, 9500, "2026-09-01")
        deals.cancel_deal(a["id"])
        self.assertEqual(buy("B", 1 * MT, 9500, "2026-09-01")["ref"], "LE/26-27/0002")

    def test_prefix_comes_from_settings(self):
        with db.tx() as conn:
            db.set_setting(conn, "sauda_prefix", "LX")
        self.assertEqual(buy("A", 1 * MT, 9500, "2026-09-01")["ref"], "LX/26-27/0001")

    # ------------------------------------------------------------ warehouse
    def test_purchase_puts_its_lot_in_the_warehouse(self):
        a = buy("A", 10 * MT, 9500, "2026-09-01", warehouse="Mundra")
        self.assertEqual(a["warehouse"], "Mundra")
        self.assertEqual(a["lot"]["warehouse"], "Mundra")
        self.assertIn("Mundra", [w["name"] for w in catalog.list_warehouses()])

    def test_sale_records_where_its_lots_sat(self):
        buy("A", 5 * MT, 9500, "2026-09-01", warehouse="Aslali")
        buy("B", 5 * MT, 9600, "2026-09-02", warehouse="Mundra")
        one = sell("X", 3 * MT, 9900, "2026-09-03", policy="fifo")
        self.assertEqual(one["warehouse"], "Aslali")
        both = sell("Y", 6 * MT, 9900, "2026-09-04", policy="fifo")
        self.assertEqual(both["warehouse"], "Aslali, Mundra")

    def test_lots_can_be_narrowed_to_one_warehouse(self):
        buy("A", 5 * MT, 9500, "2026-09-01", warehouse="Aslali")
        buy("B", 5 * MT, 9600, "2026-09-02", warehouse="Mundra")
        sku = db.q1("SELECT id FROM skus")["id"]
        self.assertEqual({l["warehouse"] for l in allocation.available_lots(sku, warehouse="Mundra")},
                         {"Mundra"})
        self.assertEqual(len(allocation.available_lots(sku)), 2)

    def test_warehouse_holding_stock_cannot_be_removed(self):
        buy("A", 5 * MT, 9500, "2026-09-01", warehouse="Mundra")
        with db.tx() as conn:
            catalog.add_warehouse(conn, "Empty Shed")
        with self.assertRaises(ValueError):
            with db.tx() as conn:
                catalog.remove_warehouse(conn, "Mundra")
        with db.tx() as conn:
            catalog.remove_warehouse(conn, "Empty Shed")

    # ------------------------------------------------------------ gst + due
    def test_gst_flag_and_due_date_are_recorded(self):
        a = buy("A", 5 * MT, 9500, "2026-09-01", plus_gst=False, payment_due="2026-10-15")
        self.assertEqual((a["plus_gst"], a["payment_due"]), (0, "2026-10-15"))
        b = buy("B", 5 * MT, 9500, "2026-09-01")
        self.assertEqual((b["plus_gst"], b["payment_due"]), (1, None))


class Migration(unittest.TestCase):
    """A database created before these columns existed must gain them on boot,
    keep every row it had, and survive being booted again."""

    def test_old_database_is_upgraded_in_place(self):
        if db.IS_PG:
            self.skipTest("builds its own SQLite file")
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        schema = open(db.SCHEMA).read()
        # strip every column added after the first deploy, to get the schema
        # a live database actually has
        added = ("warehouse ", "payment_due ", "ex_place ", "address ", "location ")
        old = "\n".join(l for l in schema.splitlines() if not l.strip().startswith(added))
        raw = sqlite3.connect(path)
        raw.executescript(old)
        raw.execute("INSERT INTO parties(name,slug,city,created_at) VALUES ('P','p','Surat','2026-01-01')")
        raw.execute("INSERT INTO skus(slug,display,material,grade,created_at) "
                    "VALUES ('s','S','PVC','S65','2026-01-01')")
        raw.execute("INSERT INTO deals(ref,side,status,party_id,sku_id,qty_g,rate_paise,"
                    "deal_date,created_at) VALUES ('B-0001','buy','booked',1,1,1000,9500,"
                    "'2026-01-01','2026-01-01')")
        raw.commit(); raw.close()

        saved = db.DB_PATH
        try:
            db.DB_PATH = path
            db._local.__dict__.clear()
            db.init_db()
            db._local.__dict__.clear()
            db.init_db()                                    # idempotent
            conn = db.connect()
            deal_cols = {r["name"] for r in conn.raw.execute("PRAGMA table_info(deals)")}
            lot_cols = {r["name"] for r in conn.raw.execute("PRAGMA table_info(lots)")}
            self.assertTrue({"warehouse", "payment_due"} <= deal_cols)
            self.assertIn("warehouse", lot_cols)
            self.assertEqual(db.q1("SELECT ref, rate_paise FROM deals")["ref"], "B-0001")
            self.assertIn("ex_place", deal_cols)
            wh_cols = {r["name"] for r in conn.raw.execute("PRAGMA table_info(warehouses)")}
            self.assertIn("location", wh_cols)
            # a city recorded before addresses existed is carried into the address
            self.assertEqual(db.q1("SELECT address FROM parties")["address"], "Surat")
        finally:
            db._local.__dict__.clear()
            db.DB_PATH = saved


if __name__ == "__main__":
    unittest.main(verbosity=2)
