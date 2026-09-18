"""Selling short: the book stays exact whatever order things happen in.

The central check is order-independence. Selling 3 MT short and then buying
10 MT must leave the book exactly as buying 10 MT and then selling 3 MT does:
the same lots, the same cost on the sale, the same margin, the same stock on
every screen. Every way stock can arrive - a purchase, a transfer, a find, a
cancelled or reduced sale, an edited purchase - is checked the same way, and
so is every way a short can reappear - an undone or cancelled purchase.

    python -m tests.test_shorts
"""
import random
import unittest

from tests.common import MT, buy, fresh, party, product, sell, warehouse
from tests.test_edit import Base, audit, book, other_product
from backend import api, db                                        # noqa: E402
from backend.services import deals, revise, stock                  # noqa: E402


REFS = {"ref", "deal_ref", "label", "mark_source", "source"}


def norefs(value, drop=REFS | {"id", "deal_id", "lot_id"}):
    """Sauda numbers and row ids follow booking order, which these tests change on purpose."""
    if isinstance(value, dict):
        return {k: norefs(v, drop) for k, v in value.items() if k not in drop}
    if isinstance(value, list):
        return [norefs(v, drop) for v in value]
    return value


def short_of(deal_id):
    return db.q1("SELECT uncovered_g FROM deals WHERE id=?", (deal_id,))["uncovered_g"]


def lot_of(deal_id):
    return db.q1("SELECT id FROM lots WHERE deal_id=? AND parent_lot_id IS NULL", (deal_id,))["id"]


def setup_book():
    """Mundra holds 5 MT of HS1000; Aslali holds 6 MT."""
    fresh()
    warehouse("Mundra"), warehouse("Aslali"), product()
    buy("Supplier A", 5 * MT, 9500, "2026-09-01", wh="Mundra")
    buy("Supplier C", 6 * MT, 9600, "2026-09-02", wh="Aslali")


class Booking(Base):
    def test_short_must_be_asked_for(self):
        setup_book()
        self.unchanged(lambda: sell("Buyer X", 8 * MT, 10000, "2026-09-03"))

    def test_short_only_once_the_warehouse_is_empty(self):
        setup_book()
        buy("Supplier B", 2 * MT, 9700, "2026-09-02", wh="Mundra")
        first = lot_of(1)
        # takes 3 of A's 5 - refused, a short must take everything the warehouse holds first
        self.unchanged(lambda: sell("Buyer X", 8 * MT, 10000, "2026-09-03", allow_short=True,
                                    pins=[{"lot_id": first, "qty_g": 3 * MT}]))
        d = sell("Buyer X", 8 * MT, 10000, "2026-09-03", allow_short=True)
        self.assertEqual(short_of(d["id"]), 1 * MT)
        self.assertEqual(stock.available_in(product(), warehouse("Mundra")), 0)
        audit(self)

    def test_screens_show_negative_stock(self):
        setup_book()
        d = sell("Buyer X", 8 * MT, 10000, "2026-09-03", allow_short=True)
        api.set_mark(product(), api.MarkIn(rate_paise=9800))
        cells = {r["warehouse"]: (r["stock_g"], r["short_g"]) for r in api.list_stock(limit=50)["items"]}
        self.assertEqual(cells, {"Mundra": (-3 * MT, 3 * MT), "Aslali": (6 * MT, 0)})
        s = api.summary()
        self.assertEqual(s["stock_g"], 3 * MT)                              # 6 in Aslali less 3 short
        # open P&L: Aslali 6 MT at 98 - 96, and the short 3 MT sold at 100 against a mark of 98
        self.assertEqual(s["unrealised_paise"], 6 * 1000 * 200 + 3 * 1000 * 200)
        # realised: only what is covered - 5 MT at 100 - 95
        self.assertEqual(s["realised_paise"], 5 * 1000 * 500)
        pos = api.list_positions(limit=10)["items"][0]
        self.assertEqual((pos["stock_g"], pos["short_g"]), (3 * MT, 3 * MT))
        self.assertEqual({w["name"]: w["stock_g"] for w in pos["warehouses"]},
                         {"Aslali": 6 * MT, "Mundra": -3 * MT})
        detail = api.get_position(product())
        self.assertEqual([x["ref"] for x in detail["shorts"]], [d["ref"]])
        g = deals.get_deal(d["id"])
        self.assertEqual((g["short_g"], g["short_est_paise"]), (3 * MT, 3 * 1000 * 200))
        whs = {w["name"]: w["stock_g"] for w in api.list_warehouses(limit=10)["items"]}
        self.assertEqual(whs, {"Mundra": -3 * MT, "Aslali": 6 * MT})
        audit(self)

    def test_a_short_still_shows_on_the_desk(self):
        setup_book()
        sell("Buyer X", 8 * MT, 10000, "2026-09-03", allow_short=True)
        alert = [a for a in api.summary()["attention"] if a["kind"] == "short"][0]
        self.assertIn("3 MT sold short", alert["title"])


class Covering(Base):
    """Stock arriving covers a short, and the result equals having bought first."""

    def test_short_then_buy_equals_buy_then_sell(self):
        def short_first():
            setup_book()
            party("Buyer X"), party("Supplier B")
            sell("Buyer X", 8 * MT, 10000, "2026-09-03", allow_short=True)
            buy("Supplier B", 10 * MT, 9700, "2026-09-04", wh="Mundra")

        def buy_first():
            setup_book()
            party("Buyer X"), party("Supplier B")
            b = buy("Supplier B", 10 * MT, 9700, "2026-09-04", wh="Mundra")
            sell("Buyer X", 8 * MT, 10000, "2026-09-03")
            return b
        short_first()
        audit(self)
        self.assertEqual(short_of(3), 0)
        # 5 at 95 + 3 at 97, the margin a buy-then-sell would have booked
        self.assertEqual(deals.get_deal(3)["margin_paise"], 5 * 1000 * 500 + 3 * 1000 * 300)
        got = book()
        buy_first()
        want = book()
        # the deal rows differ only in id order; compare every figure a screen shows
        for key in ("summary", "positions", "stock", "warehouses", "lots", "marks"):
            if key == "lots":
                self.assertEqual(sorted((l["qty_g"], l["qty_allocated_g"], l["rate_paise"]) for l in want[key]),
                                 sorted((l["qty_g"], l["qty_allocated_g"], l["rate_paise"]) for l in got[key]))
            else:
                self.assertEqual(norefs(want[key]), norefs(got[key]), key)

    def test_a_small_buy_covers_part_and_the_oldest_short_first(self):
        setup_book()
        x = sell("Buyer X", 8 * MT, 10000, "2026-09-03", allow_short=True)      # 3 short
        y = sell("Buyer Y", 2 * MT, 10100, "2026-09-04", allow_short=True)      # 2 short
        buy("Supplier B", 4 * MT, 9700, "2026-09-05", wh="Mundra")
        self.assertEqual((short_of(x["id"]), short_of(y["id"])), (0, 1 * MT))
        buy("Supplier D", 4 * MT, 9900, "2026-09-06", wh="Mundra")
        self.assertEqual((short_of(x["id"]), short_of(y["id"])), (0, 0))
        self.assertEqual(stock.available_in(product(), warehouse("Mundra")), 3 * MT)
        cover = db.q1("SELECT summary FROM events WHERE action='cover' ORDER BY id")["summary"]
        self.assertIn(x["ref"], cover)
        audit(self)

    def test_a_purchase_elsewhere_does_not_cover(self):
        setup_book()
        x = sell("Buyer X", 8 * MT, 10000, "2026-09-03", allow_short=True)
        buy("Supplier B", 4 * MT, 9700, "2026-09-05", wh="Aslali")
        buy("Supplier D", 4 * MT, 9700, "2026-09-05", wh="Mundra", grade="SG5", manufacturer="Reliance")
        self.assertEqual(short_of(x["id"]), 3 * MT)
        audit(self)

    def test_a_transfer_in_covers(self):
        setup_book()
        x = sell("Buyer X", 8 * MT, 10000, "2026-09-03", allow_short=True)
        stock.transfer(lot_of(2), warehouse("Mundra"), 4 * MT)
        self.assertEqual(short_of(x["id"]), 0)
        self.assertEqual(stock.available_in(product(), warehouse("Mundra")), 1 * MT)
        # the covered part came from C, at C's cost
        self.assertEqual(deals.get_deal(x["id"])["margin_paise"], 5 * 1000 * 500 + 3 * 1000 * 400)
        audit(self)
        # and that transfer can no longer be undone: its stock is sold
        with self.assertRaises(deals.DealError):
            deals.undo_event(int(deals.last_undoable()["id"]))
        audit(self)

    def test_found_stock_covers(self):
        setup_book()
        x = sell("Buyer X", 8 * MT, 10000, "2026-09-03", allow_short=True)
        stock.adjust(lot_of(1), 2 * MT, reason="recount")
        self.assertEqual(short_of(x["id"]), 1 * MT)
        audit(self)

    def test_a_cancelled_sale_hands_its_stock_to_the_short(self):
        def edited():
            setup_book(); party("Buyer Y")
            a = sell("Buyer X", 5 * MT, 10000, "2026-09-03")
            sell("Buyer Y", 3 * MT, 10200, "2026-09-04", allow_short=True)
            deals.cancel_deal(a["id"])

        def fresh_book():
            setup_book(); party("Buyer Y")
            a = sell("Buyer X", 5 * MT, 10000, "2026-09-03")
            deals.cancel_deal(a["id"])
            sell("Buyer Y", 3 * MT, 10200, "2026-09-04")
        self.same(edited, fresh_book)

    def test_a_reduced_sale_hands_its_stock_to_the_short(self):
        def edited():
            setup_book(); party("Buyer Y")
            a = sell("Buyer X", 5 * MT, 10000, "2026-09-03")
            sell("Buyer Y", 3 * MT, 10200, "2026-09-04", allow_short=True)
            revise.edit_deal(a["id"], {"qty_g": 1 * MT})

        def fresh_book():
            setup_book(); party("Buyer Y")
            sell("Buyer X", 1 * MT, 10000, "2026-09-03")
            sell("Buyer Y", 3 * MT, 10200, "2026-09-04")
        self.same(edited, fresh_book)

    def test_a_bigger_purchase_covers(self):
        def edited():
            setup_book()
            sell("Buyer X", 8 * MT, 10000, "2026-09-03", allow_short=True)
            revise.edit_deal(1, {"qty_g": 9 * MT})

        def fresh_book():
            fresh(); warehouse("Mundra"), warehouse("Aslali"), product()
            buy("Supplier A", 9 * MT, 9500, "2026-09-01", wh="Mundra")
            buy("Supplier C", 6 * MT, 9600, "2026-09-02", wh="Aslali")
            sell("Buyer X", 8 * MT, 10000, "2026-09-03")
        self.same(edited, fresh_book)

    def test_a_purchase_moved_into_the_warehouse_covers(self):
        def edited():
            setup_book()
            sell("Buyer X", 8 * MT, 10000, "2026-09-03", allow_short=True)
            revise.edit_deal(2, {"warehouse_id": warehouse("Mundra")})

        def fresh_book():
            fresh(); warehouse("Mundra"), warehouse("Aslali"), product()
            buy("Supplier A", 5 * MT, 9500, "2026-09-01", wh="Mundra")
            buy("Supplier C", 6 * MT, 9600, "2026-09-02", wh="Mundra")
            sell("Buyer X", 8 * MT, 10000, "2026-09-03")
        self.same(edited, fresh_book)


class Reopening(Base):
    """Taking stock away that covered a short makes it short again - exactly."""

    def test_undoing_the_covering_purchase_reopens_the_short(self):
        setup_book()
        sell("Buyer X", 8 * MT, 10000, "2026-09-03", allow_short=True)
        before = book()
        buy("Supplier B", 10 * MT, 9700, "2026-09-04", wh="Mundra")
        deals.undo_event(int(deals.last_undoable()["id"]))
        after = book()
        before.pop("deals"), after.pop("deals"), before.pop("parties_holding"), after.pop("parties_holding")
        after["lots"] = [l for l in after["lots"] if l["status"] != "cancelled"]
        self.assertEqual(before, after)
        audit(self)

    def test_undo_will_not_quietly_move_a_normal_sale(self):
        setup_book()
        b = buy("Supplier B", 10 * MT, 9700, "2026-09-04", wh="Mundra")
        s = sell("Buyer X", 8 * MT, 10000, "2026-09-05")
        with db.tx() as conn:           # the sale's own booking already undone some other way
            conn.execute("UPDATE events SET undone=1 WHERE entity='deal' AND entity_id=? AND action='book'", (s["id"],))
        self.unchanged(lambda: deals.undo_event(int(deals.last_undoable()["id"])), deals.RehomeNeeded)
        self.assertEqual(deals.last_undoable()["entity_id"], b["id"])

    def test_cancelling_the_covering_purchase_reopens_the_short(self):
        def edited():
            setup_book()
            sell("Buyer X", 8 * MT, 10000, "2026-09-03", allow_short=True)
            b = buy("Supplier B", 10 * MT, 9700, "2026-09-04", wh="Mundra")
            deals.cancel_deal(b["id"], rehome=True)

        def fresh_book():
            setup_book()
            party("Buyer X")
            b = buy("Supplier B", 10 * MT, 9700, "2026-09-04", wh="Mundra")
            deals.cancel_deal(b["id"])
            sell("Buyer X", 8 * MT, 10000, "2026-09-03", allow_short=True)
        edited()
        audit(self)
        self.assertEqual(short_of(3), 3 * MT)            # X is 3 MT short again
        got = book()
        fresh_book()
        want = book()
        for key in ("summary", "positions", "stock", "warehouses", "counterparties"):
            self.assertEqual(norefs(want[key]), norefs(got[key]), key)

    def test_cancelling_a_short_sale_restores_everything(self):
        setup_book()
        before = book()
        d = sell("Buyer X", 8 * MT, 10000, "2026-09-03", allow_short=True)
        deals.cancel_deal(d["id"])
        after = book()
        for key in ("summary", "positions", "stock", "warehouses", "lots", "allocations", "marks"):
            self.assertEqual(before[key], after[key], key)
        audit(self)


class Editing(Base):
    def test_raising_a_sale_past_stock_asks_first(self):
        setup_book()
        d = sell("Buyer X", 4 * MT, 10000, "2026-09-03")
        self.unchanged(lambda: revise.edit_deal(d["id"], {"qty_g": 8 * MT}), deals.ShortNeeded)
        p = revise.preview_edit(d["id"], {"qty_g": 8 * MT})
        self.assertTrue(p["needs_short"])
        self.assertEqual(p["short_grams"], 3 * MT)
        self.assertIsNone(p["error"])
        self.assertEqual(p["effects"]["sales"][0]["short_after_g"], 3 * MT)

    def test_raising_a_sale_short_equals_booking_it_short(self):
        def edited():
            setup_book()
            d = sell("Buyer X", 4 * MT, 10000, "2026-09-03")
            revise.edit_deal(d["id"], {"qty_g": 8 * MT}, allow_short=True)
        self.same(edited, lambda: (setup_book(), sell("Buyer X", 8 * MT, 10000, "2026-09-03", allow_short=True)))

    def test_lowering_a_short_sale_shrinks_the_short_first(self):
        def edited():
            setup_book()
            d = sell("Buyer X", 8 * MT, 10000, "2026-09-03", allow_short=True)
            revise.edit_deal(d["id"], {"qty_g": 6 * MT})
        self.same(edited, lambda: (setup_book(), sell("Buyer X", 6 * MT, 10000, "2026-09-03", allow_short=True)))

    def test_lowering_below_what_is_covered(self):
        def edited():
            setup_book()
            d = sell("Buyer X", 8 * MT, 10000, "2026-09-03", allow_short=True)
            revise.edit_deal(d["id"], {"qty_g": 2 * MT})
        self.same(edited, lambda: (setup_book(), sell("Buyer X", 2 * MT, 10000, "2026-09-03")))

    def test_rate_of_a_short_sale(self):
        def edited():
            setup_book()
            d = sell("Buyer X", 8 * MT, 10000, "2026-09-03", allow_short=True)
            revise.edit_deal(d["id"], {"rate_paise": 10300})
        self.same(edited, lambda: (setup_book(), sell("Buyer X", 8 * MT, 10300, "2026-09-03", allow_short=True)))

    def test_moving_a_short_sale_to_a_warehouse_with_stock(self):
        def edited():
            setup_book()
            d = sell("Buyer X", 8 * MT, 10000, "2026-09-03", allow_short=True)
            revise.edit_deal(d["id"], {"warehouse_id": warehouse("Aslali")}, allow_short=True)
        self.same(edited, lambda: (setup_book(), sell("Buyer X", 8 * MT, 10000, "2026-09-03", wh="Aslali",
                                                       allow_short=True)))

    def test_a_split_that_leaves_stock_behind_cannot_go_short(self):
        setup_book()
        buy("Supplier B", 2 * MT, 9700, "2026-09-02", wh="Mundra")
        d = sell("Buyer X", 4 * MT, 10000, "2026-09-03")
        self.unchanged(lambda: revise.edit_deal(d["id"], {"qty_g": 9 * MT, "pins": [{"lot_id": lot_of(1), "qty_g": 3 * MT}]},
                                                allow_short=True))

    def test_purchase_rate_reprices_a_covered_short(self):
        def edited():
            setup_book()
            sell("Buyer X", 8 * MT, 10000, "2026-09-03", allow_short=True)
            b = buy("Supplier B", 10 * MT, 9700, "2026-09-04", wh="Mundra")
            revise.edit_deal(b["id"], {"rate_paise": 9800})

        def fresh_book():
            setup_book()
            sell("Buyer X", 8 * MT, 10000, "2026-09-03", allow_short=True)
            buy("Supplier B", 10 * MT, 9800, "2026-09-04", wh="Mundra")
        self.same(edited, fresh_book)


class Random(Base):
    """Random sales (many of them short), purchases, transfers, edits, cancels and undos."""

    def test_random_book_stays_whole(self):
        for seed in range(8):
            with self.subTest(seed=seed):
                self.run_seed(seed)

    def run_seed(self, seed):
        rng = random.Random(seed)
        fresh()
        whs = ["Mundra", "Aslali"]
        prods = [product(), other_product()]
        for i in range(4):
            extra = {} if i % 2 else dict(grade="SG5", manufacturer="Reliance")
            buy("S%d" % i, rng.randint(2, 8) * MT, rng.randint(9000, 10000), "2026-09-%02d" % (i + 1),
                wh=rng.choice(whs), **extra)
        done = 0
        for step in range(220):
            live = db.dicts(db.q("SELECT * FROM deals WHERE status='booked'"))
            kind = rng.random()
            try:
                if kind < 0.3:
                    pid, wid = rng.choice(prods), warehouse(rng.choice(whs))
                    free = stock.available_in(pid, wid)
                    deals.create_deal(dict(side="sell", party_id=party("B%d" % rng.randint(1, 4)),
                                           product_id=pid, warehouse_id=wid,
                                           qty_g=rng.randint(1, free + 4 * MT), allow_short=True,
                                           rate_paise=rng.randint(9000, 11000), deal_date="2026-09-%02d" % rng.randint(5, 28)))
                elif kind < 0.45:
                    pid = rng.choice(prods)
                    deals.create_deal(dict(side="buy", party_id=party("S%d" % rng.randint(1, 4)),
                                           product_id=pid, warehouse_id=warehouse(rng.choice(whs)),
                                           qty_g=rng.randint(1, 6) * MT, rate_paise=rng.randint(9000, 10000),
                                           deal_date="2026-09-%02d" % rng.randint(5, 28)))
                elif kind < 0.52:
                    lots = db.dicts(db.q("SELECT id, warehouse_id FROM lots WHERE status='open'"))
                    if lots:
                        lot = rng.choice(lots)
                        dest = [w for w in whs if warehouse(w) != lot["warehouse_id"]][0]
                        free = stock.get_lot(lot["id"])["available_g"]
                        stock.transfer(lot["id"], warehouse(dest), rng.randint(1, free))
                elif kind < 0.58:
                    ev = deals.last_undoable()
                    if ev:
                        deals.undo_event(int(ev["id"]))
                elif live:
                    d = rng.choice(live)
                    field = rng.choice(["qty_g", "rate_paise", "warehouse_id", "product_id", "cancel"])
                    if field == "cancel":
                        deals.cancel_deal(d["id"], rehome=rng.random() < 0.8)
                    else:
                        value = {"qty_g": max(1, d["qty_g"] + rng.randint(-3 * MT, 3 * MT)),
                                 "rate_paise": rng.randint(8500, 11500),
                                 "warehouse_id": warehouse(rng.choice(whs)),
                                 "product_id": rng.choice(prods)}[field]
                        revise.edit_deal(d["id"], {field: value}, rehome=rng.random() < 0.8,
                                         allow_short=rng.random() < 0.8)
                done += 1
            except (deals.DealError, ValueError):
                pass
            audit(self)
            # a screen total is the same sum as the rows under it
            rows = api.list_stock(limit=200)["items"]
            self.assertEqual(api.summary()["stock_g"], sum(r["stock_g"] for r in rows))
        self.assertGreater(done, 80)
        self.assertGreater(db.scalar("SELECT COUNT(*) FROM events WHERE action='cover'"), 0)
        # the movement ledger still walks back from today's free stock to zero
        full = stock.movements(limit=200)
        self.assertLessEqual(full["total"], 200)
        rows = full["items"]
        self.assertEqual(rows[0]["balance_g"], db.scalar(
            "SELECT COALESCE(SUM(qty_g - qty_allocated_g - qty_out_g),0) FROM lots WHERE status != 'cancelled'"))
        self.assertEqual(rows[-1]["balance_g"] - rows[-1]["qty_g"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
