"""
cli.py -- entry point. Pipeline:

    detect format  ->  ingest to DataFrame  ->  TRIAGE (gate)  ->  correct  ->  report

Triage runs before correction every time. If the engine isn't running well
enough to tune (no-crank / cranking / stall / hunting idle), we stop there and
print the targeted "fix this first" recommendations instead of a bogus VE table.

    python -m tuneassist.cli LOG.csv [--platform auto|gm|holley] [--out-dir DIR]
"""
from __future__ import annotations
import argparse, os, sys

import pandas as pd

from .engine_gm import (Config, load_log, resolve_columns, normalize_map_to_kpa,
                        analyze, write_report)
from .triage import triage, TriageResult
from . import holley


def detect_platform(path: str) -> str:
    head = ""
    try:
        head = open(path, encoding="latin-1").read(4000).lower()
    except Exception:
        pass
    if "hp tuners" in head or "vcm" in head:
        return "gm"
    if "holley" in head or "terminator" in head or "sniper" in head or "tslcd" in head:
        return "holley"
    # fall back on channel names
    if "target afr" in head or "cl comp" in head:
        return "holley"
    return "gm"


def run(path: str, platform: str = "auto", out_dir: str = "./out") -> None:
    if platform == "auto":
        platform = detect_platform(path)
    cfg = Config()

    # --- ingest ---
    if platform == "holley":
        df = holley.load_holley_csv(path)
        col = holley.resolve_holley(df)
    else:
        df, units = load_log(path)
        for c in df.columns:
            conv = pd.to_numeric(df[c], errors="coerce")
            if conv.notna().mean() > 0.5:
                df[c] = conv
        col = resolve_columns(df)
        normalize_map_to_kpa(df, col, units, [])

    # --- triage gate (same for both platforms) ---
    tcol = {k: col[k] for k in ("rpm", "time", "tps", "map") if k in col}
    tr = triage(df, tcol)
    print("=" * 70)
    print(f"PLATFORM: {platform.upper()}   |   TRIAGE: {tr.state}")
    print("=" * 70)
    print(tr.detail, "\n")
    for rec in tr.recommendations:
        print("  - " + rec)
    print()
    if not tr.can_correct:
        print("Stopping before correction: get the engine to a tunable state first.")
        return

    # --- correct ---
    os.makedirs(out_dir, exist_ok=True)
    from .core import grid_tsv

    def _write_tsv(grid, name):
        tsv = grid_tsv(grid, "percent")
        if tsv:
            p = os.path.join(out_dir, name)
            open(p, "w", encoding="ascii").write(tsv)
            print(f"Wrote paste-ready TSV to {p}  "
                  "(VCM Editor / Holley: Paste Special > Multiply by Percentage)")

    if platform == "holley":
        corr, counts, disagree, notes = holley.analyze_holley(df, cfg)
        for n in notes:
            print(n)
        if not corr.empty:
            corr.to_csv(os.path.join(out_dir, "holley_base_fuel_correction.csv"))
            print(f"\nWrote base-fuel correction grid to {out_dir}/holley_base_fuel_correction.csv")
            _write_tsv(corr, "holley_base_fuel_correction.tsv")
    else:
        res = analyze(df, cfg)
        report = write_report(res, out_dir)
        print(report)
        _write_tsv(getattr(res, "correction", None), "ve_correction.tsv")


def _print_update_notice():
    """One-line, throttled, fail-silent nudge for the text CLI flows."""
    try:
        from . import update
        update.cleanup_old_binary()
        info = update.passive_check()
        if info:
            how = ("run 'tuneassist --update'" if update.is_frozen()
                   else "pipx upgrade tuneassist")
            print(f"[tuneassist] a newer version is available: "
                  f"v{info.current} -> v{info.latest}  ({how})\n")
    except Exception:
        pass


def main(argv=None):
    from . import __version__
    p = argparse.ArgumentParser(description="AI-assisted tuning analyzer (GM/HPTuners + Holley).")
    p.add_argument("--version", action="version", version=f"tuneassist {__version__}")
    p.add_argument("log", nargs="?", help="log CSV; omit to start the guided session")
    p.add_argument("--platform", choices=["auto", "gm", "holley"], default="auto")
    p.add_argument("--out-dir", default="./out")
    p.add_argument("--batch", action="store_true",
                   help="non-interactive: print the plain report and exit")
    p.add_argument("--tui", action="store_true",
                   help="launch the full Textual UI (this is the default with no args)")
    p.add_argument("--demo", action="store_true",
                   help="launch the locked-down demo (bundled sample logs only)")
    p.add_argument("--json", action="store_true",
                   help="headless: print the structured analysis as JSON and exit")
    p.add_argument("--airflow", choices=["ve_sd", "maf", "no_maf"], default="ve_sd",
                   help="airflow strategy for --json runs (default ve_sd)")
    p.add_argument("--spark", action="store_true", help="include spark analysis in --json")
    p.add_argument("--check-update", action="store_true",
                   help="check GitHub for a newer release and exit")
    p.add_argument("--update", action="store_true",
                   help="download and install the latest release (packaged binary)")
    p.add_argument("--install-shortcut", action="store_true",
                   help="create a double-clickable desktop launcher for the TUI")
    args = p.parse_args(argv)

    if getattr(args, "install_shortcut", False):
        from . import shortcut
        ok, msg = shortcut.install_shortcut()
        print(msg)
        sys.exit(0 if ok else 1)

    if args.check_update or args.update:
        from . import update, __version__ as _v
        if args.update:
            ok, msg = update.self_update()
            print(msg)
            sys.exit(0 if ok else 1)
        info = update.check_for_update()
        if info is None:
            print(f"tuneassist v{_v} is the latest (or the check couldn't reach GitHub).")
        else:
            print(f"Update available: v{info.current} -> v{info.latest}\n  {info.page_url}")
            print("  Install it with: tuneassist --update"
                  if update.is_frozen() else "  Upgrade with: pipx upgrade tuneassist")
        return

    if args.tui:
        from .tui import run_tui
        run_tui()
        return

    if getattr(args, "demo", False):
        from .demo import run_demo
        run_demo()
        return

    if args.json and args.log:
        import json
        from .core import SessionOpts, analyze_log
        from .engine_gm import Config
        plat = None if args.platform == "auto" else args.platform
        opts = SessionOpts(cfg=Config(), airflow_mode=args.airflow, tune_spark=args.spark)
        cr = analyze_log(args.log, opts, platform=plat, out_dir=None)
        print(json.dumps(cr.to_dict(), indent=2))
        return

    # A bare log path -> the quick non-interactive report (also covers SSH / dumb
    # terminals where the TUI can't draw). --json above is the headless contract.
    if args.log:
        _print_update_notice()
        run(args.log, args.platform, args.out_dir)
        return
    if args.batch:
        print("--batch needs a log file, e.g.  tuneassist your_log.csv --batch")
        return

    # Default (e.g. double-clicking the downloaded binary): the full Textual UI.
    from .tui import run_tui
    run_tui()


if __name__ == "__main__":
    main()
