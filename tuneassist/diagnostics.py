"""
diagnostics.py -- pattern-based engine diagnosis (DESIGN.md S12).

The correction math says "how far off each cell is." This says *why*, and *what
to change* -- the symptom -> cause -> correction reasoning a good tuner applies,
plus where there's free power. Each detector reads whatever channels are present
and skips itself when its inputs aren't logged, so it degrades gracefully on any
export. Findings are advisory and ranked; safety-critical ones sort to the top.
"""

from __future__ import annotations
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


SEVERITY_RANK = {"critical": 0, "warning": 1, "opportunity": 2, "info": 3}


@dataclass
class DiagnosticConfig:
    lean_trim: float = 6.0        # mean cruise total-trim % above this = lean
    rich_trim: float = 6.0        # below -this = rich
    idle_lean_trim: float = 9.0   # idle total-trim % above this w/ ok cruise = leak
    bank_split: float = 5.0       # |bank1 - bank2| trim % = one-bank fault
    trim_osc_std: float = 8.0     # STFT std above this = oscillation/instability
    o2_suspect: float = 4.0       # wideband vs commanded gap % (closed loop)
    wot_map_min: float = 80.0     # kPa: WOT / power region
    wot_lean_afr: float = 13.0    # WOT measured AFR leaner than this = danger
    wot_target_afr: float = 12.6  # a safe NA pump WOT target
    wot_rich_afr: float = 12.2    # richer than this at WOT = power left on table
    inj_duty_max: float = 85.0    # injector duty % above this = fuel-system limit
    knock_deg: float = 1.0        # sustained retard above this = real knock
    iat_hot: float = 140.0        # F
    ect_hot: float = 235.0        # F


@dataclass
class Finding:
    id: str
    severity: str                 # critical | warning | opportunity | info
    title: str
    detail: str                   # the observed symptom, with numbers
    causes: list = field(default_factory=list)        # ranked likely causes
    corrections: list = field(default_factory=list)   # what to change
    confidence: str = "medium"    # high | medium | low

    def to_dict(self) -> dict:
        return {"id": self.id, "severity": self.severity, "title": self.title,
                "detail": self.detail, "causes": self.causes,
                "corrections": self.corrections, "confidence": self.confidence}


# --------------------------------------------------------------------------
def _num(df, col, key):
    return pd.to_numeric(df[col[key]], errors="coerce") if key in col else None


def _warm_mask(df, col, ect_min):
    m = pd.Series(True, index=df.index)
    ect = _num(df, col, "ect")
    if ect is not None:
        m &= ect >= ect_min
    rpm = _num(df, col, "rpm")
    if rpm is not None:
        m &= rpm > 400
    return m


def _total_trim(df, col):
    st = [col[k] for k in ("stft", "stft2") if k in col]
    lt = [col[k] for k in ("ltft", "ltft2") if k in col]
    if not (st and lt):
        return None
    s = df[st].apply(pd.to_numeric, errors="coerce").mean(axis=1).fillna(0)
    l = df[lt].apply(pd.to_numeric, errors="coerce").mean(axis=1).fillna(0)
    return s + l


def _duty(df, col):
    """Injector duty %, from a duty channel or PW(ms)*RPM/1200."""
    d = _num(df, col, "duty")
    if d is not None and d.notna().mean() > 0.3:
        return d
    pw = _num(df, col, "injpw")
    rpm = _num(df, col, "rpm")
    if pw is not None and rpm is not None:
        return pw * rpm / 1200.0
    return None


# --------------------------------------------------------------------------
# Detectors. Each returns a Finding or None (or a list).
# --------------------------------------------------------------------------
def _d_cruise_trim(df, col, cfg, dc, warm):
    trim = _total_trim(df, col)
    if trim is None:
        return None
    mapk = _num(df, col, "map")
    cruise = warm & (mapk < dc.wot_map_min) if mapk is not None else warm
    vals = trim[cruise].dropna()
    if len(vals) < 30:
        return None
    m = float(vals.mean())
    if m > dc.lean_trim:
        causes = ["VE/MAF airflow under-estimated (apply the correction grid)"]
        idle = _idle_trim(df, col, trim, warm)
        conf = "high"
        if idle is not None and idle - m > 4:
            causes.insert(0, f"vacuum/unmetered-air leak (idle trim {idle:+.0f}% >> "
                             f"cruise {m:+.0f}%)")
        causes += ["low injector flow data / dead-time", "weak fuel pump if it grows with load"]
        return Finding("LEAN_CRUISE", "warning", "Running lean at cruise",
                       f"Mean fuel trim at cruise is {m:+.1f}% (PCM adding fuel).",
                       causes, ["Raise VE/MAF where trims are positive, re-log.",
                                "If idle is much leaner than cruise, smoke-test for leaks."],
                       conf)
    if m < -dc.rich_trim:
        return Finding("RICH_CRUISE", "warning", "Running rich at cruise",
                       f"Mean fuel trim at cruise is {m:+.1f}% (PCM pulling fuel).",
                       ["VE/MAF airflow over-estimated", "leaking/oversized injector",
                        "fuel pressure high", "contaminated MAF reading high"],
                       ["Lower VE/MAF where trims are negative, re-log.",
                        "Check injector scaling and fuel pressure."], "high")
    return None


def _idle_trim(df, col, trim, warm):
    rpm = _num(df, col, "rpm")
    if rpm is None:
        return None
    idle = warm & (rpm > 500) & (rpm < 1100)
    tps = _num(df, col, "tps")
    if tps is not None:
        idle &= tps < 8
    vals = trim[idle].dropna()
    return float(vals.mean()) if len(vals) > 20 else None


def _d_idle_vacuum_leak(df, col, cfg, dc, warm):
    trim = _total_trim(df, col)
    if trim is None:
        return None
    idle = _idle_trim(df, col, trim, warm)
    mapk = _num(df, col, "map")
    cruise = warm & (mapk < dc.wot_map_min) if mapk is not None else warm
    cvals = trim[cruise].dropna()
    if idle is None or len(cvals) < 30:
        return None
    cruise_mean = float(cvals.mean())
    if idle > dc.idle_lean_trim and idle - cruise_mean > 5:
        return Finding("VACUUM_LEAK", "warning", "Vacuum leak suspected",
                       f"Idle fuel trim is {idle:+.1f}% but cruise is only "
                       f"{cruise_mean:+.1f}% - unmetered air dominates at idle.",
                       ["intake/manifold gasket or vacuum line leak",
                        "PCV / brake-booster hose", "injector o-rings", "throttle-body gasket"],
                       ["Smoke-test the intake; fix the leak before idle VE work.",
                        "Don't 'tune out' a leak by raising idle VE - re-log after the fix."],
                       "medium")
    return None


def _d_bank_imbalance(df, col, cfg, dc, warm):
    have2 = ("stft2" in col or "ltft2" in col)
    if not have2:
        return None
    def bank(stk, ltk):
        s = _num(df, col, stk); l = _num(df, col, ltk)
        if s is None and l is None:
            return None
        tot = (s if s is not None else 0) + (l if l is not None else 0)
        return tot[warm].dropna()
    b1 = bank("stft", "ltft"); b2 = bank("stft2", "ltft2")
    if b1 is None or b2 is None or len(b1) < 30 or len(b2) < 30:
        return None
    diff = float(b1.mean() - b2.mean())
    if abs(diff) > dc.bank_split:
        lean_bank = 1 if diff > 0 else 2
        return Finding("BANK_IMBALANCE", "warning",
                       f"Banks disagree (bank {lean_bank} leaner)",
                       f"Bank1-Bank2 trim differs by {diff:+.1f}% - a one-bank issue.",
                       [f"injector(s) on bank {lean_bank}", "a single biased/dead O2 sensor",
                        "exhaust leak upstream of one O2 (false-lean)",
                        "intake/vacuum leak feeding one bank"],
                       ["Swap-test O2 sensors side-to-side to see if the split follows.",
                        "Inspect that bank's injectors and intake sealing."], "medium")
    return None


def _d_wb_vs_commanded(df, col, cfg, dc, warm):
    act = _num(df, col, "afr_actual"); cmd = _num(df, col, "afr_cmd")
    if act is None or cmd is None:
        return None
    cl = warm & (cmd >= 14.0)              # closed-loop / stoich target cells
    a = act[cl].dropna()
    if len(a) < 30:
        return None
    dev = float((a.median() / cmd[cl].dropna().median() - 1.0) * 100)
    if abs(dev) > dc.o2_suspect:
        return Finding("WB_VS_NB", "warning", "Wideband disagrees with target",
                       f"At stoich the wideband reads {dev:+.1f}% off commanded - "
                       "the ECM may be trimming to a lying narrowband.",
                       ["narrowband O2 bias or exhaust leak at the sensor",
                        "fuel-content / stoich mismatch (E-blend vs gasoline stoich)",
                        "wideband calibration drift"],
                       ["Verify the configured stoich matches the actual fuel.",
                        "Resolve the sensor before chasing airflow in these cells."],
                       "medium")
    return None


def _d_wot_fueling(df, col, cfg, dc, warm):
    act = _num(df, col, "afr_actual")
    mapk = _num(df, col, "map")
    if act is None or mapk is None:
        return None
    wot = warm & (mapk >= dc.wot_map_min)
    a = act[wot].dropna()
    if len(a) < 12:
        return None
    afr = float(a.median())
    cmd = _num(df, col, "afr_cmd")
    cmdmed = float(cmd[wot].dropna().median()) if cmd is not None else None
    duty = _duty(df, col)
    duty_hi = duty is not None and float(duty[wot].dropna().quantile(0.95) or 0) > dc.inj_duty_max

    if cmdmed is not None:
        # Hitting target? Then it's working -- only flag a real shortfall, a risky
        # *target*, or a power opportunity. Don't cry "lean" when AFR == commanded.
        if afr - cmdmed > 0.4:                    # measured leaner than asked = real
            causes = ["fuel system out of headroom (injector duty / pressure)",
                      "airflow / fuel model under-reads at high load"]
            if duty_hi:
                causes.insert(0, "injector duty maxed - running out of injector")
            sev = "critical" if afr > 13.3 else "warning"
            return Finding("WOT_SHORTFALL", sev, "WOT leaner than commanded",
                           f"Commanded ~{cmdmed:.1f} but measured ~{afr:.1f} at WOT.",
                           causes,
                           ["Verify fuel pressure holds and injector duty has headroom.",
                            "Add fuel up top until it meets the target; stop pulls if it's lean."],
                           "high")
        if cmdmed >= 13.7:                        # the target itself is lean for WOT
            return Finding("WOT_TARGET_RISK", "warning", "WOT target is lean",
                           f"WOT is commanding ~{cmdmed:.1f} - lean for sustained load.",
                           ["a conservative/economy WOT AFR target"],
                           ["Richen the WOT target toward ~12.8 for safety margin and power."],
                           "medium")
        if 12.9 <= cmdmed < 13.7:                 # safe but leaving power on the table
            return Finding("WOT_TARGET_LEAN", "opportunity", "Richer WOT could make power",
                           f"WOT target ~{cmdmed:.1f}; ~{dc.wot_target_afr:.1f} usually "
                           "makes peak power on pump and adds knock margin.",
                           ["WOT AFR target set on the lean side"],
                           [f"Try richening the WOT target toward ~{dc.wot_target_afr:.1f}, "
                            "watch the wideband and knock.",
                            "Re-log; keep whatever makes the most power without knock."],
                           "medium")
        if afr < dc.wot_rich_afr and cmdmed < dc.wot_rich_afr:
            return Finding("WOT_RICH", "opportunity", "WOT richer than needed",
                           f"WOT AFR ~{afr:.1f}; ~{dc.wot_target_afr:.1f} usually makes "
                           "more power and burns cooler.",
                           ["PE target set conservatively rich"],
                           [f"Lean the WOT target toward ~{dc.wot_target_afr:.1f} in steps, "
                            "watching knock.", "Re-log each step; back off if knock appears."],
                           "medium")
        return None

    # No commanded channel: judge on absolute AFR.
    if afr > dc.wot_lean_afr:
        causes = ["airflow / fuel under-read at high load",
                  "fuel system out of headroom (injector duty / pressure)"]
        if duty_hi:
            causes.insert(0, "injector duty maxed - running out of injector")
        return Finding("WOT_LEAN", "critical", "Lean at wide-open throttle",
                       f"WOT AFR is ~{afr:.1f} (leaner than {dc.wot_lean_afr:.1f}). "
                       "This is where pistons die - fix before more pulls.",
                       causes,
                       ["Add fuel at WOT immediately; target ~12.5-12.8 on pump.",
                        "Check fuel pressure under load and injector duty.",
                        "Stop WOT pulls until it's safe."], "high")
    if afr < dc.wot_rich_afr:
        return Finding("WOT_RICH", "opportunity", "WOT richer than needed",
                       f"WOT AFR is ~{afr:.1f}; ~{dc.wot_target_afr:.1f} usually makes "
                       "more power on pump and runs cooler-burning.",
                       ["PE target set conservatively rich"],
                       [f"Lean the WOT target toward ~{dc.wot_target_afr:.1f} in steps, "
                        "watching knock.", "Re-log each step; back off if knock appears."],
                       "medium")
    return None


def _d_injector_duty(df, col, cfg, dc, warm):
    duty = _duty(df, col)
    if duty is None:
        return None
    d = duty[warm].dropna()
    if len(d) < 20:
        return None
    peak = float(d.quantile(0.99))
    if peak > dc.inj_duty_max:
        sev = "critical" if peak > 95 else "warning"
        return Finding("INJ_DUTY", sev, "Injectors near their limit",
                       f"Injector duty peaks ~{peak:.0f}% - little headroom; "
                       "at ~100% it can't add fuel and goes lean.",
                       ["injectors undersized for the power level",
                        "fuel pressure dropping under load"],
                       ["Move to larger injectors (and re-scale) or raise fuel pressure.",
                        "Don't lean WOT to mask it - that's the lean-failure path."],
                       "high")
    return None


def _d_knock(df, col, cfg, dc, warm):
    kn = _num(df, col, "knock")
    if kn is None:
        return None
    knocking = warm & (kn > dc.knock_deg)
    n = int(knocking.sum())
    if n == 0:
        return None
    worst = float(kn[knocking].max())
    causes = ["too much spark advance for the conditions"]
    iat = _num(df, col, "iat")
    if iat is not None and float(iat[knocking].mean()) > dc.iat_hot - 20:
        causes.append("high charge temp (IAT) - heat-soak raises knock")
    act = _num(df, col, "afr_actual"); mapk = _num(df, col, "map")
    if act is not None and mapk is not None:
        loaded = knocking & (mapk >= dc.wot_map_min)
        if loaded.any() and float(act[loaded].median()) > 13.0:
            causes.append("lean under load - fix fueling before pulling timing")
    causes += ["low octane / old fuel", "carbon or hot-spot in a chamber"]
    sev = "critical" if (worst > 4 or n > 50) else "warning"
    return Finding("KNOCK", sev, "Knock retard detected",
                   f"{n} sample(s) of knock retard, worst {worst:.1f} deg.",
                   causes,
                   ["Pull timing in the affected cells (observed retard + margin).",
                    "Address any lean/hot-IAT root cause first.",
                    "Verify fuel octane and that knock sensors are healthy."], "high")


def _d_temps(df, col, cfg, dc, warm):
    out = []
    ect = _num(df, col, "ect")
    if ect is not None and float(ect.max() or 0) > dc.ect_hot:
        out.append(Finding("OVERHEAT", "warning", "Coolant temp ran hot",
                           f"Peak coolant {float(ect.max()):.0f}F (> {dc.ect_hot:.0f}).",
                           ["cooling system / fan tables", "lean or over-advanced tune making heat"],
                           ["Check fan-on temps and cooling capacity.",
                            "A hot engine has less knock margin - verify timing."], "medium"))
    iat = _num(df, col, "iat")
    if iat is not None and float(iat.quantile(0.95) or 0) > dc.iat_hot:
        out.append(Finding("HIGH_IAT", "info", "High intake-air temp",
                           f"IAT 95th-percentile {float(iat.quantile(0.95)):.0f}F "
                           f"(> {dc.iat_hot:.0f}) - heat-soak/density loss, raises knock risk.",
                           ["heat-soaked intake / under-hood heat", "no cold-air feed"],
                           ["Ensure IAT-based spark retard is active.",
                            "Cooler intake charge will recover power and knock margin."], "medium"))
    return out


def _d_trim_oscillation(df, col, cfg, dc, warm):
    st = _num(df, col, "stft")
    if st is None:
        return None
    vals = st[warm].dropna()
    if len(vals) < 50:
        return None
    sd = float(vals.std())
    if sd > dc.trim_osc_std:
        return Finding("TRIM_OSCILLATION", "info", "Fuel trims are oscillating",
                       f"Short-term trim std is {sd:.1f}% - bouncing around.",
                       ["O2 sensor wiring/heater or aging sensor",
                        "exhaust leak near the O2", "closed-loop gains too aggressive"],
                       ["Check O2 sensor health and exhaust sealing.",
                        "Stabilize before trusting cell-by-cell trims."], "low")
    return None


_DETECTORS = [_d_cruise_trim, _d_idle_vacuum_leak, _d_bank_imbalance,
              _d_wb_vs_commanded, _d_wot_fueling, _d_injector_duty, _d_knock,
              _d_temps, _d_trim_oscillation]


# Detectors that only make sense on the GM/HPTuners model (the wideband is
# external and the ECM controls fuel via a narrowband). On Holley the wideband
# IS the control sensor, so a wideband-vs-target gap is just un-converged Learn,
# already captured by the base-fuel correction grid.
_PLATFORM_SKIP = {"holley": {"WB_VS_NB"}}


def diagnose(df: pd.DataFrame, col: dict, cfg, dc: DiagnosticConfig | None = None,
             platform: str = "gm") -> list:
    """Run all detectors and return Findings sorted by severity then confidence."""
    dc = dc or DiagnosticConfig()
    warm = _warm_mask(df, col, getattr(cfg, "ect_min_f", 160.0))
    skip = _PLATFORM_SKIP.get(platform, set())
    findings = []
    for det in _DETECTORS:
        try:
            r = det(df, col, cfg, dc, warm)
        except Exception:    # a detector must never take down the analysis
            continue
        if r is None:
            continue
        for f in (r if isinstance(r, list) else [r]):
            if f.id not in skip:
                findings.append(f)
    conf_rank = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (SEVERITY_RANK.get(f.severity, 9),
                                 conf_rank.get(f.confidence, 9)))
    return findings
