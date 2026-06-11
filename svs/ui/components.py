"""
Reusable Streamlit UI components.

All HTML rendering is contained here so app.py stays focused on data flow.
"""

from __future__ import annotations
from pathlib import Path

import streamlit as st


_CSS_PATH = Path(__file__).parent / "styles.css"


def load_css() -> None:
    """Inject the external stylesheet into the Streamlit page."""
    try:
        css = _CSS_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        st.warning("styles.css not found — UI may look unstyled.")
        return
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def banner(kind: str, html: str) -> None:
    """
    Render an alert/info/success/error banner.

    Args:
        kind : One of 'alert', 'info', 'success', 'error'.
        html : Inner HTML content (may contain <b>, <code>, etc.).
    """
    st.markdown(f'<div class="{kind}-banner">{html}</div>', unsafe_allow_html=True)


def stat_card(value, label: str, warn: bool = False) -> str:
    """Return HTML string for a single stat card (not rendered — call stat_row)."""
    cls = "stat-card warn" if warn else "stat-card"
    return (
        f'<div class="{cls}">'
        f'<div class="stat-value">{value}</div>'
        f'<div class="stat-label">{label}</div>'
        f'</div>'
    )


def stat_row(*cards: str) -> None:
    """Render a horizontal row of stat cards produced by stat_card()."""
    st.markdown(
        f'<div class="stats-row">{"".join(cards)}</div>',
        unsafe_allow_html=True,
    )


def render_stepper(steps: list[tuple[str, str]]) -> None:
    """
    Render the workflow progress stepper.

    Args:
        steps: List of (label, state) tuples.
               state is one of: 'done', 'active', 'idle'.
    """
    parts = []
    for i, (label, state) in enumerate(steps):
        parts.append(
            f'<div class="step {state}">'
            f'<div class="step-circle">{i + 1}</div>'
            f'<span class="step-label">{label}</span>'
            f'</div>'
        )
    st.markdown(
        f'<div class="stepper">{"".join(parts)}</div>',
        unsafe_allow_html=True,
    )
