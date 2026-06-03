"""
render.py -- the classic wizard's print layer.

All visual construction now lives in panels.py (pure renderable builders, shared
with the Textual UI). render.py is the thin Rich-Console adapter: it prints those
renderables, plus a little extra spacing the scrolling wizard wants. Keeping this
as a wrapper means the wizard's output is unchanged while the Textual app reuses
the very same panels.
"""

from __future__ import annotations

from rich.console import Console

from . import panels
# Re-export so existing imports (and tests) keep working.
from .panels import (TRIAGE_COLOR, REC_COLOR, SPARK_COLOR, _interval_label,  # noqa: F401
                     _pct_style)

console = Console()


def _print(renderable, blank_after=False):
    if renderable is None:
        return
    console.print(renderable)
    if blank_after:
        console.print()


def banner():
    _print(panels.build_banner())


def journey_bar(current_stage: str):
    _print(panels.build_journey_bar(current_stage))


def triage_panel(tr, platform: str):
    _print(panels.build_triage(tr, platform))


def correction_heatmap(correction, counts=None,
                       title="VE / FUEL CORRECTION  (% change to apply)"):
    _print(panels.build_correction_heatmap(correction, counts, title), blank_after=True)


def recommendation_grid(rec):
    _print(panels.build_recommendation_grid(rec), blank_after=True)


def largest_changes(correction, n: int = 10):
    _print(panels.build_largest_changes(correction, n))


def spark_grid(spark):
    _print(panels.build_spark(spark), blank_after=True)


def maf_table(corr, counts):
    _print(panels.build_maf(corr, counts), blank_after=True)


def safety_panel(events):
    _print(panels.build_safety(events))


def diagnostics_panel(findings):
    _print(panels.build_diagnostics(findings))


def prescription_panel(rx):
    _print(panels.build_prescription(rx))


def convergence_panel(history):
    _print(panels.build_convergence(history), blank_after=True)
