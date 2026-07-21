"""
Field-level parsing and normalisation helpers.

Key fixes vs the original monolith:
  • slots_str_to_slot_indices() replaces slots_str_to_hours().
    The old function collapsed "HH:MM" tokens to integer hours then re-expanded
    each hour to two 30-min slots, silently adding 14:00-14:30 when a user
    only offered "14:30".  The new function maps each "HH:MM" token directly
    to its 30-min slot index (0-47), preserving full precision end-to-end.

  • parse_fc_count() and parse_shard_count() replace the CSV-parser hack of
    reassembling a fake "FC {n} shards {m}" string just to feed it back
    through parse_fc_shards().  Both functions operate on their raw column
    value directly.

  • clean_str() centralises the 10+ repeated nan-string guards that were
    scattered throughout the original CSV parsing loop.
"""

from __future__ import annotations
import re
from svs.models import HIGH, MEDIUM, LOW
from svs.config import NUM, MAX_REASONABLE_DAYS, MIN_TIME_WINDOW_SLOTS


# ── Utility ───────────────────────────────────────────────────────────────────

def clean_str(val) -> str:
    """
    Coerce *val* to a stripped string, converting pandas 'nan', None,
    and float NaN to an empty string.

    Centralises the repeated `if val.lower() == 'nan': val = ''` guards
    that appeared 10+ times in the original CSV parsing loop.
    """
    import math
    if val is None:
        return ""
    s = str(val).strip()
    if s.lower() == "nan":
        return ""
    try:
        if math.isnan(float(s)):
            return ""
    except (ValueError, TypeError):
        pass
    return s


def _parse_number_str(val: str) -> str:
    """
    Normalise a numeric string with thousand-separators to a plain decimal.

      "1.900"  → "1900"   (dot as thousands sep — exactly 3 trailing digits)
      "1,900"  → "1900"   (comma as thousands sep)
      "4.17"   → "4.17"   (dot as decimal — NOT stripped)
      "1,2"    → "1.2"    (comma as decimal sep → converted to dot)
    """
    dot_parts = val.split(".")
    if (
        len(dot_parts) == 2
        and len(dot_parts[-1]) == 3
        and dot_parts[-1].isdigit()
        and dot_parts[0].isdigit()
    ):
        return val.replace(".", "")

    comma_parts = val.split(",")
    if (
        len(comma_parts) == 2
        and len(comma_parts[-1]) == 3
        and comma_parts[-1].isdigit()
        and comma_parts[0].isdigit()
    ):
        return val.replace(",", "")

    return val.replace(",", ".")


# ── Duration ─────────────────────────────────────────────────────────────────

def normalize_duration(raw: str) -> tuple[float | str, str]:
    """Parse a speedup duration string into (days_float, confidence)."""
    if not raw:
        return "", LOW
    raw = raw.strip().lower()
    days = 0.0

    # Explicit range like "3d-5d" — take the lower bound
    range_m = re.match(rf'({NUM})\s*d?\s*[-\u2013]\s*({NUM})\s*d', raw)
    if range_m:
        val  = float(_parse_number_str(range_m.group(1)))
        conf = MEDIUM if val <= MAX_REASONABLE_DAYS else LOW
        return round(val, 2), conf

    day_m  = re.search(rf'({NUM})\s*(?:d|day|days)\b', raw)
    hour_m = re.search(rf'({NUM})\s*(?:h|hr|hour|hours)\b', raw)
    min_m  = re.search(rf'({NUM})\s*(?:m|min|minute|minutes)\b', raw)

    if day_m:  days += float(_parse_number_str(day_m.group(1)))
    if hour_m: days += float(_parse_number_str(hour_m.group(1))) / 24
    if min_m:  days += float(_parse_number_str(min_m.group(1))) / 1440

    if days > 0:
        conf = MEDIUM if days > MAX_REASONABLE_DAYS else HIGH
        return round(days, 2), conf

    # Bare number fallback
    num_m = re.search(rf'({NUM})', raw)
    if num_m:
        val  = float(_parse_number_str(num_m.group(1)))
        conf = MEDIUM if val > MAX_REASONABLE_DAYS else HIGH
        return val, conf

    return "", LOW


# ── FC / Shards ───────────────────────────────────────────────────────────────

def parse_fc_shards(raw: str) -> tuple[int | str, str, int | str, str]:
    """
    Parse a combined FC/shards line (from raw text) into
    (fc_val, fc_conf, shard_val, shard_conf).

    Used by the *text* parser where both values appear on one line such as:
        "FC 2693 shards 434"
        "3 FCs, 3 FC shards"
    """
    if not raw:
        return "", LOW, "", LOW

    text       = raw.strip().lower()
    fc_val     = None
    shard_val  = None

    for pat in [
        r'shards?\s*[:\-]?\s*(\d+(?:[.,]\d+)?)',
        r'(\d+(?:[.,]\d+)?)\s*(?:fc\s+)?shards?',
    ]:
        m = re.search(pat, text, re.I)
        if m:
            try:
                shard_val = int(float(_parse_number_str(m.group(1))))
            except ValueError:
                pass
            break

    for pat in [
        r'fcs?\s*[:\-]\s*(\d+(?:[.,]\d+)?)',
        r'fcs?\s+(\d+(?:[.,]\d+)?)',
        r'(\d+(?:[.,]\d+)?)\s*fcs?\b',
        r'(\d+(?:[.,]\d+)?)\s*crystals?',
    ]:
        m = re.search(pat, text, re.I)
        if m:
            try:
                fc_val = int(float(_parse_number_str(m.group(1))))
            except ValueError:
                pass
            break

    # Single bare number — treat as FC count, zero shards
    if fc_val is None and shard_val is None:
        m = re.match(r'^\s*(\d+(?:[.,]\d+)?)\s*$', text)
        if m:
            try:
                fc_val    = int(float(_parse_number_str(m.group(1))))
                shard_val = 0
            except ValueError:
                pass

    return (
        fc_val    if fc_val    is not None else "",
        HIGH      if fc_val    is not None else LOW,
        shard_val if shard_val is not None else "",
        HIGH      if shard_val is not None else LOW,
    )


def parse_fc_count(raw: str) -> tuple[int | str, str]:
    """
    Parse a *standalone* FC count string into (value, confidence).

    Used by the CSV parser to avoid synthesising a fake combined string.
    Accepts: "2693", "2,700", "2700 FCs", "FC: 2700", "2700 crystals".
    """
    text = clean_str(raw).lower()
    if not text:
        return "", LOW
    for pat in [
        r'fcs?\s*[:\-]\s*(\d+(?:[.,]\d+)?)',
        r'fcs?\s+(\d+(?:[.,]\d+)?)',
        r'(\d+(?:[.,]\d+)?)\s*fcs?\b',
        r'(\d+(?:[.,]\d+)?)\s*crystals?',
        r'^(\d+(?:[.,]\d+)?)$',
    ]:
        m = re.search(pat, text, re.I)
        if m:
            try:
                return int(float(_parse_number_str(m.group(1)))), HIGH
            except ValueError:
                pass
    return "", LOW


def parse_shard_count(raw: str) -> tuple[int | str, str]:
    """
    Parse a *standalone* shard count string into (value, confidence).

    Used by the CSV parser to avoid synthesising a fake combined string.
    Accepts: "434", "434 shards", "shards: 434".
    """
    text = clean_str(raw).lower()
    if not text:
        return "", LOW
    for pat in [
        r'shards?\s*[:\-]?\s*(\d+(?:[.,]\d+)?)',
        r'(\d+(?:[.,]\d+)?)\s*(?:fc\s+)?shards?',
        r'^(\d+(?:[.,]\d+)?)$',
    ]:
        m = re.search(pat, text, re.I)
        if m:
            try:
                return int(float(_parse_number_str(m.group(1)))), HIGH
            except ValueError:
                pass
    return "", LOW


def parse_refined_fc_count(raw: str) -> tuple[int | str, str]:
    """
    Parse a *standalone* Refined FC count string into (value, confidence).

    Accepts: "150", "150 Refined FC", "Refined FC: 150", "Refined FCs 150".
    """
    text = clean_str(raw).lower()
    if not text:
        return "", LOW
    for pat in [
        r'refined\s*fc[s]?\s*[:\-]\s*(\d+(?:[.,]\d+)?)',
        r'refined\s*fc[s]?\s+(\d+(?:[.,]\d+)?)',
        r'(\d+(?:[.,]\d+)?)\s*refined\s*fc[s]?',
        r'^(\d+(?:[.,]\d+)?)$',
    ]:
        m = re.search(pat, text, re.I)
        if m:
            try:
                return int(float(_parse_number_str(m.group(1)))), HIGH
            except ValueError:
                pass
    return "", LOW


# ── Time UTC ─────────────────────────────────────────────────────────────────

def parse_hhmm(s: str) -> int | None:
    """Parse 'HH:MM', 'HH.MM', or bare 'HH' → total minutes since midnight."""
    s = s.strip()
    m = re.match(r'^(\d{1,2})[.:](\d{2})$', s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
    else:
        m = re.match(r'^(\d{1,2})$', s)
        if not m:
            return None
        h, mi = int(m.group(1)), 0
    return h * 60 + mi if (0 <= h <= 23 and 0 <= mi <= 59) else None


def normalize_time_utc(raw: str) -> tuple[str, str, int]:
    """
    Parse an availability string into (slots_csv, confidence, slot_count).

    Output slots_csv is a comma-separated list of "HH:MM" tokens at 30-minute
    resolution, e.g. "14:00,14:30,15:00,15:30".  Each token represents the
    *start* of a 30-minute window.

    This is the canonical internal format: downstream callers use
    slots_str_to_slot_indices() to convert to scheduler slot indices.
    """
    if not raw:
        return "", LOW, 0

    text = raw.lower().strip()
    text = text.replace("till", "-").replace(" to ", "-").replace("\u2013", "-")
    slots: set[int] = set()   # minutes since midnight, multiples of 30

    TIME_PAT  = r'\d{1,2}(?:[.:]\d{2})?'
    range_pat = rf'({TIME_PAT})\s*(?:utc)?\s*-\s*({TIME_PAT})\s*(?:utc)?'

    for m in re.finditer(range_pat, text):
        start = parse_hhmm(m.group(1))
        end   = parse_hhmm(m.group(2))
        if start is None or end is None:
            continue
        start = (start // 30) * 30
        end   = (end   // 30) * 30
        if start == end:
            continue
        t, steps = start, 0
        while t != end and steps < 48:
            slots.add(t)
            t = (t + 30) % (24 * 60)
            steps += 1

    text_no_ranges = re.sub(range_pat, " ", text)
    for m in re.finditer(rf'\b({TIME_PAT})\s*(?:utc)?\b', text_no_ranges):
        t = parse_hhmm(m.group(1))
        if t is None:
            continue
        slot = (t // 30) * 30
        if 0 <= slot < 24 * 60:
            slots.add(slot)
            nxt = slot + 30
            if nxt < 24 * 60:
                slots.add(nxt)

    if not slots:
        return "", LOW, 0

    valid     = sorted(s for s in slots if 0 <= s < 24 * 60)
    slots_csv = ",".join(f"{s // 60:02d}:{s % 60:02d}" for s in valid)
    return slots_csv, HIGH, len(valid)


def slots_str_to_slot_indices(slots_str: str) -> list[int]:
    """
    Convert the parser's Time UTC output to a list of 30-min slot indices (0-47).

    Input  : "14:00,14:30,15:00"   (from normalize_time_utc)
              or bare integers "14,15,16"  (legacy / manual entry)
    Output : [28, 29, 30]

    Slot index formula: hour * 2 + (1 if minutes >= 30 else 0)
      slot 0  = 00:00-00:30
      slot 1  = 00:30-01:00
      slot 28 = 14:00-14:30
      slot 29 = 14:30-15:00

    Fix vs original slots_str_to_hours():
    The old function stripped minutes before computing slot sets, so "14:30"
    alone would contribute hour 14 → slots {28, 29}, silently adding the
    14:00-14:30 slot the user never offered.  This function maps each token
    directly to its exact slot index.
    """
    if not slots_str:
        return []
    indices: set[int] = set()
    for tok in str(slots_str).split(","):
        tok = tok.strip()
        if not tok:
            continue
        if ":" in tok:
            try:
                h, m = tok.split(":", 1)
                idx  = int(h) * 2 + (1 if int(m) >= 30 else 0)
                if 0 <= idx < 48:
                    indices.add(idx)
            except (ValueError, IndexError):
                pass
        elif tok.isdigit():
            h = int(tok)
            if 0 <= h <= 23:
                # Bare hour — include both half-hours
                indices.add(h * 2)
                indices.add(h * 2 + 1)
    return sorted(indices)


# ── Days ─────────────────────────────────────────────────────────────────────

def normalize_days(raw: str) -> tuple[str, str]:
    """Parse a days string into (csv_of_day_numbers, confidence)."""
    if not raw:
        return "", LOW

    text    = raw.lower()
    day_map = {
        "mon": "1", "monday": "1",
        "tue": "2", "tuesday": "2",
        "wed": "3", "wednesday": "3",
        "thu": "4", "thursday": "4",
        "fri": "5", "friday": "5",
        "sat": "6", "saturday": "6",
        "sun": "7", "sunday": "7",
    }
    found = list(re.findall(r'\b([1-7])\b', text))
    for k, v in day_map.items():
        if re.search(rf'\b{k}\b', text):
            found.append(v)
    found = sorted(set(found), key=int)
    return (",".join(found), HIGH) if found else ("", LOW)


# ── Misc helpers ──────────────────────────────────────────────────────────────

def parse_ints(s) -> list[int]:
    """Parse a comma-separated string of integers, silently skipping bad tokens."""
    result = []
    for x in str(s).split(","):
        x = x.strip()
        if x:
            try:
                result.append(int(x))
            except ValueError:
                pass
    return result


def slot_label(slot: int) -> str:
    """
    Return a human-readable 30-minute window label, e.g. '14:00 – 14:30'.

    slot: 0-47 (slot 0 = 00:00-00:30, slot 47 = 23:30-00:00)
    """
    start_min = slot * 30
    end_min   = (start_min + 30) % (24 * 60)
    sh, sm    = divmod(start_min, 60)
    eh, em    = divmod(end_min,   60)
    return f"{sh:02d}:{sm:02d} – {eh:02d}:{em:02d}"
