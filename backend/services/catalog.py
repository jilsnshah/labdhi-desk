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


# ------------------------------------------------------------------ GSTIN
# A GSTIN is two digits of state, the holder's ten-character PAN, an entity
# number, a literal Z and a check character. The check character is computed
# over the first fourteen, so most single-key typos are caught before they
# become a second party with a nearly identical number.
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")
_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

STATES = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "26": "Dadra & Nagar Haveli and Daman & Diu", "27": "Maharashtra",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu",
    "34": "Puducherry", "35": "Andaman & Nicobar", "36": "Telangana", "37": "Andhra Pradesh",
    "38": "Ladakh", "97": "Other Territory",
}


def normalize_gstin(value) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def _gstin_checksum_ok(g: str) -> bool:
    total = 0
    for i, ch in enumerate(g[:14]):
        v = _B36.index(ch) * (2 if i % 2 else 1)
        total += v // 36 + v % 36
    return _B36[(36 - total % 36) % 36] == g[14]


def gstin_problem(g: str) -> Optional[str]:
    """None if the GSTIN is sound, otherwise what is wrong with it."""
    if not g:
        return None
    if not GSTIN_RE.match(g):
        return "not in GSTIN format"
    if not _gstin_checksum_ok(g):
        return "check character does not match - likely a typo"
    return None


def pan_from_gstin(g: str) -> str:
    return g[2:12] if g and GSTIN_RE.match(g) else ""


def state_of(g: str) -> str:
    return STATES.get((g or "")[:2], "") if g else ""


# ------------------------------------------------------------------ warehouses
def list_warehouses() -> List[Dict[str, Any]]:
    """Every stock location, with what is sitting in it right now."""
    rows = db.q(
        """SELECT w.name, w.location,
                  COALESCE((SELECT SUM(l.qty_g - l.qty_allocated_g) FROM lots l
                            WHERE l.warehouse = w.name AND l.status = 'open'), 0) AS stock_g,
                  (SELECT COUNT(*) FROM lots l WHERE l.warehouse = w.name
                   AND l.status != 'cancelled') AS lots
           FROM warehouses w ORDER BY stock_g DESC, w.name""")
    return [dict(r) for r in rows]


def add_warehouse(conn, name: str, location: Optional[str] = None) -> str:
    name = clean(name)
    if not name:
        raise ValueError("Warehouse name is required")
    exists = db.q1("SELECT 1 FROM warehouses WHERE name=?", (name,))
    if not exists:
        conn.execute("INSERT OR IGNORE INTO warehouses(name,location,created_at) VALUES (?,?,?)",
                     (name, clean(location) or None, db.now()))
        db.log(conn, "warehouse", None, "create", "Added warehouse %s" % name, {"name": name})
    return name


def save_warehouse(conn, name: str, location: Optional[str] = None,
                   old_name: Optional[str] = None) -> str:
    """Add a warehouse, or edit one - including renaming it.

    Lots and deals record the warehouse by name, so a rename is carried through
    every row that mentions it inside the same transaction. A sale drawn from two
    warehouses records both ("Aslali, Mundra"), so those are rewritten name by
    name rather than by a blind string replace that could hit a longer name.
    """
    name = clean(name)
    if not name:
        raise ValueError("Warehouse name is required")
    old_name = clean(old_name) if old_name else None

    if not old_name or old_name == name:
        if not db.q1("SELECT 1 FROM warehouses WHERE name=?", (name,)):
            return add_warehouse(conn, name, location)
        if location is not None:
            conn.execute("UPDATE warehouses SET location=? WHERE name=?", (clean(location) or None, name))
            db.log(conn, "warehouse", None, "update", "Updated %s" % name, {"location": location})
        return name

    if not db.q1("SELECT 1 FROM warehouses WHERE name=?", (old_name,)):
        raise ValueError("No warehouse called %s" % old_name)
    if db.q1("SELECT 1 FROM warehouses WHERE name=?", (name,)):
        raise ValueError("A warehouse called %s already exists" % name)

    conn.execute("UPDATE warehouses SET name=?, location=COALESCE(?, location) WHERE name=?",
                 (name, (clean(location) or None) if location is not None else None, old_name))
    conn.execute("UPDATE lots SET warehouse=? WHERE warehouse=?", (name, old_name))
    for r in db.q("SELECT id, warehouse FROM deals WHERE warehouse LIKE ?", ("%" + old_name + "%",)):
        parts = [name if p.strip() == old_name else p.strip() for p in r["warehouse"].split(",")]
        joined = ", ".join(parts)
        if joined != r["warehouse"]:
            conn.execute("UPDATE deals SET warehouse=? WHERE id=?", (joined, r["id"]))
    db.log(conn, "warehouse", None, "rename", "Renamed %s to %s" % (old_name, name),
           {"from": old_name, "to": name})
    return name


def remove_warehouse(conn, name: str) -> None:
    n = db.scalar("SELECT COUNT(*) FROM lots WHERE warehouse=? AND status != 'cancelled'", (name,))
    if n:
        raise ValueError("Cannot remove - %d lot%s are recorded in %s"
                         % (n, "" if n == 1 else "s", name))
    conn.execute("DELETE FROM warehouses WHERE name=?", (name,))
    db.log(conn, "warehouse", None, "remove", "Removed warehouse %s" % name, {"name": name})


def list_parties(term: str = "") -> List[Dict[str, Any]]:
    """Every counterparty, for the Setup screen. There is no buyer/seller split:
    the same firm is on either side of a deal from one week to the next."""
    where, args = [], []
    if term:
        t = "%%%s%%" % term.strip().lower()
        where.append("(LOWER(p.name) LIKE ? OR LOWER(COALESCE(p.address,'')) LIKE ? "
                     "OR LOWER(COALESCE(p.gstin,'')) LIKE ? OR COALESCE(p.phone,'') LIKE ?)")
        args += [t, t, t, "%%%s%%" % term.strip()]
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = db.q(
        """SELECT * FROM (
             SELECT p.*,
                    (SELECT COUNT(*) FROM deals d WHERE d.party_id = p.id
                     AND d.status != 'cancelled') AS deal_count,
                    (SELECT MAX(d.deal_date) FROM deals d WHERE d.party_id = p.id
                     AND d.status != 'cancelled') AS last_deal
             FROM parties p {clause}
           ) ranked
           ORDER BY (last_deal IS NULL), last_deal DESC, deal_count DESC, name""".format(clause=clause),
        args)
    out = []
    for r in rows:
        d = dict(r)
        d["state"] = state_of(d.get("gstin"))
        out.append(d)
    return out


def save_party(conn, name: str, phone: str = "", city: str = "",
               is_supplier: bool = False, is_customer: bool = False,
               party_id: Optional[int] = None, address: str = "",
               gstin: str = "", pan: str = "", strict: bool = True,
               merge: bool = False, quiet: bool = False) -> int:
    """Create or edit a counterparty: name, phone, address, GSTIN, PAN.

    Identity is the GSTIN when there is one; otherwise the name. PAN is taken
    from the GSTIN and cannot disagree with it.

    `merge` is for imports: a blank incoming field leaves the existing value
    alone, so loading a sheet with no phone numbers never erases the ones
    already typed in. `strict` rejects a GSTIN whose check character fails; the
    importer turns it off and reports those instead of silently dropping them.
    """
    name = clean(name)
    if not name:
        raise ValueError("Name is required")
    phone, city = clean(phone), clean(city)
    address = (address or "").strip()
    gstin = normalize_gstin(gstin)
    if gstin and strict:
        problem = gstin_problem(gstin)
        if problem:
            raise ValueError("GSTIN %s is %s" % (gstin, problem))
    pan = pan_from_gstin(gstin) or normalize_gstin(pan)
    if pan and strict and not PAN_RE.match(pan):
        raise ValueError("PAN %s is not in PAN format" % pan)

    # ---- who is this?
    adopted = False
    if party_id is None and gstin:
        row = db.q1("SELECT id FROM parties WHERE gstin=?", (gstin,))
        if row:
            party_id, adopted = int(row["id"]), True
    base = db.slugify(name)
    holder = db.q1("SELECT id, gstin FROM parties WHERE slug=?", (base,))
    if party_id is None and holder is not None:
        # Same name already on the books. It is the same party unless both
        # carry GSTINs and they differ - then it is another branch of the firm.
        if not gstin or not holder["gstin"] or holder["gstin"] == gstin:
            party_id, adopted = int(holder["id"]), True

    # ---- the name slug stays unique; a second branch is told apart by GSTIN
    slug = base
    if holder is not None and (party_id is None or int(holder["id"]) != int(party_id)):
        if gstin:
            slug = "%s-%s" % (base, gstin.lower())
        else:
            raise ValueError("Another party is already called %s" % name)

    if party_id:
        cur = db.q1("SELECT * FROM parties WHERE id=?", (int(party_id),))
        if cur is None:
            raise ValueError("No such party")
        keep = merge or adopted
        pick = lambda new, old: new if (new or not keep) else old          # noqa: E731
        final_gstin = pick(gstin, cur["gstin"]) or None
        final_pan = pan_from_gstin(final_gstin or "") or pick(pan, cur["pan"]) or None
        if final_gstin:
            clash = db.q1("SELECT name FROM parties WHERE gstin=? AND id != ?",
                          (final_gstin, int(party_id)))
            if clash:
                raise ValueError("GSTIN %s already belongs to %s" % (final_gstin, clash["name"]))
        # keep the stored slug unless the name itself changed
        if cur["name"] == name:
            slug = cur["slug"]
        conn.execute(
            "UPDATE parties SET name=?, slug=?, phone=?, city=?, address=?, gstin=?, pan=? WHERE id=?",
            (name, slug, pick(phone, cur["phone"]) or None, pick(city, cur["city"]) or None,
             pick(address, cur["address"]) or None, final_gstin, final_pan, int(party_id)))
        if not quiet:
            db.log(conn, "party", int(party_id), "update", "Updated %s" % name,
                   {"name": name, "gstin": final_gstin})
        return int(party_id)

    if gstin:
        clash = db.q1("SELECT name FROM parties WHERE gstin=?", (gstin,))
        if clash:
            raise ValueError("GSTIN %s already belongs to %s" % (gstin, clash["name"]))
    new_id = conn.insert(
        "INSERT INTO parties(name,slug,is_supplier,is_customer,is_transporter,city,phone,address,"
        "gstin,pan,notes,created_at) VALUES (?,?,?,?,0,?,?,?,?,?,NULL,?)",
        (name, slug, 1 if is_supplier else 0, 1 if is_customer else 0,
         city or None, phone or None, address or None, gstin or None, pan or None, db.now()))
    if not quiet:
        db.log(conn, "party", new_id, "create", "Added %s" % name, {"name": name, "gstin": gstin})
    return new_id


def remove_party(conn, party_id: int) -> None:
    n = db.scalar("SELECT COUNT(*) FROM deals WHERE party_id=? AND status != 'cancelled'",
                  (int(party_id),))
    if n:
        raise ValueError("Cannot remove - %d deal%s already use this party" % (n, "" if n == 1 else "s"))
    row = db.q1("SELECT name FROM parties WHERE id=?", (int(party_id),))
    conn.execute("DELETE FROM parties WHERE id=?", (int(party_id),))
    db.log(conn, "party", int(party_id), "remove",
           "Removed %s" % (row["name"] if row else party_id), {})


def search_parties(term: str = "", role: Optional[str] = None, limit: int = 8) -> List[Dict[str, Any]]:
    """Ranked chips: recent + frequent first, then name match."""
    where, args = [], []
    # No buyer/seller split: anyone can be on either side. The only thing kept
    # out of the picker is a transporter who has never been a trading party.
    if role in ("supplier", "customer"):
        where.append("NOT (p.is_transporter = 1 AND p.id NOT IN (SELECT party_id FROM deals))")
    elif role == "transporter":
        where.append("p.is_transporter = 1")
    if term:
        where.append("(p.slug LIKE ? OR p.name LIKE ? OR UPPER(COALESCE(p.gstin,'')) LIKE ?)")
        args += ["%%%s%%" % db.slugify(term), "%%%s%%" % term, "%%%s%%" % term.strip().upper()]
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    side = "buy" if role == "supplier" else ("sell" if role == "customer" else None)
    side_filter = "AND d.side='%s'" % side if side else ""
    # The ordering wraps the query rather than sorting on aliases in place:
    # Postgres accepts a bare alias in ORDER BY but not one inside an
    # expression, so `(last_deal IS NULL)` only works once it is a real column.
    sql = """
        SELECT * FROM (
            SELECT p.*,
                   (SELECT COUNT(*) FROM deals d WHERE d.party_id=p.id {sf}) AS deal_count,
                   (SELECT MAX(d.deal_date) FROM deals d WHERE d.party_id=p.id {sf}) AS last_deal
            FROM parties p
            {clause}
        ) ranked
        ORDER BY (last_deal IS NULL), last_deal DESC, deal_count DESC, name
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
