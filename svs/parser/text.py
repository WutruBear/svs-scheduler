"""
Raw-text parser: splits a multi-player paste into blocks and normalises each one.
"""

from __future__ import annotations
import re

from svs.models import HIGH, MEDIUM, LOW
from svs.config import MIN_TIME_WINDOW_SLOTS
from .normalizers import (
    normalize_duration,
    parse_fc_shards,
    normalize_time_utc,
    normalize_days,
)


# ── Field extraction patterns ─────────────────────────────────────────────────
# Defined at module level to avoid re-allocating on every parse_block() call.

_CONSTRUCTION_PATS = [
    r'CONSTRUCTION\s*(?:\([^)]*\))?\s*[:\-]\s*([^\n]+)',
    r'Constr[a-z]{2,10}\s*(?:\([^)]*\))?\s*[:\-]\s*([^\n]+)',
]
_RESEARCH_PATS = [
    r'RESEARCH\s*(?:\([^)]*\))?\s*[:\-]\s*([^\n]+)',
    r'Rese?[a-z]{2,7}\s*(?:\([^)]*\))?\s*[:\-]\s*([^\n]+)',
]
_TROOPS_PATS = [
    r'TROOPS?\s*(?:\([^)]*\))?\s*[:\-]\s*([^\n]+)',
    r'Troop[a-z]{0,4}\s*(?:\([^)]*\))?\s*[:\-]\s*([^\n]+)',
]

_SPEEDUP_FIELDS: list[tuple[str, list[str]]] = [
    ("Construction", _CONSTRUCTION_PATS),
    ("Research",     _RESEARCH_PATS),
    ("Troops",       _TROOPS_PATS),
]

_FC_PATS = [
    r'(?:How many FCs[^:?\n]*|FC[s]?\s*/\s*[Ss]hard[s]?[^:?\n]*)\s*[:?\-]?\s*([^\n]+)',
    r'FC[s]?\s+and[^:\n]*[:\-]?\s*([^\n]+)',
    r'(?:Crystal[s]?[^:\n]*)\s*[:\-]?\s*([^\n]+)',
]

_TIME_PATS = [
    r'Desired\s+time\s+UTC[^:\n]*[:\-]?\s*([^\n]+)',
    r'Time\s+UTC[^:\n]*[:\-]?\s*([^\n]+)',
    r'UTC\s*[:\-]\s*([^\n]+)',
]

_DAYS_PATS = [
    r'Desired\s+day(?:\(s\))?\s*(?:\([^)]*\))?\s*[:\-]\s*([^\n]+)',
    r'Day(?:s)?\s*(?:\([^)]*\))?\s*[:\-]\s*([^\n]+)',
]


def extract_field(block: str, patterns: list[str]) -> str:
    """Return the first capture group from the first matching pattern."""
    for pat in patterns:
        m = re.search(pat, block, re.I | re.MULTILINE)
        if m:
            return m.group(1).strip()
    return ""


def parse_block(block: str) -> dict:
    """
    Parse a single player text block into a record dict with confidence metadata.

    Returns a flat dict where:
      - Core field values are stored under their display name ("Construction", etc.)
      - Confidence is stored as "_conf_<field>" (one of HIGH / MEDIUM / LOW)
      - Warnings are stored as "_warn_<field>" (present only when confidence < HIGH)
    """
    r: dict = {}

    r["User ID"] = extract_field(block, [
        r'User\s*ID\s*[:\-]?\s*(\d+)',
        r'\bID\s*[:\-]?\s*(\d+)',
    ])

    r["Level"]       = extract_field(block, [r'Level\s*[:\-]?\s*(\S+)', r'LVL\s*[:\-]?\s*(\S+)'])
    r["_conf_Level"] = HIGH if r["Level"] else LOW

    for field, pats in _SPEEDUP_FIELDS:
        val, conf           = normalize_duration(extract_field(block, pats))
        r[field]            = val
        r[f"_conf_{field}"] = conf

    fc_line          = extract_field(block, _FC_PATS)
    r["_fc_raw"]     = fc_line
    fc_v, fc_c, sh_v, sh_c = parse_fc_shards(fc_line)
    r["FCs"]           = fc_v;  r["_conf_FCs"]        = fc_c
    r["FC Shards"]     = sh_v;  r["_conf_FC Shards"]  = sh_c

    tv, tc, slot_count = normalize_time_utc(extract_field(block, _TIME_PATS).strip())
    r["Time UTC"] = tv
    if tv and slot_count < MIN_TIME_WINDOW_SLOTS:
        r["_conf_Time UTC"] = MEDIUM
        r["_warn_Time UTC"] = f"Only {slot_count / 2:.4g}h window — minimum is 3h"
    else:
        r["_conf_Time UTC"] = tc

    dv, dc       = normalize_days(extract_field(block, _DAYS_PATS))
    r["Days"]    = dv
    r["_conf_Days"] = dc

    return r


def parse_input(text: str) -> tuple[list[dict], list[str], dict[str, list[dict]]]:
    """
    Split raw text into player blocks and parse each one.

    Returns:
        records   : list of dicts, one per unique User ID (first occurrence kept
                    until the caller resolves duplicates)
        warnings  : list of human-readable issue strings (non-duplicate problems)
        duplicates: {uid: [rec, rec, ...]} for every User ID appearing > once
    """
    parts = re.split(r'(?=User\s*ID\s*[:\-]?\s*\d)', text.strip(), flags=re.I)
    parts = [
        p.strip() for p in parts
        if p.strip() and re.search(r'User\s*ID\s*[:\-]?\s*\d', p, re.I)
    ]

    all_parsed: list[dict] = []
    warnings:   list[str]  = []

    for i, part in enumerate(parts, 1):
        rec = parse_block(part)
        if not rec["User ID"]:
            warnings.append(f"Block {i}: Could not find User ID — skipped.")
            continue
        rec["_raw_block"] = part
        rec["_manual"]    = False
        all_parsed.append(rec)

    # Group by User ID to detect duplicates
    by_uid: dict[str, list[dict]] = {}
    for rec in all_parsed:
        by_uid.setdefault(rec["User ID"], []).append(rec)

    duplicates = {uid: recs for uid, recs in by_uid.items() if len(recs) > 1}
    records    = [recs[0] for recs in by_uid.values()]

    return records, warnings, duplicates