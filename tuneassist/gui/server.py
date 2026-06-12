"""
gui/server.py -- the local web server behind the tuneassist GUI.

Design (docs/V2.md):
  * stdlib http.server only -- no Flask/FastAPI, keeping the binary lean.
  * binds 127.0.0.1 on a random free port; every URL is prefixed with a random
    token so other local processes can't probe the API.
  * the JSON API is a thin wrapper over core/garage/update/submit -- all logic
    stays in the headless engine.
  * lifecycle: the frontend POSTs /api/ping every few seconds; when pings stop
    (window closed) the server shuts itself down.
"""
from __future__ import annotations

import json
import mimetypes
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .. import __version__, garage
from .. import core
from .. import fitment
from ..engine_gm import Config
from ..profile import ENGINE_PRESETS, COMMON_MODS, preset_to_profile, EngineProfile
from .. import cams

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

FUELS = [("Pump gas 91-93", 14.7), ("E10 / 87-89", 14.08), ("E85", 9.76),
         ("Race / other", 14.7)]
AIRFLOWS = [("Step 1: MAF OFF - tuning VE (speed-density)  [start here]", "ve_sd"),
            ("Step 2: MAF back on - tuning the MAF curve", "maf"),
            ("MAF enabled / blended (already past VE)", "maf"),
            ("No MAF installed (pure speed-density)", "no_maf")]
CAM_TIERS = [("Stock", "stock"), ("Mild (e.g. 212-218)", "mild"),
             ("Race / big (224+)", "race"), ("Custom (enter specs)", "custom")]


class GuiState:
    """Server-wide mutable state shared by request handlers."""

    def __init__(self, garage_path: str | None = None):
        self.garage_path = garage_path
        self.data = garage.load(garage_path)
        self.last_ping = time.time()
        self.last_result: core.CoreResult | None = None
        self.last_log_path: str | None = None
        self.last_opts: core.SessionOpts | None = None

    def save_garage(self):
        try:
            garage.save(self.data, self.garage_path)
        except OSError:
            pass


def _opts_from_payload(p: dict) -> tuple[str | None, core.SessionOpts]:
    """Build SessionOpts from the JSON the frontend sends."""
    cfg = Config()
    cfg.stoich = float(p.get("stoich", 14.7))
    opts = core.SessionOpts(
        cfg=cfg,
        airflow_mode=p.get("airflow_mode", "ve_sd"),
        tune_spark=bool(p.get("tune_spark", False)),
        find_power=bool(p.get("find_power", False)),
        make=p.get("make") or None,
        architecture=p.get("architecture") or None,
    )
    preset = p.get("engine_preset")
    mods = list(p.get("mods", []) or [])
    power_adder = fitment.infer_power_adder(preset, mods)
    if preset and preset != "custom":
        opts.profile = preset_to_profile(preset, power_adder=power_adder, mods=mods)
    elif mods or p.get("block") or p.get("displacement"):
        opts.profile = EngineProfile(
            block=p.get("block") or None,
            compression=(float(p["compression"]) if p.get("compression") else None),
            displacement=(float(p["displacement"]) if p.get("displacement") else None),
            mods=mods)
    tier = p.get("cam_tier")
    if tier and tier not in ("stock", "custom"):
        spec = cams.tier_spec(tier)
        if spec:
            opts.cam_spec = spec
            opts.cam_points = cams.starting_points(spec)
    elif tier == "custom" and p.get("cam"):
        c = p["cam"]
        try:
            opts.cam_spec = cams.CamSpec(
                intake_dur_050=float(c.get("intake", 0)) or None,
                exhaust_dur_050=float(c.get("exhaust", 0)) or None,
                lsa=float(c.get("lsa", 0)) or None, lift=float(c.get("lift", 0)) or None)
            opts.cam_points = cams.starting_points(opts.cam_spec)
        except (TypeError, ValueError):
            pass
    platform = p.get("platform") or None
    return platform, opts


def _vehicle_record(state: GuiState, name: str) -> dict | None:
    rec = garage.get(state.data, name)
    if rec is None:
        return None
    return {"name": name, "nickname": rec.get("nickname"),
            "platform": rec.get("platform", "gm"),
            "platform_label": core.platform_label(rec.get("platform", "gm")),
            "make": rec.get("make"), "architecture": rec.get("architecture"),
            "stage": rec.get("stage"), "updated": rec.get("updated"),
            "stoich": rec.get("stoich", 14.7),
            "airflow_mode": rec.get("airflow_mode", "ve_sd"),
            "tune_spark": rec.get("tune_spark", False),
            "find_power": rec.get("find_power", False),
            "profile": rec.get("profile"), "cam": rec.get("cam"),
            "history": rec.get("history", [])}


def _pick_file_native() -> str | None:
    """OS-native open-file dialog (PowerShell on Windows). Returns path or None.

    The dialog gets a hidden TOPMOST owner form -- without one it opens BEHIND
    the chromeless Edge app window and looks like the Browse button did nothing
    (the bug this fixes)."""
    if not sys.platform.startswith("win"):
        return None
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms | Out-Null; "
        "$owner = New-Object System.Windows.Forms.Form; "
        "$owner.TopMost = $true; $owner.ShowInTaskbar = $false; "
        "$owner.WindowState = 'Minimized'; $owner.Opacity = 0; "
        "$owner.Show(); $owner.Activate(); "
        "$f = New-Object System.Windows.Forms.OpenFileDialog; "
        "$f.Title = 'Select a log CSV'; "
        "$f.Filter = 'Log CSV (*.csv)|*.csv|All files (*.*)|*.*'; "
        "$r = $f.ShowDialog($owner); $owner.Close(); "
        "if ($r -eq [System.Windows.Forms.DialogResult]::OK) "
        "{ [Console]::Out.Write($f.FileName) }")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", ps],
            capture_output=True, text=True, timeout=300)
        path = (out.stdout or "").strip()
        return path or None
    except Exception:
        return None


def make_handler(state: GuiState, token: str):
    """Build the request-handler class bound to this server's state + token."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # ---- plumbing -----------------------------------------------------
        def log_message(self, *a):           # quiet
            pass

        def _json(self, obj, status=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, msg, status=400):
            self._json({"error": str(msg)}, status)

        def _body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def _payload(self) -> dict:
            try:
                return json.loads(self._body().decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return {}

        def _route(self) -> str | None:
            """Strip and verify the token prefix; returns the path or None."""
            if not self.path.startswith(f"/{token}"):
                return None
            p = self.path[len(token) + 1:]
            return p or "/"

        # ---- static -------------------------------------------------------
        def _static(self, path: str):
            rel = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
            full = os.path.normpath(os.path.join(STATIC_DIR, rel))
            if not full.startswith(os.path.normpath(STATIC_DIR)) or not os.path.isfile(full):
                self._error("not found", 404)
                return
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            with open(full, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # ---- GET ----------------------------------------------------------
        def do_GET(self):
            path = self._route()
            if path is None:
                return self._error("forbidden", 403)
            if path.startswith("/api/"):
                return self._api_get(path)
            return self._static(path)

        def _api_get(self, path):
            if path == "/api/version":
                return self._json({"version": __version__})
            if path == "/api/garage":
                names = garage.list_vehicles(state.data)
                return self._json({"vehicles": [_vehicle_record(state, n) for n in names]})
            if path == "/api/presets":
                from .. import stages
                return self._json({
                    "journey": [{"key": k, "title": t} for k, t in stages.STAGES],
                    "fitment": fitment.FITMENT,
                    "fuels": [{"label": l, "stoich": s} for l, s in FUELS],
                    "airflows": [{"label": l, "mode": m} for l, m in AIRFLOWS],
                    "cam_tiers": [{"label": l, "tier": t} for l, t in CAM_TIERS],
                    "engines": [{"label": e[0]} for e in ENGINE_PRESETS],
                    "mods": list(COMMON_MODS),
                    "architectures": [{"key": k, "label": v}
                                      for k, v in core.ARCHITECTURES.items()],
                    "platforms": [{"key": "gm", "label": "HP Tuners"},
                                  {"key": "holley", "label": "Holley EFI"}],
                    "makes": [{"key": k, "label": k.upper() if k == "gm" else k.title()}
                              for k in ("gm", "ford", "mopar", "pontiac", "other")],
                })
            return self._error("not found", 404)

        # ---- POST ---------------------------------------------------------
        def do_POST(self):
            path = self._route()
            if path is None:
                return self._error("forbidden", 403)
            try:
                return self._api_post(path)
            except Exception as e:           # surface analysis errors to the UI
                traceback.print_exc()
                return self._error(f"{type(e).__name__}: {e}", 500)

        def _api_post(self, path):
            if path == "/api/ping":
                state.last_ping = time.time()
                return self._json({"ok": True})

            if path == "/api/pick-file":
                return self._json({"path": _pick_file_native()})

            if path == "/api/analyze":
                p = self._payload()
                log_path = (p.get("path") or "").strip().strip('"')
                if not log_path or not os.path.isfile(log_path):
                    return self._error(f"log not found: {log_path!r}", 404)
                platform, opts = _opts_from_payload(p)
                cr = core.analyze_log(log_path, opts, platform=platform, out_dir=None)
                state.last_result, state.last_log_path = cr, log_path
                state.last_opts = opts
                d = cr.to_dict()
                d["log_name"] = os.path.basename(log_path)
                # persist progress when analyzing a saved vehicle
                name = p.get("vehicle")
                if name and garage.get(state.data, name) is not None:
                    import datetime
                    rec = core.opts_to_record(cr.platform, opts)
                    keep = garage.get(state.data, name)
                    rec["nickname"] = keep.get("nickname")
                    hist = list(keep.get("history", []))
                    if cr.has_grid:
                        hist.append([f"pass {len(hist) + 1}",
                                     cr.summary.median_pct, cr.summary.max_abs_pct])
                    rec["history"] = hist[-20:]
                    rec["stage"] = cr.stage
                    rec["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
                    garage.upsert(state.data, name, rec)
                    state.save_garage()
                return self._json(d)

            if path == "/api/analyze-upload":
                # drag & drop: raw CSV bytes; query-ish params come via headers
                raw = self._body()
                if not raw:
                    return self._error("empty upload")
                fname = self.headers.get("X-Filename", "upload.csv")
                tmp = os.path.join(tempfile.gettempdir(),
                                   "tuneassist-" + secrets.token_hex(4) + ".csv")
                with open(tmp, "wb") as f:
                    f.write(raw)
                try:
                    opts_json = self.headers.get("X-Opts", "{}")
                    platform, opts = _opts_from_payload(json.loads(opts_json))
                    cr = core.analyze_log(tmp, opts, platform=platform, out_dir=None)
                    state.last_result, state.last_log_path = cr, tmp
                    state.last_opts = opts
                    d = cr.to_dict()
                    d["log_name"] = fname
                    return self._json(d)
                finally:
                    pass    # temp kept so 'share log' can bundle it this session

            if path == "/api/garage/upsert":
                p = self._payload()
                name = (p.get("name") or "").strip()
                if not name:
                    return self._error("name required")
                platform, opts = _opts_from_payload(p)
                rec = core.opts_to_record(platform or "gm", opts)
                rec["nickname"] = (p.get("nickname") or "").strip() or None
                old = garage.get(state.data, name)
                if old:
                    rec["history"] = old.get("history", [])
                    rec["stage"] = old.get("stage")
                garage.upsert(state.data, name, rec)
                state.save_garage()
                return self._json({"ok": True, "vehicle": _vehicle_record(state, name)})

            if path == "/api/garage/rename":
                p = self._payload()
                rec = garage.get(state.data, p.get("name", ""))
                if rec is None:
                    return self._error("vehicle not found", 404)
                rec["nickname"] = (p.get("nickname") or "").strip() or None
                state.save_garage()
                return self._json({"ok": True})

            if path == "/api/garage/delete":
                p = self._payload()
                garage.delete(state.data, p.get("name", ""))
                state.save_garage()
                return self._json({"ok": True})

            if path == "/api/update/passive":
                # once-a-day throttled check (TUNEASSIST_NO_UPDATE_CHECK aware);
                # the frontend calls this on boot and toasts if something's new
                from .. import update
                try:
                    update.cleanup_old_binary()
                    info = update.passive_check()
                except Exception:
                    info = None
                if info is None:
                    return self._json({"update": None})
                return self._json({"update": {"current": info.current,
                                              "latest": info.latest,
                                              "url": info.page_url}})

            if path == "/api/update/check":
                from .. import update
                info = update.check_for_update()
                if info is None:
                    return self._json({"update": None,
                                       "message": f"You're on the latest version "
                                                  f"(v{__version__})."})
                return self._json({"update": {"current": info.current,
                                              "latest": info.latest,
                                              "url": info.page_url}})

            if path == "/api/update/install":
                from .. import update
                ok, msg = update.self_update()
                return self._json({"ok": ok, "message": msg,
                                   "restarting": ok and update.is_frozen()})

            if path == "/api/submit":
                from .. import submit
                if not submit.is_enabled():
                    return self._error("submissions disabled", 400)
                if not state.last_result or not state.last_log_path:
                    return self._error("analyze a log first", 400)
                p = self._payload()
                bundle, url = submit.submit(state.last_log_path, state.last_result,
                                            state.last_opts,
                                            note=p.get("note", ""),
                                            contact=p.get("contact", ""))
                return self._json({"ok": True, "bundle": bundle, "url": url})

            return self._error("not found", 404)

    return Handler


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(garage_path: str | None = None):
    """Start the GUI server. Returns (server, url, state). Caller owns lifecycle.
    Dev overrides: TUNEASSIST_GUI_PORT / TUNEASSIST_GUI_TOKEN pin the random
    port/token so a browser or preview tool can reconnect across restarts."""
    state = GuiState(garage_path)
    token = os.environ.get("TUNEASSIST_GUI_TOKEN") or secrets.token_urlsafe(12)
    port = int(os.environ.get("TUNEASSIST_GUI_PORT") or _free_port())
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state, token))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{port}/{token}/", state


def serve_until_closed(httpd, state: GuiState, grace_s: float = 12.0,
                       poll_s: float = 1.0) -> None:
    """Block until the frontend stops pinging (window closed), then shut down."""
    try:
        while time.time() - state.last_ping < grace_s:
            time.sleep(poll_s)
    except KeyboardInterrupt:
        pass
    httpd.shutdown()
