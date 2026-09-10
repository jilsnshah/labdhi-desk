"""Bring an older book up to the current shape. Runs on every boot; a no-op
once the book is current.

Version 1 kept master data as text inside transactions: a lot said which
warehouse it sat in by name, a sale could say "Aslali, Mundra", and a stock
line carried its material, grade and manufacturer as three strings copied
from a separate catalogue. Version 2 makes every one of those an entity with an
id, and transactions point at the ids.

The upgrade is one transaction. Tables whose shape changes are renamed out of
the way, rebuilt from schema.sql, refilled by id, and the old copies dropped.
Ids are kept, so every reference - including the audit log - still resolves.
If anything fails, nothing has changed.
"""
from __future__ import annotations

from typing import Dict, Optional

from . import db
from .gst import state_code_of

# Columns v1 gained after its first deploy. A v1 book may predate any of them;
# adding them first means the copy below reads one shape, not three.
LEGACY_COLUMNS = [
    ("deals", "warehouse", "TEXT"),
    ("deals", "payment_due", "TEXT"),
    ("deals", "ex_place", "TEXT"),
    ("lots", "warehouse", "TEXT"),
    ("parties", "address", "TEXT"),
    ("parties", "gstin", "TEXT"),
    ("parties", "pan", "TEXT"),
]

# Rebuilt under the same name. The catalogue tables and skus are read and
# dropped - their contents become materials, grades, manufacturers, products.
REBUILT = ["parties", "deals", "lots", "allocations", "marks", "warehouses"]

DEFAULT_WAREHOUSE = "Main"
UNSPECIFIED_MAKER = "Unspecified"


def upgrade(conn) -> bool:
    if not db.has_table(conn, "skus"):
        return False
    with open(db.SCHEMA) as fh:
        schema = fh.read()

    with db.tx() as c:
        for table, column, kind in LEGACY_COLUMNS:
            if db.has_table(c, table) and not db.has_column(c, table, column):
                c.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, kind))
        had_warehouses = db.has_table(c, "warehouses")
        if had_warehouses and not db.has_column(c, "warehouses", "location"):
            c.execute("ALTER TABLE warehouses ADD COLUMN location TEXT")

        for table in REBUILT:
            if db.has_table(c, table):
                c.execute("ALTER TABLE %s RENAME TO old_%s" % (table, table))

        c.script(schema)
        db.seed_reference(c)

        _parties(c)
        wh = _warehouses(c, had_warehouses)
        _products(c)
        _deals_and_lots(c, wh)

        # children before parents, so SQLite's foreign keys never see an orphan
        for name in ["old_allocations", "old_marks", "old_lots", "old_deals", "skus",
                     "old_parties", "old_warehouses",
                     "catalog_makers", "catalog_grades", "catalog_materials"]:
            if db.has_table(c, name):
                c.execute("DROP TABLE %s%s" % (name, " CASCADE" if c.is_pg else ""))

        # Index names that belonged to the renamed tables are free again.
        c.script(schema)
        for table in ["parties", "warehouses", "materials", "grades", "manufacturers",
                      "products", "deals", "lots", "allocations", "stock_moves"]:
            db.sync_sequence(c, table)
        db.set_setting(c, "schema_version", db.SCHEMA_VERSION)
        db.log(c, "system", None, "upgrade",
               "Book upgraded: parties, warehouses and products are now shared records", {})
    return True


# ------------------------------------------------------------------ parties
def _parties(c) -> None:
    for r in db.q("SELECT * FROM old_parties ORDER BY id"):
        gstin = (r["gstin"] or "").strip().upper() or None
        address = r["address"] if r["address"] else (r["city"] or None)
        c.execute(
            "INSERT INTO parties(id,name,slug,gstin,pan,state_code,phone,address,notes,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r["id"], r["name"], r["slug"], gstin, r["pan"], state_code_of(gstin or "") or None,
             r["phone"], address, r["notes"], r["created_at"]))
    db.sync_sequence(c, "parties")


# ------------------------------------------------------------------ warehouses
def _warehouses(c, had_table: bool) -> Dict[str, int]:
    """Every warehouse name v1 ever wrote down becomes one warehouse record."""
    names: Dict[str, Optional[str]] = {}
    if had_table:
        for r in db.q("SELECT name, location FROM old_warehouses ORDER BY created_at, name"):
            names[db.clean(r["name"])] = r["location"]
    for r in db.q("SELECT DISTINCT warehouse FROM old_lots WHERE warehouse IS NOT NULL"):
        names.setdefault(db.clean(r["warehouse"]), None)
    for r in db.q("SELECT DISTINCT warehouse FROM old_deals WHERE warehouse IS NOT NULL"):
        for part in r["warehouse"].split(","):        # a v1 sale could name two
            if db.clean(part):
                names.setdefault(db.clean(part), None)

    ids = {}
    for name, address in names.items():
        if name:
            ids[name] = c.insert("INSERT INTO warehouses(name,address,created_at) VALUES (?,?,?)",
                                 (name, address, db.now()))
    # Stock booked before warehouses existed still sits somewhere. It is put in
    # one named place the trader can rename, never left without a location.
    homeless = db.scalar("SELECT COUNT(*) FROM old_lots WHERE warehouse IS NULL OR TRIM(warehouse) = ''")
    if homeless and DEFAULT_WAREHOUSE not in ids:
        ids[DEFAULT_WAREHOUSE] = c.insert(
            "INSERT INTO warehouses(name,address,created_at) VALUES (?,?,?)",
            (DEFAULT_WAREHOUSE, None, db.now()))
    return ids


# ------------------------------------------------------------------ products
def _products(c) -> None:
    materials: Dict[str, int] = {}
    grades: Dict[tuple, int] = {}
    makers: Dict[str, int] = {}

    def material(name):
        name = db.clean(name).upper()
        if name not in materials:
            materials[name] = c.insert("INSERT INTO materials(name,created_at) VALUES (?,?)",
                                       (name, db.now()))
        return materials[name]

    def grade(mat, name):
        key = (db.clean(mat).upper(), db.clean(name).upper())
        if key not in grades:
            grades[key] = c.insert("INSERT INTO grades(material_id,name,created_at) VALUES (?,?,?)",
                                   (material(key[0]), key[1], db.now()))
        return grades[key]

    def maker(name):
        name = db.clean(name) or UNSPECIFIED_MAKER
        if name not in makers:
            makers[name] = c.insert("INSERT INTO manufacturers(name,created_at) VALUES (?,?)",
                                    (name, db.now()))
        return makers[name]

    has = lambda t: db.has_table(c, t)                                   # noqa: E731
    if has("catalog_materials"):
        for r in db.q("SELECT name FROM catalog_materials ORDER BY name"):
            material(r["name"])
    if has("catalog_grades"):
        for r in db.q("SELECT material, grade FROM catalog_grades ORDER BY material, grade"):
            grade(r["material"], r["grade"])

    taken = set()
    for r in db.q("SELECT * FROM skus ORDER BY id"):
        gid, kid = grade(r["material"], r["grade"]), maker(r["manufacturer"])
        c.execute("INSERT INTO products(id,grade_id,manufacturer_id,packing,created_at) "
                  "VALUES (?,?,?,?,?)", (r["id"], gid, kid, r["packing"], r["created_at"]))
        taken.add((gid, kid))
    db.sync_sequence(c, "products")

    # A maker registered for a grade but never traded is still a product on offer.
    if has("catalog_makers"):
        for r in db.q("SELECT material, grade, manufacturer FROM catalog_makers"):
            gid, kid = grade(r["material"], r["grade"]), maker(r["manufacturer"])
            if (gid, kid) not in taken:
                c.insert("INSERT INTO products(grade_id,manufacturer_id,created_at) VALUES (?,?,?)",
                         (gid, kid, db.now()))
                taken.add((gid, kid))


# ------------------------------------------------------------------ transactions
def _deals_and_lots(c, wh: Dict[str, int]) -> None:
    main = wh.get(DEFAULT_WAREHOUSE)
    one = lambda text: wh.get(db.clean(text)) if text and "," not in text else None   # noqa: E731

    for d in db.q("SELECT * FROM old_deals ORDER BY id"):
        c.execute(
            """INSERT INTO deals(id,ref,side,status,party_id,product_id,warehouse_id,qty_g,
                 rate_paise,plus_gst,deal_date,payment_due,ex_place,transporter_id,freight_by,
                 delivery_by,payment_terms,eway,remarks,alloc_policy,uncovered_g,created_at,
                 booked_at,cancelled_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (d["id"], d["ref"], d["side"], d["status"], d["party_id"], d["sku_id"],
             one(d["warehouse"]), d["qty_g"], d["rate_paise"], d["plus_gst"], d["deal_date"],
             d["payment_due"], d["ex_place"], d["transporter_id"], d["freight_by"],
             d["delivery_by"], d["payment_terms"], d["eway"], d["remarks"], d["alloc_policy"],
             d["uncovered_g"], d["created_at"], d["booked_at"], d["cancelled_at"]))
    db.sync_sequence(c, "deals")

    for l in db.q("SELECT * FROM old_lots ORDER BY id"):
        c.execute(
            """INSERT INTO lots(id,label,deal_id,product_id,warehouse_id,supplier_id,
                 parent_lot_id,rate_paise,qty_g,qty_allocated_g,qty_out_g,status,booked_at)
               VALUES (?,?,?,?,?,?,NULL,?,?,?,0,?,?)""",
            (l["id"], l["label"], l["deal_id"], l["sku_id"], one(l["warehouse"]) or main,
             l["supplier_id"], l["rate_paise"], l["qty_g"], l["qty_allocated_g"],
             l["status"], l["booked_at"]))
    db.sync_sequence(c, "lots")

    c.execute("""INSERT INTO allocations(id,sale_deal_id,lot_id,qty_g,cost_paise,sale_rate_paise,
                   method,created_at,active)
                 SELECT id,sale_deal_id,lot_id,qty_g,cost_paise,sale_rate_paise,method,created_at,active
                 FROM old_allocations""")
    db.sync_sequence(c, "allocations")
    c.execute("INSERT INTO marks(product_id,rate_paise,source,updated_at) "
              "SELECT sku_id,rate_paise,source,updated_at FROM old_marks")

    # A purchase is received where its lot sits. A sale is dispatched from the
    # one warehouse its lots sat in; a v1 sale drawn from two keeps no single
    # warehouse rather than a made-up one.
    c.execute("""UPDATE deals SET warehouse_id =
                   (SELECT MIN(l.warehouse_id) FROM lots l
                    WHERE l.deal_id = deals.id AND l.parent_lot_id IS NULL)
                 WHERE side = 'buy' AND warehouse_id IS NULL""")
    c.execute("""UPDATE deals SET warehouse_id =
                   (SELECT MIN(l.warehouse_id) FROM allocations a JOIN lots l ON l.id = a.lot_id
                    WHERE a.sale_deal_id = deals.id AND a.active = 1)
                 WHERE side = 'sell' AND warehouse_id IS NULL
                   AND (SELECT COUNT(DISTINCT l.warehouse_id) FROM allocations a
                        JOIN lots l ON l.id = a.lot_id
                        WHERE a.sale_deal_id = deals.id AND a.active = 1) = 1""")

