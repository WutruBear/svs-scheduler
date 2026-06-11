"""
Central configuration for SvS Scheduler.

All game-specific constants live here so a coordinator can update
day mappings, score columns, or UI copy in one place.
"""

from pathlib import Path

# ── Numeric limits ────────────────────────────────────────────────────────────
NUM = r'\d+(?:[.,]\d+)?'          # regex fragment: integer or decimal with , or .
MAX_REASONABLE_DAYS   = 365       # speedup values above this are flagged MEDIUM
MIN_TIME_WINDOW_SLOTS = 6         # 3 hours × 2 slots/hour; shorter windows are warned

# ── Day configuration ─────────────────────────────────────────────────────────
# Each entry maps a SvS day number to the spreadsheet column used for scoring.
# Extend this list to add new event days without changing any other file.
DAY_CONFIG: list[dict] = [
    {"day": 1, "label": "Day 1 — VP",  "col": "Construction"},
    {"day": 2, "label": "Day 2 — VP",  "col": "Research"},
    {"day": 4, "label": "Day 4 — MoE", "col": "Troops"},
]

# Day 4 VP is a virtual second pass for players unassigned in the Day 4 MoE run.
DAY4_VP_CONFIG: dict = {"day": 4, "label": "Day 4 — VP", "col": "Troops"}

# Extra column shown alongside User ID in the per-day timeline view.
DAY_EXTRA_COL: dict[int, str | None] = {1: "FCs", 2: "FC Shards", 4: None}

# ── Sample input ──────────────────────────────────────────────────────────────
_SAMPLE_PATH = Path(__file__).parent.parent / "sample_input.txt"

def load_sample_input() -> str:
    """Return the bundled sample raw-text input, or a short fallback."""
    try:
        return _SAMPLE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return (
            "User ID: 1\nLevel: FC1\nCONSTRUCTION: 1\nRESEARCH: 1\nTROOPS: 1\n"
            "FCs and shards: FC 100 shards 10\nTime UTC: 14-17\nDays: 1, 2\n"
        )