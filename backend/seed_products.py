"""Labdhi Exim's product list: what the desk can buy and sell.

    python -m backend.seed_products            # add anything missing
    python -m backend.seed_products --dry-run  # just print the list

Built from labdhiexim.com (the materials it lists, and the PVC makers whose
logos it shows as "brands we deal in") and from the makers' own grade codes.
Grade names are the codes printed on the bag and quoted in the trade.

Safe to re-run: a product that already exists is left alone, so this only ever
adds. Where the site names a material but no maker, the maker is "Unspecified"
- rename it in Setup › Manufacturers once the actual make is known.
"""
from __future__ import annotations

import sys

from . import db
from .services import products

PACK = "25 kg bag"

#  material,            grade,          manufacturer,           packing,  note (K-value / use)
CATALOG = [
    # ---- PVC suspension resin: the core line. The eight brands on the site first.
    ("PVC", "K-6701",  "Reliance",          PACK, "K67 rigid pipe, profile"),
    ("PVC", "K-6711",  "Reliance",          PACK, "K67 rigid and flexible"),
    ("PVC", "57-01",   "Reliance",          PACK, "K57 fittings, injection"),
    ("PVC", "S-65D",   "Formosa Plastics",  PACK, "K66-67 pipe"),
    ("PVC", "S-65",    "Formosa Plastics",  PACK, "K65"),
    ("PVC", "S-60",    "Formosa Plastics",  PACK, "K60 profiles, flooring"),
    ("PVC", "S-58",    "Formosa Plastics",  PACK, "K57-58 fittings"),
    ("PVC", "S-70",    "Formosa Plastics",  PACK, "K70 cable, film"),
    ("PVC", "LS100H",  "LG Chem",           PACK, "K67 pipe, window"),
    ("PVC", "LS100S",  "LG Chem",           PACK, "K66"),
    ("PVC", "LS080S",  "LG Chem",           PACK, "K61"),
    ("PVC", "LS130S",  "LG Chem",           PACK, "K71"),
    ("PVC", "S5702",   "Vynova",            PACK, "K57"),
    ("PVC", "267RC",   "INEOS",             PACK, "INOVYN suspension"),
    ("PVC", "S5736",   "INEOS",             PACK, "INOVYN suspension, low K"),
    ("PVC", "H-66",    "CGPC",              PACK, "K65-67"),
    ("PVC", "H-61",    "CGPC",              PACK, "low K, injection"),
    ("PVC", "SG660",   "SCG",               PACK, "medium K, pipe, film"),
    ("PVC", "SG610",   "SCG",               PACK, "low K"),
    ("PVC", "SG710",   "SCG",               PACK, "high K"),
    ("PVC", "SPVC 67S", "SABIC",            PACK, "K67 rigid extrusion"),
    ("PVC", "FS 6701", "Finolex",           PACK, "K67"),
    ("PVC", "SR10A",   "DCM Shriram",       PACK, "K67"),
    # ---- paste (emulsion) PVC
    ("PVC", "124",     "Chemplast Sanmar",  PACK, "paste, leather cloth, flooring"),
    ("PVC", "120",     "Chemplast Sanmar",  PACK, "paste, dot printing"),
    # ---- CPVC resin
    ("CPVC", "H829",         "Kaneka",   PACK, "hot-water pipe, fittings"),
    ("CPVC", "H727",         "Kaneka",   PACK, ""),
    ("CPVC", "TEMPRITE",     "Lubrizol", PACK, "plumbing pipe"),
    ("CPVC", "PIPE GRADE",   "DCW",      PACK, "extrusion"),
    ("CPVC", "FITTING GRADE", "DCW",     PACK, "injection"),
    # ---- PVC regrind (colour and type, no maker)
    ("PVC REGRIND", "GREY",       "Recycled", PACK, ""),
    ("PVC REGRIND", "BLACK",      "Recycled", PACK, ""),
    ("PVC REGRIND", "MIX",        "Recycled", PACK, ""),
    ("PVC REGRIND", "SOFT CABLE", "Recycled", PACK, ""),
    # ---- fillers and pigments
    ("TIO2", "CR-828",  "Tronox",          PACK, "rutile"),
    ("TIO2", "R-902+",  "Chemours",        PACK, "rutile"),
    ("TIO2", "R-996",   "Lomon Billions",  PACK, "rutile"),
    ("CACO3", "COATED",   "Vietnam origin",  PACK, "stearic-coated"),
    ("CACO3", "COATED",   "Malaysia origin", PACK, "stearic-coated"),
    ("CACO3", "COATED",   "Egypt origin",    PACK, "stearic-coated"),
    ("CACO3", "UNCOATED", "Vietnam origin",  PACK, ""),
    ("CACO3", "UNCOATED", "Malaysia origin", PACK, ""),
    ("CACO3", "UNCOATED", "Egypt origin",    PACK, ""),
    # ---- heat stabilisers
    ("TIN STABILIZER", "METHYL TIN", "PMC Organometallix", "", "Thermolite"),
    ("TIN STABILIZER", "METHYL TIN", "Baerlocher",         "", ""),
    ("TIN STABILIZER", "METHYL TIN", "Songwon",            "", ""),
    ("TIN STABILIZER", "BUTYL TIN",  "PMC Organometallix", "", "Thermolite"),
    ("TIN STABILIZER", "OCTYL TIN",  "PMC Organometallix", "", "Thermolite"),
    ("CA-ZN STABILIZER", "ONE PACK", "Baerlocher", PACK, ""),
    ("LEAD STABILIZER",  "ONE PACK", "Baerlocher", PACK, ""),
    ("CALCIUM STEARATE", "STANDARD", "Baerlocher", PACK, ""),
    # ---- lubricants and waxes
    ("STEARIC ACID", "TRIPLE PRESSED", "Godrej Industries", PACK, ""),
    ("STEARIC ACID", "TRIPLE PRESSED", "VVF",               PACK, ""),
    ("PE WAX",       "G-110",          "Unspecified",       PACK, ""),
    ("HC WAX",       "STANDARD",       "Unspecified",       PACK, "hydrocarbon wax"),
    ("PARAFFIN WAX", "STANDARD",       "Unspecified",       PACK, ""),
    ("GMS",          "STANDARD",       "Unspecified",       PACK, "glycerol monostearate"),
    # ---- modifiers, aids, plasticiser, brightener
    ("CPE",             "135A",   "Weifang Yaxing", PACK, "impact modifier"),
    ("IMPACT MODIFIER", "FM-40",  "Kaneka",         PACK, "acrylic"),
    ("PROCESSING AID",  "PA-20",  "Kaneka",         PACK, "acrylic"),
    ("PROCESSING AID",  "K-125",  "Dow",            PACK, "Paraloid"),
    ("DOP",             "STANDARD", "KLJ",          "", "dioctyl phthalate"),
    ("DOP",             "STANDARD", "LG Chem",      "", "dioctyl phthalate"),
    ("OPTICAL BRIGHTENER", "OB-1", "Unspecified",   "", ""),
]


def run(dry: bool = False) -> None:
    db.init_db()
    if dry:
        for m, g, k, pack, note in CATALOG:
            print("%-18s %-15s %-20s %s" % (m, g, k, note))
        print("%d products" % len(CATALOG))
        return
    added = 0
    with db.tx() as conn:
        for m, g, k, pack, _note in CATALOG:
            if products.find_product(m, g, k):
                continue
            products.save_product(conn, m, g, k, pack or None)
            added += 1
    print("%d products in the list, %d added, %d already there"
          % (len(CATALOG), added, len(CATALOG) - added))


if __name__ == "__main__":
    run(dry="--dry-run" in sys.argv)
