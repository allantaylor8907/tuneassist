"""
wizard.py -- the interactive, guided tuning session.

This is the conversation: it greets the user, asks for a log, asks the few
clarifying questions that actually change the advice (fuel/stoich, airflow
strategy, spark, optional cam + engine profile) ONCE, remembers them for the
rest of the session, runs triage, renders the analysis beautifully (render.py),
then -- the whole point -- tells the user the single next thing to change, the
drive to go do, and what to log, then loops to ingest the *next* log so the tune
walks forward across passes. Engine hardware can't be read from a log so it's
asked; the fuel default is gleaned from commanded AFR; airflow MODE can't be
gleaned (the MAF sensor reads air whether or not the tune uses it) so it stays a
quick per-pass toggle on the remembered setup.

IO is funnelled through WizardIO so the flow is scriptable in tests (the live
path uses rich prompts; a test can feed canned answers).
"""

from __future__ import annotations
import os
import re
from dataclasses import dataclass

import pandas as pd

from rich.prompt import Prompt, Confirm

from .engine_gm import Config
from .core import (SessionOpts, analyze_log, detect_platform, resolve_for,
                   ingest as _ingest, opts_to_record as _opts_to_record,
                   record_to_opts as _record_to_opts)
from . import render
from .render import console
from . import stages
from . import cams
from . import garage
from .profile import EngineProfile


FUELS = {
    "1": ("Pump gas (91-93)", 14.7),
    "2": ("E10 / 87-89", 14.08),
    "3": ("E85", 9.76),
    "4": ("Race / other", 14.7),
}

# Airflow strategy (GM only). Maps to stages.determine_stage airflow_mode.
AIRFLOW = {
    "1": ("MAF disabled now -- tuning VE (speed-density)", "ve_sd"),
    "2": ("MAF enabled / normal blended", "maf"),
    "3": ("No MAF at all (pure speed-density build)", "no_maf"),
    "4": ("Tuning the MAF curve now (VE already done)", "maf"),
}


@dataclass
class WizardIO:
    """Thin IO seam. Live mode uses rich; tests inject `scripted` answers."""
    scripted: list | None = None
    _i: int = 0

    def _next(self, default=None):
        if self.scripted is None:
            return None
        val = self.scripted[self._i] if self._i < len(self.scripted) else default
        self._i += 1
        return val

    def ask(self, prompt, default=None, choices=None):
        if self.scripted is not None:
            v = self._next(default)
            return default if v is None else v
        return Prompt.ask(prompt, default=default, choices=choices)

    def confirm(self, prompt, default=True):
        if self.scripted is not None:
            v = self._next(default)
            return bool(default if v is None else v)
        return Confirm.ask(prompt, default=default)

    def ask_float(self, prompt):
        """Optional numeric prompt: blank/skip -> None."""
        v = self.ask(prompt + " [grey50](blank to skip)[/]", default="")
        try:
            return float(str(v).strip()) if str(v).strip() else None
        except ValueError:
            return None


def _prompt_for_log(io: WizardIO, initial: str | None) -> str | None:
    path = initial
    while True:
        if not path:
            path = io.ask("[bold]Path to your log CSV[/] (or 'q' to quit)")
        if path is None or str(path).strip().lower() in ("q", "quit", "exit"):
            return None
        path = os.path.expanduser(str(path).strip().strip('"'))
        if os.path.isfile(path):
            return path
        console.print(f"[red]Can't find:[/] {path}")
        if path.lower().endswith((".hpl", ".dl")):
            console.print("[yellow]That's a native binary log. Export it to CSV from the "
                          "vendor software first (binaries don't carry channel names).[/]")
        path = None
        if io.scripted is not None:   # don't loop forever in tests
            return None


def _detect_fuel_default(df, col) -> str:
    """Glean the fuel from commanded AFR at cruise (it targets the fuel's stoich).
    Returns a FUELS key; falls back to pump gas."""
    if "afr_cmd" not in col:
        return "1"
    cmd = pd.to_numeric(df[col["afr_cmd"]], errors="coerce")
    cl = cmd[(cmd >= 9) & (cmd <= 15.5)]   # plausible stoich targets
    if len(cl) < 20:
        return "1"
    m = float(cl.median())
    cand = {"1": 14.7, "2": 14.08, "3": 9.76}
    return min(cand, key=lambda k: abs(cand[k] - m))


def _clarify(io: WizardIO, df, platform: str) -> SessionOpts:
    cfg = Config()
    console.print("\n[bold]A few quick questions so the advice fits your setup[/] "
                  "[grey50](asked once -- I'll remember them for the next logs)[/]:")

    # --- fuel / stoich (pre-selected from the log when possible) ---
    fuel_default = _detect_fuel_default(df, resolve_for(df, platform))
    menu = "  ".join(f"[bold]{k}[/]={v[0]}" for k, v in FUELS.items())
    console.print(f"  Fuel:  {menu}")
    if fuel_default != "1":
        console.print(f"  [grey50](log's commanded AFR looks like {FUELS[fuel_default][0]})[/]")
    choice = io.ask("  Fuel type", default=fuel_default, choices=list(FUELS))
    label, stoich = FUELS.get(str(choice), FUELS["1"])
    cfg.stoich = stoich
    console.print(f"  -> {label} (stoich {stoich})")

    col = resolve_for(df, platform)
    if "afr_actual" in col:
        console.print("  [green]Wideband detected[/] -- "
                      + ("it's the control sensor on Holley." if platform == "holley"
                         else "used for WOT and as a cross-check at cruise."))
    else:
        console.print("  [yellow]No wideband detected[/] -- "
                      "closed-loop trims only; WOT cells need one.")

    opts = SessionOpts(cfg=cfg)

    # --- airflow strategy (GM only; Holley self-learns off its wideband) ---
    if platform == "holley":
        opts.airflow_mode = "no_maf"
    else:
        has_maf = ("maf_air" in col or "maf_freq" in col)
        default = "2" if has_maf else "3"
        amenu = "  ".join(f"[bold]{k}[/]={v[0]}" for k, v in AIRFLOW.items())
        console.print(f"  Airflow strategy:\n    {amenu}")
        ac = io.ask("  Airflow", default=default, choices=list(AIRFLOW))
        alabel, amode = AIRFLOW.get(str(ac), AIRFLOW[default])
        opts.airflow_mode = amode
        console.print(f"  -> {alabel}")

    # --- spark / power ---
    if io.confirm("  Tune spark/timing too? (needs a knock channel)", default=False):
        opts.tune_spark = True
        opts.find_power = io.confirm("    Probe for MORE power (add timing cautiously)?",
                                     default=False)
        # engine profile sharpens the spark ceiling + pull-back advice (all optional)
        if io.confirm("    Add engine details for tailored spark advice? (optional)",
                      default=False):
            bl = str(io.ask("      Block: [bold]i[/]ron / [bold]a[/]luminum",
                            default="")).strip().lower()
            block = "iron" if bl.startswith("i") else "alum" if bl.startswith("a") else None
            cr = io.ask_float("      Static compression ratio (e.g. 10.5)")
            disp = io.ask_float("      Displacement (liters)")
            adder = str(io.ask("      Power adder: [bold]na[/] / boost / nitrous",
                               default="na")).strip().lower() or "na"
            if adder not in ("na", "boost", "nitrous"):
                adder = "na"
            opts.profile = EngineProfile(block=block, compression=cr,
                                         displacement=disp, power_adder=adder)
            console.print(f"  -> engine: {block or '?'} block, "
                          f"{cr or '?'}:1, {adder}")

    # --- optional cam specs -> starting points ---
    if io.confirm("  Enter cam specs for idle/timing starting points? (optional)",
                  default=False):
        cam = cams.CamSpec(
            intake_dur_050=io.ask_float("    Intake duration @ .050"),
            exhaust_dur_050=io.ask_float("    Exhaust duration @ .050"),
            lsa=io.ask_float("    LSA (lobe separation angle)"),
            lift=io.ask_float("    Max lift (in)"))
        opts.cam_spec = cam
        opts.cam_points = cams.starting_points(cam)
        console.print(f"  -> cam classified [bold]{opts.cam_points.klass}[/]")

    return opts


def _ask_platform(io: WizardIO, path: str) -> str:
    platform = detect_platform(path)
    ovr = io.ask(f"\nDetected platform [bold]{platform.upper()}[/]. "
                 "Enter to accept, or type gm/holley", default=platform,
                 choices=["gm", "holley", platform])
    platform = (ovr or platform).lower()
    return platform if platform in ("gm", "holley") else detect_platform(path)


def _setup_summary(platform: str, opts: SessionOpts) -> str:
    """One-line recap of the remembered session setup."""
    amode = {"ve_sd": "VE/SD (MAF off)", "maf": "MAF curve", "no_maf": "no-MAF SD"}
    bits = [platform.upper(),
            f"{opts.cfg.stoich:g} stoich",
            amode.get(opts.airflow_mode, opts.airflow_mode)]
    if opts.tune_spark:
        bits.append("spark on" + (" + find-power" if opts.find_power else ""))
    if opts.cam_points:
        bits.append(f"{opts.cam_points.klass} cam")
    if opts.profile:
        p = opts.profile
        bits.append(f"{p.block or '?'} {p.compression or '?'}:1 {p.power_adder}")
    return "[grey70]Saved setup:[/] " + "  |  ".join(bits)


def _run_one(io: WizardIO, path: str, platform: str, opts: SessionOpts,
             out_dir: str, history: list, pass_no: int) -> str:
    """Analyze a single log via the headless core, render it, return the stage."""
    cr = analyze_log(path, opts, platform=platform, out_dir=out_dir)
    for n in cr.notes:
        console.print(f"  [yellow]{n}[/]")

    render.journey_bar(cr.stage)
    render.triage_panel(cr.triage, cr.platform)
    render.diagnostics_panel(cr.findings)

    res = cr.result
    if res is not None and not cr.has_grid:
        for n in getattr(res, "notes", []):
            if n.startswith("RESULT") or "WARNING" in n:
                console.print(f"  [yellow]{n}[/]")

    if cr.has_grid:
        s = cr.summary
        render.correction_heatmap(res.correction, res.samples)
        render.recommendation_grid(getattr(res, "recommendation", None))
        render.largest_changes(res.correction)
        console.print(f"  [grey70]Coverage:[/] {s.coverage_pct:.0f}%  "
                      f"[grey70]median[/] {s.median_pct:+.1f}%  "
                      f"[grey70]worst cell[/] {s.max_abs_pct:.1f}%\n")
        render.safety_panel(getattr(res, "safety", []))
        history.append((f"pass {pass_no}", s.median_pct, s.max_abs_pct))
        render.convergence_panel(history)

    if cr.maf[0] is not None:
        render.maf_table(cr.maf[0], cr.maf[1])
    if opts.tune_spark:
        render.spark_grid(cr.spark)

    render.prescription_panel(cr.prescription)
    return cr.stage


def _new_vehicle(io: WizardIO):
    """Prompt to create a new (optionally saved) vehicle."""
    name = str(io.ask("\n[bold]Name this vehicle[/] to save its tune progress "
                      "[grey50](blank = don't save)[/]", default="")).strip()
    if not name:
        return None, None, None, []
    nickname = str(io.ask("  Nickname [grey50](optional, for easy ID -- e.g. "
                          "\"Goldie\")[/]", default="")).strip() or None
    if nickname:
        console.print(f"  -> saving as [bold]\"{nickname}\"[/] [grey70]({name})[/]")
    return name, nickname, None, []


def _select_vehicle(io: WizardIO, data: dict, garage_path: str | None = None):
    """Pick, create, rename, or delete a vehicle. Returns
    (name, nickname, saved_or_None, history). saved is {'platform','opts'} when
    an existing vehicle is loaded. Pick by #, name, or nickname; manage with
    'r <#>' (rename) and 'd <#>' (delete)."""
    while True:
        names = garage.list_vehicles(data)
        if not names:
            return _new_vehicle(io)

        console.print("\n[bold]Your garage:[/]")
        for i, n in enumerate(names, 1):
            rec = garage.get(data, n) or {}
            nick = rec.get("nickname")
            label = f"[bold]\"{nick}\"[/] [grey70]({n})[/]" if nick else f"[bold]{n}[/]"
            console.print(f"  [bold]{i}[/]) {label}  [grey50]({rec.get('platform','?').upper()}, "
                          f"last stage: {rec.get('stage','?')})[/]")
        pick = str(io.ask("  Select # / name / nickname  |  [bold]n[/]=new  "
                          "[bold]r[/] #=rename  [bold]d[/] #=delete", default="1")).strip()

        # --- manage commands: 'r <target>' / 'd <target>' (space required) ---
        m = re.match(r"^([rd])\s+(\S.*)$", pick, re.I)
        if m:
            target = _resolve_name(data, names, m.group(2))
            if not target:
                console.print(f"  [yellow]No vehicle matches '{m.group(2)}'.[/]")
                continue
            if m.group(1).lower() == "d":
                if io.confirm(f"  Delete [bold]{_display(data, target)}[/] permanently?",
                              default=False):
                    garage.delete(data, target)
                    _safe_save(data, garage_path)
                    console.print(f"  [grey50]Deleted '{target}'.[/]")
            else:  # rename / set nickname
                new = str(io.ask(f"  New nickname for [bold]{target}[/] "
                                 "[grey50](blank to clear)[/]", default="")).strip() or None
                garage.get(data, target)["nickname"] = new
                _safe_save(data, garage_path)
                console.print(f"  [grey50]Updated nickname for '{target}'.[/]")
            continue

        # --- selection ---
        if pick.lower() in ("n", "new", ""):
            return _new_vehicle(io)
        chosen = _resolve_name(data, names, pick)
        if chosen is None:                    # typo / no match -> re-show, don't guess
            console.print(f"  [yellow]No vehicle matches '{pick}'. Try again.[/]")
            continue
        platform, opts = _record_to_opts(garage.get(data, chosen))
        rec = garage.get(data, chosen)
        console.print(f"  [green]Loaded[/] [bold]{_display(data, chosen)}[/] -- "
                      f"{_setup_summary(platform, opts)}")
        return chosen, rec.get("nickname"), {"platform": platform, "opts": opts}, \
            list(rec.get("history", []))


def _display(data: dict, name: str) -> str:
    nick = (garage.get(data, name) or {}).get("nickname")
    return f'"{nick}" ({name})' if nick else name


def _resolve_name(data: dict, names: list, token: str) -> str | None:
    """Resolve a number / name / nickname to a vehicle name, or None if it's a
    new-vehicle request or doesn't match anything."""
    t = token.strip()
    if t.lower() in ("n", "new", ""):
        return None
    if t.isdigit():
        i = int(t) - 1
        return names[i] if 0 <= i < len(names) else None
    low = t.lower()
    for n in names:                       # exact name match
        if n.lower() == low:
            return n
    for n in names:                       # nickname match
        nick = (garage.get(data, n) or {}).get("nickname")
        if nick and nick.lower() == low:
            return n
    return None


def _safe_save(data: dict, garage_path: str | None) -> None:
    try:
        garage.save(data, garage_path)
    except OSError as e:
        console.print(f"  [yellow]Couldn't save garage: {e}[/]")


def run_session(initial_log: str | None = None, out_dir: str = "./out",
                io: WizardIO | None = None, garage_path: str | None = None) -> None:
    io = io or WizardIO()
    render.banner()
    console.print("[grey70]I'll read a datalog, check the engine's in a tunable "
                  "state, recommend changes you apply in your tuning software, then "
                  "tell you exactly what to log next. I never touch the tune or the "
                  "ECU.[/]\n")

    # --- the garage: per-vehicle memory across launches ---
    data = garage.load(garage_path)
    vehicle, nickname, saved, history = _select_vehicle(io, data, garage_path)
    pass_no = len(history) + 1
    next_log = initial_log
    while True:
        path = _prompt_for_log(io, next_log)
        next_log = None
        if path is None:
            console.print("\n[grey70]No log to analyze. See you on the next pass.[/]")
            return

        if saved is None:
            # brand-new vehicle, first log: detect platform + ask the setup once
            platform = _ask_platform(io, path)
            df, _ = _ingest(path, platform, Config())
            opts = _clarify(io, df, platform)
        else:
            # remembered (this session or loaded from disk): reuse unless edited
            console.print("\n" + _setup_summary(saved["platform"], saved["opts"]))
            if io.confirm("Keep this setup? ([bold]n[/] to change, e.g. you've "
                          "switched to the MAF pass)", default=True):
                platform, opts = saved["platform"], saved["opts"]
            else:
                platform = _ask_platform(io, path)
                df, _ = _ingest(path, platform, Config())
                opts = _clarify(io, df, platform)
        saved = {"platform": platform, "opts": opts}

        stage = _run_one(io, path, platform, opts, out_dir, history, pass_no)
        pass_no += 1

        # --- persist this vehicle's progress to the garage ---
        if vehicle:
            import datetime
            record = _opts_to_record(platform, opts)
            record["nickname"] = nickname
            record["history"] = history
            record["stage"] = stage
            record["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
            garage.upsert(data, vehicle, record)
            try:
                garage.save(data, garage_path)
                console.print(f"  [grey50]Saved progress for "
                              f"'{nickname or vehicle}' to the garage.[/]")
            except OSError as e:
                console.print(f"  [yellow]Couldn't save garage: {e}[/]")

        if stage == "CONVERGED":
            if not io.confirm("\nLooks converged. Analyze another log anyway?", default=False):
                console.print("\n[green]Done. Save this tune as your baseline.[/]")
                return
        else:
            if not io.confirm("\nGo do the drive above, then come back. "
                              "Analyze the new log now?", default=True):
                console.print("\n[grey70]Paused. Re-run me with the new log when ready.[/]")
                return
        next_log = io.ask("Path to the new log CSV", default=None)
