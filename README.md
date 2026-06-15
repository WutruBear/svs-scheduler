# SvS Ministry Scheduler

A Streamlit app for scheduling Server-vs-Server ministry assignments.
It parses raw player sign-up responses, assigns each player to the
best available 30-minute time slot using **min-cost max-flow**, and
exports the result to Excel.

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/WutruBear/svs-scheduler.git
cd svs-scheduler

# 2. Install deps
pip install -r requirements.txt

# 3. Run
streamlit run app.py
```

---

## Project layout

```
svs/
├── svs/
│   ├── models.py          # Shared types (Confidence constants, TypedDicts)
│   ├── config.py          # DAY_CONFIG, game constants, sample loader
│   ├── parser/
│   │   ├── normalizers.py # Field-level parsers (duration, time, days, FC)
│   │   ├── text.py        # Raw-text block parser
│   │   └── csv_parser.py  # DataFrame parser
│   ├── scheduler.py       # MCMF scheduler + DataFrame builders
│   ├── export.py          # Excel export (parser + scheduler)
│   └── ui/
│       ├── components.py  # banner(), stat_card(), stepper()
│       └── styles.css     # All CSS (extracted from Python)
├── tests/
│   ├── test_normalizers.py
│   └── test_scheduler.py
├── app.py                 # Streamlit entrypoint (UI only)
├── sample_input.txt       # Bundled sample player responses
└── requirements.txt
```

---

## Running tests

```bash
pytest tests/ -v
```

Tests cover:
- All normaliser functions including the **slot-precision regression** (see below)
- MCMF scheduler: cardinality maximisation, score ranking, manual overrides, VP pass

---

## Key design decisions

### Slot precision (`slots_str_to_slot_indices`)

The original code collapsed `"HH:MM"` tokens to integer hours before
computing slot sets.  This meant `"14:30"` → hour `14` → slots `{28, 29}`,
silently adding the `14:00–14:30` slot the player never offered.

`slots_str_to_slot_indices()` maps each `"HH:MM"` token directly to its
30-minute slot index (`hour * 2 + (1 if minutes >= 30 else 0)`), preserving
full precision end-to-end.  The regression test `TestSlotPrecision` guards
this behaviour permanently.

### FC / shards CSV parsing

The original CSV parser synthesised a fake `"FC {n} shards {m}"` string and
fed it back through `parse_fc_shards()`.  The refactored code calls
`parse_fc_count()` and `parse_shard_count()` directly on the raw column
values, each handling their own input format.

### Scheduler isolation

`svs/scheduler.py` has zero Streamlit imports.  It takes and returns plain
Python dicts and lists, making it independently testable and reusable outside
Streamlit (e.g. in a CLI script or API).

### CSS extraction

All styles live in `svs/ui/styles.css`.  `load_css()` reads it at runtime,
giving syntax highlighting, linting support, and easy theming without touching
any Python file.

---

## Configuring event days

Edit `DAY_CONFIG` in `svs/config.py` to change which days are scheduled and
which speedup column is used for scoring:

```python
DAY_CONFIG = [
    {"day": 1, "label": "Day 1 — VP",  "col": "Construction"},
    {"day": 2, "label": "Day 2 — VP",  "col": "Research"},
    {"day": 4, "label": "Day 4 — MoE", "col": "Troops"},
]
```

No other file needs to change.

---

## Deploying to Streamlit Community Cloud

1. Push this repo to GitHub (public or private).
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**.
3. Set **Main file path** to `app.py`.
4. Deploy.

The app reads `sample_input.txt` at startup; real player data is never
committed to the repo (`.gitignore` excludes `*.csv` and `*.xlsx`).
