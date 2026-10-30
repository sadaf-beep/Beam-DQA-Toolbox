"""
Manufacturer reference data from the DQA team's OEM-TOP-100 list (Data
Entry sheet): each manufacturer's standard warranty and official website.
Pure Python, no UI. Used by Warranty Validation (the standard warranty is
the DQA fallback when a product has no warranty of its own) and OEM URL
Check (official domains).

To update: edit oem_standard_warranties.csv next to this file.
"""

import csv
import re
from functools import lru_cache
from pathlib import Path

CSV_PATH = Path(__file__).with_name("oem_standard_warranties.csv")


def _key(name: str) -> str:
    """'ARRI Lighting (ARRI)' -> 'arrilighting'; 'Anton/Bauer' -> 'antonbauer'."""
    name = re.sub(r"\(.*?\)", "", str(name or ""))
    return re.sub(r"[^a-z0-9]", "", name.lower())


@lru_cache(maxsize=1)
def _entries() -> list:
    with open(CSV_PATH, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["key"] = _key(r["manufacturer"])
        # Short names in brackets are aliases: "Hewlett Packard (HP)" -> "hp".
        r["aliases"] = [_key(a) for a in re.findall(r"\((.*?)\)", r["manufacturer"])]
        r["warranty_days"] = int(r["warranty_days"]) if r["warranty_days"] else None
    return rows


def lookup(mfr: str):
    """The reference row for a manufacturer name, or None. Matches exact
    names first, then bracketed aliases, then names that contain each other
    (e.g. 'Sony' and 'Sony Electronics'), preferring the longest match."""
    k = _key(mfr)
    if len(k) < 2:
        return None
    rows = _entries()
    for r in rows:
        if r["key"] == k:
            return r
    for r in rows:
        if k in r["aliases"]:
            return r
    hits = [r for r in rows if len(r["key"]) >= 4 and (r["key"] in k or k in r["key"])]
    return max(hits, key=lambda r: len(r["key"]), default=None)


def official_domains() -> dict:
    """{manufacturer key: website domain} for every row with a website."""
    return {r["key"]: r["website_domain"] for r in _entries() if r["website_domain"]}
