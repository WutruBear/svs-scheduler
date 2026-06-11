"""
Tests for svs/parser/normalizers.py

Run with:  pytest tests/test_normalizers.py -v
"""

import pytest
from svs.models import HIGH, MEDIUM, LOW
from svs.parser.normalizers import (
    clean_str,
    normalize_duration,
    normalize_time_utc,
    normalize_days,
    parse_fc_shards,
    parse_fc_count,
    parse_shard_count,
    slots_str_to_slot_indices,
    slot_label,
    _parse_number_str,
)


# ── clean_str ─────────────────────────────────────────────────────────────────

class TestCleanStr:
    def test_none_returns_empty(self):
        assert clean_str(None) == ""

    def test_nan_string_returns_empty(self):
        assert clean_str("nan")  == ""
        assert clean_str("NaN")  == ""
        assert clean_str("NAN")  == ""

    def test_float_nan_returns_empty(self):
        import math
        assert clean_str(math.nan) == ""

    def test_strips_whitespace(self):
        assert clean_str("  hello  ") == "hello"

    def test_preserves_valid_string(self):
        assert clean_str("FC 2693 shards 434") == "FC 2693 shards 434"

    def test_numeric_string_preserved(self):
        assert clean_str("2693") == "2693"


# ── _parse_number_str ─────────────────────────────────────────────────────────

class TestParseNumberStr:
    def test_dot_thousands(self):
        assert _parse_number_str("1.900") == "1900"

    def test_comma_thousands(self):
        assert _parse_number_str("1,900") == "1900"

    def test_dot_decimal_preserved(self):
        assert _parse_number_str("4.17") == "4.17"

    def test_comma_decimal_converted(self):
        assert _parse_number_str("1,2") == "1.2"

    def test_plain_int(self):
        assert _parse_number_str("42") == "42"


# ── normalize_duration ────────────────────────────────────────────────────────

class TestNormalizeDuration:
    def test_empty(self):
        assert normalize_duration("") == ("", LOW)

    def test_days_only(self):
        val, conf = normalize_duration("4d")
        assert val == 4.0
        assert conf == HIGH

    def test_days_and_hours(self):
        val, conf = normalize_duration("3d 12h")
        assert abs(val - 3.5) < 0.01
        assert conf == HIGH

    def test_days_full_word(self):
        val, conf = normalize_duration("3 days")
        assert val == 3.0

    def test_hours_only(self):
        val, conf = normalize_duration("48h")
        assert abs(val - 2.0) < 0.01

    def test_minutes(self):
        val, conf = normalize_duration("7200min")
        assert abs(val - 5.0) < 0.01

    def test_bare_number(self):
        val, conf = normalize_duration("5")
        assert val == 5.0
        assert conf == HIGH

    def test_thousands_separator(self):
        # "1.900" should be parsed as 1900 days (dot-as-thousands)
        val, conf = normalize_duration("1.900")
        assert val == 1900.0

    def test_range_takes_lower_bound(self):
        val, conf = normalize_duration("3d-5d")
        assert val == 3.0
        assert conf == MEDIUM


# ── normalize_time_utc ────────────────────────────────────────────────────────

class TestNormalizeTimeUtc:
    def test_empty(self):
        assert normalize_time_utc("") == ("", LOW, 0)

    def test_simple_range(self):
        slots_csv, conf, count = normalize_time_utc("14:00-17:00")
        assert conf == HIGH
        assert count == 6   # 14:00,14:30,15:00,15:30,16:00,16:30
        tokens = slots_csv.split(",")
        assert "14:00" in tokens
        assert "16:30" in tokens
        assert "17:00" not in tokens   # endpoint is exclusive

    def test_dot_separator(self):
        slots_csv, conf, count = normalize_time_utc("16.00-19.00")
        assert count == 6

    def test_utc_suffix(self):
        slots_csv, conf, count = normalize_time_utc("7utc till 21utc")
        # 7:00 to 21:00 = 14h = 28 slots
        assert count == 28

    def test_cross_midnight(self):
        slots_csv, conf, count = normalize_time_utc("22:00-02:00")
        tokens = set(slots_csv.split(","))
        assert "22:00" in tokens
        assert "23:30" in tokens
        assert "00:00" in tokens
        assert "01:30" in tokens
        assert "02:00" not in tokens

    def test_bare_hour_adds_two_slots(self):
        """A bare hour like '14' should add both 14:00 and 14:30."""
        slots_csv, conf, count = normalize_time_utc("14")
        assert "14:00" in slots_csv
        assert "14:30" in slots_csv

    def test_bare_hhmm_does_not_bleed(self):
        """
        '14:30' alone should NOT add slot 14:00 — the core precision bug
        that slots_str_to_slot_indices() (formerly slots_str_to_hours) fixed.
        After normalize_time_utc the token "14:30" should appear in the
        output; slots_str_to_slot_indices maps it to index 29 only.
        """
        slots_csv, conf, count = normalize_time_utc("14:30-15:30")
        # 14:30,15:00  (2 slots — 15:30 is exclusive)
        tokens = slots_csv.split(",")
        assert "14:30" in tokens
        assert "15:00" in tokens
        assert "14:00" not in tokens

    def test_multiple_ranges(self):
        slots_csv, conf, count = normalize_time_utc("00utc - 4utc, 9utc, 20-23")
        tokens = set(slots_csv.split(","))
        assert "00:00" in tokens
        assert "03:30" in tokens
        assert count >= 10


# ── slots_str_to_slot_indices ─────────────────────────────────────────────────

class TestSlotsStrToSlotIndices:
    def test_empty(self):
        assert slots_str_to_slot_indices("") == []

    def test_hhmm_on_hour(self):
        assert slots_str_to_slot_indices("14:00") == [28]

    def test_hhmm_half_hour(self):
        assert slots_str_to_slot_indices("14:30") == [29]

    def test_multiple_tokens(self):
        result = slots_str_to_slot_indices("14:00,14:30,15:00")
        assert result == [28, 29, 30]

    def test_bare_hour_expands_to_two_slots(self):
        result = slots_str_to_slot_indices("14")
        assert result == [28, 29]

    def test_no_bleed_from_half_hour(self):
        """
        Slot index for "14:30" must be 29 only — the precision fix.
        The old slots_str_to_hours() would have produced hour=14 → {28, 29},
        incorrectly adding slot 28 (14:00-14:30) which was never offered.
        """
        result = slots_str_to_slot_indices("14:30")
        assert result == [29]
        assert 28 not in result

    def test_midnight(self):
        assert slots_str_to_slot_indices("00:00") == [0]

    def test_end_of_day(self):
        assert slots_str_to_slot_indices("23:30") == [47]


# ── slot_label ────────────────────────────────────────────────────────────────

class TestSlotLabel:
    def test_slot_0(self):
        assert slot_label(0) == "00:00 – 00:30"

    def test_slot_28(self):
        assert slot_label(28) == "14:00 – 14:30"

    def test_slot_47(self):
        assert slot_label(47) == "23:30 – 00:00"


# ── parse_fc_shards ───────────────────────────────────────────────────────────

class TestParseFcShards:
    def test_empty(self):
        assert parse_fc_shards("") == ("", LOW, "", LOW)

    def test_combined_line(self):
        fc, fc_c, sh, sh_c = parse_fc_shards("FC 2693 shards 434")
        assert fc == 2693
        assert sh == 434
        assert fc_c == HIGH
        assert sh_c == HIGH

    def test_colon_format(self):
        fc, _, sh, _ = parse_fc_shards("FC: 2, FC shards: 2")
        assert fc == 2
        assert sh == 2

    def test_crystals_alias(self):
        fc, _, sh, _ = parse_fc_shards("5 Crystals, 5 shards")
        assert fc == 5
        assert sh == 5

    def test_bare_number_is_fc_count(self):
        fc, fc_c, sh, _ = parse_fc_shards("2693")
        assert fc == 2693
        assert fc_c == HIGH
        assert sh == 0  # zero shards when only one number given

    def test_thousands_separator(self):
        fc, _, sh, _ = parse_fc_shards("FC 1.900 shards 434")
        assert fc == 1900

    def test_comma_decimal_shards(self):
        fc, _, sh, _ = parse_fc_shards("FC 2 shards 1.900")
        assert sh == 1900


# ── parse_fc_count ────────────────────────────────────────────────────────────

class TestParseFcCount:
    def test_empty(self):
        assert parse_fc_count("") == ("", LOW)

    def test_bare_number(self):
        val, conf = parse_fc_count("2693")
        assert val == 2693
        assert conf == HIGH

    def test_with_label(self):
        val, _ = parse_fc_count("2700 FCs")
        assert val == 2700

    def test_crystal_alias(self):
        val, _ = parse_fc_count("5 crystals")
        assert val == 5

    def test_comma_thousands(self):
        val, _ = parse_fc_count("2,700")
        assert val == 2700


# ── parse_shard_count ─────────────────────────────────────────────────────────

class TestParseShardCount:
    def test_empty(self):
        assert parse_shard_count("") == ("", LOW)

    def test_bare_number(self):
        val, conf = parse_shard_count("434")
        assert val == 434
        assert conf == HIGH

    def test_with_label(self):
        val, _ = parse_shard_count("434 shards")
        assert val == 434

    def test_colon_format(self):
        val, _ = parse_shard_count("shards: 434")
        assert val == 434


# ── normalize_days ────────────────────────────────────────────────────────────

class TestNormalizeDays:
    def test_empty(self):
        assert normalize_days("") == ("", LOW)

    def test_numeric(self):
        val, conf = normalize_days("1, 2, 4")
        assert val == "1,2,4"
        assert conf == HIGH

    def test_day_names(self):
        val, conf = normalize_days("Mon, Thu")
        assert val == "1,4"

    def test_full_names(self):
        val, _ = normalize_days("Monday, Tuesday")
        assert val == "1,2"

    def test_mixed(self):
        val, _ = normalize_days("Mon, 4")
        assert val == "1,4"

    def test_deduplication(self):
        val, _ = normalize_days("1, 1, 2")
        assert val == "1,2"