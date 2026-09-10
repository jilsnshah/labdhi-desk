"""Parties: identity by GSTIN, PAN from GSTIN, no buyer/seller split, and the
sheet importer.   python -m tests.test_parties"""
import os
import tempfile
import unittest

os.environ["LABDHI_DB"] = os.path.join(tempfile.mkdtemp(), "parties.db")

from backend import db                                    # noqa: E402
from backend.services import catalog                      # noqa: E402

# real GSTINs from the supplied ledgers - the checksum must accept them
REAL = ["24AHZPG5607M1ZS", "08ABIFA3253C1ZT", "07AAJCA2774P1Z3", "24AAJCA2774P1Z7"]


def save(**kw):
    with db.tx() as conn:
        return catalog.save_party(conn, **kw)


def party(pid):
    return db.q1("SELECT * FROM parties WHERE id=?", (pid,))


class Gstin(unittest.TestCase):
    def test_real_gstins_pass_and_a_typo_fails(self):
        for g in REAL:
            self.assertIsNone(catalog.gstin_problem(g), g)
        typo = REAL[0][:5] + ("Q" if REAL[0][5] != "Q" else "R") + REAL[0][6:]
        self.assertIn("check character", catalog.gstin_problem(typo))
        self.assertIn("format", catalog.gstin_problem("24ABC"))

    def test_pan_and_state_come_from_the_gstin(self):
        self.assertEqual(catalog.pan_from_gstin("24AAJCA2774P1Z7"), "AAJCA2774P")
        self.assertEqual(catalog.state_of("24AAJCA2774P1Z7"), "Gujarat")


class Parties(unittest.TestCase):
    def setUp(self):
        db.reset()
        db.init_db()

    def test_pan_is_derived_and_cannot_disagree(self):
        pid = save(name="Aashima Impex", gstin="07aajca2774p1z3", pan="WRONG12345")
        self.assertEqual((party(pid)["gstin"], party(pid)["pan"]), ("07AAJCA2774P1Z3", "AAJCA2774P"))

    def test_gstin_is_unique(self):
        save(name="Aaditya Polymake", gstin=REAL[0])
        with self.assertRaises(ValueError):
            with db.tx() as conn:
                pid = catalog.save_party(conn, name="Somebody Else", gstin=REAL[1])
                catalog.save_party(conn, name="Somebody Else", gstin=REAL[0], party_id=pid)

    def test_same_gstin_under_new_name_is_the_same_party(self):
        a = save(name="Aaditya Polymake", gstin=REAL[0])
        b = save(name="AADITYA POLYMAKE LLP", gstin=REAL[0])
        self.assertEqual(a, b)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM parties"), 1)

    def test_two_branches_of_one_firm_are_two_parties(self):
        a = save(name="Aashima Impex Pvt Ltd", gstin="07AAJCA2774P1Z3")
        b = save(name="Aashima Impex Pvt Ltd", gstin="24AAJCA2774P1Z7")
        self.assertNotEqual(a, b)
        self.assertEqual(party(a)["pan"], party(b)["pan"])
        self.assertNotEqual(party(a)["slug"], party(b)["slug"])

    def test_bad_gstin_is_refused_when_typed(self):
        with self.assertRaises(ValueError):
            save(name="Typo Traders", gstin=REAL[0][:14] + ("0" if REAL[0][14] != "0" else "1"))

    def test_merge_never_blanks_a_filled_field(self):
        pid = save(name="Aarvi Polyplast", phone="+91 98250 00000", address="Old address")
        save(name="Aarvi Polyplast", address="New address", merge=True)
        row = party(pid)
        self.assertEqual((row["phone"], row["address"]), ("+91 98250 00000", "New address"))

    def test_pickers_show_everyone_regardless_of_side(self):
        save(name="Only Ever Bought From", is_supplier=True)
        names = [p["name"] for p in catalog.search_parties(role="customer", limit=50)]
        self.assertIn("Only Ever Bought From", names)


@unittest.skipUnless(__import__("importlib").util.find_spec("openpyxl"), "needs openpyxl")
class Import(unittest.TestCase):
    def setUp(self):
        db.reset()
        db.init_db()

    def sheet(self, rows):
        import openpyxl
        wb = openpyxl.Workbook(); ws = wb.active
        ws.append(["LABDHI EXIM"]); ws.append(["Updation of Party GSTIN/UIN"])
        ws.append(["Sl No.", "Particulars", "Address", "State", "Country",
                   "Registration Type", "GSTIN/UIN", "PAN/IT No."])
        for r in rows:
            ws.append(r)
        path = os.path.join(tempfile.mkdtemp(), "ledger.xlsx")
        wb.save(path)
        return path

    def test_import_dedupes_across_sheets_and_is_rerunnable(self):
        from backend import import_parties as imp
        one = self.sheet([
            [1, "AADITYA POLYMAKE", "550, Tajpur", "Gujarat", "India", "Regular", REAL[0], ""],
            [None, "DELIVERY", "Some godown", "Gujarat", "India", "Regular", REAL[0], ""],
            [2, "Add Duty Receivable", "", "_x0004_ Not Applicable", "India", "Regular", "", ""],
            [3, "ACCORD PLASTIC", "Junagadh", "Gujarat", "India", "Regular", "", ""],
            [4, "AASHIMA IMPEX PVT LTD", "Delhi office", "Delhi", "India", "Regular", "07AAJCA2774P1Z3", ""],
        ])
        two = self.sheet([
            [1, "Aaditya Polymake", "C/o Vijay Pulses, 550, Opp Volga, Tajpur", "Gujarat", "India",
             "Regular", REAL[0], ""],
            [2, "Accord Plastic", "Gidc-2, Plot 707, Junagadh", "Gujarat", "India", "Regular", "", ""],
            [3, "AASHIMA IMPEX PVT LTD", "Mundra godown", "Gujarat", "India", "Regular",
             "24AAJCA2774P1Z7", ""],
        ])
        records, report = imp.plan([one, two])
        self.assertEqual(report["continuation_rows"], 1)
        self.assertEqual(report["excluded"], ["Add Duty Receivable"])
        # Aaditya (1 GSTIN), Accord (by name), Aashima x2 (two branches)
        self.assertEqual(report["unique_parties"], 4)

        created, updated = imp.apply(records)
        self.assertEqual((created, updated), (4, 0))
        aaditya = db.q1("SELECT * FROM parties WHERE gstin=?", (REAL[0],))
        self.assertIn("Vijay Pulses", aaditya["address"])            # fuller address kept
        self.assertEqual(aaditya["pan"], "AHZPG5607M")

        created, updated = imp.apply(records)                         # run it again
        self.assertEqual((created, updated), (0, 4))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM parties"), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
