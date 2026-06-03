"""
tui.py -- the Textual application: a beautiful, mouse-and-keyboard front-end.

It is a *consumer of core.py* -- it never re-implements analysis. Screens:
  GarageScreen   pick / create / rename / delete a vehicle (persisted)
  SetupScreen    the once-per-vehicle setup form (fuel, airflow, spark, cam, profile)
  AnalyzeScreen  drop in a log -> renders the full result (reusing panels.py) +
                 the journey bar + the "next step" card, then loop to the next log

Run:  python -m tuneassist.cli --tui     (or the `tuneassist-tui` console script)
"""

from __future__ import annotations
import datetime
import os
import subprocess
import sys

import pandas as pd
from rich.text import Text

from textual import work
from textual.app import App, ComposeResult
from textual.screen import Screen, ModalScreen
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (Header, Footer, Button, Input, Select, Switch,
                             Static, Label, DataTable, DirectoryTree, Collapsible,
                             TabbedContent, TabPane)

from . import core, garage, cams, panels
from .engine_gm import Config
from .profile import EngineProfile


FUELS = [("Pump gas 91-93", 14.7), ("E10 / 87-89", 14.08), ("E85", 9.76),
         ("Race / other", 14.7)]
AIRFLOWS = [("MAF disabled - tuning VE (SD)", "ve_sd"),
            ("MAF enabled / blended", "maf"),
            ("No MAF (pure speed-density)", "no_maf"),
            ("Tuning the MAF curve now", "maf")]


def _native_pick_file() -> str | None:
    """Open the OS-native 'open file' dialog and return the chosen path (or None).
    Runs as a subprocess so it never conflicts with Textual's event loop. Falls
    back gracefully (returns None) if no native picker is available -- the user
    can still type a path or use the in-app browser."""
    try:
        if sys.platform.startswith("win"):
            ps = (
                'Add-Type -AssemblyName System.Windows.Forms | Out-Null; '
                '$f = New-Object System.Windows.Forms.OpenFileDialog; '
                '$f.Title = "Select a log CSV"; '
                '$f.Filter = "Logs (*.csv;*.hpl;*.dl)|*.csv;*.hpl;*.dl|All files (*.*)|*.*"; '
                'if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) '
                '{ [Console]::Out.Write($f.FileName) }')
            out = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-Command", ps],
                capture_output=True, text=True, timeout=300)
            return out.stdout.strip() or None
        if sys.platform == "darwin":
            script = ('POSIX path of (choose file with prompt "Select a log CSV")')
            out = subprocess.run(["osascript", "-e", script],
                                 capture_output=True, text=True, timeout=300)
            return out.stdout.strip() or None
        # Linux / other: try zenity, then kdialog.
        for cmd in (["zenity", "--file-selection", "--title=Select a log CSV"],
                    ["kdialog", "--getopenfilename"]):
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if out.returncode == 0 and out.stdout.strip():
                    return out.stdout.strip()
            except FileNotFoundError:
                continue
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _setup_summary(platform, opts) -> str:
    amode = {"ve_sd": "VE/SD (MAF off)", "maf": "MAF curve", "no_maf": "no-MAF SD"}
    bits = [platform.upper(), f"{opts.cfg.stoich:g} stoich",
            amode.get(opts.airflow_mode, opts.airflow_mode)]
    if opts.tune_spark:
        bits.append("spark" + ("+power" if opts.find_power else ""))
    if opts.cam_points:
        bits.append(f"{opts.cam_points.klass} cam")
    if opts.profile:
        p = opts.profile
        bits.append(f"{p.block or '?'} {p.compression or '?'}:1 {p.power_adder}")
    return "  •  ".join(bits)


# --------------------------------------------------------------------------
# Small modal dialogs
# --------------------------------------------------------------------------
class TextPrompt(ModalScreen[str]):
    def __init__(self, prompt: str, value: str = ""):
        super().__init__()
        self._prompt, self._value = prompt, value

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._prompt)
            yield Input(value=self._value, id="text")
            with Horizontal(id="dialog-buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self):
        self.query_one("#text", Input).focus()

    def on_button_pressed(self, e: Button.Pressed):
        self.dismiss(self.query_one("#text", Input).value if e.button.id == "ok" else None)

    def on_input_submitted(self, e: Input.Submitted):
        self.dismiss(e.value)


class ConfirmDialog(ModalScreen[bool]):
    def __init__(self, prompt: str):
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._prompt)
            with Horizontal(id="dialog-buttons"):
                yield Button("Yes, delete", variant="error", id="yes")
                yield Button("Cancel", variant="primary", id="no")

    def on_button_pressed(self, e: Button.Pressed):
        self.dismiss(e.button.id == "yes")


# --------------------------------------------------------------------------
# Garage screen
# --------------------------------------------------------------------------
class GarageScreen(Screen):
    BINDINGS = [("s", "quick", "Quick scan"), ("n", "new", "New"),
                ("r", "rename", "Rename"), ("d", "delete", "Delete"),
                ("q", "app.quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(panels.build_banner(), id="banner")
        yield Label("Your garage — pick a vehicle, press [b]s[/b] for a quick scan, "
                    "or [b]n[/b] for a new one:", id="garage-help")
        yield DataTable(id="vehicles", cursor_type="row", zebra_stripes=True)
        with Horizontal(id="garage-buttons"):
            yield Button("Open", variant="primary", id="open")
            yield Button("Quick scan (no save)", id="quick")
            yield Button("New vehicle", variant="success", id="new")
            yield Button("Rename", id="rename")
            yield Button("Delete", variant="error", id="delete")
        yield Footer()

    def on_mount(self):
        dt = self.query_one("#vehicles", DataTable)
        dt.add_columns("Nickname", "Name", "Platform", "Last stage")
        self._reload()

    def _reload(self):
        dt = self.query_one("#vehicles", DataTable)
        dt.clear()
        self._names = garage.list_vehicles(self.app.data)
        for n in self._names:
            rec = garage.get(self.app.data, n) or {}
            dt.add_row(rec.get("nickname") or "—", n,
                       rec.get("platform", "?").upper(), rec.get("stage", "—"))
        if not self._names:
            self.query_one("#garage-help", Label).update(
                "Garage is empty — press [b]n[/b] (or 'New vehicle') to add one.")

    def _selected(self) -> str | None:
        dt = self.query_one("#vehicles", DataTable)
        if not self._names or dt.cursor_row is None:
            return None
        return self._names[dt.cursor_row]

    def on_button_pressed(self, e: Button.Pressed):
        {"open": self._open, "quick": self.action_quick, "new": self.action_new,
         "rename": self.action_rename, "delete": self.action_delete}[e.button.id]()

    def action_quick(self):
        """Bypass the garage: an ephemeral, unsaved session for a quick look."""
        self.app.push_screen(SetupScreen(ephemeral=True))

    def on_data_table_row_selected(self, _):
        self._open()

    def _open(self):
        name = self._selected()
        if not name:
            self.notify("Pick a vehicle first (or add a new one).", severity="warning")
            return
        rec = garage.get(self.app.data, name)
        platform, opts = core.record_to_opts(rec)
        self.app.load_vehicle(name, rec.get("nickname"), platform, opts,
                              list(rec.get("history", [])))
        self.app.push_screen(AnalyzeScreen())

    def action_new(self):
        self.app.push_screen(SetupScreen())

    def action_rename(self):
        name = self._selected()
        if not name:
            return
        cur = (garage.get(self.app.data, name) or {}).get("nickname") or ""

        def done(value):
            if value is not None:
                garage.get(self.app.data, name)["nickname"] = value.strip() or None
                self.app._save()
                self._reload()
        self.app.push_screen(TextPrompt(f"Nickname for '{name}':", cur), done)

    def action_delete(self):
        name = self._selected()
        if not name:
            return

        def done(yes):
            if yes:
                garage.delete(self.app.data, name)
                self.app._save()
                self._reload()
        self.app.push_screen(ConfirmDialog(f"Delete '{name}' permanently?"), done)


# --------------------------------------------------------------------------
# Setup screen (new vehicle / edit)
# --------------------------------------------------------------------------
class SetupScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def __init__(self, ephemeral: bool = False):
        super().__init__()
        self.ephemeral = ephemeral

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="setup"):
            if self.ephemeral:
                yield Label("[b]Quick scan[/b]  (one-off — not saved to the garage)")
            else:
                yield Label("[b]New vehicle setup[/b]  (asked once — remembered for next time)")
            if not self.ephemeral:
                yield Label("Name"); yield Input(placeholder="e.g. 5.3 iron truck", id="name")
                yield Label("Nickname (optional)"); yield Input(placeholder="e.g. Goldie", id="nick")
            yield Label("Platform")
            yield Select([("GM / HPTuners", "gm"), ("Holley EFI", "holley")],
                         value="gm", allow_blank=False, id="platform")
            yield Label("Fuel")
            yield Select([(lbl, i) for i, (lbl, _s) in enumerate(FUELS)],
                         value=0, allow_blank=False, id="fuel")
            yield Label("Airflow strategy")
            yield Select([(lbl, i) for i, (lbl, _m) in enumerate(AIRFLOWS)],
                         value=0, allow_blank=False, id="airflow")
            with Horizontal(classes="switchrow"):
                yield Label("Tune spark/timing (needs knock channel)")
                yield Switch(id="spark")
            with Horizontal(classes="switchrow"):
                yield Label("Probe for more power (cautious adds)")
                yield Switch(id="findpower")
            with Collapsible(title="Cam specs (optional)"):
                yield Input(placeholder="Intake duration @ .050", id="cam_int")
                yield Input(placeholder="Exhaust duration @ .050", id="cam_exh")
                yield Input(placeholder="LSA", id="cam_lsa")
                yield Input(placeholder="Max lift (in)", id="cam_lift")
            with Collapsible(title="Engine profile (optional — sharpens spark advice)"):
                yield Select([("iron", "iron"), ("aluminum", "alum")],
                             prompt="Block material", id="block")
                yield Input(placeholder="Static compression (e.g. 10.5)", id="cr")
                yield Input(placeholder="Displacement (liters)", id="disp")
                yield Select([("naturally aspirated", "na"), ("boost", "boost"),
                              ("nitrous", "nitrous")], value="na",
                             allow_blank=False, id="adder")
            with Horizontal(id="setup-buttons"):
                yield Button("Scan" if self.ephemeral else "Save & continue",
                             variant="primary", id="save")
                yield Button("Cancel", id="cancel")
        yield Footer()

    def _f(self, wid):
        v = self.query_one(f"#{wid}", Input).value.strip()
        try:
            return float(v) if v else None
        except ValueError:
            return None

    def on_button_pressed(self, e: Button.Pressed):
        if e.button.id == "cancel":
            self.app.pop_screen()
            return
        if self.ephemeral:
            name = nick = None                          # unsaved one-off
        else:
            name = self.query_one("#name", Input).value.strip()
            if not name:
                self.notify("Give the vehicle a name to save it.", severity="warning")
                return
            nick = self.query_one("#nick", Input).value.strip() or None
        cfg = Config()
        cfg.stoich = FUELS[self.query_one("#fuel", Select).value][1]
        platform = self.query_one("#platform", Select).value
        opts = core.SessionOpts(
            cfg=cfg, airflow_mode=AIRFLOWS[self.query_one("#airflow", Select).value][1],
            tune_spark=self.query_one("#spark", Switch).value,
            find_power=self.query_one("#findpower", Switch).value)
        ci, ce = self._f("cam_int"), self._f("cam_exh")
        if ci or ce or self._f("cam_lsa"):
            opts.cam_spec = cams.CamSpec(intake_dur_050=ci, exhaust_dur_050=ce,
                                         lsa=self._f("cam_lsa"), lift=self._f("cam_lift"))
            opts.cam_points = cams.starting_points(opts.cam_spec)
        blk = self.query_one("#block", Select).value
        block = blk if isinstance(blk, str) else None    # else it's the BLANK sentinel
        cr = self._f("cr")
        if block or cr:
            opts.profile = EngineProfile(
                block=block, compression=cr, displacement=self._f("disp"),
                power_adder=self.query_one("#adder", Select).value)
        self.app.load_vehicle(name, nick, platform, opts, [])
        if not self.ephemeral:
            self.app._persist_setup()
        self.app.pop_screen()                      # drop setup
        self.app.push_screen(AnalyzeScreen())


# --------------------------------------------------------------------------
# Analyze screen (the workhorse)
# --------------------------------------------------------------------------
class AnalyzeScreen(Screen):
    BINDINGS = [("g", "garage", "Garage"), ("a", "focus_path", "Analyze a log"),
                ("ctrl+o", "pick_file", "Open file…"), ("q", "app.quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="vehinfo")
        yield Static(panels.build_journey_bar(""), id="journey")
        with Horizontal(id="pathrow"):
            yield Input(placeholder="path to your exported log CSV…", id="path")
            yield Button("Browse…", id="pick")
            yield Button("Analyze", variant="primary", id="analyze")
        with Collapsible(title="…or browse in-app", id="browser", collapsed=True):
            yield Input(value=os.path.expanduser("~"), id="treeroot",
                        placeholder="folder to browse (Enter to go there)")
            yield DirectoryTree(os.path.expanduser("~"), id="tree")
        with TabbedContent(id="tabs"):
            with TabPane("Report", id="tab-report"):
                yield VerticalScroll(Static(self._welcome(), id="results"))
            with TabPane("Correction grid", id="tab-grid"):
                yield DataTable(id="grid", cursor_type="cell", zebra_stripes=True)
                yield Static("  Click a cell for detail.", id="celldetail")
            with TabPane("Top cells", id="tab-cells"):
                yield Static("  Click a column header to sort.", id="cellshint")
                yield DataTable(id="cells", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self):
        self._cr = None
        self._sort_rev = {}
        self._refresh_info()

    def _welcome(self):
        return Text.from_markup(
            "\n  Drop in a log CSV above and press [b]Analyze[/b].\n"
            "  I'll triage it, recommend changes, and tell you the next drive to log.\n")

    def _refresh_info(self):
        a = self.app
        nick = f'"{a.nickname}"  ' if a.nickname else ""
        self.query_one("#vehinfo", Static).update(panels.Panel(
            f"[b]{nick}[/b][grey70]{a.vehicle or 'unsaved'}[/]   "
            f"[grey50]{_setup_summary(a.platform, a.opts)}[/]",
            border_style="blue", title="[b]vehicle[/b]", title_align="left"))

    def action_garage(self):
        self.app.pop_screen()

    def action_focus_path(self):
        self.query_one("#path", Input).focus()

    def on_directory_tree_file_selected(self, e: DirectoryTree.FileSelected):
        self.query_one("#path", Input).value = str(e.path)
        self.query_one("#browser", Collapsible).collapsed = True

    def on_input_submitted(self, e: Input.Submitted):
        if e.input.id == "treeroot":
            self._reroot_tree(e.value)
        else:
            self._analyze()

    def _reroot_tree(self, folder: str):
        folder = os.path.expanduser(folder.strip().strip('"'))
        if not os.path.isdir(folder):
            self.notify("Not a folder.", severity="warning")
            return
        tree = self.query_one("#tree", DirectoryTree)
        tree.path = folder            # DirectoryTree re-roots and reloads

    def on_button_pressed(self, e: Button.Pressed):
        if e.button.id == "analyze":
            self._analyze()
        elif e.button.id == "pick":
            self.action_pick_file()

    def action_pick_file(self):
        """Open the OS-native file picker (so you can browse anywhere)."""
        self._pick_file_worker()

    @work(thread=True, exclusive=True)
    def _pick_file_worker(self):
        path = _native_pick_file()
        if path:
            self.app.call_from_thread(self._set_path, path)

    def _set_path(self, path: str):
        self.query_one("#path", Input).value = path
        self.notify(f"Selected {os.path.basename(path)}")

    def _analyze(self):
        path = self.query_one("#path", Input).value.strip().strip('"')
        if not path:
            self.notify("Enter a log path first.", severity="warning")
            return
        try:
            cr = core.analyze_log(path, self.app.opts, out_dir=None)
        except (FileNotFoundError, OSError) as ex:
            self.notify(f"Can't read that file: {ex}", severity="error")
            return
        except Exception as ex:                    # analysis blew up -> show it
            self.notify(f"Analysis failed: {ex}", severity="error")
            return
        if cr.has_grid:
            self.app.history.append((f"pass {len(self.app.history) + 1}",
                                     cr.summary.median_pct, cr.summary.max_abs_pct))
        self.app.persist(cr.stage)
        self._cr = cr
        self.query_one("#journey", Static).update(panels.build_journey_bar(cr.stage))
        self.query_one("#results", Static).update(
            panels.build_report(cr, self.app.history, show_spark=self.app.opts.tune_spark))
        self._populate_grid(cr)
        self._populate_cells(cr)
        self.notify(f"Stage: {cr.stage.replace('_', ' ').title()}", severity="information")

    # ---- interactive correction grid (RPM x MAP, colored, clickable) ----
    def _populate_grid(self, cr):
        dt = self.query_one("#grid", DataTable)
        dt.clear(columns=True)
        if not cr.has_grid:
            dt.add_column("—")
            dt.add_row("no confident correction grid for this log")
            self.query_one("#celldetail", Static).update("  —")
            return
        corr = cr.result.correction
        dt.add_column("RPM \\ MAP")
        for c in corr.columns:
            dt.add_column(panels._interval_label(c))
        for r in corr.index:
            cells = [Text(panels._interval_label(r), style="bold grey70")]
            for c in corr.columns:
                v = corr.loc[r, c]
                if pd.isna(v):
                    cells.append(Text("·", style="grey30"))
                else:
                    pct = (v - 1.0) * 100.0
                    sign = "+" if pct >= 0 else ""
                    cells.append(Text(f"{sign}{pct:.1f}", style=panels._pct_style(pct)))
            dt.add_row(*cells)
        self.query_one("#celldetail", Static).update("  Click a cell for detail.")

    def on_data_table_cell_highlighted(self, e):
        if e.data_table.id != "grid" or self._cr is None or not self._cr.has_grid:
            return
        self.query_one("#celldetail", Static).update(
            self._cell_detail(e.coordinate.row, e.coordinate.column))

    def _cell_detail(self, row: int, col: int) -> str:
        res = self._cr.result
        corr = res.correction
        if row >= len(corr.index):
            return "  —"
        rlab = panels._interval_label(corr.index[row])
        if col == 0:
            return f"  RPM band [b]{rlab}[/b]"
        clab = panels._interval_label(corr.columns[col - 1])
        v = corr.iloc[row, col - 1]
        if pd.isna(v):
            return f"  RPM [b]{rlab}[/b] × MAP [b]{clab}[/b]:  no confident data (too few samples)"
        pct = (v - 1.0) * 100.0
        n = int(res.samples.iloc[row, col - 1]) if getattr(res, "samples", None) is not None else 0
        rec = getattr(res, "recommendation", None)
        cc = ""
        if rec is not None and not rec.empty:
            label = rec.iloc[row, col - 1]
            cc = f"   cross-check: [b]{label}[/b]" if not pd.isna(label) else ""
        verb = "raise VE / add fuel" if pct >= 0 else "pull fuel"
        return (f"  RPM [b]{rlab}[/b] × MAP [b]{clab}[/b]:  [b]{pct:+.1f}%[/b] "
                f"({verb})   n={n}{cc}")

    # ---- flat, sortable "top cells" table ----
    def _populate_cells(self, cr):
        dt = self.query_one("#cells", DataTable)
        dt.clear(columns=True)
        for label, key in [("RPM", "rpm"), ("MAP", "map"), ("Change %", "chg"),
                           ("|Δ|", "abs"), ("Samples", "n"), ("Cross-check", "cc")]:
            dt.add_column(label, key=key)
        if not cr.has_grid:
            dt.add_row("—", "—", "—", "—", "—", "—")
            return
        res = cr.result
        rec = getattr(res, "recommendation", None)
        for r in res.correction.index:
            for c in res.correction.columns:
                v = res.correction.loc[r, c]
                if pd.isna(v):
                    continue
                pct = round((v - 1.0) * 100.0, 1)
                n = int(res.samples.loc[r, c]) if getattr(res, "samples", None) is not None else 0
                cc = ""
                if rec is not None and not rec.empty:
                    lab = rec.loc[r, c]
                    cc = "" if pd.isna(lab) else str(lab)
                dt.add_row(panels._interval_label(r), panels._interval_label(c),
                           pct, abs(pct), n, cc)
        dt.sort("abs", reverse=True)            # biggest changes first by default

    def on_data_table_header_selected(self, e):
        if e.data_table.id != "cells":
            return
        rev = not self._sort_rev.get(e.column_key, False)
        self._sort_rev[e.column_key] = rev
        e.data_table.sort(e.column_key, reverse=rev)


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
class TuneAssistApp(App):
    TITLE = "Tune Assist"
    SUB_TITLE = "recommendation-only engine tuning"
    CSS = """
    Screen { background: $surface; }
    #banner { margin: 1 2 0 2; }
    #garage-help { margin: 1 2; }
    #vehicles { margin: 0 2; height: 1fr; }
    #garage-buttons, #setup-buttons, #dialog-buttons { height: auto; margin: 1 2; }
    #garage-buttons Button, #setup-buttons Button { margin: 0 1; }
    #vehinfo { margin: 1 2 0 2; }
    #journey { margin: 0 2; }
    #pathrow { height: auto; margin: 1 2; }
    #pathrow Input { width: 1fr; }
    #pathrow Button { margin: 0 0 0 2; }
    #browser { margin: 0 2; max-height: 14; }
    #tabs { margin: 0 2; height: 1fr; }
    #results { padding: 1 2; }
    #grid, #cells { height: 1fr; }
    #celldetail { height: auto; padding: 1 1 0 1; color: $text; }
    #cellshint { height: auto; padding: 0 1; color: $text-muted; }
    VerticalScroll { height: 1fr; }
    #setup { padding: 1 2; }
    #setup Input, #setup Select { margin: 0 0 1 0; width: 70; }
    .switchrow { height: auto; }
    .switchrow Label { padding: 1 2 0 0; }
    #dialog {
        width: 60; height: auto; padding: 1 2; margin: 4 0;
        background: $panel; border: thick $primary; align: center middle;
    }
    #dialog-buttons { align: center middle; }
    #dialog-buttons Button { margin: 0 1; }
    ModalScreen { align: center middle; }
    """
    BINDINGS = [("ctrl+c", "quit", "Quit"), ("ctrl+t", "cycle_theme", "Theme")]
    THEMES = ["gruvbox", "nord", "tokyo-night", "catppuccin-mocha",
              "textual-dark", "textual-light"]

    def __init__(self, garage_path: str | None = None):
        super().__init__()
        self.garage_path = garage_path
        self.data = garage.load(garage_path)
        self.vehicle = None
        self.nickname = None
        self.platform = "gm"
        self.opts = core.SessionOpts(cfg=Config())
        self.history: list = []

    def on_mount(self):
        # remembered theme (per-machine), default gruvbox
        saved = self.data.get("theme")
        self.theme = saved if saved in self.THEMES else "gruvbox"
        self.push_screen(GarageScreen())

    def action_cycle_theme(self):
        cur = self.theme if self.theme in self.THEMES else self.THEMES[0]
        nxt = self.THEMES[(self.THEMES.index(cur) + 1) % len(self.THEMES)]
        self.theme = nxt
        self.data["theme"] = nxt
        self._save()
        self.notify(f"Theme: {nxt}", timeout=1.5)

    def load_vehicle(self, vehicle, nickname, platform, opts, history):
        self.vehicle, self.nickname = vehicle, nickname
        self.platform, self.opts, self.history = platform, opts, history

    def _save(self):
        try:
            garage.save(self.data, self.garage_path)
        except OSError as e:
            self.notify(f"Couldn't save garage: {e}", severity="error")

    def _record(self, stage=None):
        rec = core.opts_to_record(self.platform, self.opts)
        rec.update(nickname=self.nickname, history=self.history,
                   updated=datetime.datetime.now().isoformat(timespec="seconds"))
        if stage:
            rec["stage"] = stage
        elif self.vehicle and garage.get(self.data, self.vehicle):
            rec["stage"] = garage.get(self.data, self.vehicle).get("stage", "—")
        return rec

    def _persist_setup(self):
        if self.vehicle:
            garage.upsert(self.data, self.vehicle, self._record())
            self._save()

    def persist(self, stage):
        if self.vehicle:
            garage.upsert(self.data, self.vehicle, self._record(stage))
            self._save()


def run_tui(garage_path: str | None = None):
    TuneAssistApp(garage_path).run()
