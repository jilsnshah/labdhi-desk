"""GSTIN rules and the GST state table. Pure functions, no database.

A GSTIN is two digits of state, the holder's ten-character PAN, an entity
number, a literal Z and a check character. The check character is computed over
the first fourteen, so most single-key typos are caught before they become a
second party with a nearly identical number.
"""
from __future__ import annotations

import re
from typing import Optional

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


def normalize(value) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def checksum_ok(g: str) -> bool:
    total = 0
    for i, ch in enumerate(g[:14]):
        v = _B36.index(ch) * (2 if i % 2 else 1)
        total += v // 36 + v % 36
    return _B36[(36 - total % 36) % 36] == g[14]


def problem(g: str) -> Optional[str]:
    """None if the GSTIN is sound, otherwise what is wrong with it."""
    if not g:
        return None
    if not GSTIN_RE.match(g):
        return "not in GSTIN format"
    if not checksum_ok(g):
        return "check character does not match - likely a typo"
    return None


def pan_of(g: str) -> str:
    return g[2:12] if g and GSTIN_RE.match(g) else ""


def state_code_of(g: str) -> str:
    code = (g or "")[:2]
    return code if code in STATES else ""


def state_name(code: Optional[str]) -> str:
    return STATES.get(code or "", "")


def state_code_for_name(name: str) -> str:
    """Tally writes the state as a name; map it back to its code."""
    key = re.sub(r"[^a-z]", "", (name or "").lower().replace("&", "and"))
    for code, label in STATES.items():
        if re.sub(r"[^a-z]", "", label.lower().replace("&", "and")) == key:
            return code
    return ""
