"""
Shared types for SvS Scheduler.

Using TypedDict rather than dataclasses keeps compatibility with the
dict-based Streamlit session state and pandas DataFrame pipeline while
still giving IDE type-checking and a single source of truth for field names.
"""

from __future__ import annotations
from typing import TypedDict


# Confidence levels produced by the parser
HIGH   = "high"
MEDIUM = "medium"
LOW    = "low"

DISPLAY_FIELDS = [
    "User ID", "Level", "Construction", "Research", "Troops",
    "FCs", "FC Shards", "Refined FC", "Time UTC", "Days",
]

FIELD_HINTS: dict[str, str] = {
    "Level":        "e.g. FC3, FC5",
    "Construction": "e.g. 24d 3h  or  42d  or  35",
    "Research":     "e.g. 42d  or  20",
    "Troops":       "e.g. 100d 10h  or  50",
    "FCs":          "Number of FCs, e.g. 2693 or 2700",
    "FC Shards":    "Number of shards, e.g. 434",
    "Refined FC":   "Number of Refined FCs, e.g. 150",
    "Time UTC":     "e.g. 16:00–19:00  or  14:30-17  or  13",
    "Days":         "e.g. Mon, Thu  or  1, 4",
}

FIELD_WARN_MSG: dict[str, str] = {
    LOW:    "⚠ Could not parse — please verify",
    MEDIUM: "⚠ Parsed with low confidence — please check",
}


class PlayerRecord(TypedDict, total=False):
    """
    Dict schema produced by the parser and consumed by the UI and scheduler.

    Core fields (always present):
        User ID, Level, Construction, Research, Troops, FCs, FC Shards,
        Refined FC, Time UTC, Days

    Confidence metadata (one per editable field):
        _conf_Level, _conf_Construction, _conf_Research, _conf_Troops,
        _conf_FCs, _conf_FC Shards, _conf_Refined FC, _conf_Time UTC, _conf_Days

    Warning metadata (present only when confidence < HIGH):
        _warn_Time UTC, etc.

    Internal metadata:
        _fc_raw          : raw FC / FC Shards value(s) seen by the parser
        _refined_fc_raw  : raw Refined FC value seen by the parser
        _raw_block       : the original text block (or CSV row description)
        _manual          : True if this record was added by hand in the UI
    """
    # Core fields
    user_id: str       # "User ID" key — kept as string
    Level: str
    Construction: float | str
    Research: float | str
    Troops: float | str
    FCs: int | str
    FC_Shards: int | str     # stored as "FC Shards"
    Refined_FC: int | str    # stored as "Refined FC"
    Time_UTC: str           # stored as "Time UTC" — comma-separated "HH:MM" tokens
    Days: str               # comma-separated day numbers


class SchedulerUser(TypedDict, total=False):
    """
    Minimal dict the scheduler consumes.  Created in app.py from a parsed
    PlayerRecord row (via the DataFrame) right before run_scheduler() is called.

    slots: sorted list of 30-minute slot indices (0–47) the user is available.
           Slot 0 = 00:00–00:30, slot 1 = 00:30–01:00, …, slot 47 = 23:30–00:00.
    days : list of SvS day numbers the user participates in (e.g. [1, 2, 4]).
    """
    User_ID: str            # "User ID"
    Level: str
    Construction: float
    Research: float
    Troops: float
    FCs: int | None
    FC_Shards: int | None   # "FC Shards"
    slots: list[int]
    days: list[int]
    # Optional: {day_number: slot_index} manual override set in the UI
    _slot_overrides: dict[int, int]
