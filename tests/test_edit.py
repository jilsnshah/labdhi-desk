"""Editing a sauda leaves the book exactly as if it had been booked that way.

Each test builds the same book twice: once with a deal booked and then edited,
once with the deal booked with the edited values from the start. Every figure
a screen can show must match to the gram and the paisa (see test_cancel's
snapshot), plus the deals themselves and which lot every sale draws on at what
cost. A refused edit must change nothing at all.

Moving sales onto other stock ("rehome") is tested the same way: cancelling a
purchase already sold, with rehome, equals a book where that purchase was
cancelled before those sales were made.

    python -m tests.test_edit
"""
import random
import unittest

from tests.common import MT, buy, conserved, fresh, party, product, sell, warehouse
from tests.test_cancel import snapshot
from backend import api, db                                        # noqa: E402
from backend.services import deals, revise, stock                  # noqa: E402

TIMES = {"ts", "created_at", "booked_at", "updated_at", "cancelled_at", "age_days"}


def _strip(value):
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in TIMES}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def book():
    """Everything a screen shows, plus the deals and the allocations, without clock times."""
    s = snapshot()
    s.pop("active_allocations")
    s["ledger"] = sorted(map(repr, _strip(s["ledger"])))
    # an edge is one allocation row; how many rows carry a lot's grams is bookkeeping
    s["graph"]["edges"] = sorted(repr({k: v for k, v in e.items() if k not in ("id", "method")})
                                 for e in s["graph"]["edges"])
    s["deals"] = [{k: v for k, v in d.items() if k not in TIMES} for d in
                  db.dicts(db.q("SELECT * FROM deals ORDER BY id"))]
    s["lots"] = db.dicts(db.q("SELECT id, label, deal_id, product_id, warehouse_id, supplier_id, rate_paise, "
                              "qty_g, qty_allocated_g, qty_out_g, status FROM lots ORDER BY id"))
    s["allocations"] = [tuple(r.values()) for r in db.dicts(db.q(
        """SELECT sale_deal_id, lot_id, cost_paise, sale_rate_paise, SUM(qty_g) AS g FROM allocations
           WHERE active=1 GROUP BY sale_deal_id, lot_id, cost_paise, sale_rate_paise
           ORDER BY sale_deal_id, lot_id"""))]
    return _strip(s)


def audit(test):
    """The raw rows agree with each other (same checks as the production audit)."""
    for l in db.dicts(db.q("SELECT * FROM lots")):
        held = db.scalar("SELECT COALESCE(SUM(qty_g),0) FROM allocations WHERE lot_id=? AND active=1", (l["id"],))
        test.assertEqual(l["qty_allocated_g"], held, "lot %d counter" % l["id"])
        free = l["qty_g"] - l["qty_allocated_g"] - l["qty_out_g"]
        test.assertGreaterEqual(free, 0, "lot %d negative" % l["id"])
        if l["status"] == "open":
            test.assertGreater(free, 0, "lot %d open but empty" % l["id"])
        if l["status"] == "exhausted":
            test.assertEqual(free, 0, "lot %d exhausted but holds stock" % l["id"])
    for d in db.dicts(db.q("SELECT * FROM deals WHERE status='booked'")):
        if d["side"] == "buy":
            root = db.q1("SELECT * FROM lots WHERE deal_id=? AND parent_lot_id IS NULL", (d["id"],))
            test.assertEqual(root["qty_g"], d["qty_g"])
            test.assertEqual((root["product_id"], root["warehouse_id"], root["supplier_id"], root["rate_paise"]),
                             (d["product_id"], d["warehouse_id"], d["party_id"], d["rate_paise"]))
            continue
        rows = db.dicts(db.q("""SELECT a.*, l.product_id, l.warehouse_id, l.rate_paise FROM allocations a
                                JOIN lots l ON l.id = a.lot_id WHERE a.sale_deal_id=? AND a.active=1""", (d["id"],)))
        test.assertEqual(sum(r["qty_g"] for r in rows) + d["uncovered_g"], d["qty_g"], d["ref"])
        for r in rows:
            test.assertEqual((r["product_id"], r["warehouse_id"]), (d["product_id"], d["warehouse_id"]), d["ref"])
            test.assertEqual(r["cost_paise"], r["rate_paise"], d["ref"])
            test.assertEqual(r["sale_rate_paise"], d["rate_paise"], d["ref"])
    for d in db.dicts(db.q("SELECT id FROM deals WHERE status='cancelled' AND side='sell'")):
        test.assertEqual(db.scalar("SELECT COUNT(*) FROM allocations WHERE sale_deal_id=? AND active=1",
                                   (d["id"],)), 0)
    for pid in [r["id"] for r in db.q("SELECT id FROM products")]:
        conserved(test, pid)


def base():
    """Three purchases in two warehouses and one sale already on the book."""
    fresh()
    a = buy("Supplier A", 10 * MT, 9500, "2026-09-01", wh="Mundra")
    b = buy("Supplier B", 8 * MT, 9700, "2026-09-02", wh="Mundra")
    c = buy("Supplier C", 5 * MT, 9600, "2026-09-03", wh="Aslali")
    s = sell("Buyer X", 4 * MT, 10000, "2026-09-04", wh="Mundra")
    return a, b, c, s


def other_product():
    return product(grade="SG5", manufacturer="Reliance")


class Base(unittest.TestCase):
    maxDiff = None

    def same(self, edited, fresh_book):
        """`edited` builds a book then edits it; `fresh_book` books the edited values directly."""
        edited()
        audit(self)
        got = book()
        fresh_book()
        audit(self)
        want = book()
        for key in want:
            self.assertEqual(want[key], got[key], "%s differs from a fresh booking" % key)

    def unchanged(self, action, error=deals.DealError):
        before = book()
        with self.assertRaises(error):
            action()
        self.assertEqual(before, book())
        audit(self)


class EditSale(Base):
    def test_rate(self):
        def edited():
            base(); d = sell("Buyer Y", 9 * MT, 10500, "2026-09-05")
            revise.edit_deal(d["id"], {"rate_paise": 10720})
        self.same(edited, lambda: (base(), sell("Buyer Y", 9 * MT, 10720, "2026-09-05")))

    def test_rate_of_an_older_sale_leaves_the_mark(self):
        def edited():
            _, _, _, s = base(); sell("Buyer Y", 2 * MT, 10500, "2026-09-05")
            revise.edit_deal(s["id"], {"rate_paise": 9000})
        self.same(edited, lambda: (fresh(), buy("Supplier A", 10 * MT, 9500, "2026-09-01"),
                                   buy("Supplier B", 8 * MT, 9700, "2026-09-02"),
                                   buy("Supplier C", 5 * MT, 9600, "2026-09-03", wh="Aslali"),
                                   sell("Buyer X", 4 * MT, 9000, "2026-09-04"),
                                   sell("Buyer Y", 2 * MT, 10500, "2026-09-05")))
        self.assertEqual(db.q1("SELECT source FROM marks")["source"], "sale LE/26-27/0005")

    def test_party(self):
        def edited():
            base(); d = sell("Buyer Y", 3 * MT, 10500, "2026-09-05")
            revise.edit_deal(d["id"], {"party_id": party("Buyer Z")})
        self.same(edited, lambda: (base(), party("Buyer Y"), sell("Buyer Z", 3 * MT, 10500, "2026-09-05")))

    def test_quantity_down(self):
        def edited():
            base(); d = sell("Buyer Y", 12 * MT, 10500, "2026-09-05")       # 6 of A + 6 of B
            revise.edit_deal(d["id"], {"qty_g": 5 * MT})
        self.same(edited, lambda: (base(), sell("Buyer Y", 5 * MT, 10500, "2026-09-05")))

    def test_quantity_up_across_lots(self):
        def edited():
            base(); d = sell("Buyer Y", 3 * MT, 10500, "2026-09-05")
            revise.edit_deal(d["id"], {"qty_g": 13 * MT, "rate_paise": 10600})
        self.same(edited, lambda: (base(), sell("Buyer Y", 13 * MT, 10600, "2026-09-05")))

    def test_quantity_to_the_last_gram(self):
        def edited():
            base(); d = sell("Buyer Y", 3 * MT, 10500, "2026-09-05")
            revise.edit_deal(d["id"], {"qty_g": 14 * MT})
        self.same(edited, lambda: (base(), sell("Buyer Y", 14 * MT, 10500, "2026-09-05")))
        self.assertEqual(stock.available_in(product(), warehouse("Mundra")), 0)

    def test_quantity_beyond_stock_is_refused(self):
        base(); d = sell("Buyer Y", 3 * MT, 10500, "2026-09-05")
        self.unchanged(lambda: revise.edit_deal(d["id"], {"qty_g": 14 * MT + 1}))

    def test_warehouse(self):
        def edited():
            base(); d = sell("Buyer Y", 3 * MT, 10500, "2026-09-05")
            revise.edit_deal(d["id"], {"warehouse_id": warehouse("Aslali")})
        self.same(edited, lambda: (base(), sell("Buyer Y", 3 * MT, 10500, "2026-09-05", wh="Aslali")))

    def test_warehouse_without_enough_is_refused(self):
        base(); d = sell("Buyer Y", 6 * MT, 10500, "2026-09-05")
        self.unchanged(lambda: revise.edit_deal(d["id"], {"warehouse_id": warehouse("Aslali")}))

    def test_product(self):
        def edited():
            base(); buy("Supplier D", 6 * MT, 8800, "2026-09-04", grade="SG5", manufacturer="Reliance")
            d = sell("Buyer Y", 3 * MT, 10500, "2026-09-05")
            revise.edit_deal(d["id"], {"product_id": other_product(), "rate_paise": 9100})
        self.same(edited, lambda: (base(), buy("Supplier D", 6 * MT, 8800, "2026-09-04", grade="SG5",
                                               manufacturer="Reliance"),
                                   sell("Buyer Y", 3 * MT, 9100, "2026-09-05", grade="SG5",
                                        manufacturer="Reliance")))

    def test_chosen_lots(self):
        def pins():                                   # lot ids match in both books: A is 1, B is 2
            return [{"lot_id": 2, "qty_g": 5 * MT}, {"lot_id": 1, "qty_g": 2 * MT}]

        def edited():
            base(); d = sell("Buyer Y", 7 * MT, 10500, "2026-09-05")
            revise.edit_deal(d["id"], {"pins": pins()})
        self.same(edited, lambda: (base(), sell("Buyer Y", 7 * MT, 10500, "2026-09-05", pins=pins())))

    def test_chosen_lots_may_use_what_the_sale_holds(self):
        base(); d = sell("Buyer Y", 14 * MT, 10500, "2026-09-05")          # everything in Mundra
        lots = revise.lots_for_sale(product(), warehouse("Mundra"), d["id"])
        self.assertEqual(sum(l["available_g"] for l in lots), 14 * MT)
        revise.edit_deal(d["id"], {"pins": [{"lot_id": lots[1]["id"], "qty_g": 8 * MT},
                                            {"lot_id": lots[0]["id"], "qty_g": 6 * MT}]})
        audit(self)
        self.assertEqual(sorted((a["lot_id"], a["qty_g"]) for a in deals.get_deal(d["id"])["allocations"]),
                         sorted([(lots[1]["id"], 8 * MT), (lots[0]["id"], 6 * MT)]))

    def test_paperwork_moves_nothing(self):
        base(); d = sell("Buyer Y", 3 * MT, 10500, "2026-09-05")
        before = book()
        t = party("Fast Roadways")
        revise.edit_deal(d["id"], {"payment_terms": "30 days", "payment_due": "2026-10-05", "ex_place": "Mundra",
                                   "transporter_id": t, "freight_by": "buyer", "delivery_by": "seller",
                                   "eway": "EW123", "remarks": "urgent", "plus_gst": False,
                                   "deal_date": "2026-09-06"})
        after = book()
        for key in before:
            # the date moved, so "last deal" and the dated views follow; stock and money do not
            if key not in ("deals", "counterparties", "ledger", "graph", "warehouses", "products"):
                self.assertEqual(before[key], after[key], key)
        got = deals.get_deal(d["id"])
        self.assertEqual((got["payment_terms"], got["transporter_id"], got["plus_gst"], got["deal_date"]),
                         ("30 days", t, 0, "2026-09-06"))
        audit(self)

    def test_cancelled_deal_cannot_be_edited(self):
        base(); d = sell("Buyer Y", 3 * MT, 10500, "2026-09-05")
        deals.cancel_deal(d["id"])
        self.unchanged(lambda: revise.edit_deal(d["id"], {"rate_paise": 1}))

    def test_undo_after_an_edit_restores_the_book(self):
        base()
        before = book()
        d = sell("Buyer Y", 3 * MT, 10500, "2026-09-05")
        revise.edit_deal(d["id"], {"qty_g": 11 * MT, "rate_paise": 9900})
        deals.undo_event(int(deals.last_undoable()["id"]))
        after = book()
        self.assertEqual(after.pop("deals")[-1]["status"], "cancelled")
        before.pop("deals")
        self.assertEqual(before, after)
        audit(self)

    def test_edit_is_logged(self):
        base(); d = sell("Buyer Y", 3 * MT, 10500, "2026-09-05")
        revise.edit_deal(d["id"], {"rate_paise": 10600})
        ev = db.q1("SELECT * FROM events WHERE action='edit' AND entity_id=?", (d["id"],))
        self.assertIn('"rate_paise": [10500, 10600]', ev["payload"])
        self.assertEqual(ev["undoable"], 0)


class EditPurchase(Base):
    def test_rate_reprices_sales_made_on_it(self):
        def edited():
            a, *_ = base(); sell("Buyer Y", 9 * MT, 10500, "2026-09-05")    # 6 A + 3 B
            revise.edit_deal(a["id"], {"rate_paise": 9350})
        self.same(edited, lambda: (fresh(), buy("Supplier A", 10 * MT, 9350, "2026-09-01"),
                                   buy("Supplier B", 8 * MT, 9700, "2026-09-02"),
                                   buy("Supplier C", 5 * MT, 9600, "2026-09-03", wh="Aslali"),
                                   sell("Buyer X", 4 * MT, 10000, "2026-09-04"),
                                   sell("Buyer Y", 9 * MT, 10500, "2026-09-05")))

    def test_party(self):
        def edited():
            a, *_ = base(); revise.edit_deal(a["id"], {"party_id": party("Supplier Z")})
        self.same(edited, lambda: (fresh(), [party(n) for n in ("Supplier A", "Supplier B", "Supplier C",
                                                                "Buyer X", "Supplier Z")],
                                   buy("Supplier Z", 10 * MT, 9500, "2026-09-01"),
                                   buy("Supplier B", 8 * MT, 9700, "2026-09-02"),
                                   buy("Supplier C", 5 * MT, 9600, "2026-09-03", wh="Aslali"),
                                   sell("Buyer X", 4 * MT, 10000, "2026-09-04")))

    def rebuilt(self, a_qty, a_wh="Mundra", a_product=None, then=()):
        fresh()
        warehouse("Mundra"), warehouse("Aslali"), product()       # same ids as the edited book
        if a_product:
            other_product()
        extra = dict(grade="SG5", manufacturer="Reliance") if a_product else {}
        buy("Supplier A", a_qty, 9500, "2026-09-01", wh=a_wh, **extra)
        buy("Supplier B", 8 * MT, 9700, "2026-09-02")
        buy("Supplier C", 5 * MT, 9600, "2026-09-03", wh="Aslali")
        sell("Buyer X", 4 * MT, 10000, "2026-09-04")
        for step in then:
            step()

    def test_quantity_up(self):
        self.same(lambda: revise.edit_deal(base()[0]["id"], {"qty_g": 12 * MT}),
                  lambda: self.rebuilt(12 * MT))

    def test_quantity_down_to_what_is_sold(self):
        self.same(lambda: revise.edit_deal(base()[0]["id"], {"qty_g": 4 * MT}),
                  lambda: self.rebuilt(4 * MT))
        self.assertEqual(db.q1("SELECT status FROM lots WHERE deal_id=1")["status"], "exhausted")

    def test_quantity_below_sold_needs_rehome_and_changes_nothing(self):
        a, *_ = base(); sell("Buyer Y", 5 * MT, 10500, "2026-09-05")        # A holds 9 sold
        self.unchanged(lambda: revise.edit_deal(a["id"], {"qty_g": 6 * MT}), deals.RehomeNeeded)

    def test_quantity_below_sold_with_rehome(self):
        def edited():
            a, *_ = base(); sell("Buyer Y", 5 * MT, 10500, "2026-09-05")
            revise.edit_deal(a["id"], {"qty_g": 6 * MT}, rehome=True)
        # newest sale moves first: Y keeps 2 on A, 3 go to B - exactly what FIFO does with A at 6
        self.same(edited, lambda: self.rebuilt(6 * MT, then=[lambda: sell("Buyer Y", 5 * MT, 10500, "2026-09-05")]))

    def test_rehome_without_enough_stock_is_refused(self):
        a, *_ = base(); sell("Buyer Y", 12 * MT, 10500, "2026-09-05")       # 6 A + 6 B, B keeps 2
        self.unchanged(lambda: revise.edit_deal(a["id"], {"qty_g": 5 * MT}, rehome=True))

    def test_warehouse_when_nothing_sold(self):
        def edited():
            fresh(); b = buy("Supplier A", 10 * MT, 9500, "2026-09-01")
            revise.edit_deal(b["id"], {"warehouse_id": warehouse("Aslali"), "qty_g": 7 * MT, "rate_paise": 9400})
        self.same(edited, lambda: (fresh(), warehouse("Mundra"),
                                   buy("Supplier A", 7 * MT, 9400, "2026-09-01", wh="Aslali")))

    def test_warehouse_after_sales_needs_rehome(self):
        a, *_ = base()
        self.unchanged(lambda: revise.edit_deal(a["id"], {"warehouse_id": warehouse("Aslali")}),
                       deals.RehomeNeeded)

    def test_warehouse_after_sales_with_rehome(self):
        def edited():
            a, *_ = base(); revise.edit_deal(a["id"], {"warehouse_id": warehouse("Aslali")}, rehome=True)
        self.same(edited, lambda: self.rebuilt(10 * MT, a_wh="Aslali"))

    def test_product_after_sales_with_rehome(self):
        def edited():
            a, *_ = base(); other_product()
            revise.edit_deal(a["id"], {"product_id": other_product()}, rehome=True)
        self.same(edited, lambda: self.rebuilt(10 * MT, a_product=True))

    def test_after_a_transfer_product_and_warehouse_are_refused(self):
        a, *_ = base()
        lot = db.q1("SELECT id FROM lots WHERE deal_id=?", (a["id"],))["id"]
        stock.transfer(lot, warehouse("Aslali"), 2 * MT)
        self.unchanged(lambda: revise.edit_deal(a["id"], {"warehouse_id": warehouse("Aslali")}, rehome=True))
        self.unchanged(lambda: revise.edit_deal(a["id"], {"qty_g": 1 * MT}, rehome=True))   # 2 went out
        revise.edit_deal(a["id"], {"qty_g": 6 * MT, "rate_paise": 9000})                    # 4 sold + 2 out
        audit(self)
        child = db.q1("SELECT rate_paise FROM lots WHERE deal_id=? AND parent_lot_id IS NOT NULL", (a["id"],))
        self.assertEqual(child["rate_paise"], 9000)


class Rehome(Base):
    def test_cancel_a_sold_purchase_by_moving_its_sales(self):
        def edited():
            a, *_ = base(); sell("Buyer Y", 7 * MT, 10500, "2026-09-05")     # 6 A + 1 B
            deals.cancel_deal(a["id"], rehome=True)

        def fresh_book():
            fresh(); a = buy("Supplier A", 10 * MT, 9500, "2026-09-01")
            buy("Supplier B", 8 * MT, 9700, "2026-09-02")
            buy("Supplier C", 5 * MT, 9600, "2026-09-03", wh="Aslali")
            deals.cancel_deal(a["id"])
            sell("Buyer X", 4 * MT, 10000, "2026-09-04")
            sell("Buyer Y", 4 * MT, 10500, "2026-09-05")
        # B holds 8: X takes 4, Y only 4 of its 7 fit - so compare a Y that fits
        def edited_fits():
            a, *_ = base(); sell("Buyer Y", 4 * MT, 10500, "2026-09-05")
            deals.cancel_deal(a["id"], rehome=True)
        self.same(edited_fits, fresh_book)
        base(); a = deals.get_deal(1); sell("Buyer Y", 7 * MT, 10500, "2026-09-05")
        self.unchanged(lambda: deals.cancel_deal(a["id"], rehome=True))

    def test_cancel_without_rehome_is_refused_with_what_is_sold(self):
        a, *_ = base()
        before = book()
        with self.assertRaises(deals.RehomeNeeded) as err:
            deals.cancel_deal(a["id"])
        self.assertEqual(err.exception.grams, 4 * MT)
        self.assertIn("LE/26-27/0004", str(err.exception))
        self.assertEqual(before, book())

    def test_a_purchase_with_transfers_is_still_refused(self):
        a, *_ = base()
        lot = db.q1("SELECT id FROM lots WHERE deal_id=?", (a["id"],))["id"]
        stock.transfer(lot, warehouse("Aslali"), 1 * MT)
        self.unchanged(lambda: deals.cancel_deal(a["id"], rehome=True))

    def test_undo_after_a_rehomed_cancel_moves_on(self):
        a, b, c, s = base()
        deals.cancel_deal(a["id"], rehome=True)
        self.assertEqual(deals.last_undoable()["entity_id"], s["id"])
        audit(self)


class Preview(Base):
    def test_edit_preview_is_what_saving_does_and_saves_nothing(self):
        a, *_ = base(); y = sell("Buyer Y", 9 * MT, 10500, "2026-09-05")
        before = book()
        p = revise.preview_edit(a["id"], {"rate_paise": 9300})
        self.assertEqual(before, book())
        self.assertIsNone(p["error"])
        self.assertEqual([c["field"] for c in p["changes"]], ["rate_paise"])
        margins = {s["id"]: s["margin_after_paise"] for s in p["effects"]["sales"]}
        revise.edit_deal(a["id"], {"rate_paise": 9300})
        for sid, m in margins.items():
            self.assertEqual(deals.get_deal(sid)["margin_paise"], m)
        self.assertEqual(set(margins), {1 + 3, y["id"]})
        self.assertEqual(p["effects"]["realised_after_paise"], api.summary()["realised_paise"])

    def test_edit_preview_offers_rehome(self):
        a, *_ = base(); sell("Buyer Y", 5 * MT, 10500, "2026-09-05")
        before = book()
        p = revise.preview_edit(a["id"], {"qty_g": 6 * MT})
        self.assertEqual(before, book())
        self.assertTrue(p["needs_rehome"])
        self.assertEqual(p["rehome_grams"], 3 * MT)
        self.assertIsNone(p["error"])
        moved = {s["ref"]: (s["cost_before_paise"], s["cost_after_paise"]) for s in p["effects"]["sales"]}
        self.assertEqual(moved, {"LE/26-27/0005": (9500, 9620)})              # 2 @ 95 + 3 @ 97

    def test_cancel_preview(self):
        a, *_ = base(); sell("Buyer Y", 12 * MT, 10500, "2026-09-05")
        before = book()
        p = revise.preview_cancel(a["id"])
        self.assertEqual(before, book())
        self.assertTrue(p["needs_rehome"])
        self.assertIn("Not enough", p["error"])
        p = revise.preview_cancel(4)                                          # a sale: plain cancel
        self.assertFalse(p["needs_rehome"])
        self.assertEqual(p["effects"]["sales"][0]["margin_after_paise"], None)
        self.assertEqual(before, book())

    def test_api(self):
        a, *_ = base()
        p = api.edit_preview(a["id"], api.EditIn(qty_g=2 * MT))
        self.assertTrue(p["needs_rehome"])
        api.edit(a["id"], api.EditIn(qty_g=2 * MT, rehome=True, remarks="short shipped"))
        got = deals.get_deal(a["id"])
        self.assertEqual((got["qty_g"], got["remarks"]), (2 * MT, "short shipped"))
        api.edit(a["id"], api.EditIn(remarks=None))
        self.assertIsNone(deals.get_deal(a["id"])["remarks"])
        self.assertEqual(deals.get_deal(a["id"])["qty_g"], 2 * MT)
        audit(self)


class Random(Base):
    """Hundreds of random edits and cancels; the rows must agree after every one."""

    def test_random_edits_keep_the_book_whole(self):
        for seed in range(6):
            with self.subTest(seed=seed):
                self.run_seed(seed)

    def run_seed(self, seed):
        rng = random.Random(seed)
        fresh()
        whs = ["Mundra", "Aslali"]
        prods = [product(), other_product()]
        for i in range(8):
            extra = {} if i % 3 else dict(grade="SG5", manufacturer="Reliance")
            buy("S%d" % i, rng.randint(2, 12) * MT, rng.randint(9000, 10000), "2026-09-%02d" % (i + 1),
                wh=rng.choice(whs), **extra)
        done = 0
        for step in range(250):
            live = db.dicts(db.q("SELECT * FROM deals WHERE status='booked'"))
            kind = rng.random()
            try:
                if kind < 0.3:
                    pid, wid = rng.choice(prods), warehouse(rng.choice(whs))
                    free = stock.available_in(pid, wid)
                    if free > 0:
                        deals.create_deal(dict(side="sell", party_id=party("B%d" % rng.randint(1, 4)),
                                               product_id=pid, warehouse_id=wid,
                                               qty_g=rng.randint(1, free), rate_paise=rng.randint(9000, 11000),
                                               deal_date="2026-09-20"))
                elif kind < 0.4:
                    buy("S%d" % step, rng.randint(1, 6) * MT, rng.randint(9000, 10000), "2026-09-15",
                        wh=rng.choice(whs))
                else:
                    d = rng.choice(live)
                    field = rng.choice(["qty_g", "rate_paise", "warehouse_id", "product_id", "cancel", "pins"])
                    if field == "pins" and d["side"] == "sell":
                        lots = revise.lots_for_sale(d["product_id"], d["warehouse_id"], d["id"])
                        rng.shuffle(lots)
                        left, pins = d["qty_g"], []
                        for l in lots:
                            take = min(l["available_g"], rng.randint(0, left))
                            pins.append({"lot_id": l["id"], "qty_g": take})
                            left -= take
                        if left and lots:
                            pins[-1]["qty_g"] += left          # may overshoot what is there: refused
                        revise.edit_deal(d["id"], {"pins": pins, "qty_g": d["qty_g"]})
                        done += 1
                        audit(self)
                        continue
                    if field == "pins":
                        field = "rate_paise"
                    if field == "cancel":
                        deals.cancel_deal(d["id"], rehome=rng.random() < 0.7)
                    else:
                        value = {"qty_g": max(1, d["qty_g"] + rng.randint(-3 * MT, 3 * MT)),
                                 "rate_paise": rng.randint(8500, 11500),
                                 "warehouse_id": warehouse(rng.choice(whs)),
                                 "product_id": rng.choice(prods)}[field]
                        revise.edit_deal(d["id"], {field: value}, rehome=rng.random() < 0.7)
                done += 1
            except deals.DealError:
                pass
            audit(self)
        self.assertGreater(done, 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
