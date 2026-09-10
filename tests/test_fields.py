"""Sauda No., warehouse, GST flag and payment due date.

    python -m tests.test_fields
"""
import unittest

from tests.common import MT, buy, fresh, sell
from backend import db                                          # noqa: E402
from backend.services import deals, warehouses                 # noqa: E402

LINE = dict(grade="S65", manufacturer="DCW")


class Fields(unittest.TestCase):
    def setUp(self):
        fresh()

    # ------------------------------------------------------------ sauda no
    def test_financial_year_turns_over_in_april(self):
        self.assertEqual(deals.financial_year("2026-03-31"), "25-26")
        self.assertEqual(deals.financial_year("2026-04-01"), "26-27")
        self.assertEqual(deals.financial_year("2099-12-31"), "99-00")

    def test_one_series_across_buys_and_sells_restarting_each_year(self):
        a = buy("A", 10 * MT, 9500, "2026-09-01", **LINE)
        b = sell("B", 2 * MT, 9800, "2026-09-02", **LINE)
        c = buy("C", 5 * MT, 9500, "2027-04-02", **LINE)
        self.assertEqual((a["ref"], b["ref"], c["ref"]),
                         ("LE/26-27/0001", "LE/26-27/0002", "LE/27-28/0001"))

    def test_typed_number_is_kept_and_must_be_unique(self):
        a = buy("A", 10 * MT, 9500, "2026-09-01", sauda_no="LE/26-27/0100", **LINE)
        self.assertEqual(a["ref"], "LE/26-27/0100")
        with self.assertRaises(deals.DealError):
            buy("B", 1 * MT, 9500, "2026-09-01", sauda_no="LE/26-27/0100", **LINE)
        self.assertEqual(buy("C", 1 * MT, 9500, "2026-09-01", **LINE)["ref"], "LE/26-27/0101")

    def test_cancelled_numbers_are_not_reissued(self):
        a = buy("A", 10 * MT, 9500, "2026-09-01", **LINE)
        deals.cancel_deal(a["id"])
        self.assertEqual(buy("B", 1 * MT, 9500, "2026-09-01", **LINE)["ref"], "LE/26-27/0002")

    def test_prefix_comes_from_settings(self):
        with db.tx() as conn:
            db.set_setting(conn, "sauda_prefix", "LX")
        self.assertEqual(buy("A", 1 * MT, 9500, "2026-09-01", **LINE)["ref"], "LX/26-27/0001")

    # ------------------------------------------------------------ warehouse
    def test_purchase_is_received_into_its_warehouse(self):
        a = buy("A", 10 * MT, 9500, "2026-09-01", wh="Mundra", **LINE)
        self.assertEqual((a["warehouse"], a["lot"]["warehouse"]), ("Mundra", "Mundra"))
        rows = {w["name"]: w["stock_g"] for w in warehouses.list_warehouses()["items"]}
        self.assertEqual(rows["Mundra"], 10 * MT)

    def test_a_deal_without_a_warehouse_is_refused(self):
        with self.assertRaises(deals.DealError):
            deals.create_deal(dict(side="buy", party_id=buy("A", MT, 9500, "2026-09-01", **LINE)["party_id"],
                                   product_id=1, qty_g=MT, rate_paise=9500))

    def test_warehouse_holding_stock_cannot_be_removed(self):
        a = buy("A", 5 * MT, 9500, "2026-09-01", wh="Mundra", **LINE)
        with db.tx() as conn:
            empty = warehouses.save_warehouse(conn, "Empty Shed")
        with self.assertRaises(ValueError):
            with db.tx() as conn:
                warehouses.remove_warehouse(conn, a["warehouse_id"])
        with db.tx() as conn:
            warehouses.remove_warehouse(conn, empty)

    def test_warehouse_names_are_unique_ignoring_case(self):
        with db.tx() as conn:
            warehouses.save_warehouse(conn, "Mundra")
        with self.assertRaises(ValueError):
            with db.tx() as conn:
                warehouses.save_warehouse(conn, "mundra")

    # ------------------------------------------------------------ gst + due
    def test_gst_flag_and_due_date_are_recorded(self):
        a = buy("A", 5 * MT, 9500, "2026-09-01", plus_gst=False, payment_due="2026-10-15", **LINE)
        self.assertEqual((a["plus_gst"], a["payment_due"]), (0, "2026-10-15"))
        b = buy("B", 5 * MT, 9500, "2026-09-01", **LINE)
        self.assertEqual((b["plus_gst"], b["payment_due"]), (1, None))

    def test_transporter_is_a_party(self):
        from tests.common import party
        t = party("Ekta Roadways")
        a = buy("A", 5 * MT, 9500, "2026-09-01", transporter_id=t, **LINE)
        self.assertEqual(a["transporter_name"], "Ekta Roadways")
        with self.assertRaises(deals.DealError):
            buy("A", 5 * MT, 9500, "2026-09-01", transporter_id=9999, **LINE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
