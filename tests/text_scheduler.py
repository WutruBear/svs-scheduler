"""
Tests for svs/scheduler.py

All tests use plain Python dicts — no Streamlit, no pandas.

Run with:  pytest tests/test_scheduler.py -v
"""

import pytest
from svs.scheduler import run_day, run_scheduler, run_day4_vp


def _make_user(
    uid: str,
    score: float,
    slots: list[int],
    days: list[int],
    overrides: dict | None = None,
) -> dict:
    """Build a minimal SchedulerUser dict for tests."""
    u: dict = {
        "User ID":      uid,
        "Level":        "FC3",
        "Construction": score,
        "Research":     score,
        "Troops":       score,
        "FCs":          None,
        "FC Shards":    None,
        "slots":        slots,
        "days":         days,
    }
    if overrides:
        u["_slot_overrides"] = overrides
    return u


# ── run_day ───────────────────────────────────────────────────────────────────

class TestRunDay:
    """Tests for the core single-day MCMF scheduler."""

    DAY_CFG = {"day": 1, "label": "Day 1 — VP", "col": "Construction"}

    def test_empty_eligible_returns_empty_result(self):
        result = run_day([], self.DAY_CFG)
        assert result["user_slot"] == {}
        assert result["unassigned"] == []

    def test_single_user_gets_assigned(self):
        user   = _make_user("A", 5.0, [28, 29, 30], [1])
        result = run_day([user], self.DAY_CFG)
        assert "A" in result["user_slot"]
        assert result["user_slot"]["A"] in {28, 29, 30}

    def test_user_not_participating_skipped(self):
        user   = _make_user("A", 5.0, [28, 29, 30], [2])   # day 2 only
        result = run_day([user], self.DAY_CFG)              # day 1
        assert result["user_slot"] == {}

    def test_two_users_different_windows_both_assigned(self):
        u1 = _make_user("A", 5.0, [28, 29],       [1])
        u2 = _make_user("B", 3.0, [30, 31],       [1])
        result = run_day([u1, u2], self.DAY_CFG)
        assert "A" in result["user_slot"]
        assert "B" in result["user_slot"]
        assert result["user_slot"]["A"] != result["user_slot"]["B"]

    def test_slot_contention_higher_score_wins(self):
        """When A and B both want the same single slot, the higher scorer gets it."""
        u1 = _make_user("high",  10.0, [28], [1])
        u2 = _make_user("low",    2.0, [28], [1])
        result = run_day([u1, u2], self.DAY_CFG)
        assert result["user_slot"].get("high") == 28
        assert "low" not in result["user_slot"]
        assert any(e["user"]["User ID"] == "low" for e in result["unassigned"])

    def test_maximise_cardinality_before_score(self):
        """
        Two slots, three users.  Users A and B have score 10.0 and share slot 28.
        User C has score 1.0 but can take slot 29.
        Optimal: A wins slot 28, C takes slot 29  → 2 placed, total score 11.
        Sub-optimal greedy: A + B contest slot 28, C gets 29 → still 2 placed.
        The MCMF should always place 2 users (max cardinality).
        """
        u_a = _make_user("A", 10.0, [28],     [1])
        u_b = _make_user("B", 10.0, [28],     [1])
        u_c = _make_user("C",  1.0, [28, 29], [1])
        result = run_day([u_a, u_b, u_c], self.DAY_CFG)
        assert len(result["user_slot"]) == 2

    def test_manual_override_respected(self):
        """A pinned user should receive their override slot, not a different one."""
        u = _make_user("pinned", 5.0, [28, 29, 30], [1], overrides={1: 30})
        result = run_day([u], self.DAY_CFG)
        assert result["user_slot"].get("pinned") == 30

    def test_no_slot_available_marks_unassigned(self):
        u = _make_user("A", 5.0, [], [1])   # no slots at all
        result = run_day([u], self.DAY_CFG)
        assert "A" not in result["user_slot"]

    def test_duplicate_uid_keeps_highest_score(self):
        """If the same User ID appears twice, only the higher-scored entry participates."""
        u_low  = _make_user("dup", 1.0, [28], [1])
        u_high = _make_user("dup", 9.0, [29], [1])
        result = run_day([u_low, u_high], self.DAY_CFG)
        uid   = "dup"
        assert uid in result["user_slot"]
        assert result["user_slot"][uid] == 29   # high-score entry's slot

    def test_unassigned_reason_populated(self):
        u1 = _make_user("A", 5.0, [28], [1])
        u2 = _make_user("B", 3.0, [28], [1])
        result = run_day([u1, u2], self.DAY_CFG)
        unassigned_ids = [e["user"]["User ID"] for e in result["unassigned"]]
        assert "B" in unassigned_ids
        entry = next(e for e in result["unassigned"] if e["user"]["User ID"] == "B")
        assert entry["reason"] in ("Window saturated", "not enough speedups")


# ── run_scheduler (multi-day) ─────────────────────────────────────────────────

class TestRunScheduler:
    def test_returns_one_result_per_day_config(self):
        from svs.config import DAY_CONFIG
        user = _make_user("A", 5.0, list(range(48)), [1, 2, 4])
        results = run_scheduler([user])
        assert len(results) == len(DAY_CONFIG)

    def test_each_day_uses_correct_score_column(self):
        from svs.config import DAY_CONFIG
        # User with different scores per column
        u: dict = {
            "User ID":      "X",
            "Level":        "FC5",
            "Construction": 10.0,
            "Research":     20.0,
            "Troops":       30.0,
            "FCs":          None,
            "FC Shards":    None,
            "slots":        list(range(48)),
            "days":         [1, 2, 4],
        }
        results = run_scheduler([u])
        for dr, dc in zip(results, DAY_CONFIG):
            assert dr["col"] == dc["col"]
            assert dr["day"] == dc["day"]
            assert "X" in dr["user_slot"]


# ── run_day4_vp ───────────────────────────────────────────────────────────────

class TestRunDay4VP:
    DAY4_CFG = {"day": 4, "label": "Day 4 — MoE", "col": "Troops"}

    def test_empty_unassigned_skips_scheduling(self):
        user        = _make_user("A", 5.0, [28], [4])
        day4_result = run_day([user], self.DAY4_CFG)
        # A is assigned in MoE — VP should have no eligible users
        vp4 = run_day4_vp([user], day4_result)
        assert vp4["eligible"] == []
        assert vp4.get("is_vp4") is True

    def test_unassigned_moe_user_gets_vp_slot(self):
        # Two users, one slot → one is left out of MoE
        u1 = _make_user("A", 10.0, [28], [4])
        u2 = _make_user("B",  1.0, [28], [4])
        day4_result = run_day([u1, u2], self.DAY4_CFG)
        assert "A" in day4_result["user_slot"]
        assert "B" not in day4_result["user_slot"]

        # B should get a VP slot (different slot available)
        u2_with_more = _make_user("B", 1.0, [28, 29], [4])
        vp4 = run_day4_vp([u1, u2_with_more], day4_result)
        assert "B" in vp4["user_slot"]
        assert vp4["is_vp4"] is True


# ── Slot precision regression ─────────────────────────────────────────────────

class TestSlotPrecision:
    """
    Regression tests for the slot precision fix.

    The old code stored user["hours"] (list of ints 0-23) and expanded each
    hour to two slots via {h*2, h*2+1}.  This meant that if a user's Time UTC
    was "14:30-15:30", normalize_time_utc would produce "14:30,15:00" and
    slots_str_to_hours would collapse "14:30" → hour 14, then expand to
    slots {28, 29} — incorrectly adding slot 28 (14:00-14:30) which the
    user never offered.

    With the fix, slots_str_to_slot_indices("14:30") → [29] only.
    """

    DAY_CFG = {"day": 1, "label": "Day 1 — VP", "col": "Construction"}

    def test_half_hour_slot_not_assigned_outside_window(self):
        """
        User offers exactly one slot: 14:30-15:00 (slot index 29).
        They must NOT be assigned to slot 28 (14:00-14:30).
        """
        from svs.parser.normalizers import slots_str_to_slot_indices
        slot_indices = slots_str_to_slot_indices("14:30")
        assert slot_indices == [29]
        assert 28 not in slot_indices

        user   = _make_user("A", 5.0, slot_indices, [1])
        result = run_day([user], self.DAY_CFG)
        if "A" in result["user_slot"]:
            assert result["user_slot"]["A"] == 29, (
                "User should be placed at slot 29 (14:30), not slot 28 (14:00)"
            )

    def test_full_pipeline_preserves_half_hour_boundary(self):
        """
        End-to-end: parse "14:30-15:30" → scheduler → assigned slot must be
        either 29 (14:30) or 30 (15:00), never 28 (14:00).
        """
        from svs.parser.normalizers import normalize_time_utc, slots_str_to_slot_indices
        slots_csv, _, _ = normalize_time_utc("14:30-15:30")
        # Should produce "14:30,15:00"
        assert "14:00" not in slots_csv.split(","), (
            f"normalize_time_utc should not include 14:00 for range 14:30-15:30, "
            f"got: {slots_csv}"
        )
        indices = slots_str_to_slot_indices(slots_csv)
        assert 28 not in indices
        assert 29 in indices
        assert 30 in indices