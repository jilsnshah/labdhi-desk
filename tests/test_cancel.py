"""Cancelling a deal puts the whole book back exactly as it was.

Every figure a screen can show is snapshotted before a deal is booked, the deal
is booked and then cancelled (by Cancel or by Undo), and the snapshot must match
to the gram and the paisa: summary, realised profit for every period, open P&L,
positions, stock per warehouse, warehouse totals, every lot, the movement
ledger, counterparty profit, marks, the flow graph and the alerts.

    python -m tests.test_cancel
"""
import unittest

from tests.common import MT, buy, fresh, product, sell, warehouse
from backend import api, db                                        # noqa: E402
from backend.services import deals, stock                          # noqa: E402


def snapshot():
    s = api.summary()
    # a cancelled purchase keeps its lot as history (status cancelled); no screen counts it
    lots = db.dicts(db.q("SELECT id, product_id, warehouse_id, qty_g, qty_allocated_g, qty_out_g, status "
                         "FROM lots WHERE status != 'cancelled' ORDER BY id"))
    return {
        "summary": {k: v for k, v in s.items() if k not in ("date", "attention")},
        "attention": [(a["kind"], a.get("deal_id"), a.get("lot_id")) for a in s["attention"]],
        "positions": api.list_positions(include_flat=True, limit=200)["items"],
        "stock": api.list_stock(limit=200)["items"],
        "warehouses": api.list_warehouses(limit=200)["items"],
        "products": api.list_products(limit=200)["items"],
        "parties_holding": [p["id"] for p in api.list_parties(holding=True, limit=200)["items"]],
        "lots": lots,
        "ledger": api.stock_moves(limit=200)["items"],
        "counterparties": api.counterparties(limit=200)["items"],
        "marks": db.dicts(db.q("SELECT product_id, rate_paise, source FROM marks ORDER BY product_id")),
        "graph": {k: v for k, v in api.graph(date_from="2000-01-01").items() if k in ("nodes", "edges")},
        "active_allocations": db.scalar("SELECT COUNT(*) FROM allocations WHERE active=1"),
    }


class Cancel(unittest.TestCase):
    def setUp(self):
        fresh()
        buy("Supplier A", 10 * MT, 9500, "2026-09-01", wh="Mundra")
        buy("Supplier B", 8 * MT, 9700, "2026-09-02", wh="Mundra")
        buy("Supplier C", 5 * MT, 9600, "2026-09-03", wh="Aslali")
        sell("Buyer X", 4 * MT, 10000, "2026-09-04", wh="Mundra")      # an existing sale on the book
        self.pid = product()

    def assertBookEqual(self, before, after):
        for key in before:
            self.assertEqual(before[key], after[key], "%s did not go back" % key)

    def test_cancelling_a_sale_restores_everything(self):
        before = snapshot()
        d = sell("Buyer Y", 9 * MT, 10500, "2026-09-05", wh="Mundra",
                 pins=None, policy="fifo")
        self.assertNotEqual(before["summary"]["realised_paise"], api.summary()["realised_paise"])
        deals.cancel_deal(d["id"])
        self.assertBookEqual(before, snapshot())

    def test_cancelling_a_sale_across_several_lots(self):
        lots = stock.lots_for(self.pid, warehouse("Mundra"))
        before = snapshot()
        d = sell("Buyer Y", 12 * MT, 10500, "2026-09-05", wh="Mundra",
                 pins=[{"lot_id": lots[0]["id"], "qty_g": 6 * MT}, {"lot_id": lots[1]["id"], "qty_g": 6 * MT}])
        self.assertEqual(len(d["allocations"]), 2)
        deals.cancel_deal(d["id"])
        self.assertBookEqual(before, snapshot())

    def test_cancelling_a_purchase_restores_everything(self):
        before = snapshot()
        d = buy("Supplier D", 7 * MT, 9900, "2026-09-05", wh="Aslali")
        deals.cancel_deal(d["id"])
        self.assertBookEqual(before, snapshot())

    def test_undo_restores_everything(self):
        before = snapshot()
        sell("Buyer Y", 5 * MT, 10500, "2026-09-05", wh="Mundra")
        deals.undo_event(int(deals.last_undoable()["id"]))
        self.assertBookEqual(before, snapshot())

    def test_cancelling_older_and_newer_sales_in_any_order(self):
        before = snapshot()
        a = sell("Buyer Y", 3 * MT, 10400, "2026-09-05", wh="Mundra")
        b = sell("Buyer Z", 3 * MT, 10800, "2026-09-06", wh="Mundra")
        deals.cancel_deal(a["id"])                     # the older one first
        deals.cancel_deal(b["id"])
        self.assertBookEqual(before, snapshot())

    def test_a_manual_mark_comes_back_after_a_cancelled_sale(self):
        api.set_mark(self.pid, api.MarkIn(rate_paise=11000))
        before = snapshot()
        d = sell("Buyer Y", 2 * MT, 10500, "2026-09-05", wh="Mundra")
        self.assertEqual(db.q1("SELECT rate_paise FROM marks WHERE product_id=?", (self.pid,))["rate_paise"], 10500)
        deals.cancel_deal(d["id"])
        self.assertBookEqual(before, snapshot())

    def test_cancel_then_undo_does_not_hit_the_cancelled_deal(self):
        # book a purchase, then a sale; cancel the sale from the tape; Undo must
        # now reverse the purchase, not complain that the sale is already gone
        before = snapshot()
        p = buy("Supplier D", 3 * MT, 9900, "2026-09-05", wh="Aslali")
        s = sell("Buyer Y", 2 * MT, 10500, "2026-09-06", wh="Mundra")
        deals.cancel_deal(s["id"])
        undone = deals.undo_event(int(deals.last_undoable()["id"]))
        self.assertEqual(undone["deal"]["id"], p["id"])
        self.assertBookEqual(before, snapshot())

    def test_a_purchase_already_sold_cannot_be_cancelled_and_nothing_changes(self):
        first_buy = db.q1("SELECT id FROM deals WHERE side='buy' ORDER BY id")["id"]
        before = snapshot()
        with self.assertRaises(deals.DealError):
            deals.cancel_deal(first_buy)
        self.assertBookEqual(before, snapshot())


if __name__ == "__main__":
    unittest.main(verbosity=2)
