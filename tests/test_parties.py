"""Parties: identity by GSTIN, PAN and state from GSTIN, one record per firm,
and the sheet importer.   python -m tests.test_parties"""
import os
import tempfile
import unittest

from tests.common import fresh
from backend import db, gst                              # noqa: E402
from backend.services import parties                    # noqa: E402

# real GSTINs from the supplied ledgers - the checksum must accept them
REAL = ["24AHZPG5607M1ZS", "08ABIFA3253C1ZT", "07AAJCA2774P1Z3", "24AAJCA2774P1Z7"]


def save(**kw):
    with db.tx() as conn:
        return parties.save_party(conn, **kw)


def party(pid):
    return db.q1("SELECT * FROM parties WHERE id=?", (pid,))


class Gstin(unittest.TestCase):
    def test_real_gstins_pass_and_a_typo_fails(self):
        for g in REAL:
            self.assertIsNone(gst.problem(g), g)
        typo = REAL[0][:5] + ("Q" if REAL[0][5] != "Q" else "R") + REAL[0][6:]
        self.assertIn("check character", gst.problem(typo))
        self.assertIn("format", gst.problem("24ABC"))

    def test_pan_and_state_come_from_the_gstin(self):
        self.assertEqual(gst.pan_of("24AAJCA2774P1Z7"), "AAJCA2774P")
        self.assertEqual(gst.state_name(gst.state_code_of("24AAJCA2774P1Z7")), "Gujarat")
        self.assertEqual(gst.state_code_for_name("Dadra & Nagar Haveli and Daman & Diu"), "26")


class Parties(unittest.TestCase):
    def setUp(self):
        fresh()

    def test_pan_and_state_are_derived_and_cannot_disagree(self):
        pid = save(name="Aashima Impex", gstin="07aajca2774p1z3", pan="WRONG12345", state_code="24")
        row = party(pid)
        self.assertEqual((row["gstin"], row["pan"], row["state_code"]), ("07AAJCA2774P1Z3", "AAJCA2774P", "07"))

    def test_state_is_chosen_when_there_is_no_gstin(self):
        pid = save(name="Accord Plastic", state_code="24")
        self.assertEqual(parties.get_party(pid)["state"], "Gujarat")

    def test_the_form_refuses_a_second_copy(self):
        save(name="Aaditya Polymake", gstin=REAL[0])
        with self.assertRaises(ValueError) as err:
            save(name="AADITYA POLYMAKE LLP", gstin=REAL[0])
        self.assertIn("already belongs to Aaditya Polymake", str(err.exception))
        with self.assertRaises(ValueError):
            save(name="aaditya polymake")
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM parties"), 1)

    def test_the_importer_folds_a_repeat_into_the_record(self):
        a = save(name="Aaditya Polymake", gstin=REAL[0])
        b = save(name="AADITYA POLYMAKE LLP", gstin=REAL[0], merge=True)
        self.assertEqual(a, b)

    def test_gstin_is_unique_on_edit(self):
        save(name="Aaditya Polymake", gstin=REAL[0])
        pid = save(name="Somebody Else", gstin=REAL[1])
        with self.assertRaises(ValueError):
            save(name="Somebody Else", gstin=REAL[0], party_id=pid)

    def test_two_branches_of_one_firm_are_two_parties(self):
        a = save(name="Aashima Impex Pvt Ltd", gstin="07AAJCA2774P1Z3")
        b = save(name="Aashima Impex Pvt Ltd", gstin="24AAJCA2774P1Z7")
        self.assertNotEqual(a, b)
        self.assertEqual(party(a)["pan"], party(b)["pan"])

    def test_bad_gstin_is_refused_when_typed(self):
        with self.assertRaises(ValueError):
            save(name="Typo Traders", gstin=REAL[0][:14] + ("0" if REAL[0][14] != "0" else "1"))

    def test_merge_never_blanks_a_filled_field(self):
        pid = save(name="Aarvi Polyplast", phone="+91 98250 00000", address="Old address")
        save(name="Aarvi Polyplast", address="New address", merge=True)
        row = party(pid)
        self.assertEqual((row["phone"], row["address"]), ("+91 98250 00000", "New address"))

    def test_check_gstin_names_the_owner(self):
        pid = save(name="Aaditya Polymake", gstin=REAL[0])
        c = parties.check_gstin(REAL[0].lower())
        self.assertEqual((c["valid"], c["pan"], c["state"], c["party"]["id"]),
                         (True, "AHZPG5607M", "Gujarat", pid))
        self.assertFalse(parties.check_gstin("24ABC")["valid"])

    def test_list_is_paged_and_searchable(self):
        for i in range(30):
            save(name="Party %02d" % i, state_code="24" if i % 2 else "27")
        first = parties.list_parties(limit=10)
        self.assertEqual((len(first["items"]), first["total"], first["has_more"]), (10, 30, True))
        rest = parties.list_parties(limit=25, offset=10)
        self.assertEqual((len(rest["items"]), rest["has_more"]), (20, False))
        self.assertFalse({p["id"] for p in first["items"]} & {p["id"] for p in rest["items"]})
        self.assertEqual(parties.list_parties(q="party 1")["total"], 10)
        self.assertEqual(parties.list_parties(state_code="24")["total"], 15)
        states = {s["code"]: s for s in parties.list_states(limit=100)["items"]}
        self.assertEqual(states["27"]["parties"], 15)


@unittest.skipUnless(__import__("importlib").util.find_spec("openpyxl"), "needs openpyxl")
class Import(unittest.TestCase):
    def setUp(self):
        fresh()

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
        self.assertEqual(report["unique_parties"], 4)

        created, updated = imp.apply(records)
        self.assertEqual((created, updated), (4, 0))
        aaditya = db.q1("SELECT * FROM parties WHERE gstin=?", (REAL[0],))
        self.assertIn("Vijay Pulses", aaditya["address"])
        self.assertEqual(aaditya["pan"], "AHZPG5607M")
        accord = db.q1("SELECT * FROM parties WHERE gstin IS NULL")
        self.assertEqual(accord["state_code"], "24", "a party without GSTIN keeps the sheet's state")

        created, updated = imp.apply(records)
        self.assertEqual((created, updated), (0, 4))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM parties"), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
