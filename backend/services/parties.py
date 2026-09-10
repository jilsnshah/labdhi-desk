"""Parties: the one record of every firm the desk deals with.

A party is picked by id in every ticket - as the buyer, the seller or the
transporter - and created only through the party form. Nothing else in the
system stores a party's name, so correcting a name or an address corrects it
on every deal at once.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .. import db, gst


def _row(r) -> Dict[str, Any]:
    d = dict(r)
    d["state"] = gst.state_name(d.get("state_code"))
    return d


_SEARCH = ("(LOWER(p.name) LIKE ? OR p.slug LIKE ? OR LOWER(COALESCE(p.gstin,'')) LIKE ? "
           "OR COALESCE(p.phone,'') LIKE ? OR LOWER(COALESCE(p.address,'')) LIKE ?)")


def list_parties(q: str = "", state_code: Optional[str] = None, holding: bool = False,
                 limit: Optional[int] = None, offset: int = 0) -> Dict[str, Any]:
    """One page of parties, most recently traded first.

    `holding` narrows to suppliers whose stock is still on the books - the
    only parties a "bought from" filter can usefully offer.
    """
    limit, offset = db.page_args(limit, offset)
    where, args = [], []
    like = db.like(q)
    if like:
        where.append(_SEARCH)
        args += [like, "%%%s%%" % db.slugify(q), like, "%%%s%%" % q.strip(), like]
    if state_code:
        where.append("p.state_code = ?"); args.append(state_code)
    if holding:
        where.append("EXISTS (SELECT 1 FROM lots l WHERE l.supplier_id = p.id AND l.status = 'open')")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.scalar("SELECT COUNT(*) FROM parties p " + clause, args)
    # Sorting on an alias inside an expression is SQLite-only; wrapping the
    # query makes last_deal a real column for Postgres too.
    rows = db.q(
        """SELECT * FROM (
             SELECT p.*,
                    (SELECT COUNT(*) FROM deals d WHERE d.party_id = p.id
                     AND d.status != 'cancelled') AS deal_count,
                    (SELECT MAX(d.deal_date) FROM deals d WHERE d.party_id = p.id
                     AND d.status != 'cancelled') AS last_deal
             FROM parties p {clause}
           ) ranked
           ORDER BY (last_deal IS NULL), last_deal DESC, deal_count DESC, name
           LIMIT ? OFFSET ?""".format(clause=clause), args + [limit, offset])
    return db.page([_row(r) for r in rows], total, limit, offset)


def get_party(party_id: int) -> Optional[Dict[str, Any]]:
    row = db.q1(
        """SELECT p.*,
                  (SELECT COUNT(*) FROM deals d WHERE d.party_id = p.id
                   AND d.status != 'cancelled') AS deal_count,
                  (SELECT MAX(d.deal_date) FROM deals d WHERE d.party_id = p.id
                   AND d.status != 'cancelled') AS last_deal
           FROM parties p WHERE p.id = ?""", (int(party_id),))
    return _row(row) if row else None


def require(party_id, what: str = "party") -> Dict[str, Any]:
    party = get_party(int(party_id)) if party_id else None
    if party is None:
        raise ValueError("Choose a %s from the list, or add a new one" % what)
    return party


def check_gstin(value: str) -> Dict[str, Any]:
    """Everything the party form wants to know about a GSTIN as it is typed."""
    g = gst.normalize(value)
    trouble = gst.problem(g) if g else "empty"
    owner = db.q1("SELECT id, name FROM parties WHERE gstin = ?", (g,)) if g else None
    code = gst.state_code_of(g) if not trouble else ""
    return {
        "gstin": g, "valid": trouble is None, "problem": trouble,
        "pan": gst.pan_of(g) if not trouble else "",
        "state_code": code, "state": gst.state_name(code),
        "party": dict(owner) if owner else None,
    }


def save_party(conn, name: str, phone: str = "", address: str = "", gstin: str = "",
               pan: str = "", state_code: str = "", party_id: Optional[int] = None,
               strict: bool = True, merge: bool = False, quiet: bool = False) -> int:
    """Create or edit a party: name, GSTIN, PAN, state, phone, address.

    Identity is the GSTIN when there is one, otherwise the name. PAN and state
    are read off the GSTIN and cannot disagree with it; without a GSTIN they
    are taken as entered.

    Typed into the party form (`merge` off), a party that already exists is
    refused with its name, so the trader picks the existing record instead of
    making a second one. The importer (`merge` on) folds a repeated party into
    the one on file and never blanks a field the sheet happens to leave empty.
    `strict` rejects a GSTIN whose check character fails; the importer turns it
    off and reports those instead of silently dropping them.
    """
    name = db.clean(name)
    if not name:
        raise ValueError("Name is required")
    phone = db.clean(phone)
    address = (address or "").strip()
    gstin = gst.normalize(gstin)
    if gstin and strict:
        trouble = gst.problem(gstin)
        if trouble:
            raise ValueError("GSTIN %s is %s" % (gstin, trouble))
    pan = gst.pan_of(gstin) or gst.normalize(pan)
    if pan and strict and not gst.PAN_RE.match(pan):
        raise ValueError("PAN %s is not in PAN format" % pan)
    state_code = gst.state_code_of(gstin) or (state_code if state_code in gst.STATES else "")

    # ---- who is this?
    adopted = False
    if party_id is None and gstin:
        row = db.q1("SELECT id, name FROM parties WHERE gstin=?", (gstin,))
        if row:
            if not merge:
                raise ValueError("GSTIN %s already belongs to %s" % (gstin, row["name"]))
            party_id, adopted = int(row["id"]), True
    base = db.slugify(name)
    holder = db.q1("SELECT id, gstin, name FROM parties WHERE slug=?", (base,))
    if party_id is None and holder is not None:
        # Same name already on the books. It is the same party unless both
        # carry GSTINs and they differ - then it is another branch of the firm.
        if not gstin or not holder["gstin"] or holder["gstin"] == gstin:
            if not merge:
                raise ValueError("%s is already on the list - pick it instead" % holder["name"])
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
        final_pan = gst.pan_of(final_gstin or "") or pick(pan, cur["pan"]) or None
        final_state = (gst.state_code_of(final_gstin or "")
                       or pick(state_code, cur["state_code"]) or None)
        if final_gstin:
            clash = db.q1("SELECT name FROM parties WHERE gstin=? AND id != ?",
                          (final_gstin, int(party_id)))
            if clash:
                raise ValueError("GSTIN %s already belongs to %s" % (final_gstin, clash["name"]))
        if cur["name"] == name:
            slug = cur["slug"]          # keep the stored slug unless the name itself changed
        conn.execute(
            "UPDATE parties SET name=?, slug=?, phone=?, address=?, gstin=?, pan=?, state_code=? "
            "WHERE id=?",
            (name, slug, pick(phone, cur["phone"]) or None, pick(address, cur["address"]) or None,
             final_gstin, final_pan, final_state, int(party_id)))
        if not quiet:
            db.log(conn, "party", int(party_id), "update", "Updated %s" % name,
                   {"name": name, "gstin": final_gstin})
        return int(party_id)

    new_id = conn.insert(
        "INSERT INTO parties(name,slug,gstin,pan,state_code,phone,address,created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (name, slug, gstin or None, pan or None, state_code or None, phone or None,
         address or None, db.now()))
    if not quiet:
        db.log(conn, "party", new_id, "create", "Added %s" % name, {"name": name, "gstin": gstin})
    return new_id


def remove_party(conn, party_id: int) -> None:
    party = require(party_id)
    used = db.scalar(
        "SELECT COUNT(*) FROM deals WHERE (party_id=? OR transporter_id=?)", (party["id"], party["id"]))
    if used:
        raise ValueError("Cannot remove %s - %d deal%s refer to it"
                         % (party["name"], used, "" if used == 1 else "s"))
    conn.execute("DELETE FROM parties WHERE id=?", (party["id"],))
    db.log(conn, "party", party["id"], "remove", "Removed %s" % party["name"], {})


# ------------------------------------------------------------------ states
def list_states(q: str = "", limit: Optional[int] = None, offset: int = 0) -> Dict[str, Any]:
    limit, offset = db.page_args(limit, offset, default=50)
    where, args = [], []
    like = db.like(q)
    if like:
        where.append("(LOWER(s.name) LIKE ? OR s.code LIKE ?)"); args += [like, like]
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.scalar("SELECT COUNT(*) FROM states s " + clause, args)
    rows = db.q(
        """SELECT s.code, s.name,
                  (SELECT COUNT(*) FROM parties p WHERE p.state_code = s.code) AS parties
           FROM states s {clause} ORDER BY s.code LIMIT ? OFFSET ?""".format(clause=clause),
        args + [limit, offset])
    return db.page(db.dicts(rows), total, limit, offset)
