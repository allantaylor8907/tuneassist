# tuneassist

AI-assisted **analysis & recommendation** for engine tuning logs. Reads a datalog,
checks the engine is in a tunable state, and recommends fuel/VE changes you apply
in the vendor software. It never writes a tune file or touches the ECU.

Supports **GM / HPTuners** (Gen 3/4 LS, speed-density) and **Holley EFI**
(Terminator X, Sniper).

## Screenshots

The Textual UI. **The analysis screen** — journey bar, plain-language diagnosis,
the fuel/VE correction, and the "next step" card:

<p align="center"><img src="docs/images/04-report.svg" width="100%" alt="Analysis report"></p>

**Interactive correction grid** (RPM × MAP, sortable cell detail):

<p align="center"><img src="docs/images/05-correction-grid.svg" width="100%" alt="Correction grid"></p>

**Garage** (pick / create / quick-scan) and the **setup form** (engine preset, mods, cam tier):

<p align="center">
  <img src="docs/images/01-garage.svg" width="49%" alt="Garage">
  <img src="docs/images/02-setup.svg" width="49%" alt="Setup">
</p>

> Vector SVGs — sharp at any zoom. Regenerate with `python tools/capture_screens.py`.
> See [docs/images/README.md](docs/images/README.md) for recording an animated GIF.

## Install / run

**Just want to run it?** Grab the single-file binary for your OS from the
[Releases](../../releases) page — no Python needed, double-click or run from a
terminal:

| OS | Download | Run |
|---|---|---|
| Windows | `tuneassist-windows-x64.exe` | double-click, or `tuneassist-windows-x64.exe --tui` |
| macOS   | `tuneassist-macos-arm64`     | `chmod +x tuneassist-macos-arm64 && ./tuneassist-macos-arm64 --tui` |
| Linux   | `tuneassist-linux-x64`       | `chmod +x tuneassist-linux-x64 && ./tuneassist-linux-x64 --tui` |

> macOS Gatekeeper may block an unsigned binary the first time — right-click →
> Open, or `xattr -d com.apple.quarantine ./tuneassist-macos-arm64`.

**Desktop shortcut.** For a double-clickable launcher that opens the full UI:

    tuneassist --install-shortcut

It drops a native launcher on your desktop (a `.lnk` on Windows, a `.command`
on macOS, a `.desktop` entry + app-menu item on Linux) that runs `--tui`.

**Staying up to date.** The app checks GitHub for new releases (at most once a
day, and never if you set `TUNEASSIST_NO_UPDATE_CHECK=1`) and tells you when one
is out. To install it:

    tuneassist --update          # binary: downloads + swaps itself in place
    tuneassist --check-update    # just check, don't install
    # in the TUI: press Ctrl+U

(If you installed via pip/pipx, `--update` points you at `pipx upgrade tuneassist`.)

**From source:**

    pipx install .            # or: uv tool install .  -> `tuneassist` command
    # or, for development:
    pip install -r requirements.txt
    python -m tuneassist.cli

**Build your own binary:**

    pip install ".[build]"
    pyinstaller --onefile --name tuneassist --paths . packaging/entry.py
    # -> dist/tuneassist(.exe)

## Use
Export your log to **CSV** from the vendor software first (native `.hpl`/`.dl`
binaries don't carry channel names and aren't parsed — see DESIGN.md §1).

### Full UI (Textual) — recommended
    python -m tuneassist.cli --tui            # or the `tuneassist-tui` command

A mouse-and-keyboard terminal app: a **garage** of your vehicles (pick, create,
rename, delete), a setup form, and an analysis screen where you point it at a log
and get the journey bar, color heatmaps, an interactive correction grid, the
spark grid, a plain-language **diagnosis** panel, and the "next step" card.
Press **Ctrl+T** to cycle themes (default textual-dark; then gruvbox, nord,
tokyo-night, …) — remembered per machine. **Quick scan** skips the garage for a one-off look; **Browse…**
opens the native file picker.

### Web demo (zero install for viewers)
    pip install ".[serve]"
    python demo/serve.py        # browser demo at http://localhost:8000

Hosts the Textual app in a browser with bundled sample logs. Locked down (no
server filesystem access) — see `demo/README.md`. For local use: `--demo`.

### Guided session (classic Rich wizard)
    python -m tuneassist.cli                 # asks for a log, walks you through
    python -m tuneassist.cli your_log.csv     # start on a specific log

It keeps a **garage** of your vehicles in `~/.tuneassist/garage.json`. Each
vehicle remembers its hardware (cam, block, compression), fuel, airflow strategy,
and where it sits on the journey — so when you come back days later with the next
drive's log, you just pick the truck and keep going. Setup is asked once per
session; the fuel is pre-filled from the log's commanded AFR. Give each vehicle a
**nickname** for easy ID. In the picker you can select by number, name, or
nickname, and manage the garage with `r <#>` (rename) and `d <#>` (delete).

The guided session greets you, asks the few questions that change the advice
(fuel/stoich, airflow strategy, whether to tune spark, optional cam specs), runs
triage, and renders the analysis as color heatmaps. Then — the point of the tool
— it tells you the **single next thing to change**, the **drive to go do**, and
**what to log** on that drive, and loops to ingest the next log so the tune walks
forward across passes. It tracks where you are on the journey:

    Get it running → Stabilize idle → Get driving data → Tune VE (MAF off)
      → Tune MAF curve → Tune WOT fuel → Tune spark → Converged

It encodes the standard GM workflow (tune VE in speed-density with the MAF
disabled, then re-enable and tune the MAF curve — DESIGN.md §9) and does
**knock-governed** spark recommendations: it pulls timing where knock shows and,
only if you opt in, suggests small cautious adds toward MBT (DESIGN.md §10). It
will not recommend any timing change without a logged knock channel.

### Batch / headless
    python -m tuneassist.cli your_log.csv --batch --out-dir ./out   # plain report
    python -m tuneassist.cli your_log.csv --json --spark            # structured JSON

`--json` runs the headless analysis core and prints a structured result (triage,
correction grid, cross-check, MAF/spark grids, the prescription). That JSON is
the stable contract any UI or port consumes — see `tuneassist/core.py`.

The tool runs triage first. If the engine isn't running well enough to tune, it
prints what to fix (crank/start/idle) and stops. Otherwise it writes a
per-cell correction grid you paste into the base fuel / VE table.

## Docs
- `CLAUDE.md` — orientation for continuing the build in Claude Code.
- `DESIGN.md` — the tuning logic and the reasoning behind every decision.

## Tests
    python tests/test_triage.py
