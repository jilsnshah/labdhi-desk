"""Load parties from Tally's "Updation of Party GSTIN/UIN" export.

    python -m backend.import_parties "labdhi exim ledger address.xlsx" "om ledger address.xlsx"
    python -m backend.import_parties --dry-run  <files...>

Needs openpyxl (requirements-dev.txt); the running app does not.

How a sheet is read:
  * the header is the row whose first cell is "Sl No."
  * a numbered row is a party
  * an un-numbered row under it (Tally writes "DELIVERY") is that party's
    ship-to address, not a party of its own, and is skipped
  * a row whose State is "Not Applicable" is an accounting ledger - duty,
    tax, suspense - and is excluded
  * Tally's escape sequences (_x0004_ and friends) are stripped

How duplicates are resolved:
  * a GSTIN identifies a party, across both sheets; its PAN comes from it
  * the same GSTIN seen twice keeps the first name and the fuller address
  * a row with no GSTIN is matched by name; if that name belongs to exactly
    one GSTIN party, it is that party
  * two GSTINs sharing a PAN are two branches of one firm - separate parties

Re-running is safe: matching is by GSTIN then name, and an import never blanks
a field someone has already filled in (phone numbers, say, which the sheet
does not carry).
"""
from __future__ import annotations

import re
import sys
from typing import Any, Dict, List, Tuple

from . import db, gst
from .services import parties as party_svc

HEADER = "Sl No."
DEFAULT_FILES = ["labdhi exim ledger address.xlsx", "om ledger address.xlsx"]
_TALLY_ESCAPE = re.compile(r"_x[0-9A-Fa-f]{4}_")


def _clean(value) -> str:
    s = _TALLY_ESCAPE.sub("", str(value or ""))
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"(\s*,\s*)+", ", ", s).strip(" ,")
    return s


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


_OLD = re.compile(r"(\bold\b|-\s*o$)", re.I)
_NOISE = re.compile(r"\b(pvt|private|ltd|limited|llp|co|company|and|the|india|p)\b|\(.*?\)|-.*$", re.I)


def _is_old(name: str) -> bool:
    """Tally keeps retired ledgers as "X - OLD"; never prefer that name."""
    return bool(_OLD.search(name.strip()))


def _core(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _NOISE.sub(" ", name.lower())).strip()


def _looks_different(a: str, b: str) -> bool:
    """Two names on one GSTIN that share no real word are probably two firms,
    which cannot legally share a GSTIN - one of them was keyed wrongly."""
    import difflib
    ca, cb = _core(a), _core(b)
    if not ca or not cb:
        return False
    if set(ca.split()) & set(cb.split()):
        return False
    return difflib.SequenceMatcher(None, ca, cb).ratio() < 0.6


def _is_bank_ledger(name: str) -> bool:
    return bool(re.search(r"\bbank\b", name, re.I)) and bool(
        re.search(r"a/c|\d{6,}|\bcc\b|\bgst\b", name, re.I))


def read_rows(path: str) -> Tuple[List[Dict[str, str]], int]:
    try:
        import openpyxl
    except ImportError:                                       # pragma: no cover
        raise SystemExit("openpyxl is needed to read .xlsx: pip install -r requirements-dev.txt")
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    try:
        start = next(i for i, r in enumerate(rows) if r and str(r[0] or "").strip() == HEADER)
    except StopIteration:
        raise ValueError("%s: no '%s' header row" % (path, HEADER))

    parties, continuation = [], 0
    for r in rows[start + 1:]:
        r = list(r) + [None] * 8
        if not any(v not in (None, "") for v in r):
            continue
        if r[0] in (None, ""):
            continuation += 1
            continue
        parties.append({
            "name": _clean(r[1]), "address": _clean(r[2]), "state": _clean(r[3]),
            "gstin": gst.normalize(_clean(r[6])),
            "pan": gst.normalize(_clean(r[7])),
        })
    return parties, continuation


def plan(paths: List[str]) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    report: Dict[str, Any] = {
        "files": paths, "rows_read": 0, "continuation_rows": 0, "merged": 0,
        "excluded": [], "bad_gstin": [], "variants": {}, "suspect": {}, "banks": [],
    }
    by_gstin: Dict[str, Dict[str, str]] = {}
    by_name: Dict[str, Dict[str, str]] = {}

    for path in paths:
        rows, cont = read_rows(path)
        report["continuation_rows"] += cont
        for r in rows:
            report["rows_read"] += 1
            if not r["name"]:
                continue
            if r["state"].lower() == "not applicable":
                report["excluded"].append(r["name"])
                continue
            address = r["address"]
            if r["state"] and r["state"].lower() not in address.lower():
                address = "%s, %s" % (address, r["state"]) if address else r["state"]
            # The state is kept as a record reference; a GSTIN overrides it anyway.
            rec = {"name": r["name"], "address": address, "gstin": r["gstin"], "pan": r["pan"],
                   "state_code": gst.state_code_for_name(r["state"])}

            if rec["gstin"]:
                cur = by_gstin.get(rec["gstin"])
                if cur is None:
                    by_gstin[rec["gstin"]] = rec
                    problem = gst.problem(rec["gstin"])
                    if problem:
                        report["bad_gstin"].append((rec["name"], rec["gstin"], problem))
                    continue
                report["merged"] += 1
                if _key(cur["name"]) != _key(rec["name"]):
                    bucket = "suspect" if _looks_different(cur["name"], rec["name"]) else "variants"
                    report[bucket].setdefault(rec["gstin"], {cur["name"]}).add(rec["name"])
                    if _is_old(cur["name"]) and not _is_old(rec["name"]):
                        cur["name"] = rec["name"]
                if len(rec["address"]) > len(cur["address"]):
                    cur["address"] = rec["address"]
            else:
                k = _key(rec["name"])
                cur = by_name.get(k)
                if cur is None:
                    by_name[k] = rec
                    continue
                report["merged"] += 1
                if len(rec["address"]) > len(cur["address"]):
                    cur["address"] = rec["address"]
                if rec["pan"] and not cur["pan"]:
                    cur["pan"] = rec["pan"]

    # A GSTIN-less row that names exactly one GSTIN party is that party - the
    # other ledger simply never recorded the number.
    owners: Dict[str, List[str]] = {}
    for g, rec in by_gstin.items():
        owners.setdefault(_key(rec["name"]), []).append(g)
    for k in list(by_name):
        if len(owners.get(k, [])) == 1:
            rec, cur = by_name.pop(k), by_gstin[owners[k][0]]
            if not cur["address"]:
                cur["address"] = rec["address"]
            report["merged"] += 1

    records = list(by_gstin.values()) + list(by_name.values())
    report["banks"] = sorted(r["name"] for r in records if _is_bank_ledger(r["name"]))
    # a GSTIN flagged suspect is not also a harmless variant
    for g in report["suspect"]:
        report["variants"].pop(g, None)
    report["unique_parties"] = len(records)
    report["with_gstin"] = len(by_gstin)
    report["without_gstin"] = len(by_name)
    return records, report


def apply(records: List[Dict[str, str]]) -> Tuple[int, int]:
    created = updated = 0
    with db.tx() as conn:
        before = db.scalar("SELECT COUNT(*) FROM parties")
        for rec in records:
            party_svc.save_party(conn, name=rec["name"], address=rec["address"],
                                 gstin=rec["gstin"], pan=rec["pan"],
                                 state_code=rec.get("state_code", ""),
                                 strict=False, merge=True, quiet=True)
        after = db.scalar("SELECT COUNT(*) FROM parties")
        created = after - before
        updated = len(records) - created
        db.log(conn, "party", None, "import",
               "Imported parties: %d new, %d updated" % (created, updated),
               {"created": created, "updated": updated, "records": len(records)})
    return created, updated


def main(argv: List[str]) -> None:
    dry = "--dry-run" in argv
    paths = [a for a in argv if not a.startswith("--")] or DEFAULT_FILES
    db.init_db()
    records, r = plan(paths)
    print("read %d party rows from %d file(s); skipped %d ship-to rows"
          % (r["rows_read"], len(paths), r["continuation_rows"]))
    print("excluded %d accounting ledgers (State: Not Applicable)" % len(r["excluded"]))
    print("merged %d duplicates -> %d unique parties (%d with GSTIN, %d without)"
          % (r["merged"], r["unique_parties"], r["with_gstin"], r["without_gstin"]))
    if r["bad_gstin"]:
        print("GSTINs that fail validation (imported as-is, please check):")
        for name, g, why in r["bad_gstin"]:
            print("   %-15s %-40s %s" % (g, name[:40], why))
    keep = {rec["gstin"]: rec["name"] for rec in records if rec["gstin"]}
    if r["suspect"]:
        print("CHECK IN TALLY - different-looking firms on one GSTIN (merged; one is keyed wrong):")
        for g, names in sorted(r["suspect"].items()):
            print("   %-15s kept %-32s also %s" % (g, keep.get(g, "")[:32],
                  " | ".join(n for n in sorted(names) if n != keep.get(g))))
    if r["variants"]:
        print("spelling variants of one firm, merged (%d GSTINs), e.g.:" % len(r["variants"]))
        for g, names in sorted(r["variants"].items())[:8]:
            print("   %-15s kept %-32s also %s" % (g, keep.get(g, "")[:32],
                  " | ".join(n for n in sorted(names) if n != keep.get(g))))
    if r["banks"]:
        print("bank account ledgers imported as parties (%d): %s"
              % (len(r["banks"]), ", ".join(r["banks"][:5])))
    if dry:
        print("dry run - nothing written")
        return
    created, updated = apply(records)
    print("written to %s: %d new, %d updated"
          % ("Postgres" if db.IS_PG else db.DB_PATH, created, updated))


if __name__ == "__main__":
    main(sys.argv[1:])
