"""
submit.py -- optional, opt-in log submission to help improve tuneassist.

Privacy first, by design:
  * Nothing is ever sent automatically. The user is asked after an analysis, it
    defaults to NO, and we only act when they say yes.
  * We bundle only the log they just analyzed plus a small, non-identifying
    analysis summary -- never the garage, never the vehicle name/nickname.
  * The bundle is written to disk locally and the submission page is opened in
    the browser; the user attaches the file themselves and can inspect it first.

Turning it on: set SUBMIT_URL to a free file-collection form (a Tally.so or
Google Form with a file-upload field -- see docs/SUBMISSIONS.md). Leave it blank
and the whole feature stays dormant (no prompts, no UI).
"""
from __future__ import annotations

import json
import os
import shutil
import time
import webbrowser
import zipfile

from . import __version__

# Paste your file-collection form URL here to enable submissions. Blank = off.
SUBMIT_URL = ""


def is_enabled() -> bool:
    return bool(SUBMIT_URL)


def _submissions_dir() -> str:
    d = os.path.join(os.path.expanduser("~"), ".tuneassist", "submissions")
    os.makedirs(d, exist_ok=True)
    return d


def build_metadata(cr, opts, note: str = "", contact: str = "") -> dict:
    """Non-identifying analysis context to ride along with the log. Everything is
    read defensively so a partial result can't break submission."""
    prof = getattr(opts, "profile", None)
    profile = {}
    if prof is not None:
        for k in ("block", "compression", "displacement", "power_adder", "engine"):
            v = getattr(prof, k, None)
            if v is not None:
                profile[k] = v
        mods = list(getattr(prof, "mods", []) or [])
        if mods:
            profile["mods"] = mods

    summ = getattr(cr, "summary", None)
    summary = {}
    if summ is not None:
        for k in ("median_pct", "max_abs_pct", "coverage_pct", "cruise_max_abs_pct",
                  "wot_max_abs_pct"):
            v = getattr(summ, k, None)
            if v is not None:
                summary[k] = v

    return {
        "tuneassist_version": __version__,
        "platform": getattr(cr, "platform", None),
        "triage_state": getattr(getattr(cr, "triage", None), "state", None),
        "stage": getattr(cr, "stage", None),
        "airflow_mode": getattr(opts, "airflow_mode", None),
        "tune_spark": getattr(opts, "tune_spark", None),
        "stoich": getattr(getattr(opts, "cfg", None), "stoich", None),
        "profile": profile,
        "summary": summary,
        "finding_ids": [getattr(f, "id", None) for f in getattr(cr, "findings", [])],
        "note": note,
        "contact": contact,         # only what the user types; optional
        "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def build_bundle(log_path: str, cr, opts, note: str = "", contact: str = "") -> str:
    """Write a single .zip (log + submission.json) to ~/.tuneassist/submissions/
    and return its path. Does NOT send anything."""
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = os.path.join(_submissions_dir(), f"tuneassist-submission-{ts}.zip")
    meta = build_metadata(cr, opts, note, contact)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("submission.json", json.dumps(meta, indent=2))
        if log_path and os.path.exists(log_path):
            # keep the original filename, drop any directory path
            z.write(log_path, arcname="log/" + os.path.basename(log_path))
    return out


def open_form() -> bool:
    if not SUBMIT_URL:
        return False
    try:
        webbrowser.open(SUBMIT_URL)
        return True
    except Exception:
        return False


def submit(log_path: str, cr, opts, note: str = "", contact: str = "") -> tuple[str, str]:
    """Build the bundle and open the submission form. Returns (bundle_path, url)."""
    bundle = build_bundle(log_path, cr, opts, note, contact)
    open_form()
    return bundle, SUBMIT_URL
