"""Warehouse-wise stock: sales ship from one warehouse, transfers and adjustments
move grams without losing where they came from, and the ledger adds up.

    python -m tests.test_stock
"""
import unittest

from tests.common import MT, buy, conserved, fresh, product, sell, warehouse
from backend import db                                                   # noqa: E402
from backend.services import deals, inventory, stock                    # noqa: E402


class WarehouseStock(unittest.TestCase):
    def setUp(self):
        fresh()
        buy("A", 5 * MT, 9500, "2026-09-01", wh="Aslali")
        buy("B", 5 * MT, 9600, "2026-09-02", wh="Mundra")
        self.pid = product()
        self.aslali, self.mundra = warehouse("Aslali"), warehouse("Mundra")

    def test_stock_is_kept_per_warehouse(self):
        rows = {r["warehouse"]: r["stock_g"] for r in stock.stock_rows()["items"]}
        self.assertEqual(rows, {"Aslali": 5 * MT, "Mundra": 5 * MT})
        self.assertEqual(stock.available_in(self.pid, self.mundra), 5 * MT)

    def test_a_sale_draws_only_on_its_warehouse(self):
        sale = sell("X", 3 * MT, 9900, "2026-09-03", wh="Mundra", policy="fifo")
        self.assertEqual({a["warehouse"] for a in sale["allocations"]}, {"Mundra"})
        self.assertEqual(sale["warehouse"], "Mundra")
        self.assertEqual(stock.available_in(self.pid, self.aslali), 5 * MT)

    def test_a_sale_cannot_exceed_its_warehouse(self):
        with self.assertRaises(deals.DealError) as err:
            sell("X", 6 * MT, 9900, "2026-09-03", wh="Mundra")
        self.assertIn("Mundra holds 5 MT", str(err.exception))

    def test_a_lot_from_another_warehouse_is_refused(self):
        aslali_lot = stock.lots_for(self.pid, self.aslali)[0]["id"]
        with self.assertRaises(deals.DealError):
            sell("X", 2 * MT, 9900, "2026-09-03", wh="Mundra",
                 pins=[{"lot_id": aslali_lot, "qty_g": 2 * MT}])

    def test_transfer_moves_grams_and_keeps_their_origin(self):
        lot = stock.lots_for(self.pid, self.mundra)[0]
        move = stock.transfer(lot["id"], self.aslali, 2 * MT, "2026-09-04", "rebalance")
        self.assertEqual((move["from_warehouse"], move["to_warehouse"]), ("Mundra", "Aslali"))
        self.assertEqual(stock.available_in(self.pid, self.mundra), 3 * MT)
        self.assertEqual(stock.available_in(self.pid, self.aslali), 7 * MT)
        child = db.q1("SELECT * FROM lots WHERE id=?", (move["to_lot_id"],))
        self.assertEqual((child["parent_lot_id"], child["rate_paise"], child["deal_id"]),
                         (lot["id"], lot["rate_paise"], lot["deal_id"]))
        # sold from Aslali, the grams still trace to B's purchase
        sale = sell("Y", 7 * MT, 9900, "2026-09-05", wh="Aslali")
        self.assertEqual({a["supplier_name"] for a in sale["allocations"]}, {"A", "B"})
        g = inventory.graph(self.pid)
        self.assertEqual(len([n for n in g["nodes"] if n["kind"] == "lot"]), 2, "a transfer is not a purchase")
        conserved(self, self.pid)

    def test_transfer_guards(self):
        lot = stock.lots_for(self.pid, self.mundra)[0]
        for args in ((lot["id"], self.mundra, MT), (lot["id"], self.aslali, 6 * MT),
                     (lot["id"], self.aslali, 0)):
            with self.assertRaises(ValueError):
                stock.transfer(*args)

    def test_adjustments_write_off_and_find(self):
        lot = stock.lots_for(self.pid, self.mundra)[0]
        stock.adjust(lot["id"], -500_000, "2026-09-04", "Shortage at weighbridge")
        self.assertEqual(stock.available_in(self.pid, self.mundra), 4_500_000)
        found = stock.adjust(lot["id"], 200_000, "2026-09-05", "Found in godown")
        self.assertEqual(stock.available_in(self.pid, self.mundra), 4_700_000)
        conserved(self, self.pid)
        stock.cancel_move(found["id"])
        self.assertEqual(stock.available_in(self.pid, self.mundra), 4_500_000)
        with self.assertRaises(ValueError):
            stock.adjust(lot["id"], -9 * MT)
        conserved(self, self.pid)

    def test_a_used_transfer_cannot_be_undone(self):
        lot = stock.lots_for(self.pid, self.mundra)[0]
        move = stock.transfer(lot["id"], self.aslali, 2 * MT)
        sell("Y", 7 * MT, 9900, "2026-09-05", wh="Aslali")
        with self.assertRaises(ValueError):
            stock.cancel_move(move["id"])

    def test_undo_takes_back_a_transfer(self):
        lot = stock.lots_for(self.pid, self.mundra)[0]
        stock.transfer(lot["id"], self.aslali, 2 * MT)
        deals.undo_event(int(deals.last_undoable()["id"]))
        self.assertEqual(stock.available_in(self.pid, self.mundra), 5 * MT)
        self.assertEqual(stock.available_in(self.pid, self.aslali), 5 * MT)

    def test_a_moved_purchase_cannot_be_cancelled(self):
        lot = stock.lots_for(self.pid, self.mundra)[0]
        stock.transfer(lot["id"], self.aslali, MT)
        with self.assertRaises(deals.DealError):
            deals.cancel_deal(lot["deal_id"])

    def test_ledger_walks_back_to_zero(self):
        lot = stock.lots_for(self.pid, self.mundra)[0]
        stock.transfer(lot["id"], self.aslali, 2 * MT, "2026-09-04")
        stock.adjust(lot["id"], -MT, "2026-09-05", "damage")
        sell("X", 2 * MT, 9900, "2026-09-06", wh="Mundra")
        for wid in (self.mundra, self.aslali, None):
            full = stock.movements(self.pid, wid, limit=200)["items"]
            now = stock.available_in(self.pid, wid) if wid else sum(
                stock.available_in(self.pid, w) for w in (self.mundra, self.aslali))
            self.assertEqual(full[0]["balance_g"], now)
            self.assertEqual(full[-1]["balance_g"] - full[-1]["qty_g"], 0, "ledger does not start at zero")
            # a later page picks up exactly where the first one stopped
            p2 = stock.movements(self.pid, wid, limit=2, offset=2)["items"]
            if p2:
                self.assertEqual(p2[0]["balance_g"], full[2]["balance_g"])
        kinds = {r["kind"] for r in stock.movements(self.pid, self.mundra, limit=200)["items"]}
        self.assertEqual(kinds, {"receipt", "transfer_out", "loss", "sale"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
