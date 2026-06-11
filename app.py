"""
SvS Ministry Scheduler — Streamlit entrypoint.

This file contains only Streamlit page logic (session state, widget layout,
data flow between pages).  All parsing, scheduling, and export logic lives
in the svs/ package.
"""

from __future__ import annotations
import io
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from svs.config import DAY_CONFIG, DAY4_VP_CONFIG, DAY_EXTRA_COL, load_sample_input
from svs.models import HIGH, MEDIUM, LOW, DISPLAY_FIELDS, FIELD_HINTS, FIELD_WARN_MSG
from svs.parser import (
    parse_input,
    parse_dataframe,
    normalize_duration,
    normalize_time_utc,
    normalize_days,
    parse_fc_shards,
    slots_str_to_slot_indices,
    parse_ints,
    slot_label,
    clean_str,
)
from svs.scheduler import (
    run_scheduler,
    run_day4_vp,
    build_timeline_df,
    build_unassigned_df,
    build_summary_df,
)
from svs.export import build_parser_excel, build_schedule_excel
from svs.ui import banner, stat_card, stat_row, render_stepper, load_css

# ── Page config (must be first Streamlit call) ────────────────────────────────
st.set_page_config(page_title="SvS #3442 tool", page_icon="⚔️", layout="wide")
load_css()

# ── Optional: browser local storage (soft dep) ───────────────────────────────
try:
    from streamlit_local_storage import LocalStorage
    _local_storage = LocalStorage()
    _browser_saved = _local_storage.getItem("svs_raw_input")
except Exception:
    _local_storage = None
    _browser_saved = None

SAMPLE_RAW = load_sample_input()
_initial_text = _browser_saved if (_browser_saved and str(_browser_saved).strip()) else SAMPLE_RAW


# ═══════════════════════════════════════════════════════════════════════════════
# SESSION STATE
# ═══════════════════════════════════════════════════════════════════════════════

_SESSION_DEFAULTS: dict = {
    "page":           "parser",
    "raw_input":      _initial_text,
    "manual_records": [],
    "excluded_ids":   set(),
    "corrections":    {},
    "_last_hash":     None,
    "_clipboard_csv": None,
    "parsed_df":      None,
    "dup_choices":    {},
    "slot_overrides": {},
    "run_history":    [],
}
for _k, _v in _SESSION_DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ═══════════════════════════════════════════════════════════════════════════════
# TOP NAV
# ═══════════════════════════════════════════════════════════════════════════════

if st.session_state["parsed_df"] is not None:
    n           = len(st.session_state["parsed_df"])
    badge_html  = f'<span class="topnav-badge">&#10003;&nbsp;{n} player{"s" if n != 1 else ""} loaded</span>'
else:
    badge_html  = '<span class="topnav-badge dim">awaiting data</span>'

st.markdown(f"""
<div class="topnav">
  <div class="topnav-inner">
    <div class="topnav-brand">
      <div class="topnav-icon">
        <svg viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id="sg" x1="9" y1="1" x2="9" y2="14" gradientUnits="userSpaceOnUse">
              <stop offset="0%" stop-color="#e8cc80"/>
              <stop offset="100%" stop-color="#9a7230"/>
            </linearGradient>
          </defs>
          <path d="M9 1 L9.8 11.5 L9 14 L8.2 11.5 Z" fill="url(#sg)"/>
          <rect x="5" y="11" width="8" height="1.5" rx="0.75" fill="#b89040"/>
          <rect x="7.5" y="12.5" width="3" height="4" rx="1" fill="#7a5820"/>
          <circle cx="9" cy="17" r="1" fill="#9a7230"/>
        </svg>
      </div>
      <div class="topnav-text">
        <div class="topnav-name">SvS <em>#3442</em></div>
        <div class="topnav-tagline">Ministry Scheduler</div>
      </div>
    </div>
    <div class="topnav-right">
      <span class="topnav-meta">v3.0</span>
      <div class="topnav-sep"></div>
      {badge_html}
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

nav_col1, nav_col2, _ = st.columns([1.5, 1.5, 5])
with nav_col1:
    if st.button(
        "Parser", use_container_width=True,
        type="primary" if st.session_state["page"] == "parser" else "secondary",
    ):
        st.session_state["page"] = "parser"
        st.rerun()
with nav_col2:
    if st.button(
        "Scheduler", use_container_width=True,
        type="primary" if st.session_state["page"] == "scheduler" else "secondary",
    ):
        st.session_state["page"] = "scheduler"
        st.rerun()

st.markdown("<div style='margin-bottom:1.5rem'></div>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 1 — PARSER
# ═══════════════════════════════════════════════════════════════════════════════

if st.session_state["page"] == "parser":

    has_data = st.session_state["parsed_df"] is not None
    render_stepper([
        ("Paste player data",  "active"),
        ("Review & correct",   "active"),
        ("Send to Scheduler",  "done" if has_data else "idle"),
        ("Run &amp; export",   "idle"),
    ])

    with st.expander("How to use this tool", expanded=(not has_data)):
        st.markdown("""
        <div class="guide-grid">
          <div class="guide-card">
            <div class="guide-card-icon">Step 01</div>
            <div class="guide-card-title">Paste player responses</div>
            <div class="guide-card-body">
              Copy the raw sign-up replies from WOS and paste the whole block into the
              <b>Raw Input</b> box. Each player entry must start with
              <code>User ID: &lt;number&gt;</code>.
            </div>
          </div>
          <div class="guide-card">
            <div class="guide-card-icon">Step 02</div>
            <div class="guide-card-title">Fix flagged fields</div>
            <div class="guide-card-body">
              Fields the parser couldn't read confidently are highlighted below.
              Click the field and type the correct value. You can also add missing players
              with the <b>Add player manually</b> form.
            </div>
          </div>
          <div class="guide-card">
            <div class="guide-card-icon">Step 03</div>
            <div class="guide-card-title">Send to Scheduler</div>
            <div class="guide-card-body">
              Once everything looks right, click <b>Send to Scheduler →</b> at the bottom.
              Switch to the <b>Scheduler</b> tab, then hit <b>Run scheduler</b>.
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("""
        <table class="fmt-table">
          <tr><th>Field</th><th>What it means</th><th>Accepted formats</th></tr>
          <tr><td>User ID</td><td>Unique player number</td><td><code>1</code>, <code>12345</code></td></tr>
          <tr><td>Level</td><td>FC tier</td><td><code>FC1</code> … <code>FC5</code></td></tr>
          <tr><td>Construction / Research / Troops</td><td>Speedups in days</td>
              <td><code>4d</code>, <code>3d 12h</code>, <code>7200min</code>, <code>5</code></td></tr>
          <tr><td>FCs / FC Shards</td><td>Resource counts</td>
              <td><code>FC 2693 shards 434</code>, <code>2,700 FCs</code></td></tr>
          <tr><td>Time UTC</td><td>Available hours (min 3 h window)</td>
              <td><code>14-17</code>, <code>16:00-19:30</code>, <code>00utc - 4utc, 20-23</code></td></tr>
          <tr><td>Days</td><td>Which SvS days the player joins</td>
              <td><code>1, 2, 4</code>, <code>Mon, Thu</code>, <code>Monday</code></td></tr>
        </table>
        <div style="font-size:0.73rem;color:var(--text-dim);margin-top:0.6rem;">
          Days: 1 = Construction &nbsp;·&nbsp; 2 = Research &nbsp;·&nbsp; 4 = Troops
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<div style='margin-bottom:1rem'></div>", unsafe_allow_html=True)

    st.markdown('<div class="section-label">Input Source</div>', unsafe_allow_html=True)
    input_mode = st.radio(
        "Mode", ["Raw Text Paste", "Spreadsheet Upload"],
        horizontal=True, label_visibility="collapsed",
    )
    st.markdown("<div style='margin-bottom:1rem'></div>", unsafe_allow_html=True)

    records, parse_warnings, duplicates = [], [], {}
    input_hash = 0

    if input_mode == "Raw Text Paste":
        col_input, col_tools = st.columns([4, 1])

        def _save_to_browser():
            if _local_storage:
                _local_storage.setItem("svs_raw_input", st.session_state["raw_input"])

        with col_input:
            raw_text = st.text_area(
                "raw", height=340, label_visibility="collapsed",
                key="raw_input", on_change=_save_to_browser,
            )

        with col_tools:
            uploaded = st.file_uploader(
                "Load .txt", type=["txt"], label_visibility="collapsed",
                accept_multiple_files=True,
            )
            if uploaded:
                merged = "\n".join(f.read().decode("utf-8") for f in uploaded)
                st.session_state["raw_input"] = merged
                st.rerun()
            st.download_button(
                "💾 Save raw input",
                data=st.session_state["raw_input"].encode("utf-8"),
                file_name="svs_input.txt", mime="text/plain",
                use_container_width=True,
            )

        if not raw_text.strip():
            st.info("Paste your player data in the text box above.")
            st.stop()

        records, parse_warnings, duplicates = parse_input(raw_text)
        input_hash = hash(raw_text)

    else:
        uploaded_df = st.file_uploader("Upload CSV / Excel file", type=["csv", "xlsx"])
        if not uploaded_df:
            st.info("Upload a spreadsheet containing raw player responses to begin.")
            st.stop()

        df_in = (
            pd.read_csv(uploaded_df)
            if uploaded_df.name.endswith(".csv")
            else pd.read_excel(uploaded_df)
        )
        cols = ["— none —"] + df_in.columns.tolist()

        st.markdown('<div class="section-label" style="margin-top:1rem">Map Columns</div>', unsafe_allow_html=True)

        def pick(label: str, match: str) -> str:
            best = next((c for c in cols if match.lower() in c.lower()), "— none —")
            return st.selectbox(label, cols, index=cols.index(best) if best in cols else 0)

        c1, c2, c3 = st.columns(3)
        mapping: dict[str, str] = {}
        with c1:
            mapping["User ID"] = pick("User ID *", "id")
            mapping["Level"]   = pick("Level", "level")
            mapping["FCs"]     = pick("FCs", "fc")
        with c2:
            mapping["Construction"] = pick("Construction", "const")
            mapping["Research"]     = pick("Research", "res")
            mapping["Troops"]       = pick("Troops", "troop")
        with c3:
            mapping["FC Shards"] = pick("FC Shards", "shard")
            mapping["Time UTC"]  = pick("Time UTC", "time")
            mapping["Days"]      = pick("Days", "day")

        records, parse_warnings, duplicates = parse_dataframe(df_in, mapping)
        input_hash = hash(uploaded_df.getvalue()) + hash(str(mapping))

    # ── Reset corrections when input changes ──────────────────────────────────
    if st.session_state["_last_hash"] != input_hash:
        new_corr = {
            rec["User ID"]: {f: rec[f] for f in DISPLAY_FIELDS if f != "User ID"}
            for rec in records
        }
        for mrec in st.session_state["manual_records"]:
            uid = mrec["User ID"]
            if uid not in new_corr:
                new_corr[uid] = {f: mrec.get(f, "") for f in DISPLAY_FIELDS if f != "User ID"}
        st.session_state["corrections"] = new_corr
        st.session_state["_last_hash"]  = input_hash
        st.session_state["dup_choices"] = {
            uid: v for uid, v in st.session_state["dup_choices"].items()
            if uid in duplicates
        }

    # ── Duplicate resolution UI ───────────────────────────────────────────────
    if duplicates:
        banner(
            "alert",
            f"<b>{len(duplicates)} duplicate User ID{'s' if len(duplicates) > 1 else ''} found.</b> "
            "Choose which entry to keep for each.",
        )
        for uid, dup_recs in duplicates.items():
            with st.expander(f"⚠ Duplicate — User ID {uid}  ({len(dup_recs)} entries)", expanded=True):
                option_labels = []
                for idx, rec in enumerate(dup_recs):
                    parts_preview = ", ".join(
                        f"{f}: {rec.get(f, '—')}"
                        for f in ("Construction", "Research", "Troops", "Time UTC", "Days")
                        if rec.get(f)
                    )
                    option_labels.append(f"Entry {idx + 1}  —  {parts_preview}")

                current_choice = st.session_state["dup_choices"].get(uid, 0)
                chosen = st.radio(
                    "Keep which entry?",
                    options=list(range(len(dup_recs))),
                    format_func=lambda i, _labels=option_labels: _labels[i],
                    index=current_choice,
                    key=f"dup_{uid}",
                    horizontal=False,
                )
                st.session_state["dup_choices"][uid] = chosen

        choice_map = st.session_state["dup_choices"]
        for i, rec in enumerate(records):
            uid = rec["User ID"]
            if uid in duplicates:
                chosen_idx   = choice_map.get(uid, 0)
                chosen_rec   = duplicates[uid][chosen_idx]
                records[i]   = chosen_rec
                st.session_state["corrections"][uid] = {
                    f: chosen_rec[f] for f in DISPLAY_FIELDS if f != "User ID"
                }

    for mrec in st.session_state["manual_records"]:
        uid = mrec["User ID"]
        if uid not in st.session_state["corrections"]:
            st.session_state["corrections"][uid] = {
                f: mrec.get(f, "") for f in DISPLAY_FIELDS if f != "User ID"
            }

    for w in parse_warnings:
        banner("error", f"⚠ {w}")

    all_records     = records + st.session_state["manual_records"]
    visible_records = [
        r for r in all_records
        if r["User ID"] not in st.session_state["excluded_ids"]
    ]

    if not all_records:
        st.warning("No valid records found. Make sure each block starts with 'User ID: <number>'.")
        st.stop()

    uncertain = [
        rec for rec in visible_records
        if any(
            rec.get(f"_conf_{f}", HIGH) in (LOW, MEDIUM)
            for f in DISPLAY_FIELDS if f != "User ID"
        )
    ]
    n_flagged = len(uncertain)

    stat_row(
        stat_card(len(visible_records), "Players"),
        stat_card(n_flagged,            "Need Review",  warn=bool(n_flagged)),
        stat_card(len(duplicates),      "Duplicates",   warn=bool(duplicates)),
        stat_card(len(st.session_state["excluded_ids"]), "Excluded"),
    )

    if uncertain:
        banner(
            "alert",
            f"<b>{n_flagged} record{'s' if n_flagged > 1 else ''} require attention</b> — "
            "some fields could not be parsed confidently. Review and correct below.",
        )
        for rec in uncertain:
            uid     = rec["User ID"]
            flagged = [
                f for f in DISPLAY_FIELDS
                if f != "User ID" and rec.get(f"_conf_{f}", HIGH) in (LOW, MEDIUM)
            ]
            label = f"User {uid}  ·  {len(flagged)} field{'s' if len(flagged) > 1 else ''} flagged"
            if rec.get("_manual"):
                label += "  [manual]"

            with st.expander(label, expanded=True):
                for f in flagged:
                    if rec.get(f"_warn_{f}"):
                        banner("info", f"ℹ {rec[f'_warn_{f}']}")

                if "_fc_raw" in rec and any(f in flagged for f in ("FCs", "FC Shards")):
                    st.markdown(
                        f'<div class="fc-raw">FC line: <span>{rec["_fc_raw"]}</span></div>',
                        unsafe_allow_html=True,
                    )

                n_cols = min(len(flagged), 3)
                cols_w = st.columns(n_cols)
                for i, field in enumerate(flagged):
                    conf = rec.get(f"_conf_{field}", HIGH)
                    with cols_w[i % n_cols]:
                        new_val = st.text_input(
                            label=field,
                            value=st.session_state["corrections"].get(uid, {}).get(field, ""),
                            key=f"fix_{uid}_{field}",
                            placeholder=FIELD_HINTS.get(field, ""),
                        )
                        st.markdown(
                            f'<div class="field-warn">{FIELD_WARN_MSG.get(conf, "")}</div>',
                            unsafe_allow_html=True,
                        )
                        st.session_state["corrections"].setdefault(uid, {})[field] = new_val

    # ── Add player manually ───────────────────────────────────────────────────
    with st.expander("➕ Add player manually"):
        with st.form("manual_add_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                m_uid    = st.text_input("User ID *",    placeholder="e.g. 99999")
                m_level  = st.text_input("Level",        placeholder="e.g. FC3")
                m_constr = st.text_input("Construction", placeholder="e.g. 24d 3h")
            with c2:
                m_res    = st.text_input("Research",     placeholder="e.g. 42d")
                m_troops = st.text_input("Troops",       placeholder="e.g. 5d")
                m_fcs    = st.text_input("FCs",          placeholder="e.g. 2700")
            with c3:
                m_shards = st.text_input("FC Shards",    placeholder="e.g. 434")
                m_time   = st.text_input("Time UTC",     placeholder="e.g. 14:00-17:30")
                m_days   = st.text_input("Days",         placeholder="e.g. 1, 2")
            submitted = st.form_submit_button("Add Player", use_container_width=True)

        if submitted:
            uid_str = m_uid.strip()
            if not uid_str:
                st.error("User ID is required.")
            elif uid_str in [r["User ID"] for r in all_records]:
                st.error(f"User ID {uid_str} already exists.")
            else:
                from svs.parser.normalizers import parse_fc_count, parse_shard_count
                constr_v, constr_c      = normalize_duration(m_constr)
                res_v,    res_c         = normalize_duration(m_res)
                troops_v, troops_c      = normalize_duration(m_troops)
                fc_v,     fc_c          = parse_fc_count(m_fcs)
                sh_v,     sh_c          = parse_shard_count(m_shards)
                time_v,   time_c, scount = normalize_time_utc(m_time)
                from svs.config import MIN_TIME_WINDOW_SLOTS
                if time_v and scount < MIN_TIME_WINDOW_SLOTS:
                    time_c = MEDIUM
                days_v, days_c = normalize_days(m_days)

                new_rec: dict = {
                    "User ID": uid_str,
                    "Level":        m_level.strip(),  "_conf_Level":        HIGH if m_level.strip() else LOW,
                    "Construction": constr_v,          "_conf_Construction": constr_c,
                    "Research":     res_v,             "_conf_Research":     res_c,
                    "Troops":       troops_v,          "_conf_Troops":       troops_c,
                    "FCs":          fc_v,              "_conf_FCs":          fc_c,
                    "FC Shards":    sh_v,              "_conf_FC Shards":    sh_c,
                    "Time UTC":     time_v,            "_conf_Time UTC":     time_c,
                    "Days":         days_v,            "_conf_Days":         days_c,
                    "_fc_raw":      f"{m_fcs} / {m_shards}".strip(" /"),
                    "_raw_block":   "(manual entry)",
                    "_manual":      True,
                }
                if time_v and scount < MIN_TIME_WINDOW_SLOTS:
                    new_rec["_warn_Time UTC"] = f"Only {scount / 2:.4g}h window — minimum is 3h"

                st.session_state["manual_records"].append(new_rec)
                st.session_state["corrections"][uid_str] = {
                    f: new_rec[f] for f in DISPLAY_FIELDS if f != "User ID"
                }
                st.success(f"Player {uid_str} added.")
                st.rerun()

    # ── Manage records ────────────────────────────────────────────────────────
    with st.expander("Manage records"):
        all_uids       = [r["User ID"] for r in all_records]
        manual_uid_set = {r["User ID"] for r in st.session_state["manual_records"]}

        col_del, col_clear = st.columns([3, 1], vertical_alignment="bottom")
        with col_del:
            to_exclude = st.multiselect(
                "Exclude from results and export",
                options=all_uids,
                default=[uid for uid in st.session_state["excluded_ids"] if uid in all_uids],
                format_func=lambda uid: f"User {uid}" + (" [manual]" if uid in manual_uid_set else ""),
            )
            st.session_state["excluded_ids"] = set(to_exclude)
        with col_clear:
            if st.button("Clear all manual", use_container_width=True):
                manual_ids = {r["User ID"] for r in st.session_state["manual_records"]}
                st.session_state["manual_records"] = []
                st.session_state["excluded_ids"]  -= manual_ids
                st.rerun()

        if st.session_state["excluded_ids"]:
            banner(
                "info",
                f"ℹ {len(st.session_state['excluded_ids'])} record(s) excluded from results and export.",
            )

    # ── Build final DataFrame ─────────────────────────────────────────────────
    final: list[dict]       = []
    conf_lookup: dict       = {}

    for rec in visible_records:
        uid = rec["User ID"]
        row = {f: rec[f] for f in DISPLAY_FIELDS}
        row.update(st.session_state["corrections"].get(uid, {}))
        final.append(row)
        for f in DISPLAY_FIELDS:
            conf_lookup[(uid, f)] = rec.get(f"_conf_{f}", HIGH)

    df         = pd.DataFrame(final, columns=DISPLAY_FIELDS)
    df_display = df.replace("", "—")
    df_display[""] = [
        "✎" if any(r["User ID"] == uid and r.get("_manual") for r in all_records) else ""
        for uid in df_display["User ID"]
    ]
    df_display["_Raw Input"] = [
        next((rec["_raw_block"] for rec in all_records if rec["User ID"] == uid), "")
        for uid in df_display["User ID"]
    ]

    st.markdown('<div class="section-label" style="margin-top:0.5rem">Results</div>', unsafe_allow_html=True)
    st.dataframe(
        df_display,
        use_container_width=True,
        hide_index=True,
        column_config={
            "User ID":      st.column_config.TextColumn("User ID",       width="medium"),
            "Level":        st.column_config.TextColumn("Level",         width="small"),
            "Construction": st.column_config.NumberColumn("Construction", format="%.2f", width="small"),
            "Research":     st.column_config.NumberColumn("Research",     format="%.2f", width="small"),
            "Troops":       st.column_config.NumberColumn("Troops",       format="%.2f", width="small"),
            "FCs":          st.column_config.NumberColumn("FCs",          format="%d",   width="small"),
            "FC Shards":    st.column_config.NumberColumn("Shards",       format="%d",   width="small"),
            "Time UTC":     st.column_config.TextColumn("Time (UTC)",     width="large"),
            "Days":         st.column_config.TextColumn("Days",           width="small"),
            "":             st.column_config.TextColumn("",               width="small",
                                help="✎ = manually added record"),
            "_Raw Input":   st.column_config.TextColumn("Original Input",
                                help="Hover to see raw player input", width="medium"),
        },
    )

    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown('<div class="section-label">Export &amp; send</div>', unsafe_allow_html=True)

    if st.button("🗓️  Send to Scheduler →", use_container_width=True, type="primary"):
        st.session_state["parsed_df"] = df.copy()
        st.session_state["page"]      = "scheduler"
        st.rerun()

    now           = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    df_export     = df[DISPLAY_FIELDS].copy()
    flagged_cells = {
        (uid, field)
        for (uid, field), conf in conf_lookup.items()
        if conf in (LOW, MEDIUM)
    }

    col_csv, col_xlsx, col_clip = st.columns(3)
    with col_csv:
        st.download_button(
            "⬇ CSV",
            data=df_export.to_csv(index=False).encode("utf-8"),
            file_name=f"svs_3442_{now}.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with col_xlsx:
        try:
            st.download_button(
                "⬇ Excel",
                data=build_parser_excel(df_export, flagged_cells),
                file_name=f"svs_3442_{now}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        except ImportError:
            banner("info", "Install <code>openpyxl</code> to enable Excel export.")
    with col_clip:
        if st.button("📋 Copy CSV", use_container_width=True):
            st.session_state["_clipboard_csv"] = df_export.to_csv(index=False)

    if st.session_state["_clipboard_csv"]:
        st.markdown(
            '<div class="section-label" style="margin-top:0.75rem">CSV — click icon to copy</div>',
            unsafe_allow_html=True,
        )
        st.code(st.session_state["_clipboard_csv"], language=None)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 2 — SCHEDULER
# ═══════════════════════════════════════════════════════════════════════════════

elif st.session_state["page"] == "scheduler":

    has_data = st.session_state["parsed_df"] is not None
    render_stepper([
        ("Paste player data",    "done"),
        ("Review &amp; correct", "done"),
        ("Send to Scheduler",    "done" if has_data else "idle"),
        ("Run &amp; export",     "active"),
    ])

    if not has_data:
        st.markdown("""
        <div class="empty-state">
          <div class="empty-state-icon">&#128197;</div>
          <div class="empty-state-title">No player data yet</div>
          <div class="empty-state-body">
            Go to the <b>Parser</b> page, paste your player responses, fix any flagged fields,
            then click <b>Send to Scheduler &#8594;</b> to load data here.
          </div>
        </div>
        """, unsafe_allow_html=True)

    # Built-in sample data
    SCHEDULER_SAMPLE = pd.DataFrame([
        {"User ID": 1,  "Level": "FC1", "Construction": 1.0,  "Research": 1.0,  "Troops": 1.0,  "FCs": 1200, "FC Shards": 210, "Time UTC": "16:00,16:30,17:00,17:30,18:00,18:30", "Days": "1,2"},
        {"User ID": 2,  "Level": "FC2", "Construction": 2.08, "Research": 2.0,  "Troops": 2.08, "FCs": 2100, "FC Shards": 380, "Time UTC": "07:00,07:30,08:00,08:30,09:00,09:30,10:00,10:30,11:00,11:30,12:00,12:30,13:00,13:30,14:00,14:30,15:00,15:30,16:00,16:30,17:00,17:30,18:00,18:30,19:00,19:30,20:00,20:30", "Days": "2,4"},
        {"User ID": 3,  "Level": "FC3", "Construction": 3.0,  "Research": 3.04, "Troops": 3.0,  "FCs": 2450, "FC Shards": 290, "Time UTC": "00:00,00:30,10:00,10:30,11:00,11:30,23:00,23:30", "Days": "1,2"},
        {"User ID": 4,  "Level": "FC4", "Construction": 4.0,  "Research": 4.17, "Troops": 4.0,  "FCs": 2600, "FC Shards": 410, "Time UTC": "14:00,14:30,15:00,15:30,16:00,16:30", "Days": "1,2,4"},
        {"User ID": 5,  "Level": "FC5", "Construction": 5.0,  "Research": 5.21, "Troops": 5.0,  "FCs": 2693, "FC Shards": 434, "Time UTC": "00:00,00:30,01:00,01:30,02:00,02:30,03:00,03:30,09:00,09:30,20:00,20:30,21:00,21:30,22:00,22:30,23:00,23:30", "Days": "1,4"},
        {"User ID": 6,  "Level": "FC2", "Construction": 2.5,  "Research": 2.3,  "Troops": 2.1,  "FCs": 1950, "FC Shards": 310, "Time UTC": "16:00,16:30,17:00,17:30", "Days": "1"},
        {"User ID": 7,  "Level": "FC3", "Construction": 3.3,  "Research": 3.1,  "Troops": 2.8,  "FCs": 2300, "FC Shards": 350, "Time UTC": "16:00,16:30,17:00,17:30", "Days": "1"},
        {"User ID": 8,  "Level": "FC1", "Construction": 1.1,  "Research": 0.9,  "Troops": 1.0,  "FCs": 900,  "FC Shards": 150, "Time UTC": "16:00,16:30,17:00,17:30", "Days": "1"},
        {"User ID": 9,  "Level": "FC2", "Construction": 2.0,  "Research": 1.8,  "Troops": 1.9,  "FCs": 1800, "FC Shards": 270, "Time UTC": "16:00,16:30,17:00,17:30", "Days": "1"},
        {"User ID": 10, "Level": "FC4", "Construction": 4.5,  "Research": 4.2,  "Troops": 4.3,  "FCs": 2550, "FC Shards": 420, "Time UTC": "16:00,16:30,17:00,17:30", "Days": "1"},
        {"User ID": 11, "Level": "FC3", "Construction": 3.7,  "Research": 3.5,  "Troops": 3.4,  "FCs": 2400, "FC Shards": 390, "Time UTC": "16:00,16:30,17:00,17:30", "Days": "1,2"},
        {"User ID": 12, "Level": "FC2", "Construction": 2.2,  "Research": 2.4,  "Troops": 2.0,  "FCs": 2050, "FC Shards": 330, "Time UTC": "06:00,06:30,07:00,07:30,08:00,08:30", "Days": "2,4"},
        {"User ID": 13, "Level": "FC5", "Construction": 5.3,  "Research": 5.1,  "Troops": 5.2,  "FCs": 2700, "FC Shards": 440, "Time UTC": "12:00,12:30,13:00,13:30,14:00,14:30", "Days": "1,2,4"},
        {"User ID": 14, "Level": "FC1", "Construction": 1.3,  "Research": 1.1,  "Troops": 1.4,  "FCs": 1100, "FC Shards": 190, "Time UTC": "22:00,22:30,23:00,23:30", "Days": "2"},
        {"User ID": 15, "Level": "FC4", "Construction": 4.1,  "Research": 3.9,  "Troops": 4.0,  "FCs": 2580, "FC Shards": 415, "Time UTC": "16:00,16:30,17:00,17:30", "Days": "1"},
    ])

    st.markdown('<div class="section-label">Data source</div>', unsafe_allow_html=True)
    source_options = ["Use parsed data from Parser", "Use built-in sample data", "Upload CSV / Excel"]
    default_src    = 0 if has_data else 1
    source = st.radio(
        "Data source", source_options, index=default_src,
        horizontal=True, label_visibility="collapsed",
    )

    raw_df = None
    if source == "Use parsed data from Parser":
        if st.session_state["parsed_df"] is not None:
            raw_df = st.session_state["parsed_df"].copy()
            for col in ["Construction", "Research", "Troops"]:
                raw_df[col] = pd.to_numeric(raw_df[col], errors="coerce")
            n_before  = len(raw_df)
            raw_df    = raw_df.dropna(subset=["Construction", "Research", "Troops"])
            n_dropped = n_before - len(raw_df)
            banner(
                "success",
                f"✓ Using {len(raw_df)} player(s) from Parser"
                + (f" — {n_dropped} skipped (missing numeric fields)" if n_dropped else ""),
            )
        else:
            banner("alert", 'No parsed data yet — go to the Parser page first, then click "Send to Scheduler".')

    elif source == "Use built-in sample data":
        raw_df = SCHEDULER_SAMPLE.copy()
        banner("info", "ℹ Using built-in sample dataset (15 users).")

    else:
        upload = st.file_uploader("Upload file", type=["csv", "xlsx"])
        if upload:
            raw_df = (
                pd.read_csv(upload) if upload.name.endswith(".csv") else pd.read_excel(upload)
            )
            banner("success", f"✓ Loaded {len(raw_df)} rows.")

    if raw_df is not None:
        with st.expander("Preview loaded data", expanded=False):
            st.dataframe(raw_df, use_container_width=True)

    # ── Manual slot overrides ─────────────────────────────────────────────────
    st.markdown("<hr>", unsafe_allow_html=True)
    _override_player_ids = (
        [str(row[raw_df.columns[0]]) for _, row in raw_df.iterrows()]
        if raw_df is not None else []
    )
    with st.expander("📌 Manual slot overrides", expanded=bool(st.session_state["slot_overrides"])):
        if not _override_player_ids:
            banner("info", "ℹ Load data using the <b>Data source</b> selector above to enable player pins.")
        else:
            st.caption(
                "Pin one or more players to a specific slot before the solver runs. "
                "Leave a day blank to let the solver decide freely."
            )
            override_uid = st.selectbox(
                "Select player to pin",
                options=["— none —"] + _override_player_ids,
                key="override_uid_select",
            )
            if override_uid != "— none —":
                st.markdown(f"**Pinning User {override_uid}**")
                ov_cols = st.columns(len(DAY_CONFIG))
                for ci, dc in enumerate(DAY_CONFIG):
                    with ov_cols[ci]:
                        all_slot_labels = ["(solver decides)"] + [slot_label(s) for s in range(48)]
                        current_pin = st.session_state["slot_overrides"].get(override_uid, {}).get(dc["day"])
                        current_idx = (current_pin + 1) if current_pin is not None else 0
                        sel = st.selectbox(
                            dc["label"], options=all_slot_labels, index=current_idx,
                            key=f"ov_{override_uid}_{dc['day']}",
                        )
                        uid_ovs = st.session_state["slot_overrides"].setdefault(override_uid, {})
                        if sel == "(solver decides)":
                            uid_ovs.pop(dc["day"], None)
                        else:
                            uid_ovs[dc["day"]] = all_slot_labels.index(sel) - 1

        active_overrides = {uid: days for uid, days in st.session_state["slot_overrides"].items() if days}
        if active_overrides:
            st.markdown("**Active pins:**")
            pin_rows = []
            for uid, days in active_overrides.items():
                for day, s in days.items():
                    dc_label = next((d["label"] for d in DAY_CONFIG if d["day"] == day), f"Day {day}")
                    pin_rows.append({"User ID": uid, "Day": dc_label, "Pinned slot": slot_label(s)})
            st.dataframe(pd.DataFrame(pin_rows), use_container_width=True, hide_index=True)
            if st.button("🗑 Clear all pins", key="clear_pins"):
                st.session_state["slot_overrides"] = {}
                st.rerun()

    if raw_df is not None:
        st.markdown(
            '<div class="section-label" style="margin-top:1rem">Column mapping</div>',
            unsafe_allow_html=True,
        )
        cols = raw_df.columns.tolist()

        def pick_col(label: str, default: str) -> str:
            return st.selectbox(label, cols, index=cols.index(default) if default in cols else 0)

        c1, c2, c3 = st.columns(3)
        with c1:
            id_col  = pick_col("User ID column",     "User ID")
            lvl_col = pick_col("Level column",        "Level")
            fcs_col = pick_col("FCs column",          "FCs")
        with c2:
            con_col = pick_col("Construction column", "Construction")
            res_col = pick_col("Research column",     "Research")
            trp_col = pick_col("Troops column",       "Troops")
        with c3:
            shards_col   = pick_col("FC Shards column",    "FC Shards")
            time_default = next((c for c in ("Time", "Hours", "Time UTC") if c in cols), cols[0])
            time_col     = pick_col("Time UTC / Hours column", time_default)
            days_col     = pick_col("Days column", "Days")

        st.markdown("<hr>", unsafe_allow_html=True)
        banner("info", """
            <b>Scheduling objective:</b> Maximise the number of players assigned a slot.
            Among all solutions that place the same number of players, the highest-scoring
            players are preferred — guaranteed by min-cost max-flow (provably optimal score ranking).
        """)

        run_label_input = st.text_input(
            "Run label (optional)",
            placeholder="e.g. 'After fixing User 42 window'",
            key="run_label_input",
        )

        if st.button("⚡ Run scheduler", type="primary", use_container_width=True):

            def _safe_int(row, col_name):
                val = row[col_name]
                try:
                    return int(float(val)) if pd.notna(val) and clean_str(val) not in ("", "—") else None
                except (ValueError, TypeError):
                    return None

            users: list[dict] = []
            for _, row in raw_df.iterrows():
                raw_time_val = clean_str(row[time_col])
                # slots_str_to_slot_indices handles both "HH:MM,..." and bare integers
                slots = slots_str_to_slot_indices(raw_time_val)

                try:
                    con_val = float(row[con_col])
                    res_val = float(row[res_col])
                    trp_val = float(row[trp_col])
                except (ValueError, TypeError):
                    continue

                users.append({
                    "User ID":      str(row[id_col]),
                    "Level":        clean_str(row[lvl_col]),
                    "Construction": con_val,
                    "Research":     res_val,
                    "Troops":       trp_val,
                    "FCs":          _safe_int(row, fcs_col),
                    "FC Shards":    _safe_int(row, shards_col),
                    "slots":        slots,
                    "days":         parse_ints(clean_str(row[days_col])),
                })

            if not users:
                st.error("No valid users to schedule — check your column mapping and data.")
            else:
                # Inject manual slot overrides
                overrides = st.session_state["slot_overrides"]
                users_for_run = []
                for u in users:
                    uid_str = str(u["User ID"])
                    if uid_str in overrides:
                        u_copy = dict(u)
                        u_copy["_slot_overrides"] = overrides[uid_str]
                        users_for_run.append(u_copy)
                    else:
                        users_for_run.append(u)

                with st.spinner("Running min-cost max-flow scheduler…"):
                    day_results = run_scheduler(users_for_run)
                    day4_result = next((dr for dr in day_results if dr["day"] == 4), None)
                    vp4_result  = run_day4_vp(users_for_run, day4_result) if day4_result else None

                # Save to run history
                run_ts    = datetime.now(timezone.utc).strftime("%H:%M UTC")
                run_label = run_label_input.strip() or run_ts
                snap: dict = {}
                for dr in day_results:
                    for uid, s in dr["user_slot"].items():
                        snap.setdefault(str(uid), {})[dr["day"]] = s
                if vp4_result:
                    for uid, s in vp4_result["user_slot"].items():
                        snap.setdefault(str(uid), {})["4vp"] = s

                history = st.session_state["run_history"]
                history.append({"label": run_label, "snap": snap, "ts": run_ts})
                st.session_state["run_history"] = history[-5:]

                st.markdown('<div class="section-label" style="margin-top:1.5rem">Results</div>', unsafe_allow_html=True)

                total_possible   = sum(len(dr["eligible"])  for dr in day_results)
                total_assigned   = sum(len(dr["user_slot"]) for dr in day_results)
                total_unassigned = sum(len(dr["unassigned"]) for dr in day_results)
                pct = round(100 * total_assigned / total_possible) if total_possible else 0

                stat_row(
                    stat_card(total_possible,   "Eligible slots-days"),
                    stat_card(total_assigned,   "Assigned"),
                    stat_card(f"{pct}%",        "Fill rate"),
                    stat_card(total_unassigned, "Unassignable", warn=bool(total_unassigned)),
                )

                # Changelog diff
                if len(st.session_state["run_history"]) >= 2:
                    history = st.session_state["run_history"]
                    with st.expander("🔄 Changelog — diff vs previous run", expanded=False):
                        compare_options = [h["label"] for h in history[:-1]]
                        compare_label   = st.selectbox(
                            "Compare current run against:",
                            options=compare_options,
                            index=len(compare_options) - 1,
                            key="changelog_compare_select",
                        )
                        prev_snap = next(h["snap"] for h in history if h["label"] == compare_label)
                        curr_snap = history[-1]["snap"]

                        day_keys = {dc["day"]: dc["label"] for dc in DAY_CONFIG}
                        day_keys["4vp"] = DAY4_VP_CONFIG["label"]

                        all_uids  = sorted(set(prev_snap) | set(curr_snap), key=str)
                        diff_rows = []
                        for uid in all_uids:
                            prev_days = prev_snap.get(uid, {})
                            curr_days = curr_snap.get(uid, {})
                            for day in sorted(set(prev_days) | set(curr_days), key=lambda d: str(d)):
                                p = prev_days.get(day)
                                c = curr_days.get(day)
                                if p == c:
                                    continue
                                diff_rows.append({
                                    "User ID":  uid,
                                    "Day":      day_keys.get(day, f"Day {day}"),
                                    "Previous": slot_label(p) if p is not None else "unassigned",
                                    "Current":  slot_label(c) if c is not None else "unassigned",
                                    "Change": (
                                        "✅ now assigned"   if p is None and c is not None else
                                        "❌ now unassigned" if p is not None and c is None else
                                        "↔ moved slot"
                                    ),
                                })

                        if not diff_rows:
                            banner("success", "✓ No changes — this run produced identical assignments.")
                        else:
                            n_better = sum(1 for r in diff_rows if r["Change"] == "✅ now assigned")
                            n_worse  = sum(1 for r in diff_rows if r["Change"] == "❌ now unassigned")
                            n_moved  = sum(1 for r in diff_rows if r["Change"] == "↔ moved slot")
                            banner(
                                "info",
                                f"<b>{len(diff_rows)} change(s)</b> vs <em>{compare_label}</em>: "
                                f"{n_better} newly assigned &nbsp;·&nbsp; "
                                f"{n_worse} newly unassigned &nbsp;·&nbsp; "
                                f"{n_moved} slot change(s).",
                            )
                            st.dataframe(
                                pd.DataFrame(diff_rows),
                                use_container_width=True, hide_index=True,
                                column_config={
                                    "User ID":  st.column_config.TextColumn(width="small"),
                                    "Day":      st.column_config.TextColumn(width="medium"),
                                    "Previous": st.column_config.TextColumn(width="medium"),
                                    "Current":  st.column_config.TextColumn(width="medium"),
                                    "Change":   st.column_config.TextColumn(width="medium"),
                                },
                            )

                # Peak availability chart
                st.markdown("#### 📊 Peak Availability")
                st.caption("Total number of players available at each 30-minute time slot.")
                slot_counts = {s: 0 for s in range(48)}
                for u in users_for_run:
                    for s in u["slots"]:
                        slot_counts[s] += 1
                chart_df = pd.DataFrame([
                    {"Time Slot": slot_label(s), "Players Available": count}
                    for s, count in slot_counts.items()
                ])
                st.bar_chart(chart_df.set_index("Time Slot"), color="#00e5cc")

                # Summary table
                st.markdown("#### 📋 User summary — all days")
                st.caption(
                    "Each cell shows the assigned slot and speedups. "
                    "❌ = window saturated. — = not participating on that day."
                )
                st.dataframe(
                    build_summary_df(users_for_run, day_results, vp4_result),
                    use_container_width=True, hide_index=True,
                )

                # Per-day tabs
                st.markdown("#### 📅 Per-day detail")
                tab_labels = [dc["label"] for dc in DAY_CONFIG]
                if vp4_result is not None:
                    tab_labels.append(DAY4_VP_CONFIG["label"])
                tabs = st.tabs(tab_labels)

                for i, dc in enumerate(DAY_CONFIG):
                    dr = day_results[i]
                    with tabs[i]:
                        ca, cb, cc = st.columns(3)
                        ca.metric("Eligible users", len(dr["eligible"]))
                        cb.metric("Assigned",       len(dr["user_slot"]))
                        cc.metric("Unassigned",     len(dr["unassigned"]))

                        if dr["user_slot"] and dr["unassigned"]:
                            placed_scores   = [u[dc["col"]] for u in dr["eligible"] if u["User ID"] in dr["user_slot"]]
                            unplaced_scores = [e["user"][dc["col"]] for e in dr["unassigned"]]
                            min_p = min(placed_scores)
                            max_u = max(unplaced_scores)
                            if max_u > min_p + 0.001:
                                banner("info",
                                    f"ℹ Some unassigned players have more speedups than the lowest-placed player "
                                    f"(highest unassigned: <b>{max_u:.2f}</b>, lowest placed: <b>{min_p:.2f}</b>).")
                            else:
                                banner("success",
                                    f"✓ Optimal speedup order: every placed player has ≥ speedups than every unplaced player "
                                    f"(min placed {min_p:.2f} ≥ max unplaced {max_u:.2f}).")

                        st.markdown("**Slot timeline**")
                        extra_col   = DAY_EXTRA_COL.get(dc["day"])
                        timeline_df = build_timeline_df(users_for_run, dr, extra_col=extra_col)
                        col_cfg = {
                            "Speedups":  st.column_config.TextColumn(width="small"),
                            "Time Slot": st.column_config.TextColumn(width="medium"),
                            "Assigned":  st.column_config.TextColumn(width="medium"),
                        }
                        if extra_col:
                            col_cfg[extra_col] = st.column_config.TextColumn(extra_col, width="small")
                        st.dataframe(timeline_df, use_container_width=True, column_config=col_cfg)

                        ua_df = build_unassigned_df(dr)
                        if ua_df.empty:
                            banner("success", "✅ All eligible users assigned on this day.")
                        else:
                            st.markdown("**Unassigned users**")
                            st.dataframe(ua_df, use_container_width=True, hide_index=True)

                # Day 4 VP tab
                if vp4_result is not None:
                    with tabs[len(DAY_CONFIG)]:
                        banner("info",
                            "ℹ <b>Day 4 — VP</b> gives players who were <b>unassigned in Day 4 — MoE</b> "
                            "a second-chance VP slot.")

                        ca, cb, cc = st.columns(3)
                        ca.metric("Eligible (unassigned from MoE)", len(vp4_result["eligible"]))
                        cb.metric("Assigned VP slots",              len(vp4_result["user_slot"]))
                        cc.metric("Still unassigned",               len(vp4_result["unassigned"]))

                        if not vp4_result["eligible"]:
                            banner("success", "✅ No unassigned players from Day 4 — MoE.")
                        else:
                            st.markdown("**Slot timeline**")
                            vp4_tl = build_timeline_df(users_for_run, vp4_result)
                            st.dataframe(vp4_tl, use_container_width=True, column_config={
                                "Speedups":  st.column_config.TextColumn(width="small"),
                                "Time Slot": st.column_config.TextColumn(width="medium"),
                                "Assigned":  st.column_config.TextColumn(width="medium"),
                            })
                            vp4_ua = build_unassigned_df(vp4_result)
                            if vp4_ua.empty:
                                banner("success", "✅ All eligible players assigned a VP slot.")
                            else:
                                st.markdown("**Still unassigned after VP pass**")
                                st.dataframe(vp4_ua, use_container_width=True, hide_index=True)

                st.markdown("<hr>", unsafe_allow_html=True)
                st.markdown('<div class="section-label">Export</div>', unsafe_allow_html=True)
                st.download_button(
                    label="📥 Download full schedule (.xlsx)",
                    data=build_schedule_excel(users_for_run, day_results, vp4_result),
                    file_name="SvS_schedule.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )