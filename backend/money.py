"""Exact integer money/quantity arithmetic.

Two units, both integers, so nothing ever drifts:
  qty   -> grams          (20,000 kg  == 20_000_000 g)
  rate  -> paise per kg   (Rs 98.25/kg == 9825)
  value -> paise          (Rs 1        == 100)

Floats never touch a stored number. They appear only in JSON for display.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

KG = 1000          # grams in a kg
TON = 1_000_000    # grams in a metric ton
RUPEE = 100        # paise in a rupee


# ----------------------------------------------------------------- parsing
_QTY_RE = re.compile(
    r"^\s*([0-9][0-9,_ ]*\.?[0-9]*)\s*(kgs?|kilos?|k|mts?|tons?|tonnes?|t|bags?|b)?\s*$",
    re.I,
)
_UNIT_G = {
    "kg": KG, "kgs": KG, "kilo": KG, "kilos": KG, "k": KG,
    "mt": TON, "mts": TON, "ton": TON, "tons": TON, "tonne": TON, "tonnes": TON, "t": TON,
    "bag": 25 * KG, "bags": 25 * KG, "b": 25 * KG,
}


def parse_qty(text, default_unit: str = "kg") -> int:
    """'20,000 kg' | '20 MT' | '20t' | 20000 -> grams."""
    if isinstance(text, (int, float)):
        return int(round(float(text) * _UNIT_G[default_unit]))
    m = _QTY_RE.match(str(text or ""))
    if not m:
        raise ValueError("could not read quantity: %r" % (text,))
    num = float(m.group(1).replace(",", "").replace("_", "").replace(" ", ""))
    unit = (m.group(2) or default_unit).lower()
    return int(round(num * _UNIT_G[unit]))


_RATE_RE = re.compile(r"^\s*(?:rs\.?|inr|₹)?\s*([0-9][0-9,]*\.?[0-9]*)\s*(\+|plus)?\s*(?:/-?)?\s*$", re.I)


def parse_rate(text) -> Tuple[int, bool]:
    """'98.25+' -> (9825, True). The trailing '+' means basic rate, GST extra."""
    if isinstance(text, (int, float)):
        return int(round(float(text) * RUPEE)), True
    m = _RATE_RE.match(str(text or ""))
    if not m:
        raise ValueError("could not read rate: %r" % (text,))
    paise = int(round(float(m.group(1).replace(",", "")) * RUPEE))
    return paise, bool(m.group(2))


# ----------------------------------------------------------------- maths
def value_paise(qty_g: int, rate_paise: int) -> int:
    """grams x paise/kg -> paise, rounded half-up. Exact for whole kg."""
    num = qty_g * rate_paise
    q, r = divmod(abs(num), KG)
    if r * 2 >= KG:
        q += 1
    return -q if num < 0 else q


def weighted_rate(pairs) -> int:
    """[(qty_g, rate_paise)] -> weighted average paise/kg, half-up."""
    tq = sum(q for q, _ in pairs)
    if tq <= 0:
        return 0
    total = sum(q * r for q, r in pairs)
    q, rem = divmod(abs(total), tq)
    if rem * 2 >= tq:
        q += 1
    return -q if total < 0 else q


# ----------------------------------------------------------------- display
def kg(qty_g: int) -> float:
    return round(qty_g / KG, 3)


def rupees(paise: int) -> float:
    return round(paise / RUPEE, 2)


def fmt_qty(qty_g: int) -> str:
    if qty_g and qty_g % TON == 0:
        return "%g MT" % (qty_g / TON)
    return "{:,.3f} kg".format(qty_g / KG).replace(".000", "")


def fmt_money(paise: int) -> str:
    """Indian grouping: 12,34,567."""
    neg = paise < 0
    whole, frac = divmod(abs(paise), RUPEE)
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    out = "%s.%02d" % (s, frac)
    return ("-Rs " if neg else "Rs ") + out


def money_json(paise: Optional[int]) -> Optional[dict]:
    if paise is None:
        return None
    return {"paise": paise, "rupees": rupees(paise), "text": fmt_money(paise)}
