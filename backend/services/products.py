"""Products: the stock item master, and the tree it hangs from.

    material  PVC                      one row, shared by every PVC grade
    grade     S65 (of PVC)             one row per material
    maker     Reliance                 one row, shared across materials
    product   PVC S65 from Reliance    one row: what stock is kept against

A buy or sell ticket picks a product by id. A new product is made only through
the product form, which is also the one place a new material, grade or
manufacturer can come into being - so "Reliance", "reliance " and "RIL" can
never split one product into three.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .. import db
from .stock import AVAILABLE


def norm_material(name) -> str:
    return db.clean(name).upper()


def norm_grade(name) -> str:
    return db.clean(name).upper()


def norm_maker(name) -> str:
    return db.clean(name)


def _stock(where: str) -> str:
    """Open stock of every product matching `where` (a clause over v_products s2)."""
    return ("COALESCE((SELECT SUM({a}) FROM lots l JOIN v_products s2 ON s2.id = l.product_id "
            "WHERE l.status = 'open' AND {w}), 0)").format(a=AVAILABLE, w=where)


def _traded(where: str, args) -> int:
    return db.scalar("SELECT COUNT(*) FROM deals d JOIN v_products s ON s.id = d.product_id "
                     "WHERE " + where, args)


# ------------------------------------------------------------------ materials
def list_materials(q: str = "", has_products: bool = False, in_stock: bool = False,
                   limit: Optional[int] = None, offset: int = 0) -> Dict[str, Any]:
    limit, offset = db.page_args(limit, offset)
    where, args = [], []
    like = db.like(q)
    if like:
        where.append("LOWER(m.name) LIKE ?"); args.append(like)
    if has_products:
        where.append("EXISTS (SELECT 1 FROM v_products s WHERE s.material_id = m.id)")
    if in_stock:
        where.append(_stock("s2.material_id = m.id") + " > 0")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.scalar("SELECT COUNT(*) FROM materials m " + clause, args)
    rows = db.q(
        """SELECT * FROM (
             SELECT m.id, m.name,
                    (SELECT COUNT(*) FROM grades g WHERE g.material_id = m.id) AS grades,
                    (SELECT COUNT(*) FROM v_products s WHERE s.material_id = m.id) AS products,
                    {stock} AS stock_g
             FROM materials m {clause}) x
           ORDER BY stock_g DESC, name LIMIT ? OFFSET ?""".format(
            stock=_stock("s2.material_id = m.id"), clause=clause), args + [limit, offset])
    return db.page(db.dicts(rows), total, limit, offset)


def save_material(conn, name: str, material_id: Optional[int] = None) -> int:
    name = norm_material(name)
    if not name:
        raise ValueError("Material name is required")
    clash = db.q1("SELECT id FROM materials WHERE name=? AND id != ?", (name, int(material_id or 0)))
    if clash:
        if material_id:
            raise ValueError("%s is already a material" % name)
        return int(clash["id"])
    if material_id:
        conn.execute("UPDATE materials SET name=? WHERE id=?", (name, int(material_id)))
        db.log(conn, "material", int(material_id), "update", "Renamed material to %s" % name, {})
        return int(material_id)
    mid = conn.insert("INSERT INTO materials(name,created_at) VALUES (?,?)", (name, db.now()))
    db.log(conn, "material", mid, "create", "Added material %s" % name, {})
    return mid


def remove_material(conn, material_id: int) -> None:
    """Goes with its grades and products - but only if none were ever traded."""
    row = db.q1("SELECT * FROM materials WHERE id=?", (int(material_id),))
    if row is None:
        raise ValueError("No such material")
    n = _traded("s.material_id = ?", (row["id"],))
    if n:
        raise ValueError("Cannot remove %s - %d deal%s use it" % (row["name"], n, "" if n == 1 else "s"))
    conn.execute("DELETE FROM products WHERE grade_id IN (SELECT id FROM grades WHERE material_id=?)",
                 (row["id"],))
    conn.execute("DELETE FROM grades WHERE material_id=?", (row["id"],))
    conn.execute("DELETE FROM materials WHERE id=?", (row["id"],))
    db.log(conn, "material", row["id"], "remove", "Removed material %s" % row["name"], {})


# ------------------------------------------------------------------ grades
def list_grades(material_id: Optional[int] = None, q: str = "", has_products: bool = False,
                in_stock: bool = False, limit: Optional[int] = None,
                offset: int = 0) -> Dict[str, Any]:
    limit, offset = db.page_args(limit, offset)
    where, args = [], []
    if material_id:
        where.append("g.material_id = ?"); args.append(int(material_id))
    like = db.like(q)
    if like:
        where.append("(LOWER(g.name) LIKE ? OR LOWER(m.name) LIKE ?)"); args += [like, like]
    if has_products:
        where.append("EXISTS (SELECT 1 FROM products p WHERE p.grade_id = g.id)")
    if in_stock:
        where.append(_stock("s2.grade_id = g.id") + " > 0")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    base = "FROM grades g JOIN materials m ON m.id = g.material_id " + clause
    total = db.scalar("SELECT COUNT(*) " + base, args)
    rows = db.q(
        """SELECT * FROM (
             SELECT g.id, g.name, g.material_id, m.name AS material,
                    (SELECT COUNT(*) FROM products p WHERE p.grade_id = g.id) AS products,
                    {stock} AS stock_g
             {base}) x
           ORDER BY stock_g DESC, material, name LIMIT ? OFFSET ?""".format(
            stock=_stock("s2.grade_id = g.id"), base=base), args + [limit, offset])
    return db.page(db.dicts(rows), total, limit, offset)


def save_grade(conn, material_id: int, name: str, grade_id: Optional[int] = None) -> int:
    name = norm_grade(name)
    if not name:
        raise ValueError("Grade name is required")
    mat = db.q1("SELECT * FROM materials WHERE id=?", (int(material_id or 0),))
    if mat is None:
        raise ValueError("Choose the material this grade belongs to")
    clash = db.q1("SELECT id FROM grades WHERE material_id=? AND name=? AND id != ?",
                  (mat["id"], name, int(grade_id or 0)))
    if clash:
        if grade_id:
            raise ValueError("%s %s already exists" % (mat["name"], name))
        return int(clash["id"])
    if grade_id:
        conn.execute("UPDATE grades SET name=?, material_id=? WHERE id=?", (name, mat["id"], int(grade_id)))
        db.log(conn, "grade", int(grade_id), "update", "Renamed grade to %s %s" % (mat["name"], name), {})
        return int(grade_id)
    gid = conn.insert("INSERT INTO grades(material_id,name,created_at) VALUES (?,?,?)",
                      (mat["id"], name, db.now()))
    db.log(conn, "grade", gid, "create", "Added grade %s %s" % (mat["name"], name), {})
    return gid


def remove_grade(conn, grade_id: int) -> None:
    row = db.q1("SELECT g.*, m.name AS material FROM grades g JOIN materials m ON m.id = g.material_id "
                "WHERE g.id=?", (int(grade_id),))
    if row is None:
        raise ValueError("No such grade")
    n = _traded("s.grade_id = ?", (row["id"],))
    if n:
        raise ValueError("Cannot remove %s %s - %d deal%s use it"
                         % (row["material"], row["name"], n, "" if n == 1 else "s"))
    conn.execute("DELETE FROM products WHERE grade_id=?", (row["id"],))
    conn.execute("DELETE FROM grades WHERE id=?", (row["id"],))
    db.log(conn, "grade", row["id"], "remove", "Removed grade %s %s" % (row["material"], row["name"]), {})


# ------------------------------------------------------------------ manufacturers
def list_manufacturers(q: str = "", limit: Optional[int] = None, offset: int = 0) -> Dict[str, Any]:
    limit, offset = db.page_args(limit, offset)
    where, args = [], []
    like = db.like(q)
    if like:
        where.append("LOWER(k.name) LIKE ?"); args.append(like)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.scalar("SELECT COUNT(*) FROM manufacturers k " + clause, args)
    rows = db.q(
        """SELECT * FROM (
             SELECT k.id, k.name,
                    (SELECT COUNT(*) FROM products p WHERE p.manufacturer_id = k.id) AS products,
                    {stock} AS stock_g
             FROM manufacturers k {clause}) x
           ORDER BY stock_g DESC, name LIMIT ? OFFSET ?""".format(
            stock=_stock("s2.manufacturer_id = k.id"), clause=clause), args + [limit, offset])
    return db.page(db.dicts(rows), total, limit, offset)


def save_manufacturer(conn, name: str, manufacturer_id: Optional[int] = None) -> int:
    name = norm_maker(name)
    if not name:
        raise ValueError("Manufacturer name is required")
    clash = db.q1("SELECT id, name FROM manufacturers WHERE LOWER(name)=LOWER(?) AND id != ?",
                  (name, int(manufacturer_id or 0)))
    if clash:
        if manufacturer_id:
            raise ValueError("%s is already a manufacturer" % clash["name"])
        return int(clash["id"])
    if manufacturer_id:
        conn.execute("UPDATE manufacturers SET name=? WHERE id=?", (name, int(manufacturer_id)))
        db.log(conn, "manufacturer", int(manufacturer_id), "update", "Renamed manufacturer to %s" % name, {})
        return int(manufacturer_id)
    kid = conn.insert("INSERT INTO manufacturers(name,created_at) VALUES (?,?)", (name, db.now()))
    db.log(conn, "manufacturer", kid, "create", "Added manufacturer %s" % name, {})
    return kid


def remove_manufacturer(conn, manufacturer_id: int) -> None:
    row = db.q1("SELECT * FROM manufacturers WHERE id=?", (int(manufacturer_id),))
    if row is None:
        raise ValueError("No such manufacturer")
    n = _traded("s.manufacturer_id = ?", (row["id"],))
    if n:
        raise ValueError("Cannot remove %s - %d deal%s use it" % (row["name"], n, "" if n == 1 else "s"))
    conn.execute("DELETE FROM products WHERE manufacturer_id=?", (row["id"],))
    conn.execute("DELETE FROM manufacturers WHERE id=?", (row["id"],))
    db.log(conn, "manufacturer", row["id"], "remove", "Removed manufacturer %s" % row["name"], {})


# ------------------------------------------------------------------ products
_PRODUCT_SEARCH = ("(LOWER(s.display) LIKE ? OR LOWER(s.material) LIKE ? OR LOWER(s.grade) LIKE ? "
                   "OR LOWER(s.manufacturer) LIKE ?)")


def list_products(q: str = "", material_id: Optional[int] = None, grade_id: Optional[int] = None,
                  manufacturer_id: Optional[int] = None, warehouse_id: Optional[int] = None,
                  in_stock: bool = False, limit: Optional[int] = None,
                  offset: int = 0) -> Dict[str, Any]:
    """One page of products. With `warehouse_id`, stock is what sits there."""
    limit, offset = db.page_args(limit, offset)
    where, args = [], []
    like = db.like(q)
    if like:
        where.append(_PRODUCT_SEARCH); args += [like] * 4
    for col, val in (("material_id", material_id), ("grade_id", grade_id),
                     ("manufacturer_id", manufacturer_id)):
        if val:
            where.append("s.%s = ?" % col); args.append(int(val))
    stock = ("COALESCE((SELECT SUM({a}) FROM lots l WHERE l.product_id = s.id AND l.status = 'open'"
             "{w}), 0)").format(a=AVAILABLE, w=" AND l.warehouse_id = ?" if warehouse_id else "")
    sargs = [int(warehouse_id)] if warehouse_id else []
    if in_stock:
        where.append(stock + " > 0"); args += sargs
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.scalar("SELECT COUNT(*) FROM v_products s " + clause, args)
    rows = db.q(
        """SELECT * FROM (
             SELECT s.*, {stock} AS stock_g,
                    (SELECT COUNT(*) FROM deals d WHERE d.product_id = s.id
                     AND d.status != 'cancelled') AS deal_count,
                    (SELECT MAX(d.deal_date) FROM deals d WHERE d.product_id = s.id
                     AND d.status != 'cancelled') AS last_deal
             FROM v_products s {clause}) x
           ORDER BY stock_g DESC, (last_deal IS NULL), last_deal DESC, display
           LIMIT ? OFFSET ?""".format(stock=stock, clause=clause),
        sargs + args + [limit, offset])
    return db.page(db.dicts(rows), total, limit, offset)


def get_product(product_id: int) -> Optional[Dict[str, Any]]:
    row = db.q1("SELECT * FROM v_products WHERE id=?", (int(product_id),))
    if row is None:
        return None
    p = dict(row)
    p["warehouses"] = db.dicts(db.q(
        """SELECT w.id, w.name, SUM({a}) AS stock_g, COUNT(*) AS lots
           FROM lots l JOIN warehouses w ON w.id = l.warehouse_id
           WHERE l.product_id = ? AND l.status = 'open' AND {a} > 0
           GROUP BY w.id, w.name ORDER BY SUM({a}) DESC, w.name""".format(a=AVAILABLE),
        (p["id"],)))
    p["stock_g"] = sum(w["stock_g"] for w in p["warehouses"])
    p["deal_count"] = db.scalar("SELECT COUNT(*) FROM deals WHERE product_id=? AND status != 'cancelled'",
                                (p["id"],))
    return p


def require(product_id) -> Dict[str, Any]:
    p = get_product(int(product_id)) if product_id else None
    if p is None:
        raise ValueError("Choose a product from the list, or add a new one")
    return p


def find_product(material: str, grade: str, manufacturer: str) -> Optional[Dict[str, Any]]:
    return db.row_to_dict(db.q1(
        "SELECT * FROM v_products WHERE material=? AND grade=? AND LOWER(manufacturer)=LOWER(?)",
        (norm_material(material), norm_grade(grade), norm_maker(manufacturer))))


def save_product(conn, material: str, grade: str, manufacturer: str, packing: Optional[str] = None,
                 product_id: Optional[int] = None) -> Dict[str, Any]:
    """Create (or edit) a product from its three names.

    Returns {"id", "existed"}. Asking for a product that is already on file
    hands back that one instead of making a twin. A traded product keeps its
    material, grade and manufacturer - relabelling it would rewrite every past
    deal - but its packing can still change.
    """
    material, grade, manufacturer = norm_material(material), norm_grade(grade), norm_maker(manufacturer)
    if not material:
        raise ValueError("Which material?")
    if not grade:
        raise ValueError("Which grade?")
    if not manufacturer:
        raise ValueError("Which manufacturer? (who made it - not who you trade with)")
    packing = db.clean(packing) or None

    existing = find_product(material, grade, manufacturer)
    if existing and (not product_id or int(existing["id"]) != int(product_id)):
        if product_id:
            raise ValueError("%s already exists" % existing["display"])
        return {"id": int(existing["id"]), "existed": True}

    mid = save_material(conn, material)
    gid = save_grade(conn, mid, grade)
    kid = save_manufacturer(conn, manufacturer)

    if product_id:
        cur = require(product_id)
        if (cur["grade_id"], cur["manufacturer_id"]) != (gid, kid) and cur["deal_count"]:
            raise ValueError("%s has been traded - its material, grade and manufacturer are fixed. "
                             "Rename the manufacturer or grade itself instead." % cur["display"])
        conn.execute("UPDATE products SET grade_id=?, manufacturer_id=?, packing=? WHERE id=?",
                     (gid, kid, packing, cur["id"]))
        db.log(conn, "product", cur["id"], "update", "Updated %s" % cur["display"], {})
        return {"id": int(cur["id"]), "existed": False}

    pid = conn.insert("INSERT INTO products(grade_id,manufacturer_id,packing,created_at) "
                      "VALUES (?,?,?,?)", (gid, kid, packing, db.now()))
    db.log(conn, "product", pid, "create", "Added product %s %s · %s" % (material, grade, manufacturer),
           {"material": material, "grade": grade, "manufacturer": manufacturer})
    return {"id": pid, "existed": False}


def remove_product(conn, product_id: int) -> None:
    p = require(product_id)
    n = db.scalar("SELECT COUNT(*) FROM deals WHERE product_id=?", (p["id"],))
    if n:
        raise ValueError("Cannot remove %s - %d deal%s use it" % (p["display"], n, "" if n == 1 else "s"))
    conn.execute("DELETE FROM marks WHERE product_id=?", (p["id"],))
    conn.execute("DELETE FROM products WHERE id=?", (p["id"],))
    db.log(conn, "product", p["id"], "remove", "Removed %s" % p["display"], {})
