"""
Min-cost max-flow scheduler.

Completely isolated from Streamlit — takes plain Python lists/dicts,
returns plain Python dicts.  This makes it independently testable.

Users dict schema (see models.SchedulerUser):
  - "User ID"     : str
  - "Construction", "Research", "Troops" : float  (days of speedups)
  - "slots"       : list[int]  — sorted 30-min slot indices (0-47)
  - "days"        : list[int]  — SvS day numbers the user participates in
  - "_slot_overrides" (optional): {day: slot_index} — UI-set manual pins

networkx is imported lazily inside run_day() to avoid a heavy cold-start
penalty when the module is loaded (e.g. on import for tests of other modules).

Slot-precision fix:
  The original scheduler used user["hours"] (list of ints 0-23) and expanded
  each hour to two slots via {h*2, h*2+1}.  This over-counted: "14:30" alone
  would yield hour 14 → slots {28, 29}, adding 14:00-14:30 which the user
  never offered.  The new schema stores "slots" as a pre-computed list of
  slot indices (0-47) — _user_slots() now just returns set(user["slots"]).
"""

from __future__ import annotations

from svs.config import DAY_CONFIG, DAY4_VP_CONFIG
from svs.parser.normalizers import slot_label


# ── Internal helpers ─────────────────────────────────────────────────────────

def _user_slots(user: dict) -> set[int]:
    """Return the set of available 30-min slot indices for a user."""
    return set(user["slots"])


# ── Single-day solver ────────────────────────────────────────────────────────

def run_day(users: list[dict], dc: dict) -> dict:
    """
    Run the MCMF scheduler for one day config entry and return a results dict.

    Graph structure:
        S → user_i   (cap 1, cost 0)
        user_i → slot_j   (cap 1, cost ∝ max_score − score_i)
        slot_j → T   (cap 1, cost 0)

    networkx.max_flow_min_cost() maximises flow first (= max users placed),
    then minimises cost (= maximises total score of placed users).
    This is provably optimal — no assignment can simultaneously place more
    users AND rank higher-scorers better.
    """
    import networkx as nx   # lazy import — only needed during scheduling

    col = dc["col"]
    day = dc["day"]

    # 1. Deduplicate by User ID — keep the highest-scoring entry
    best_by_uid: dict = {}
    for u in users:
        uid = u["User ID"]
        if uid not in best_by_uid or u[col] > best_by_uid[uid][col]:
            best_by_uid[uid] = u

    eligible = [u for u in best_by_uid.values() if day in u["days"] and u["slots"]]
    if not eligible:
        return _empty_result(day, col)

    eligible = sorted(eligible, key=lambda u: u[col], reverse=True)

    # 2. Apply manual slot overrides — pinned users are pre-placed
    pinned_slot_occ:  dict[int, str] = {}    # slot → uid
    pinned_user_slot: dict[str, int] = {}    # uid  → slot
    free_eligible:    list[dict]     = []

    for u in eligible:
        uid          = u["User ID"]
        override_slot = u.get("_slot_overrides", {}).get(day)
        if override_slot is not None and 0 <= override_slot < 48:
            if override_slot not in pinned_slot_occ:
                pinned_slot_occ[override_slot]  = uid
                pinned_user_slot[uid]           = override_slot
            else:
                free_eligible.append(u)   # pin conflict — fall through to solver
        else:
            free_eligible.append(u)

    # 3. Build slot universe from all eligible users' available slots
    all_slots = sorted({s for u in eligible for s in u["slots"]})
    for s in pinned_slot_occ:
        if s not in all_slots:
            all_slots = sorted(set(all_slots) | {s})

    # 4. Edge costs for free_eligible (lower score → higher cost → deprioritised)
    SCALE      = 10_000
    max_score  = eligible[0][col] if eligible else 1.0
    free_costs = [
        int((max_score - u[col]) * SCALE) + i
        for i, u in enumerate(free_eligible)
    ]

    # 5. Build and solve the flow network
    slot_occ:  dict[int, str] = dict(pinned_slot_occ)
    user_slot: dict[str, int] = dict(pinned_user_slot)
    available_slots = sorted(s for s in all_slots if s not in pinned_slot_occ)

    if free_eligible and available_slots:
        S, T       = "S", "T"
        user_nodes = [f"U{i}" for i in range(len(free_eligible))]
        slot_node  = {s: f"SL{s}" for s in available_slots}

        G = nx.DiGraph()
        for i in range(len(free_eligible)):
            G.add_edge(S, user_nodes[i], capacity=1, weight=0)

        for i, u in enumerate(free_eligible):
            u_slots = _user_slots(u) & set(available_slots)
            for s in u_slots:
                G.add_edge(user_nodes[i], slot_node[s], capacity=1, weight=free_costs[i])

        for s in available_slots:
            G.add_edge(slot_node[s], T, capacity=1, weight=0)

        flow_dict = nx.max_flow_min_cost(G, S, T)

        for i, u in enumerate(free_eligible):
            uid = u["User ID"]
            un  = user_nodes[i]
            for s in available_slots:
                if flow_dict.get(un, {}).get(slot_node[s], 0) == 1:
                    slot_occ[s]    = uid
                    user_slot[uid] = s
                    break

    # 6. Build unassigned list with explanation
    min_assigned_score = min(
        (u[col] for u in eligible if u["User ID"] in user_slot),
        default=float("inf"),
    )

    unassigned = []
    for u in eligible:
        uid = u["User ID"]
        if uid in user_slot:
            continue
        all_u_slots = _user_slots(u)
        blockers    = {slot_occ[s] for s in all_u_slots if s in slot_occ}
        names       = sorted(str(b) for b in blockers)

        if u[col] < min_assigned_score:
            reason = "not enough speedups"
            detail = f"Score {u[col]:.2f} < minimum placed {min_assigned_score:.2f}."
        else:
            reason = "Window saturated"
            detail = (
                f"All {len(all_u_slots)} slot(s) in their time window are taken — "
                f"Blocked by: {', '.join(names) or 'unknown'}."
            )
        unassigned.append({"user": u, "reason": reason, "detail": detail})

    return {
        "day":           day,
        "col":           col,
        "slot_occ":      slot_occ,
        "user_slot":     user_slot,
        "via_reshuffle": set(),
        "moved":         {},
        "chains":        [],
        "unassigned":    unassigned,
        "eligible":      eligible,
    }


def _empty_result(day: int, col: str) -> dict:
    return {
        "day": day, "col": col,
        "slot_occ": {}, "user_slot": {},
        "via_reshuffle": set(), "moved": {}, "chains": [],
        "unassigned": [], "eligible": [],
    }


# ── Multi-day entry points ────────────────────────────────────────────────────

def run_scheduler(users: list[dict]) -> list[dict]:
    """Run the scheduler for all DAY_CONFIG entries and return a list of results."""
    return [run_day(users, dc) for dc in DAY_CONFIG]


def run_day4_vp(users: list[dict], day4_result: dict) -> dict:
    """
    Second VP scheduling pass for Day 4 — restricted to players who were
    unassigned in the Day 4 MoE run.

    Players who couldn't get a MoE slot (window saturated or low score) get
    a second chance here.  Scheduling logic is identical to run_day().
    """
    unassigned_ids = {e["user"]["User ID"] for e in day4_result["unassigned"]}
    if not unassigned_ids:
        result = _empty_result(4, "Troops")
        result["is_vp4"] = True
        return result

    vp4_users = [u for u in users if u["User ID"] in unassigned_ids and 4 in u["days"]]
    result = run_day(vp4_users, DAY4_VP_CONFIG)
    result["is_vp4"] = True
    return result


# ── DataFrame builders ────────────────────────────────────────────────────────
# These live here (not in export.py) because they depend on scheduler result
# dicts and need access to _user_slots / slot_label.

def build_timeline_df(users: list[dict], dr: dict, extra_col: str | None = None):
    """Build the slot-timeline DataFrame for a single day result."""
    import pandas as pd

    col         = dr["col"]
    avail_slots = sorted({s for u in dr["eligible"] for s in u["slots"]})
    user_by_id  = {u["User ID"]: u for u in users}

    rows = []
    for slot in avail_slots:
        auid = dr["slot_occ"].get(slot)
        au   = user_by_id.get(auid)
        if au:
            assigned_id    = f"{au['User ID']}"
            assigned_score = au[col]
        else:
            assigned_id, assigned_score = "empty", None

        row = {
            "Time Slot": slot_label(slot),
            "Assigned":  assigned_id,
            "Speedups":  f"{assigned_score:.2f}" if assigned_score is not None else "—",
        }
        if extra_col:
            row[extra_col] = (
                str(int(au[extra_col]))
                if au and au.get(extra_col) not in (None, "", "—")
                else "—"
            )
        rows.append(row)
    return pd.DataFrame(rows)


def build_unassigned_df(dr: dict):
    """Build the unassigned-users DataFrame for a single day result."""
    import pandas as pd

    return pd.DataFrame([{
        "User ID":  f"{e['user']['User ID']}",
        "Speedups": f"{e['user'][dr['col']]:.2f}",
        "Reason":   e.get("reason", ""),
        "Detail":   e.get("detail", "—"),
    } for e in dr["unassigned"]])


def build_summary_df(users: list[dict], day_results: list[dict], vp4_result: dict | None = None):
    """Build the cross-day summary DataFrame."""
    import pandas as pd

    day_map = {dr["day"]: dr for dr in day_results}
    day4_dr = day_map.get(4)
    day4_unassigned_ids: set = (
        {e["user"]["User ID"] for e in day4_dr["unassigned"]} if day4_dr else set()
    )

    rows = []
    for u in users:
        row = {"User ID": u["User ID"], "Level": u["Level"]}
        for dc in DAY_CONFIG:
            dr  = day_map[dc["day"]]
            col = dc["col"]
            if dc["day"] not in u["days"]:
                row[dc["label"]] = "—"
            elif u["User ID"] in dr["user_slot"]:
                slot = dr["user_slot"][u["User ID"]]
                row[dc["label"]] = f"{slot_label(slot)}  [{u[col]:.2f}]"
            else:
                row[dc["label"]] = f"❌ unassigned  [{u[col]:.2f}]"

        if vp4_result is not None:
            col = "Troops"
            if 4 not in u["days"]:
                row[DAY4_VP_CONFIG["label"]] = "—"
            elif day4_dr and u["User ID"] in day4_dr["user_slot"]:
                row[DAY4_VP_CONFIG["label"]] = "— (MoE placed)"
            elif u["User ID"] in vp4_result["user_slot"]:
                slot = vp4_result["user_slot"][u["User ID"]]
                row[DAY4_VP_CONFIG["label"]] = f"{slot_label(slot)}  [{u[col]:.2f}]"
            elif u["User ID"] in day4_unassigned_ids:
                row[DAY4_VP_CONFIG["label"]] = f"❌ unassigned  [{u[col]:.2f}]"
            else:
                row[DAY4_VP_CONFIG["label"]] = "—"

        rows.append(row)
    return pd.DataFrame(rows)