"""Labour and freight: ours on both sides, straight off the profit.

They are recorded on the sauda as two whole-deal amounts and counted on its
own date - a purchase's costs reduce profit the day it is bought, and stock
stays valued at the purchase rate. So every figure that says "profit" is
margin less costs, and nothing about stock, lots or allocations moves.

    python -m tests.test_costs
"""
import io
import unittest

from tests.common import MT, buy, conserved, fresh, party, product, sell, warehouse
from tests.test_edit import audit, book
from backend import api, db                                        # noqa: E402
from backend.services import deals, report, revise, stock          # noqa: E402

RS = 100           # paise in a rupee


class Costs(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        fresh()
        self.M, self.A, self.P = warehouse("Mundra"), warehouse("Aslali"), product()

    def deal(self, deal_id):
        return deals.get_deal(deal_id)

    # ------------------------------------------------------------ booking
    def test_a_sale_earns_its_margin_less_its_costs(self):
        buy("S1", 10 * MT, 9500, "2026-09-01")
        s = sell("B1", 4 * MT, 10000, "2026-09-02",
                 labour_paise=2000 * RS, freight_paise=8000 * RS)["id"]
        d = self.deal(s)
        self.assertEqual(d["margin_paise"], 4000 * 500)              # 4 MT at Rs 5/kg = Rs 20,000
        self.assertEqual(d["costs_paise"], 10000 * RS)
        self.assertEqual(d["net_margin_paise"], 20000 * RS - 10000 * RS)
        self.assertEqual(d["margin_rate_paise"], 10000 * RS * 1000 // (4 * MT))   # Rs 2.50/kg
        audit(self)

    def test_a_purchase_costs_are_counted_the_day_it_is_bought(self):
        b = buy("S1", 10 * MT, 9500, "2026-09-01",
                labour_paise=5000 * RS, freight_paise=25000 * RS)["id"]
        self.assertEqual(self.deal(b)["costs_paise"], 30000 * RS)
        self.assertEqual(self.deal(b)["net_margin_paise"], -30000 * RS)
        # the stock itself is untouched: still 10 MT at Rs 95
        lot = db.q1("SELECT qty_g, rate_paise FROM lots WHERE deal_id=?", (b,))
        self.assertEqual((lot["qty_g"], lot["rate_paise"]), (10 * MT, 9500))
        s = api.summary()
        self.assertEqual(s["realised_paise"], -30000 * RS)
        self.assertEqual(s["stock_value_paise"], 10000 * 9500)       # 10 MT at Rs 95, no costs added
        audit(self)

    def test_the_book_adds_up_across_both_sides(self):
        buy("S1", 10 * MT, 9500, "2026-09-01", labour_paise=5000 * RS, freight_paise=25000 * RS)
        sell("B1", 4 * MT, 10000, "2026-09-02", labour_paise=2000 * RS, freight_paise=8000 * RS)
        s = api.summary()
        self.assertEqual(s["costs_paise"], 40000 * RS)
        self.assertEqual(s["realised_paise"], 20000 * RS - 40000 * RS)   # -Rs 20,000
        self.assertEqual(s["realised_today_paise"], 0)                   # both dated earlier
        self.assertEqual(api.get_position(self.P)["realised_paise"], -20000 * RS)
        who = {c["name"]: c["margin_paise"] for c in api.counterparties(limit=10)["items"]}
        self.assertEqual(who, {"B1": 10000 * RS, "S1": -30000 * RS})
        audit(self)

    def test_costs_are_counted_in_the_period_of_their_own_sauda(self):
        from backend.services import dashboard
        buy("S1", 10 * MT, 9500, "2026-09-01", freight_paise=25000 * RS)
        sell("B1", 4 * MT, 10000, "2026-09-20", freight_paise=8000 * RS)
        self.assertEqual(dashboard.realised_between("2026-09-01", "2026-09-05"), -25000 * RS)
        self.assertEqual(dashboard.realised_between("2026-09-06", "2026-09-30"), 20000 * RS - 8000 * RS)
        self.assertEqual(dashboard.realised_between("2026-09-01", "2026-09-30"), -13000 * RS)

    def test_a_negative_amount_is_refused(self):
        with self.assertRaises(deals.DealError):
            buy("S1", 1 * MT, 9500, "2026-09-01", labour_paise=-100)

    def test_costs_do_not_touch_stock_or_open_pnl(self):
        before = book()
        buy("S1", 10 * MT, 9500, "2026-09-01")
        plain = book()
        fresh()
        warehouse("Mundra"), warehouse("Aslali"), product()
        buy("S1", 10 * MT, 9500, "2026-09-01", labour_paise=5000 * RS, freight_paise=25000 * RS)
        withcosts = book()
        self.assertEqual(plain["stock"], withcosts["stock"])
        self.assertEqual(plain["positions"][0]["stock_g"], withcosts["positions"][0]["stock_g"])
        self.assertEqual(plain["positions"][0]["unrealised_paise"], withcosts["positions"][0]["unrealised_paise"])
        self.assertEqual(plain["lots"], withcosts["lots"])
        self.assertNotEqual(before, withcosts)

    # ------------------------------------------------------------ changing them
    def test_costs_can_be_edited_and_the_preview_says_what_changes(self):
        buy("S1", 10 * MT, 9500, "2026-09-01")
        s = sell("B1", 4 * MT, 10000, "2026-09-02", labour_paise=2000 * RS)["id"]
        p = revise.preview_edit(s, {"labour_paise": 3000 * RS, "freight_paise": 9000 * RS})
        self.assertIsNone(p["error"])
        self.assertEqual({c["label"]: (c["before"], c["after"]) for c in p["changes"]},
                         {"Labour": ("₹2,000", "₹3,000"), "Freight": ("₹0", "₹9,000")})
        effect = p["effects"]["sales"][0]
        self.assertEqual((effect["margin_before_paise"], effect["margin_after_paise"]),
                         (18000 * RS, 8000 * RS))
        revise.edit_deal(s, {"labour_paise": 3000 * RS, "freight_paise": 9000 * RS})
        self.assertEqual(self.deal(s)["net_margin_paise"], 8000 * RS)
        self.assertEqual(api.summary()["realised_paise"], 8000 * RS)
        audit(self)

    def test_editing_the_costs_moves_nothing_else(self):
        buy("S1", 10 * MT, 9500, "2026-09-01")
        s = sell("B1", 4 * MT, 10000, "2026-09-02")["id"]
        before = book()
        revise.edit_deal(s, {"freight_paise": 7000 * RS})
        after = book()
        for key in ("stock", "lots", "allocations", "marks", "warehouses"):
            self.assertEqual(before[key], after[key], key)
        self.assertEqual(api.summary()["realised_paise"], 20000 * RS - 7000 * RS)
        audit(self)

    def test_cancelling_takes_its_costs_off_the_book(self):
        buy("S1", 10 * MT, 9500, "2026-09-01", freight_paise=25000 * RS)
        s = sell("B1", 4 * MT, 10000, "2026-09-02", freight_paise=8000 * RS)["id"]
        self.assertEqual(api.summary()["realised_paise"], 20000 * RS - 33000 * RS)
        deals.cancel_deal(s)
        self.assertEqual(api.summary()["realised_paise"], -25000 * RS)    # only the purchase's freight
        self.assertEqual(self.deal(s)["costs_paise"], 0)                  # a cancelled sauda costs nothing
        audit(self)

    def test_undo_takes_them_off_too(self):
        buy("S1", 10 * MT, 9500, "2026-09-01", freight_paise=25000 * RS)
        before = api.summary()["realised_paise"]
        sell("B1", 4 * MT, 10000, "2026-09-02", labour_paise=1000 * RS)
        deals.undo_event(int(deals.last_undoable()["id"]))
        self.assertEqual(api.summary()["realised_paise"], before)
        audit(self)

    def test_a_short_sale_carries_its_costs_too(self):
        s = sell("B1", 3 * MT, 10000, "2026-09-02", allow_short=True, freight_paise=6000 * RS)["id"]
        self.assertEqual((self.deal(s)["margin_paise"], self.deal(s)["net_margin_paise"]), (0, -6000 * RS))
        buy("S1", 3 * MT, 9000, "2026-09-03", freight_paise=3000 * RS)     # covers it
        self.assertEqual(self.deal(s)["net_margin_paise"], 30000 * RS - 6000 * RS)
        self.assertEqual(api.summary()["realised_paise"], 30000 * RS - 9000 * RS)
        audit(self)

    # ------------------------------------------------------------ one purchase, many buyers
    def test_one_purchase_sold_to_three_buyers_counts_its_costs_once(self):
        """A purchase's labour and freight belong to the purchase, however many
        buyers its material goes to; each sale carries only its own."""
        buy("S1", 12 * MT, 9500, "2026-09-01", labour_paise=1200 * RS, freight_paise=28800 * RS)
        a = sell("B1", 5 * MT, 10000, "2026-09-02", labour_paise=500 * RS)["id"]
        b = sell("B2", 4 * MT, 10200, "2026-09-03", freight_paise=4000 * RS)["id"]
        c = sell("B3", 3 * MT, 9900, "2026-09-04", labour_paise=300 * RS, freight_paise=1700 * RS)["id"]

        # each sale: its own kilos at its own rate against the one lot at Rs 95, less its own costs
        self.assertEqual(self.deal(a)["margin_paise"], 5000 * 500)            # Rs 25,000
        self.assertEqual(self.deal(a)["net_margin_paise"], 25000 * RS - 500 * RS)
        self.assertEqual(self.deal(b)["margin_paise"], 4000 * 700)            # Rs 28,000
        self.assertEqual(self.deal(b)["net_margin_paise"], 28000 * RS - 4000 * RS)
        self.assertEqual(self.deal(c)["margin_paise"], 3000 * 400)            # Rs 12,000
        self.assertEqual(self.deal(c)["net_margin_paise"], 12000 * RS - 2000 * RS)

        # the purchase's Rs 30,000 is counted once, not once per buyer
        s = api.summary()
        self.assertEqual(s["costs_paise"], (30000 + 500 + 4000 + 2000) * RS)
        self.assertEqual(s["realised_paise"], (25000 + 28000 + 12000) * RS - (30000 + 6500) * RS)
        self.assertEqual(s["realised_paise"],
                         sum(self.deal(d)["net_margin_paise"] for d in (a, b, c)) - 30000 * RS)
        # and by party: each buyer carries only what their own sauda cost
        who = {p["name"]: p["margin_paise"] for p in api.counterparties(limit=10)["items"]}
        self.assertEqual(who, {"B1": 24500 * RS, "B2": 24000 * RS, "B3": 10000 * RS, "S1": -30000 * RS})
        conserved(self, self.P)
        audit(self)

    def test_the_unsold_part_keeps_no_share_of_the_costs(self):
        """Half the purchase is still in stock: its value and open P&L are the
        rate it was bought at, with no labour or freight folded in."""
        buy("S1", 10 * MT, 9500, "2026-09-01", labour_paise=1000 * RS, freight_paise=24000 * RS)
        sell("B1", 6 * MT, 10000, "2026-09-02", freight_paise=3000 * RS)
        api.set_mark(self.P, api.MarkIn(rate_paise=9800))
        s = api.summary()
        self.assertEqual(s["stock_g"], 4 * MT)
        self.assertEqual(s["stock_value_paise"], 4000 * 9500)                 # 4 MT at Rs 95 flat
        self.assertEqual(s["unrealised_paise"], 4000 * 300)                   # mark 98 less cost 95
        self.assertEqual(s["realised_paise"], 6000 * 500 - 28000 * RS)
        # selling the rest later brings no further costs with it
        sell("B2", 4 * MT, 10100, "2026-09-05")
        self.assertEqual(api.summary()["realised_paise"],
                         (6000 * 500 + 4000 * 600) - 28000 * RS)
        audit(self)

    def test_partial_sales_across_two_purchases_with_costs_on_both(self):
        buy("S1", 5 * MT, 9500, "2026-09-01", freight_paise=5000 * RS)
        buy("S2", 5 * MT, 9700, "2026-09-02", freight_paise=6000 * RS)
        # one sale spanning both lots, FIFO: 5 MT at 95 then 2 MT at 97
        d = sell("B1", 7 * MT, 10100, "2026-09-03", labour_paise=1400 * RS)["id"]
        self.assertEqual(self.deal(d)["margin_paise"], 5000 * 600 + 2000 * 400)
        self.assertEqual(self.deal(d)["net_margin_paise"], (30000 + 8000 - 1400) * RS)
        self.assertEqual(api.summary()["realised_paise"], (38000 - 1400 - 11000) * RS)
        audit(self)

    # ------------------------------------------------------------ cancelling
    def test_cancelling_one_of_three_sales_drops_only_its_costs(self):
        buy("S1", 12 * MT, 9500, "2026-09-01", freight_paise=30000 * RS)
        a = sell("B1", 5 * MT, 10000, "2026-09-02", labour_paise=500 * RS)["id"]
        b = sell("B2", 4 * MT, 10200, "2026-09-03", freight_paise=4000 * RS)["id"]
        c = sell("B3", 3 * MT, 9900, "2026-09-04", freight_paise=2000 * RS)["id"]
        deals.cancel_deal(b)
        # B2's own Rs 4,000 and its Rs 28,000 margin are both gone; the others stand
        self.assertEqual(self.deal(b)["costs_paise"], 0)
        self.assertEqual(self.deal(b)["net_margin_paise"], 0)
        self.assertEqual(self.deal(a)["net_margin_paise"], (25000 - 500) * RS)
        self.assertEqual(self.deal(c)["net_margin_paise"], (12000 - 2000) * RS)
        s = api.summary()
        self.assertEqual(s["costs_paise"], (30000 + 500 + 2000) * RS)
        self.assertEqual(s["realised_paise"], (25000 + 12000 - 32500) * RS)
        self.assertEqual(s["stock_g"], 4 * MT)                    # B2's material is back
        # a party whose only sauda was cancelled is no longer a counterparty at all
        who = {p["name"]: p["margin_paise"] for p in api.counterparties(limit=10)["items"]}
        self.assertNotIn("B2", who)
        self.assertEqual(who["B1"], 24500 * RS)
        audit(self)

    def test_cancelling_the_purchase_drops_its_costs_and_moves_the_sales(self):
        buy("S1", 6 * MT, 9500, "2026-09-01", freight_paise=12000 * RS)      # the one being cancelled
        buy("S2", 6 * MT, 9700, "2026-09-02", freight_paise=9000 * RS)       # what the sales move onto
        a = sell("B1", 3 * MT, 10000, "2026-09-03", labour_paise=600 * RS)["id"]
        b = sell("B2", 3 * MT, 10100, "2026-09-04", freight_paise=900 * RS)["id"]
        p = revise.preview_cancel(1)
        self.assertTrue(p["needs_rehome"])
        promised = {x["ref"]: x["margin_after_paise"] for x in p["effects"]["sales"]}
        deals.cancel_deal(1, rehome=True)
        # both sales now draw on S2's lot at Rs 97, and each keeps its own costs
        self.assertEqual(self.deal(a)["margin_paise"], 3000 * 300)
        self.assertEqual(self.deal(a)["net_margin_paise"], (9000 - 600) * RS)
        self.assertEqual(self.deal(b)["margin_paise"], 3000 * 400)
        self.assertEqual(self.deal(b)["net_margin_paise"], (12000 - 900) * RS)
        self.assertEqual({self.deal(d)["ref"]: self.deal(d)["net_margin_paise"] for d in (a, b)}, promised)
        s = api.summary()
        self.assertEqual(s["costs_paise"], (9000 + 600 + 900) * RS)          # the cancelled Rs 12,000 is gone
        self.assertEqual(s["realised_paise"], (9000 + 12000 - 10500) * RS)
        audit(self)

    def test_cancelling_everything_leaves_nothing_behind(self):
        before = api.summary()
        buy("S1", 8 * MT, 9500, "2026-09-01", labour_paise=800 * RS, freight_paise=20000 * RS)
        a = sell("B1", 3 * MT, 10000, "2026-09-02", freight_paise=3000 * RS)["id"]
        b = sell("B2", 2 * MT, 10100, "2026-09-03", labour_paise=200 * RS)["id"]
        deals.cancel_deal(b)
        deals.cancel_deal(a)
        deals.cancel_deal(1)
        after = api.summary()
        self.assertEqual((after["realised_paise"], after["costs_paise"], after["stock_g"]),
                         (before["realised_paise"], 0, 0))
        audit(self)

    def test_undoing_a_sale_with_costs_puts_the_book_back(self):
        buy("S1", 8 * MT, 9500, "2026-09-01", freight_paise=16000 * RS)
        before = book()
        sell("B1", 3 * MT, 10000, "2026-09-02", labour_paise=300 * RS, freight_paise=2700 * RS)
        deals.undo_event(int(deals.last_undoable()["id"]))
        after = book()
        for key in ("summary", "stock", "positions", "lots", "allocations", "marks"):
            self.assertEqual(before[key], after[key], key)
        self.assertEqual(api.summary()["realised_paise"], -16000 * RS)
        audit(self)

    # ------------------------------------------------------------ the reports
    def test_the_excel_report_shows_and_totals_them(self):
        from openpyxl import load_workbook
        buy("S1", 10 * MT, 9500, "2026-09-01", labour_paise=5000 * RS, freight_paise=25000 * RS)
        sell("B1", 4 * MT, 10000, "2026-09-02", labour_paise=2000 * RS, freight_paise=8000 * RS)
        ws = load_workbook(io.BytesIO(report.tape_workbook()))["Saudas"]
        head = [c.value for c in ws[4]]
        cols = {name: head.index(name) for name in ("Sauda No.", "Labour ₹", "Freight ₹", "Net after costs ₹")}
        rows = {r[cols["Sauda No."]]: r for r in ws.iter_rows(min_row=5, values_only=True)
                if r[1] and str(r[1]).startswith("LE/")}
        self.assertEqual([rows["LE/26-27/0001"][cols[c]] for c in ("Labour ₹", "Freight ₹", "Net after costs ₹")],
                         [5000, 25000, -30000])
        self.assertEqual([rows["LE/26-27/0002"][cols[c]] for c in ("Labour ₹", "Freight ₹", "Net after costs ₹")],
                         [2000, 8000, 10000])
        net = sum(rows[r][cols["Net after costs ₹"]] for r in rows)
        self.assertEqual(net * RS, api.summary()["realised_paise"])
        report.flow_workbook()

    def test_every_screen_agrees_after_costs(self):
        buy("S1", 10 * MT, 9500, "2026-09-01", labour_paise=5000 * RS, freight_paise=25000 * RS)
        buy("S2", 6 * MT, 9700, "2026-09-02", wh="Aslali", freight_paise=4000 * RS)
        sell("B1", 4 * MT, 10000, "2026-09-03", labour_paise=2000 * RS)
        sell("B2", 5 * MT, 10200, "2026-09-04", wh="Aslali", freight_paise=3000 * RS)
        s = api.summary()
        sales = [deals.get_deal(r["id"]) for r in db.q("SELECT id FROM deals WHERE side='sell' AND status='booked'")]
        buys = [deals.get_deal(r["id"]) for r in db.q("SELECT id FROM deals WHERE side='buy' AND status='booked'")]
        self.assertEqual(s["realised_paise"],
                         sum(d["net_margin_paise"] for d in sales) - sum(d["costs_paise"] for d in buys))
        self.assertEqual(s["costs_paise"], sum(d["costs_paise"] for d in sales + buys))
        self.assertEqual(s["realised_paise"],
                         sum(api.get_position(p["id"])["realised_paise"]
                             for p in api.list_products(limit=50)["items"]))
        conserved(self, self.P)
        audit(self)


if __name__ == "__main__":
    unittest.main(verbosity=2)
