"""A book written by version 1 must come up intact under version 2.

Two v1 shapes exist: the one production runs (018838f, before warehouses,
GSTINs and paperwork fields) and the one the local desk had (5a7770a, with
warehouses as text). Each is built from its frozen schema on whatever backend
is configured - SQLite by default, Postgres with DATABASE_URL - filled with a
small book, and booted twice.

    python -m tests.test_migrate
    DATABASE_URL=postgres://... python -m tests.test_migrate
"""
import os
import unittest

from tests.common import MT, conserved, fresh               # noqa: F401
from backend import db                                      # noqa: E402
from backend.services import deals, inventory, stock        # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
T = "2026-01-01T10:00:00+05:30"


def build_v1(shape: str) -> None:
    db.reset()
    conn = db.connect()
    with open(os.path.join(FIXTURES, "schema_v1_%s.sql" % shape)) as fh:
        conn.script(fh.read())
    local = shape == "local"
    x = conn.execute

    x("INSERT INTO parties(id,name,slug,is_supplier,is_customer,is_transporter,city,phone,created_at) "
      "VALUES (1,'Vora Polychem','vora-polychem',1,0,0,'Surat','98250',?)", (T,))
    x("INSERT INTO parties(id,name,slug,is_supplier,is_customer,is_transporter,city,created_at) "
      "VALUES (2,'Krishna Dehgam','krishna-dehgam',0,1,0,NULL,?)", (T,))
    x("INSERT INTO parties(id,name,slug,is_supplier,is_customer,is_transporter,created_at) "
      "VALUES (3,'Mahavir Pipes','mahavir-pipes',0,1,0,?)", (T,))
    if local:
        x("UPDATE parties SET gstin='24AHZPG5607M1ZS', pan='AHZPG5607M', address='Tajpur' WHERE id=3")
        x("INSERT INTO warehouses(name,location,created_at) VALUES ('Mundra','Port road',?)", (T,))
        x("INSERT INTO warehouses(name,location,created_at) VALUES ('Aslali',NULL,?)", (T,))

    x("INSERT INTO skus(id,slug,display,material,grade,manufacturer,created_at) "
      "VALUES (1,'pvc-s65-reliance','PVC S65 · Reliance','PVC','S65','Reliance',?)", (T,))
    x("INSERT INTO skus(id,slug,display,material,grade,manufacturer,created_at) "
      "VALUES (2,'pvc-s65','PVC S65','PVC','S65','',?)", (T,))
    for m in ("PVC", "PP"):
        x("INSERT INTO catalog_materials(name,created_at) VALUES (?,?)", (m, T))
    for m, g in (("PVC", "S65"), ("PP", "H030SG")):
        x("INSERT INTO catalog_grades(material,grade,created_at) VALUES (?,?,?)", (m, g, T))
    for m, g, k in (("PVC", "S65", "Reliance"), ("PVC", "S65", "DCW"), ("PP", "H030SG", "Reliance")):
        x("INSERT INTO catalog_makers(material,grade,manufacturer,created_at) VALUES (?,?,?,?)", (m, g, k, T))

    def deal(i, side, party, sku, qty, rate, wh=None):
        cols = "id,ref,side,status,party_id,sku_id,qty_g,rate_paise,deal_date,created_at,booked_at"
        vals = [i, "B-%04d" % i, side, "booked", party, sku, qty, rate, "2026-01-0%d" % i, T, T]
        if local:
            cols += ",warehouse"; vals.append(wh)
        x("INSERT INTO deals(%s) VALUES (%s)" % (cols, ",".join("?" * len(vals))), vals)

    def lot(i, deal_id, sku, qty, allocated, wh=None):
        cols = "id,label,deal_id,sku_id,supplier_id,rate_paise,qty_g,qty_allocated_g,status,booked_at"
        vals = [i, "B-%04d / Vora" % deal_id, deal_id, sku, 1, 9500, qty, allocated,
                "exhausted" if allocated >= qty else "open", T]
        if local:
            cols += ",warehouse"; vals.append(wh)
        x("INSERT INTO lots(%s) VALUES (%s)" % (cols, ",".join("?" * len(vals))), vals)

    deal(1, "buy", 1, 1, 10 * MT, 9500, "Mundra")
    deal(2, "buy", 1, 1, 5 * MT, 9500, "Aslali")
    deal(3, "sell", 2, 1, 12 * MT, 9900, "Aslali, Mundra")
    deal(4, "sell", 3, 1, 2 * MT, 9900, "Aslali")
    deal(5, "buy", 1, 2, 3 * MT, 9400, None)
    lot(1, 1, 1, 10 * MT, 10 * MT, "Mundra")
    lot(2, 2, 1, 5 * MT, 4 * MT, "Aslali")
    lot(3, 5, 2, 3 * MT, 0, None)
    for sale, lot_id, qty in ((3, 1, 10 * MT), (3, 2, 2 * MT), (4, 2, 2 * MT)):
        x("INSERT INTO allocations(sale_deal_id,lot_id,qty_g,cost_paise,sale_rate_paise,method,created_at) "
          "VALUES (?,?,?,9500,9900,'fifo',?)", (sale, lot_id, qty, T))
    x("INSERT INTO marks(sku_id,rate_paise,source,updated_at) VALUES (1,9900,'sale B-0004',?)", (T,))
    x("INSERT INTO events(ts,actor,entity,entity_id,action,summary,payload) "
      "VALUES (?,'trader','deal',1,'book','Bought','{}')", (T,))
    x("INSERT INTO settings(key,value) VALUES ('company_name','Labdhi Exim')")
    db.close()


class Upgrade(unittest.TestCase):
    def boot_twice(self):
        db.close()
        db.init_db()
        db.close()
        db.init_db()                                             # a second boot changes nothing

    def common(self):
        self.assertFalse(db.has_table(db.connect(), "skus"))
        self.assertFalse(db.has_table(db.connect(), "catalog_makers"))
        self.assertEqual(db.settings()["schema_version"], db.SCHEMA_VERSION)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM deals"), 5)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM events WHERE action='book'"), 1)

        # the catalogue and the stock lines became one product tree
        names = sorted(r["display"] for r in db.q("SELECT display FROM v_products"))
        self.assertEqual(names, ["PP H030SG · Reliance", "PVC S65 · DCW", "PVC S65 · Reliance",
                                 "PVC S65 · Unspecified"])
        self.assertEqual(db.q1("SELECT display FROM v_products WHERE id=1")["display"], "PVC S65 · Reliance")
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM manufacturers WHERE name='Reliance'"), 1)
        self.assertEqual(db.q1("SELECT product_id FROM marks")["product_id"], 1)

        # parties kept their ids and their city became the address
        self.assertEqual(db.q1("SELECT address FROM parties WHERE id=1")["address"], "Surat")

        # nothing lost: every gram is still in stock or sold, and trading goes on
        conserved(self, 1)
        conserved(self, 2)
        self.assertEqual(inventory.totals()["stock_g"], 4 * MT)
        lot2 = db.q1("SELECT warehouse_id FROM lots WHERE id=2")["warehouse_id"]
        sale = deals.create_deal(dict(side="sell", party_id=2, product_id=1, warehouse_id=lot2,
                                      qty_g=MT, rate_paise=9900, deal_date="2026-02-01"))
        self.assertEqual(sale["allocations"][0]["lot_id"], 2)
        new_party = deals.create_deal(dict(side="buy", party_id=1, product_id=1, warehouse_id=lot2,
                                           qty_g=MT, rate_paise=9500))
        self.assertGreater(new_party["id"], 5, "id sequence did not move past copied rows")

    def test_production_book(self):
        build_v1("prod")
        self.boot_twice()
        self.common()
        # nothing recorded a warehouse, so everything sits in one the trader can rename
        self.assertEqual([r["name"] for r in db.q("SELECT name FROM warehouses")], ["Main"])
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM deals WHERE warehouse_id IS NULL"), 0)

    def test_local_book(self):
        build_v1("local")
        self.boot_twice()
        self.common()
        wh = {r["name"]: r for r in db.q("SELECT * FROM warehouses")}
        self.assertEqual(set(wh), {"Mundra", "Aslali", "Main"})
        self.assertEqual(wh["Mundra"]["address"], "Port road")
        at = lambda i: db.q1("SELECT w.name FROM lots l JOIN warehouses w ON w.id = l.warehouse_id "  # noqa: E731
                             "WHERE l.id=?", (i,))["name"]
        self.assertEqual((at(1), at(2), at(3)), ("Mundra", "Aslali", "Main"))
        dw = lambda i: db.q1("SELECT warehouse_id FROM deals WHERE id=?", (i,))["warehouse_id"]  # noqa: E731
        self.assertIsNone(dw(3), "a v1 sale from two warehouses must not pretend it came from one")
        self.assertEqual(dw(4), wh["Aslali"]["id"])
        self.assertEqual(dw(5), wh["Main"]["id"])
        p = db.q1("SELECT * FROM parties WHERE id=3")
        self.assertEqual((p["gstin"], p["state_code"]), ("24AHZPG5607M1ZS", "24"))
        # 3 v1 receipts + 3 v1 sale lines, plus the buy and sale common() just booked
        self.assertEqual(stock.movements(limit=100)["total"], 4 + 4)

    def tearDown(self):
        db.close()
        fresh()


if __name__ == "__main__":
    unittest.main(verbosity=2)
