"""
panels.py -- pure Rich *renderable builders* (no printing, no IO).

Every function returns a Rich renderable (Panel / Table / Group). Two consumers
share them so the Rich wizard and the Textual app look identical:
  * render.py prints them to a Console (the classic wizard).
  * tui.py mounts them inside Textual `Static` widgets.

Color language is consistent everywhere:
  * a VE/fuel correction is a multiplier. >1 = "add fuel / raise VE"; <1 = "pull".
    Warm = add, cool = pull, dim green = leave it.
  * triage/stage states have fixed accent colors so the user learns them.
"""

from __future__ import annotations

import pandas as pd

from rich.console import Group
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich import box

from tuneassist.stages import STAGES, STAGE_ORDER


TRIAGE_COLOR = {
    "NO_DATA": "grey50", "NO_CRANK": "red", "CRANKING_NO_START": "red",
    "STARTED_STALLED": "dark_orange", "UNSTABLE_IDLE": "yellow",
    "IDLE_ONLY": "cyan", "RUNNING_DRIVE": "green",
}
REC_COLOR = {
    "VE/MAF": "bright_cyan", "O2/STOICH": "yellow", "WOT": "magenta",
    "OK": "green", None: "grey30",
}
SPARK_COLOR = {"PULL": "bold red", "LEAN": "bold magenta", "HOT": "dark_orange",
               "ADD": "bright_green", "OK": "grey50"}


def _interval_label(iv) -> str:
    """'(1600, 2000]' -> '1600-2000'. Falls back to str()."""
    try:
        return f"{int(iv.left)}-{int(iv.right)}"
    except (AttributeError, ValueError, TypeError):
        return str(iv)


def _pct_style(change_pct: float) -> str:
    """Warm = add fuel (positive), cool = pull fuel (negative), green = tiny."""
    a = abs(change_pct)
    if a < 1.0:
        return "green"
    if change_pct > 0:
        return "bright_yellow" if a < 3 else ("dark_orange" if a < 6 else "bold red")
    return "bright_cyan" if a < 3 else ("blue" if a < 6 else "bold blue")


# ASCII wordmark (figlet "small", ASCII-only so it can't crash legacy consoles).
_LOGO = [
    ' _____ _   _ _  _ ___     _   ___ ___ ___ ___ _____',
    '|_   _| | | | \\| | __|   /_\\ / __/ __|_ _/ __|_   _|',
    '  | | | |_| | .` | _|   / _ \\\\__ \\__ \\| |\\__ \\ | |',
    '  |_|  \\___/|_|\\_|___| /_/ \\_\\___/___/___|___/ |_|',
]
# A piston/tach motif to the right of the wordmark would misalign on narrow
# terminals, so we keep the logo a clean centered wordmark.


def build_banner():
    art = Text()
    for i, line in enumerate(_LOGO):
        # subtle vertical gradient blue -> cyan
        shade = ["#5e81ac", "#81a1c1", "#88c0d0", "#8fbcbb"][i % 4]
        art.append(line + "\n", style=f"bold {shade}")
    sub = Text("recommendation-only tuning analysis   "
               "it advises, you apply.", style="italic grey66")
    return Panel(Align.center(Group(Align.center(art), Align.center(sub))),
                 box=box.HEAVY, border_style="#81a1c1", padding=(1, 2))


def build_journey_bar(current_stage: str):
    cur = STAGE_ORDER.get(current_stage, 0)
    line = Text(" ")
    for n, (key, title) in enumerate(STAGES, 1):
        i = STAGE_ORDER[key]
        seg = f" {n}.{title} "
        if i < cur:
            line.append(seg, style="green")
            mark = ("green", ">")
        elif i == cur:
            line.append(seg, style="bold white on blue")
            mark = ("blue", ">")
        else:
            line.append(seg, style="grey42")
            mark = ("grey42", ">")
        if key != STAGES[-1][0]:
            line.append(mark[1], style=mark[0])

    # Spell out "you are here -> what's next" so the ORDER is unmistakable on the
    # very first upload. The canonical airflow order is VE (MAF off) THEN the MAF
    # curve -- the #1 thing people ask. State it plainly.
    cap = Text("  ", style="grey50")
    cur_title = STAGES[cur][1]
    cap.append(f"you are here: {cur + 1}. {cur_title}", style="bold grey70")
    if cur < len(STAGES) - 1:
        cap.append("   ->   next: ", style="grey50")
        cap.append(f"{cur + 2}. {STAGES[cur + 1][1]}", style="bold cyan")
    body = Group(line, Text(""), cap)
    return Panel(body, box=box.ROUNDED, border_style="grey42",
                 title="[grey70]tuning journey[/]  [grey42](do these in order)[/]",
                 subtitle="[grey42]fuel order: VE with MAF OFF first, THEN the MAF "
                          "curve -- once VE is right, SD airmass is the MAF's reference[/]",
                 title_align="left", subtitle_align="left")


def build_triage(tr, platform: str, make: str | None = None):
    from tuneassist.core import platform_label
    color = TRIAGE_COLOR.get(tr.state, "white")
    head = Text()
    label = platform_label(platform) + (f" - {make.upper()}" if make else "")
    head.append(label, style="bold grey70")
    head.append("   state: ")
    head.append(tr.state, style=f"bold {color}")
    head.append("   " + ("OK to correct" if tr.can_correct else "GATED"),
                style="green" if tr.can_correct else "red")
    body = [head, Text("")]
    body.append(Text(tr.detail, style="white"))
    body.append(Text(""))
    for rec in tr.recommendations:
        body.append(Text.from_markup(f"  [grey50]-[/] {rec}"))
    return Panel(Group(*body), box=box.ROUNDED, border_style=color,
                 title="[bold]TRIAGE[/]", title_align="left")


def _grid_table(grid, title, cell_fn):
    """Shared RPM x MAP table builder. cell_fn(value) -> Text or None (-> '-')."""
    t = Table(title=title, box=box.SIMPLE_HEAVY, title_style="bold",
              header_style="bold grey70", pad_edge=False)
    t.add_column("RPM \\ MAP", style="bold grey70", justify="right")
    for c in grid.columns:
        t.add_column(_interval_label(c), justify="center")
    for r in grid.index:
        row = [Text(_interval_label(r), style="bold grey70")]
        for c in grid.columns:
            cell = cell_fn(grid.loc[r, c])
            row.append(cell if cell is not None else Text("-", style="grey30"))
        t.add_row(*row)
    return t


def build_correction_heatmap(correction, counts=None,
                             title="VE / FUEL CORRECTION  (% change to apply)"):
    if correction is None or correction.empty:
        return Panel("[grey50]No confident cells to show.[/]",
                     border_style="grey42", title=title, title_align="left")

    def cell(v):
        if pd.isna(v):
            return None
        change = (v - 1.0) * 100.0
        sign = "+" if change >= 0 else ""
        return Text(f"{sign}{change:.1f}", style=_pct_style(change))

    howto = Text.from_markup(
        "  [b]These are the changes to apply.[/] Each cell is the % to change that "
        "RPM x MAP cell -- [b]multiply-by-percent[/] (a +5 cell = multiply it by 1.05).")
    legend = Text.from_markup(
        "  [bright_yellow]warm[/]=add fuel/raise VE  [bright_cyan]cool[/]=pull fuel  "
        "[green]green[/]=<1%, leave  [grey30]-[/]=too few samples, leave it")
    return Group(_grid_table(correction, title, cell), howto, legend)


def build_recommendation_grid(rec):
    if rec is None or rec.empty or rec.stack().dropna().empty:
        return None
    short = {"VE/MAF": "VE", "O2/STOICH": "O2", "WOT": "WOT", "OK": "ok"}

    def cell(v):
        if pd.isna(v):
            return None
        label = str(v)
        return Text(short.get(label, "-"), style=REC_COLOR.get(label, "grey30"))

    legend = Text.from_markup(
        "  [bright_cyan]VE[/]=real airflow error, apply it   "
        "[yellow]O2[/]=sensor/stoich suspect, fix first   "
        "[magenta]WOT[/]=open-loop, wideband-driven   [green]ok[/]=leave")
    return Group(_grid_table(rec, "CROSS-CHECK: which knob does each cell want?", cell),
                 legend)


def build_largest_changes(correction, n: int = 10):
    if correction is None or correction.empty:
        return None
    pct = (correction - 1.0) * 100.0
    big = pct.stack().reindex(pct.abs().stack().sort_values(ascending=False).index)
    t = Table(title=f"Top {n} changes", box=box.MINIMAL, header_style="bold grey70")
    t.add_column("RPM", justify="right"); t.add_column("MAP", justify="right")
    t.add_column("change", justify="right")
    for (r, m), v in list(big.items())[:n]:
        sign = "+" if v >= 0 else ""
        t.add_row(_interval_label(r), _interval_label(m),
                  Text(f"{sign}{v:.1f}%", style=_pct_style(v)))
    return t


def build_spark(spark):
    if spark is None or not getattr(spark, "can_run", False):
        msg = getattr(spark, "reason", "") if spark else "No spark data."
        return Panel(f"[yellow]{msg}[/]", box=box.ROUNDED, border_style="yellow",
                     title="SPARK", title_align="left")
    change, action = spark.change, spark.action

    def cell_for(r, c):
        v = change.loc[r, c]
        act = action.loc[r, c]
        if pd.isna(v) or pd.isna(act):
            return Text("-", style="grey30")
        if act == "OK":
            return Text("ok", style="grey50")
        sign = "+" if v > 0 else ""
        return Text(f"{sign}{v:g}", style=SPARK_COLOR.get(act, "white"))

    t = Table(title="SPARK TIMING CHANGE  (degrees: - pull / + add)",
              box=box.SIMPLE_HEAVY, header_style="bold grey70", title_style="bold")
    t.add_column("RPM \\ MAP", style="bold grey70", justify="right")
    for c in change.columns:
        t.add_column(_interval_label(c), justify="center")
    for r in change.index:
        t.add_row(Text(_interval_label(r), style="bold grey70"),
                  *[cell_for(r, c) for c in change.columns])

    parts = [t, Text.from_markup(
        "  [bold red]-N[/]=pull (knock)  [magenta]LEAN[/]=fix fuel first  "
        "[dark_orange]HOT[/]=cool IAT first  [bright_green]+N[/]=add to probe MBT  "
        "[grey50]ok[/]=leave"), Text(f"  {spark.advisory}", style="grey70")]
    if getattr(spark, "pullback", None):
        body = [Text.from_markup(f"  [bold red]![/] {c}") for c in spark.pullback]
        parts.append(Panel(Group(*body), box=box.ROUNDED, border_style="dark_orange",
                           title="[bold]PULL TIMING BACK WHEN[/]", title_align="left"))
    return Group(*parts)


def build_maf(corr, counts):
    """MAF correction as a SINGLE ROW with frequency across the columns -- the
    same shape as the HPTuners 'Airflow vs Frequency' table you paste into."""
    if corr is None or corr.dropna().empty:
        return None
    cells = []
    for fb in corr.index:
        v = corr.loc[fb]
        if pd.isna(v):
            continue
        n = int(counts.loc[fb]) if counts is not None and fb in counts.index else 0
        # label by the frequency breakpoint (bucket start), like the editor's axis
        try:
            lab = str(int(fb.left))
        except (AttributeError, ValueError, TypeError):
            lab = _interval_label(fb)
        cells.append((lab, (v - 1.0) * 100.0, n))
    if not cells:
        return None
    t = Table(title="MAF CURVE CORRECTION  (1 row, frequency across -- matches "
              "HPTuners 'Airflow vs Frequency')",
              box=box.SIMPLE_HEAVY, header_style="bold grey70", title_style="bold",
              pad_edge=False)
    t.add_column("Hz", style="bold grey70")
    for lab, _v, _n in cells:
        t.add_column(lab, justify="center")
    pct_row = [Text("% chg", style="bold grey70")]
    for _lab, change, _n in cells:
        sign = "+" if change >= 0 else ""
        pct_row.append(Text(f"{sign}{change:.1f}", style=_pct_style(change)))
    t.add_row(*pct_row)
    t.add_row(Text("n", style="grey50"),
              *[Text(str(n), style="grey50") for _l, _c, n in cells])
    return Group(t, Text("  One row indexed by frequency -- paste into the MAF cal "
                         "(Airflow vs Frequency), multiply-by-percent. Not the VE table.",
                         style="grey70"))


def build_safety(events):
    if not events:
        return Panel("[green]No safety events flagged.[/]", box=box.ROUNDED,
                     border_style="green", title="SAFETY", title_align="left")
    body = [Text.from_markup(
        f"  [bold red]![/] [bold]{e['type']}[/]  "
        f"[grey50]t={e.get('time', 0):.1f}s[/]  {e.get('detail', '')}") for e in events]
    return Panel(Group(*body), box=box.ROUNDED, border_style="red",
                 title="[bold red]SAFETY -- review before applying[/]", title_align="left")


def build_prescription(rx):
    color = "green" if rx.converged else "blue"
    step1 = "DONE -- no changes needed" if rx.converged else "1) CHANGE YOUR TUNE NOW"
    parts = [Text(rx.rationale, style="italic grey78"), Text(""),
             Text(step1 + ":", style="bold white")]
    for a in rx.actions:
        parts.append(Text.from_markup(f"  [bold {color}]>[/] {a}"))
    parts += [Text(""), Text("2) THEN, BEFORE YOUR NEXT LOG:", style="bold white"),
              Text.from_markup(f"  [magenta]~[/] {rx.drive}")]
    parts += [Text(""), Text("   LOG THESE CHANNELS:", style="bold white"),
              Text.from_markup("  [grey70]" + ", ".join(rx.capture) + "[/]")]
    return Panel(Group(*parts), box=box.HEAVY, border_style=color,
                 title=f"[bold {color}]NEXT STEP -- {rx.title}[/]",
                 title_align="left", padding=(1, 2))


SEVERITY_STYLE = {"critical": ("bold red", "[!]"), "warning": ("yellow", "[!]"),
                  "opportunity": ("bright_green", "[+]"), "info": ("grey70", "[i]")}


def build_diagnostics(findings):
    """A ranked, easy-to-scan symptom -> cause -> fix panel from Findings."""
    if not findings:
        return None
    # summary header: counts by severity
    from collections import Counter
    counts = Counter(f.severity for f in findings)
    order = [("critical", "bold red"), ("warning", "yellow"),
             ("opportunity", "bright_green"), ("info", "grey70")]
    summary = Text("  ")
    chips = [(f"{counts[s]} {s}", clr) for s, clr in order if counts.get(s)]
    for i, (txt, clr) in enumerate(chips):
        if i:
            summary.append("   ")
        summary.append(txt, style=clr)

    blocks = [summary, Text("")]
    for f in findings:
        style, mark = SEVERITY_STYLE.get(f.severity, ("white", "[ ]"))
        head = Text()
        head.append(f"{mark} ", style=style)
        head.append(f.title, style=f"bold {style}")
        lines = [head]
        lines.append(Text("    What I see:  ", style="bold grey62") +
                     Text(f.detail, style="grey85"))
        if f.causes:
            lines.append(Text("    Likely:     ", style="bold grey62") +
                         Text("; ".join(f.causes), style="grey74"))
        for j, c in enumerate(f.corrections):
            label = "    Do this:    " if j == 0 else "                "
            lines.append(Text(label, style="bold grey62") +
                         Text.from_markup(f"[{style}]>[/] {c}"))
        blocks.append(Group(*lines))
        blocks.append(Text(""))
    if blocks and isinstance(blocks[-1], Text):
        blocks.pop()
    return Panel(Group(*blocks), box=box.ROUNDED, border_style="cyan",
                 title="[bold]DIAGNOSIS -- what I see & what to change[/]",
                 title_align="left", padding=(1, 1))


def build_report(cr, history=None, show_spark=False):
    """Assemble a full result view (Group) from a core.CoreResult. Shared by the
    Textual UI; mirrors the wizard's section order."""
    parts = [build_triage(cr.triage, cr.platform, getattr(cr, "make", None))]
    diag = build_diagnostics(getattr(cr, "findings", None))
    if diag is not None:
        parts.append(diag)
    res = cr.result
    if res is not None and not cr.has_grid:
        for n in getattr(res, "notes", []):
            if n.startswith("RESULT") or "WARNING" in n:
                parts.append(Text(f"  {n}", style="yellow"))
    if cr.has_grid:
        s = cr.summary
        parts.append(build_correction_heatmap(res.correction, res.samples))
        rec = build_recommendation_grid(getattr(res, "recommendation", None))
        if rec is not None:
            parts.append(rec)
        big = build_largest_changes(res.correction)
        if big is not None:
            parts.append(big)
        parts.append(Text.from_markup(
            f"  [grey70]Coverage:[/] {s.coverage_pct:.0f}%  [grey70]median[/] "
            f"{s.median_pct:+.1f}%  [grey70]worst cell[/] {s.max_abs_pct:.1f}%"))
        parts.append(build_safety(getattr(res, "safety", [])))
        conv = build_convergence(history)
        if conv is not None:
            parts.append(conv)
    if cr.maf[0] is not None:
        m = build_maf(cr.maf[0], cr.maf[1])
        if m is not None:
            parts.append(m)
    if show_spark and cr.spark is not None:
        parts.append(build_spark(cr.spark))
    parts.append(build_prescription(cr.prescription))
    return Group(*[p for p in parts if p is not None])


def build_convergence(history):
    if not history or len(history) < 2:
        return None
    t = Table(title="Convergence across passes", box=box.MINIMAL, header_style="bold grey70")
    t.add_column("pass"); t.add_column("median", justify="right")
    t.add_column("worst cell", justify="right")
    for label, med, mx in history:
        t.add_row(label, f"{med:+.1f}%", f"{mx:.1f}%")
    first_w, last_w = history[0][2], history[-1][2]
    if last_w < first_w:
        note = Text(f"  Trending down: worst cell {first_w:.1f}% -> {last_w:.1f}%. "
                    "The tune is converging.", style="green")
    else:
        note = Text("  Worst cell isn't shrinking -- check the change actually got "
                    "applied, or look for a non-VE cause.", style="yellow")
    return Group(t, note)
