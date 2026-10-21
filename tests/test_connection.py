"""A dead Postgres connection must not become a failed request.

The desk runs on a host that freezes the container between requests, against a
database that closes an idle client and then suspends its compute. So by the
time the trader opens the app again, the socket this process cached is usually
gone - and nothing has told it. Every test here kills the connection the way
the real thing dies, from the far end, and then asks the book a question that
must simply be answered.

On SQLite there is no socket to lose, so the suite only checks that nothing in
the healing code changed the way a file-backed book behaves.
"""
import os
import time
import unittest

from tests import common
from backend import db
from backend.services import deals, inventory, stock


def kill_from_the_server_side() -> None:
    """End this process' session the way Neon ends an idle one."""
    import psycopg
    pid = db.scalar("SELECT pg_backend_pid()")
    with psycopg.connect(db.DATABASE_URL, autocommit=True) as other:
        other.execute("SELECT pg_terminate_backend(%s)", (pid,))
    time.sleep(0.2)


class Healing(unittest.TestCase):
    """Postgres only: the socket is what breaks."""

    @classmethod
    def setUpClass(cls):
        if not db.IS_PG:
            raise unittest.SkipTest("Postgres only - SQLite has no connection to lose")
        common.fresh()
        cls.party = common.party("Shree Polymers")
        common.warehouse("Mundra")
        cls.product = common.product()
        common.buy("Shree Polymers", 5 * common.MT, 10_000, "2026-09-01")
        common.sell("Shree Polymers", 2 * common.MT, 11_000, "2026-09-05")

    def test_a_read_after_the_server_hung_up(self):
        before = db.scalar("SELECT COUNT(*) FROM deals")
        kill_from_the_server_side()
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM deals"), before)

    def test_a_list_screen_after_the_server_hung_up(self):
        kill_from_the_server_side()
        self.assertTrue(deals.list_deals(limit=10)["items"])
        kill_from_the_server_side()
        self.assertTrue(stock.stock_rows(limit=10)["items"])
        kill_from_the_server_side()
        self.assertTrue(inventory.positions(limit=10)["items"])

    def test_a_write_after_the_server_hung_up(self):
        kill_from_the_server_side()
        db.connect().used = 0                      # as if the app had been left alone
        ref = common.buy("Shree Polymers", 1 * common.MT, 10_500, "2026-09-10")
        self.assertTrue(ref)
        common.conserved(self, self.product)

    def test_the_handle_is_replaced_not_reused(self):
        first = db.connect().raw
        kill_from_the_server_side()
        db.q("SELECT 1")
        self.assertIsNot(db.connect().raw, first)
        self.assertTrue(first.closed)

    def test_a_quiet_spell_is_proved_before_the_next_question(self):
        kill_from_the_server_side()
        db.connect().used = time.monotonic() - (db.IDLE_PROVE + 1)
        self.assertEqual(db.q("SELECT 1 AS one")[0]["one"], 1)

    def test_a_broken_connection_inside_a_transaction_is_not_retried(self):
        """Half of a unit of work is worse than none: the error must go up."""
        with self.assertRaises(Exception):
            with db.tx() as conn:
                conn.execute("INSERT INTO settings(key,value) VALUES ('probe','1') "
                             "ON CONFLICT DO NOTHING")
                kill_from_the_server_side()
                conn.execute("SELECT 1")
        self.assertIsNone(db.q1("SELECT value FROM settings WHERE key = 'probe'"))

    def test_a_wrong_query_is_still_an_error(self):
        """Healing must not swallow a genuine mistake."""
        with self.assertRaises(Exception):
            db.q("SELECT no_such_column FROM deals")
        self.assertTrue(db.q("SELECT 1"))          # and the book still answers


class SqliteUntouched(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if db.IS_PG:
            raise unittest.SkipTest("SQLite only")
        common.fresh()
        common.party("Shree Polymers")
        common.warehouse("Mundra")
        cls.product = common.product()
        common.buy("Shree Polymers", 5 * common.MT, 10_000, "2026-09-01")

    def test_one_handle_is_kept_and_never_probed(self):
        first = db.connect()
        first.used = 0
        self.assertIs(db.connect(), first)
        self.assertIs(db.connect(prove=True), first)

    def test_the_book_still_reads_and_writes(self):
        common.sell("Shree Polymers", 1 * common.MT, 11_000, "2026-09-06")
        common.conserved(self, self.product)
        self.assertTrue(deals.list_deals(limit=5)["items"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
