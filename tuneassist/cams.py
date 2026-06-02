"""
cams.py -- optional cam specs -> conservative tuning starting points.

A camshaft's duration @ .050 and lobe-separation angle (LSA) change how much
intake charge gets diluted by residuals at idle and low RPM (overlap/reversion),
which lowers dynamic compression and shifts where the engine makes torque. That
moves the *starting points* for idle airflow, idle timing, and low-RPM advance.

This is starting-point guidance ONLY (DESIGN.md S11). Nothing here is applied
without a knock-logged pull confirming it. All inputs optional; we degrade to
"stock-ish assumptions" when a field is missing.
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass
class CamSpec:
    """Numbers off a cam card. All optional."""
    intake_dur_050: float | None = None   # intake duration @ .050"
    exhaust_dur_050: float | None = None
    lsa: float | None = None              # lobe separation angle, deg
    lift: float | None = None             # max lift, inches (informational)

    @property
    def avg_dur_050(self) -> float | None:
        ds = [d for d in (self.intake_dur_050, self.exhaust_dur_050) if d]
        return sum(ds) / len(ds) if ds else None


def classify(cam: CamSpec) -> str:
    """stock / mild / big / unknown, from duration @ .050 and LSA.
    LS rule-of-thumb breakpoints: <210 stock-ish, 210-228 mild street, >228 big."""
    d = cam.avg_dur_050
    if d is None:
        return "unknown"
    if d < 210:
        klass = "stock"
    elif d <= 228:
        klass = "mild"
    else:
        klass = "big"
    # A tight LSA (< 112) adds overlap -> behaves "bigger" at idle.
    if cam.lsa is not None and cam.lsa < 112 and klass == "mild":
        klass = "big"
    return klass


@dataclass
class CamStartingPoints:
    klass: str
    idle_timing_deg: tuple            # (low, high) suggested idle advance
    idle_rpm: int                     # suggested target idle rpm
    notes: list


def starting_points(cam: CamSpec) -> CamStartingPoints:
    """Conservative starting numbers for a cammed LS, by class."""
    klass = classify(cam)
    table = {
        "stock":   ((14, 18), 600, [
            "Stock-ish cam: factory idle/timing strategy is a fine baseline."]),
        "mild":    ((18, 24), 700, [
            "Mild cam: bump idle timing and idle RPM a touch; it dilutes more at idle.",
            "Low-RPM/light-load can usually take a little more advance -- verify with knock."]),
        "big":     ((22, 28), 850, [
            "Big cam: idle wants noticeably more timing and airflow to stay lit.",
            "Expect empty low-RPM VE cells; torque peak is higher -- prescribe higher-RPM drives.",
            "Lower dynamic compression down low improves knock tolerance there (still verify).",
            "Watch idle MAP -- it'll be higher/unsteadier than stock; don't chase it as a leak."]),
        "unknown": ((14, 20), 650, [
            "No cam specs given: assuming a mild street baseline. Enter duration @ .050 "
            "and LSA for sharper idle/timing starting points."]),
    }
    lo_hi, rpm, notes = table[klass]
    return CamStartingPoints(klass, lo_hi, rpm, list(notes))
