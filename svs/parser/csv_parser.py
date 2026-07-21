"""
CSV / DataFrame parser.

Converts a pandas DataFrame (from an uploaded CSV or Excel file) into the same
record-dict format that the text parser produces.

Key difference from the original:
  The original CSV parser synthesised a fake "FC {n} shards {m}" string and
  fed it back through parse_fc_shards().  This module calls parse_fc_count()
  and parse_shard_count() directly on the raw column values, which is both
  cleaner and marginally faster.
"""

from __future__ import annotations
import re

import pandas as pd

from svs.models import HIGH, MEDIUM, LOW
from svs.config import MIN_TIME_WINDOW_SLOTS
from .normalizers import (
    normalize_duration,
    normalize_time_utc,
    normalize_days,
    parse_fc_count,
    parse_shard_count,
    parse_refined_fc_count,
    clean_str,
)


def parse_dataframe(
    df: pd.DataFrame,
    mapping: dict[str, str],
) -> tuple[list[dict], list[str], dict[str, list[dict]]]:
    """
    Parse a pandas DataFrame row by row using the normalisation functions.

    Args:
        df     : Input DataFrame (CSV or Excel).
        mapping: {display_field_name: df_column_name}, e.g.
                 {"User ID": "id", "Construction": "constr_days", ...}.
                 Unmapped fields ("— none —") are silently skipped.

    Returns:
        Same (records, warnings, duplicates) triple as parse_input().
    """
    NONE_SENTINEL = "— none —"

    def _col(field: str) -> str | None:
        """Return the mapped column name or None if the field is unmapped."""
        c = mapping.get(field, NONE_SENTINEL)
        return None if c == NONE_SENTINEL else c

    def _get(row, field: str) -> str:
        """Safe clean string access for a mapped field."""
        col = _col(field)
        return clean_str(row[col]) if col and col in row.index else ""

    all_parsed: list[dict] = []
    warnings:   list[str]  = []

    for i, row in df.iterrows():
        rec: dict = {}

        # ── User ID ───────────────────────────────────────────────────────────
        uid_raw = _get(row, "User ID")
        if not uid_raw:
            warnings.append(f"Row {i + 2}: Missing User ID — skipped.")
            continue
        m = re.search(r'(\d+)', uid_raw)
        if not m:
            warnings.append(f"Row {i + 2}: Could not extract User ID from '{uid_raw}' — skipped.")
            continue
        rec["User ID"] = m.group(1)

        # ── Level ─────────────────────────────────────────────────────────────
        level_raw        = _get(row, "Level")
        rec["Level"]     = level_raw
        rec["_conf_Level"] = HIGH if level_raw else LOW

        # ── Speedup fields (Construction / Research / Troops) ─────────────────
        for field in ("Construction", "Research", "Troops"):
            raw           = _get(row, field)
            val, conf     = normalize_duration(raw)
            rec[field]              = val
            rec[f"_conf_{field}"]   = conf

        # ── FCs and FC Shards (direct parsers — no fake string synthesis) ─────
        fc_raw    = _get(row, "FCs")
        sh_raw    = _get(row, "FC Shards")
        fc_v, fc_c   = parse_fc_count(fc_raw)
        sh_v, sh_c   = parse_shard_count(sh_raw)

        # Fallback: if either column is empty try a combined field named "FCs" that
        # contains both (some export formats put "2693 / 434" in one cell).
        if not fc_v and not sh_v and fc_raw:
            from .normalizers import parse_fc_shards   # noqa: PLC0415
            fc_v, fc_c, sh_v, sh_c = parse_fc_shards(fc_raw)

        rec["FCs"]           = fc_v;  rec["_conf_FCs"]        = fc_c
        rec["FC Shards"]     = sh_v;  rec["_conf_FC Shards"]  = sh_c
        rec["_fc_raw"]       = f"{fc_raw} / {sh_raw}".strip(" /") if (fc_raw or sh_raw) else ""

        # ── Refined FC (independent column) ────────────────────────────────────
        refined_raw = _get(row, "Refined FC")
        ref_v, ref_c = parse_refined_fc_count(refined_raw)
        rec["Refined FC"]        = ref_v;  rec["_conf_Refined FC"] = ref_c
        if refined_raw:
            rec["_refined_fc_raw"] = refined_raw

        # ── Time UTC ──────────────────────────────────────────────────────────
        time_raw          = _get(row, "Time UTC")
        tv, tc, slot_count = normalize_time_utc(time_raw)
        rec["Time UTC"] = tv
        if tv and slot_count < MIN_TIME_WINDOW_SLOTS:
            rec["_conf_Time UTC"] = MEDIUM
            rec["_warn_Time UTC"] = f"Only {slot_count / 2:.4g}h window — minimum is 3h"
        else:
            rec["_conf_Time UTC"] = tc

        # ── Days ──────────────────────────────────────────────────────────────
        days_raw          = _get(row, "Days")
        dv, dc            = normalize_days(days_raw)
        rec["Days"]       = dv
        rec["_conf_Days"] = dc

        # ── Metadata ──────────────────────────────────────────────────────────
        rec["_raw_block"] = f"Imported from CSV row {i + 2}"
        rec["_manual"]    = False

        all_parsed.append(rec)

    # Deduplicate identically to parse_input()
    by_uid: dict[str, list[dict]] = {}
    for rec in all_parsed:
        by_uid.setdefault(rec["User ID"], []).append(rec)

    duplicates = {uid: recs for uid, recs in by_uid.items() if len(recs) > 1}
    records    = [recs[0] for recs in by_uid.values()]

    return records, warnings, duplicates
