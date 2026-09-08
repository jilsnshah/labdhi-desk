"""Parties and SKUs.

Design rule: there are NO master-data screens. A party or a material comes
into existence the first time the trader names it in a deal. Autocomplete is
ranked by how often and how recently he actually traded it, so after a week
the top three chips are almost always the right answer.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .. import db

# ------------------------------------------------------------------ parties
def find_party(name: str) -> Optional[Dict[str, Any]]:
    if not name:
        return None
    return db.row_to_dict(q_party(db.slugify(name)))


def q_party(slug: str):
    return db.q1("SELECT * FROM parties WHERE slug=?", (slug,))


def upsert_party(conn, name: str, role: Optional[str] = None, **fields) -> int:
    """Find by slug or create. `role` in {supplier, customer, transporter}."""
    name = (name or "").strip()
    if not name:
        raise ValueError("party name required")
    slug = db.slugify(name)
    row = q_party(slug)
    if row is None:
        pid = conn.insert(
            "INSERT INTO parties(name,slug,is_supplier,is_customer,is_transporter,city,phone,notes,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (name, slug,
             1 if role == "supplier" else 0,
             1 if role == "customer" else 0,
             1 if role == "transporter" else 0,
             fields.get("city"), fields.get("phone"), fields.get("notes"), db.now()),
        )
        db.log(conn, "party", pid, "create", "New party %s" % name, {"name": name, "role": role})
        return pid

    pid = int(row["id"])
    col = {"supplier": "is_supplier", "customer": "is_customer", "transporter": "is_transporter"}.get(role or "")
    if col and not row[col]:
        conn.execute("UPDATE parties SET %s=1 WHERE id=?" % col, (pid,))
    return pid


def search_parties(term: str = "", role: Optional[str] = None, limit: int = 8) -> List[Dict[str, Any]]:
    """Ranked chips: recent + frequent first, then name match."""
    where, args = [], []
    if role in ("supplier", "customer", "transporter"):
        where.append("(p.is_%s = 1 OR p.id IN (SELECT party_id FROM deals))" % role)
    if term:
        where.append("(p.slug LIKE ? OR p.name LIKE ?)")
        args += ["%%%s%%" % db.slugify(term), "%%%s%%" % term]
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    side = "buy" if role == "supplier" else ("sell" if role == "customer" else None)
    side_filter = "AND d.side='%s'" % side if side else ""
    sql = """
        SELECT p.*,
               (SELECT COUNT(*) FROM deals d WHERE d.party_id=p.id {sf}) AS deal_count,
               (SELECT MAX(d.deal_date) FROM deals d WHERE d.party_id=p.id {sf}) AS last_deal
        FROM parties p
        {clause}
        ORDER BY (last_deal IS NULL), last_deal DESC, deal_count DESC, p.name
        LIMIT ?
    """.format(sf=side_filter, clause=clause)
    return [dict(r) for r in db.q(sql, args + [limit])]


# ------------------------------------------------------------------ skus
# Inventory is kept per material x grade x manufacturer. The three are chosen
# one after another in the ticket, each list narrowed by the one before it, so
# the trader never picks a combination that does not exist.

MATERIALS = ["PVC", "CPVC", "UPVC", "PP", "HDPE", "LLDPE", "LDPE", "PET", "ABS", "PS", "EVA"]
MAKERS = [
    "Reliance", "Chemplast Sanmar", "Finolex", "DCW", "GAIL", "Haldia", "IOCL",
    "Hanwha", "LG Chem", "Formosa", "Shintech", "Vinnolit", "Xinjiang Zhongtai",
]


def display_name(material, grade, manufacturer=None, packing=None) -> str:
    name = " ".join(b for b in (material, grade) if b) or "Material"
    if manufacturer:
        name = "%s \u00b7 %s" % (name, manufacturer)
    if packing:
        name = "%s (%s)" % (name, packing)
    return name


def clean(text) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def upsert_sku(conn, material: str = "", grade: str = "", manufacturer: str = "",
               packing: Optional[str] = None) -> int:
    material = clean(material).upper()
    grade = clean(grade).upper()
    manufacturer = clean(manufacturer)
    if not material:
        raise ValueError("Which material?")
    if not grade:
        raise ValueError("Which grade?")

    row = db.q1(
        "SELECT id FROM skus WHERE material=? AND grade=? AND manufacturer=?",
        (material, grade, manufacturer),
    )
    if row:
        return int(row["id"])

    display = display_name(material, grade, manufacturer, packing)
    sid = conn.insert(
        "INSERT INTO skus(slug,display,material,grade,manufacturer,packing,created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (db.slugify(display), display, material, grade, manufacturer, packing, db.now()),
    )
    register(conn, material, grade, manufacturer)
    db.log(conn, "sku", sid, "create", "New stock line %s" % display,
           {"material": material, "grade": grade, "manufacturer": manufacturer})
    return sid


# ------------------------------------------------------------------ the tree
def register(conn, material: str, grade: str = "", manufacturer: str = "") -> None:
    """Make sure the master tree knows about this combination."""
    material = clean(material).upper()
    if not material:
        return
    conn.execute("INSERT OR IGNORE INTO catalog_materials(name,created_at) VALUES (?,?)",
                 (material, db.now()))
    grade = clean(grade).upper()
    if not grade:
        return
    conn.execute("INSERT OR IGNORE INTO catalog_grades(material,grade,created_at) VALUES (?,?,?)",
                 (material, grade, db.now()))
    manufacturer = clean(manufacturer)
    if not manufacturer:
        return
    conn.execute(
        "INSERT OR IGNORE INTO catalog_makers(material,grade,manufacturer,created_at) VALUES (?,?,?,?)",
        (material, grade, manufacturer, db.now()))


def _usage():
    """Stock and last-traded per (material, grade, manufacturer)."""
    rows = db.q(
        """SELECT s.material, s.grade, s.manufacturer, s.id AS sku_id,
                  COALESCE((SELECT SUM(l.qty_g - l.qty_allocated_g) FROM lots l
                            WHERE l.sku_id = s.id AND l.status='open'), 0) AS stock_g,
                  (SELECT COUNT(*) FROM deals d WHERE d.sku_id = s.id AND d.status != 'cancelled') AS deals,
                  (SELECT MAX(d.deal_date) FROM deals d WHERE d.sku_id = s.id AND d.status != 'cancelled') AS last_deal
           FROM skus s""")
    return [dict(r) for r in rows]


def _roll(entries, key):
    out = {}
    for e in entries:
        k = key(e)
        cur = out.setdefault(k, {"stock_g": 0, "deals": 0, "lines": 0, "last_deal": None, "sku_id": None})
        cur["stock_g"] += e["stock_g"]
        cur["deals"] += e["deals"]
        cur["lines"] += 1
        cur["sku_id"] = e["sku_id"]
        if e["last_deal"] and (cur["last_deal"] is None or e["last_deal"] > cur["last_deal"]):
            cur["last_deal"] = e["last_deal"]
    return out


def tree() -> List[Dict[str, Any]]:
    """The whole master tree with usage attached, for the Setup screen."""
    used = _usage()
    materials = [r["name"] for r in db.q("SELECT name FROM catalog_materials ORDER BY name")]
    grades = [(r["material"], r["grade"]) for r in
              db.q("SELECT material, grade FROM catalog_grades ORDER BY material, grade")]
    makers = [(r["material"], r["grade"], r["manufacturer"]) for r in
              db.q("SELECT material, grade, manufacturer FROM catalog_makers "
                   "ORDER BY material, grade, manufacturer")]

    by_mat = _roll(used, lambda e: e["material"])
    by_grade = _roll(used, lambda e: (e["material"], e["grade"]))
    by_maker = _roll(used, lambda e: (e["material"], e["grade"], e["manufacturer"]))
    blank = {"stock_g": 0, "deals": 0, "lines": 0, "last_deal": None, "sku_id": None}

    out = []
    for m in materials:
        node = {"material": m, **by_mat.get(m, blank), "grades": []}
        for (gm, g) in grades:
            if gm != m:
                continue
            gnode = {"grade": g, **by_grade.get((m, g), blank), "manufacturers": []}
            for (km, kg, maker) in makers:
                if km != m or kg != g:
                    continue
                gnode["manufacturers"].append({"manufacturer": maker, **by_maker.get((m, g, maker), blank)})
            node["grades"].append(gnode)
        out.append(node)
    return out


def add_material(conn, name: str) -> str:
    name = clean(name).upper()
    if not name:
        raise ValueError("Material name is required")
    register(conn, name)
    db.log(conn, "catalog", None, "add_material", "Added material %s" % name, {"material": name})
    return name


def add_grade(conn, material: str, grade: str) -> Dict[str, str]:
    material, grade = clean(material).upper(), clean(grade).upper()
    if not material or not grade:
        raise ValueError("Material and grade are both required")
    register(conn, material, grade)
    db.log(conn, "catalog", None, "add_grade", "Added grade %s %s" % (material, grade),
           {"material": material, "grade": grade})
    return {"material": material, "grade": grade}


def add_maker(conn, material: str, grade: str, manufacturer: str) -> Dict[str, str]:
    material, grade = clean(material).upper(), clean(grade).upper()
    manufacturer = clean(manufacturer)
    if not (material and grade and manufacturer):
        raise ValueError("Material, grade and manufacturer are all required")
    register(conn, material, grade, manufacturer)
    db.log(conn, "catalog", None, "add_maker",
           "Added %s for %s %s" % (manufacturer, material, grade),
           {"material": material, "grade": grade, "manufacturer": manufacturer})
    return {"material": material, "grade": grade, "manufacturer": manufacturer}


def _traded(material, grade=None, manufacturer=None) -> int:
    where, args = ["s.material = ?"], [material]
    if grade is not None:
        where.append("s.grade = ?"); args.append(grade)
    if manufacturer is not None:
        where.append("s.manufacturer = ?"); args.append(manufacturer)
    return db.scalar(
        "SELECT COUNT(*) FROM deals d JOIN skus s ON s.id = d.sku_id "
        "WHERE d.status != 'cancelled' AND " + " AND ".join(where), args)


def remove(conn, material: str, grade: Optional[str] = None,
           manufacturer: Optional[str] = None) -> None:
    """Only ever removes catalogue entries that carry no history."""
    material = clean(material).upper()
    grade = clean(grade).upper() if grade else None
    manufacturer = clean(manufacturer) if manufacturer else None
    n = _traded(material, grade, manufacturer)
    if n:
        raise ValueError("Cannot remove - %d deal%s already use it" % (n, "" if n == 1 else "s"))

    if manufacturer:
        conn.execute("DELETE FROM catalog_makers WHERE material=? AND grade=? AND manufacturer=?",
                     (material, grade, manufacturer))
    elif grade:
        conn.execute("DELETE FROM catalog_makers WHERE material=? AND grade=?", (material, grade))
        conn.execute("DELETE FROM catalog_grades WHERE material=? AND grade=?", (material, grade))
    else:
        conn.execute("DELETE FROM catalog_makers WHERE material=?", (material,))
        conn.execute("DELETE FROM catalog_grades WHERE material=?", (material,))
        conn.execute("DELETE FROM catalog_materials WHERE name=?", (material,))
    db.log(conn, "catalog", None, "remove", "Removed %s" %
           " / ".join(x for x in (material, grade, manufacturer) if x), {})


_STOCK_JOIN = """
    LEFT JOIN (SELECT sku_id, SUM(qty_g - qty_allocated_g) AS stock_g
               FROM lots WHERE status='open' GROUP BY sku_id) st ON st.sku_id = s.id
"""

_LEVEL_COL = {"material": "s.material", "grade": "s.grade", "manufacturer": "s.manufacturer"}


def options(level: str, material: Optional[str] = None, grade: Optional[str] = None,
            in_stock: bool = False) -> List[Dict[str, Any]]:
    """One rung of material -> grade -> manufacturer, narrowed by the rungs above.

    Names come from the master tree so a line can be offered before it has ever
    been traded; stock and recency come from what is actually on the books.
    """
    if level not in ("material", "grade", "manufacturer"):
        raise ValueError("level must be material, grade or manufacturer")
    material = clean(material).upper() if material else None
    grade = clean(grade).upper() if grade else None
    used = _usage()

    if level == "material":
        names = [r["name"] for r in db.q("SELECT name FROM catalog_materials")]
        stats = _roll(used, lambda e: e["material"])
        keys = {n: n for n in names}
    elif level == "grade":
        names = [r["grade"] for r in
                 db.q("SELECT grade FROM catalog_grades WHERE material=?", (material,))]
        stats = _roll([e for e in used if e["material"] == material], lambda e: e["grade"])
        keys = {n: n for n in names}
    else:
        names = [r["manufacturer"] for r in
                 db.q("SELECT manufacturer FROM catalog_makers WHERE material=? AND grade=?",
                      (material, grade))]
        stats = _roll([e for e in used if e["material"] == material and e["grade"] == grade],
                      lambda e: e["manufacturer"])
        keys = {n: n for n in names}

    blank = {"stock_g": 0, "deals": 0, "lines": 0, "last_deal": None, "sku_id": None}
    out = [dict(value=n, **stats.get(keys[n], blank)) for n in sorted(set(names))]
    if in_stock:
        out = [o for o in out if o["stock_g"] > 0]
    out.sort(key=lambda o: (-o["stock_g"], o["last_deal"] is None,
                            "" if o["last_deal"] is None else o["last_deal"], o["value"]))
    return out


def resolve(material: str, grade: str, manufacturer: str = "") -> Optional[Dict[str, Any]]:
    return db.row_to_dict(db.q1(
        "SELECT * FROM skus WHERE material=? AND grade=? AND manufacturer=?",
        (clean(material).upper(), clean(grade).upper(), clean(manufacturer))))


def search_skus(term: str = "", limit: int = 8, in_stock_only: bool = False) -> List[Dict[str, Any]]:
    where, args = [], []
    if term:
        where.append("(slug LIKE ? OR display LIKE ?)")
        args += ["%%%s%%" % db.slugify(term), "%%%s%%" % term]
    if in_stock_only:
        where.append("stock_g > 0")
    inner = """
        SELECT s.*, COALESCE(st.stock_g, 0) AS stock_g,
          (SELECT MAX(d.deal_date) FROM deals d WHERE d.sku_id=s.id) AS last_deal,
          (SELECT COUNT(*) FROM deals d WHERE d.sku_id=s.id) AS deal_count
        FROM skus s %s
    """ % _STOCK_JOIN
    sql = "SELECT * FROM (%s)" % inner
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY stock_g DESC, (last_deal IS NULL), last_deal DESC, deal_count DESC LIMIT ?"
    return [dict(r) for r in db.q(sql, args + [limit])]


def get_sku(sku_id: int) -> Optional[Dict[str, Any]]:
    return db.row_to_dict(db.q1("SELECT * FROM skus WHERE id=?", (sku_id,)))
