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
    idle_lean_trim: float = 8.0   # idle fuel-add % above this is suspicious
    vac_idle_delta: float = 5.0   # idle add must exceed cruise add by this (tapers w/ load)
    vac_idle_afr_lean: float = 15.3  # idle wideband leaner than this corroborates a leak
    bank_split: float = 5.0       # |bank1 - bank2| trim % = one-bank fault
    trim_osc_std: float = 8.0     # STFT std above this = oscillation/instability
    o2_suspect: float = 4.0       # wideband vs commanded gap % (closed loop)
    wot_map_min: float = 80.0     # kPa: WOT / power region
    wot_lean_afr: float = 13.0    # WOT measured AFR leaner than this = danger
    wot_target_afr: float = 12.8  # NA pump WOT sweet spot (12.8-12.9 makes best power)
    wot_rich_afr: float = 12.2    # richer than this at WOT = power left on table
    trim_clip: float = 22.0       # |trim|% at/above this = hitting ECU authority limit
    batt_low: float = 12.8        # avg running voltage below this = charging/dead-time
    fp_low: float = 38.0          # base fuel pressure (psi) below this = too low for EFI
    inj_duty_max: float = 85.0    # injector duty % above this = fuel-system limit
    knock_deg: float = 1.0        # sustained retard above this = real knock
    iat_hot: float = 140.0        # F
    ect_hot: float = 235.0        # F
    # --- forced induction (turbo / supercharger) ---
    boost_map: float = 105.0      # kPa above (baro) this = under boost
    boost_lean_afr: float = 11.9  # under boost, leaner than this = danger
    boost_iat_hot: float = 150.0  # F: post-compressor charge heat under boost
    fp_drop: float = 8.0          # psi fuel-pressure drop under load = supply limit
    cl_in_boost: float = 1.5      # |trim|% under boost above this = closed-loop-in-boost
    # --- cold start / warmup ---
    warmup_rich_afr: float = 12.0   # warmup AFR richer than this = loading up
    warmup_lean_afr: float = 15.5   # warmup AFR leaner than this = cold stumble/stall
    thermostat_min_f: float = 170.0 # should exceed this on a long drive
    warmup_min_duration_s: float = 240.0  # only judge "never warmed" over a long log
    ase_warm_pct: float = 2.0       # afterstart/warmup enrichment still active when warm
    # --- idle quality ---
    idle_hunt_std: float = 90.0     # trimmed idle-RPM std above this = hunting/surging
    idle_rpm_tol: float = 150.0     # actual vs target idle RPM gap that matters
    idle_afr_rich: float = 13.2     # idle AFR richer than this = over-rich idle
    idle_afr_lean: float = 15.3     # idle AFR leaner than this = lean idle
    idle_timing_std: float = 4.0    # idle spark swing (deg) above this = fighting itself


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


def _idle_mask(df, col, warm):
    """A *true* idle mask. The trap: closed-throttle decel/coast-down also has low
    TPS and a wide RPM ramp, which inflates 'idle hunting'. Real idle is the engine
    stopped at low RPM with manifold pressure in the idle range (NOT the deep
    vacuum of decel), so we gate on MAP and, when present, vehicle speed."""
    rpm = _num(df, col, "rpm")
    if rpm is None:
        return None
    m = warm & (rpm > 500) & (rpm < 1100)
    tps = _num(df, col, "tps")
    if tps is not None:
        m &= tps < 5
    mapk = _num(df, col, "map")
    if mapk is not None:
        m &= (mapk >= 35) & (mapk <= 62)     # idle vacuum; excludes decel (low) / load (high)
    spd = _num(df, col, "speed")
    if spd is not None:
        m &= spd < 3                          # stopped, not coasting
    return m


def _applied_correction(df, col):
    """The % fuel the system added/removed: STFT+LTFT (GM) or CL-comp+Learn
    (Holley). Positive = adding fuel. Works cross-platform for the leak detector."""
    t = _total_trim(df, col)
    if t is not None:
        return t
    comp = _num(df, col, "cl_comp")
    learn = _num(df, col, "learn")
    if comp is None and learn is None:
        return None
    return ((comp.fillna(0) if comp is not None else 0)
            + (learn.fillna(0) if learn is not None else 0))


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
    idle = _idle_mask(df, col, warm)
    if idle is None:
        return None
    vals = trim[idle].dropna()
    return float(vals.mean()) if len(vals) > 20 else None


def _d_vacuum_leak(df, col, cfg, dc, warm, cam_class=None):
    """Vacuum leak = a fixed amount of UNMETERED air. Its fueling effect is a big
    % at idle (low airflow) and shrinks as airflow rises -- so the tell is a
    high idle fuel-add that TAPERS with load. Corroborate with a lean idle AFR
    (the leak exceeds trim authority) and, on Holley, a low IAC at high idle.
    A big cam can mimic this (reversion/dilution), so we soften confidence then.
    Works on GM (trims) and Holley (CL-comp + Learn)."""
    corr = _applied_correction(df, col)
    rpm = _num(df, col, "rpm")
    mapk = _num(df, col, "map")
    if corr is None or rpm is None or mapk is None:
        return None
    idle = _idle_mask(df, col, warm)
    if idle is None:
        return None
    # cruise bands gated above idle RPM so idle samples don't dilute the reference
    light = warm & (mapk >= 35) & (mapk < 55) & (rpm > 1300)
    mid = warm & (mapk >= 55) & (mapk < dc.wot_map_min) & (rpm > 1300)

    def avg(m, n=15):
        v = corr[m].dropna()
        return float(v.mean()) if len(v) >= n else None

    it = avg(idle)
    if it is None or it < dc.idle_lean_trim:
        return None
    cruise_ref = avg(light)
    if cruise_ref is None:
        cruise_ref = avg(mid)
    if cruise_ref is None:
        return None
    taper = it - cruise_ref
    if taper < dc.vac_idle_delta:
        return None                                  # doesn't taper -> not a leak

    # Corroboration ----------------------------------------------------------
    conf, extra_cause, lead = "medium", [], ""
    afr = _num(df, col, "afr_actual")
    if afr is not None:
        ia = afr[idle].dropna()
        if len(ia) > 10 and float(ia.median()) > dc.vac_idle_afr_lean:
            conf = "high"
            lead = (f" Idle is also running lean (~{float(ia.median()):.1f} AFR) even "
                    "with fuel added - the leak is past what trims can hide.")
    # big cam can mimic the idle signature
    if cam_class == "big":
        conf = "low"
        extra_cause.append("(note: a big cam can mimic this via idle reversion/"
                            "dilution - verify with a smoke test)")

    sev = "warning"
    causes = ["intake-manifold or throttle-body gasket leak",
              "vacuum line / PCV / brake-booster hose",
              "injector o-rings", "leaking intake at a runner"]
    causes += extra_cause
    return Finding(
        "VACUUM_LEAK", sev, "Vacuum leak suspected",
        f"Idle is adding {it:+.0f}% fuel but cruise only {cruise_ref:+.0f}% - "
        f"that taper with airflow is the classic unmetered-air signature.{lead}",
        causes,
        ["Smoke-test the intake (and PCV/booster lines); fix the leak FIRST.",
         "Don't 'tune out' a leak by raising idle fuel/VE - re-log after the repair.",
         "On a fresh swap, re-check manifold bolts/gaskets and the throttle-body seal."],
        conf)


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


def _d_trim_clipping(df, col, cfg, dc, warm):
    """Any fuel-trim bank pegged near the ECU's correction authority (~+/-25%)
    means it's out of room to compensate -- the base is way off or there's a leak
    / fuel-supply problem."""
    for k in ("stft", "ltft", "stft2", "ltft2"):
        s = _num(df, col, k)
        if s is None:
            continue
        sv = s[warm].dropna()
        if len(sv) < 30:
            continue
        peak = float(sv.abs().quantile(0.95))
        if peak >= dc.trim_clip:
            direction = "adding" if sv.median() > 0 else "pulling"
            return Finding("TRIM_CLIPPING", "warning", "Fuel trims are maxed out",
                           f"Fuel trim is hitting ~{peak:.0f}% ({direction} fuel) -- near "
                           "the ECU's correction limit, so it's out of room to compensate.",
                           ["base fuel/VE airflow is way off",
                            "big vacuum leak or a fuel-supply problem",
                            "wrong injector flow scaling"],
                           ["Fix the underlying airflow/fuel error (apply the correction, "
                            "check for leaks and fuel pressure) -- at the limit the ECU can "
                            "no longer hide it, so the engine will go lean/rich for real."],
                           "high")
    return None


def _d_low_voltage(df, col, cfg, dc, warm):
    b = _num(df, col, "battery")
    if b is None:
        return None
    bv = b[warm].dropna()
    bv = bv[bv > 6]                       # ignore key-off / bad reads
    if len(bv) < 30:
        return None
    med = float(bv.median())
    if med < dc.batt_low:
        return Finding("LOW_VOLTAGE", "warning", "Low system voltage",
                       f"Voltage averages ~{med:.1f}V while running (want ~13.8-14.5). "
                       "Low voltage slows the fuel pump and lengthens injector opening, "
                       "which skews fueling.",
                       ["charging system / alternator / ground", "heavy electrical load"],
                       ["Fix charging so it holds ~14V.",
                        "Confirm the injector dead-time (offset) vs voltage table is set, "
                        "or fueling drifts as voltage changes."], "medium")
    return None


def _d_low_fuel_pressure(df, col, cfg, dc, warm):
    fp = _num(df, col, "fuelpres")
    if fp is None:
        return None
    mapk = _num(df, col, "map")
    base = fp[warm & (mapk < 60)].dropna() if mapk is not None else fp[warm].dropna()
    base = base[base > 5]
    if len(base) < 20:
        return None
    med = float(base.median())
    if med < dc.fp_low:
        return Finding("LOW_FUEL_PRESSURE", "warning", "Fuel pressure is low",
                       f"Fuel pressure sits ~{med:.0f} psi at light load -- low for port "
                       "EFI (typically ~43-60). Less fuel per pulse means a lean tendency, "
                       "worst up top.",
                       ["weak pump / clogged filter", "regulator set low", "restricted supply"],
                       ["Set base fuel pressure to spec and verify it holds before tuning fuel."],
                       "medium")
    return None


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


def _boost_findings(df, col, cfg, dc, warm, profile):
    """Forced-induction detectors. Boost is detected from MAP exceeding baro, so
    these light up only on a boosted log (or when the engine profile says boost)."""
    out = []
    mapk = _num(df, col, "map")
    if mapk is None:
        return out
    baro = _num(df, col, "baro")
    baro_kpa = float(baro[warm].median()) if baro is not None and baro[warm].notna().any() else 101.3
    peak_map = float(mapk[warm].quantile(0.999)) if warm.any() else float(mapk.max())
    boosted_log = peak_map > dc.boost_map + 3
    adder = getattr(profile, "power_adder", "na") if profile else "na"
    expects_boost = adder == "boost"

    def psi(kpa):
        return (kpa - baro_kpa) / 6.89476

    boost = warm & (mapk > dc.boost_map)

    # MAP sensor can't see boost: profile says boost but MAP never clears ~1 bar.
    if expects_boost and not boosted_log:
        tps = _num(df, col, "tps")
        hi = warm & (tps > 80) if tps is not None else warm
        if hi.any() and float(mapk[hi].max()) < dc.boost_map + 6:
            out.append(Finding(
                "MAP_SENSOR_RANGE", "warning", "MAP sensor can't read your boost",
                f"You set a boosted engine but MAP never exceeds ~{int(peak_map)} kPa "
                "(about 1 bar) at full throttle - a 1-bar sensor is blind above "
                "atmospheric.",
                ["stock 1-bar MAP sensor on a boosted engine"],
                ["Fit a 2-bar (to ~15 psi) or 3-bar (to ~30 psi) MAP sensor and "
                 "re-scale it, so the tune can actually see boost."], "medium"))
        return out

    if not boosted_log:
        return out

    out.append(Finding(
        "FORCED_INDUCTION", "info", "Boost detected",
        f"Peak manifold pressure ~{int(peak_map)} kPa (~{psi(peak_map):.1f} psi of "
        "boost). Boost cells are open-loop and unforgiving - fuel and timing margins "
        "matter most here.",
        [], [], "high"))

    # Lean under boost = the fastest way to melt a piston.
    act = _num(df, col, "afr_actual")
    if act is not None:
        a = act[boost].dropna()
        if len(a) >= 8:
            afr = float(a.median())
            if afr > dc.boost_lean_afr:
                causes = ["fuel system out of headroom (injectors / pump / pressure)",
                          "boost AFR target too lean", "fuel pressure not rising with boost"]
                duty = _duty(df, col)
                if duty is not None and float(duty[boost].dropna().quantile(0.95) or 0) > dc.inj_duty_max:
                    causes.insert(0, "injector duty maxed under boost")
                out.append(Finding(
                    "BOOST_LEAN", "critical", "Lean under boost",
                    f"Under boost the AFR is ~{afr:.1f} - dangerously lean for "
                    "forced induction (want ~11.0-11.8).",
                    causes,
                    ["Richen the boost AFR target to ~11.5 and confirm the fuel system "
                     "can deliver it.", "Do not make more boost until it's safe."], "high"))

    # Fuel pressure should hold (or rise 1:1 with boost); a drop means supply limit.
    fp = _num(df, col, "fuelpres")
    if fp is not None:
        base = fp[warm & (mapk < 60)].dropna()
        load = fp[boost].dropna()
        if len(base) > 10 and len(load) > 8:
            drop = float(base.median() - load.median())
            if drop > dc.fp_drop:
                out.append(Finding(
                    "FUEL_PRESSURE_DROP", "critical", "Fuel pressure dropping under load",
                    f"Fuel pressure falls ~{drop:.0f} psi from idle to boost - the "
                    "supply can't keep up (it should hold, or rise with boost).",
                    ["pump / line / filter undersized", "regulator not boost-referenced",
                     "failing pump"],
                    ["Fix fuel supply before more boost; lean-out under boost follows a "
                     "pressure drop.", "Use a boost-referenced (1:1) regulator if not already."],
                    "high"))

    # Closed-loop fueling under boost is risky (it can lean a rich PE target out).
    trim = _total_trim(df, col)
    if trim is not None:
        t = trim[boost].dropna()
        if len(t) > 8 and abs(float(t.mean())) > dc.cl_in_boost:
            out.append(Finding(
                "CL_IN_BOOST", "warning", "Closed-loop fueling under boost",
                f"Fuel trims are still active under boost (mean {float(t.mean()):+.1f}%). "
                "Power enrichment should run OPEN loop.",
                ["closed-loop / PE disable point set too high"],
                ["Make sure closed loop drops out before boost (lower the PE/open-loop "
                 "enable point)."], "medium"))

    # Charge-heat under boost (weak/heat-soaked intercooler).
    iat = _num(df, col, "iat")
    if iat is not None:
        i = iat[boost].dropna()
        if len(i) > 8 and float(i.median()) > dc.boost_iat_hot:
            out.append(Finding(
                "BOOST_IAT", "warning", "Hot charge temps under boost",
                f"Intake-air temp under boost medians ~{float(i.median()):.0f}F - the "
                "charge is hot, which steals power and invites knock.",
                ["intercooler too small or heat-soaked", "no intercooler / air-to-water pump off"],
                ["Improve charge cooling; add IAT-based timing retard as a safety net."],
                "medium"))
    return out


def _coldstart_findings(df, col, cfg, dc):
    """Cold-start / warmup analysis. Runs on the FULL log (not warm-masked) since
    the point is the cold portion. Degrades to nothing if the log is warm-only."""
    out = []
    ect = _num(df, col, "ect")
    rpm = _num(df, col, "rpm")
    if ect is None or rpm is None:
        return out
    warm_temp = getattr(cfg, "ect_min_f", 160.0)
    running = rpm > 500
    cold_run = running & (ect < warm_temp) & (ect > 90)   # warming, not stone-cold crank

    # Never reached operating temp over a long log -> stuck/missing thermostat.
    t = _num(df, col, "time")
    dur = float(t.max() - t.min()) if t is not None and t.notna().any() else 0.0
    ect_max = float(ect[running].max()) if running.any() else float(ect.max())
    if dur > dc.warmup_min_duration_s and ect_max < dc.thermostat_min_f and ect_max > 110:
        out.append(Finding(
            "THERMOSTAT", "warning", "Engine never reached operating temp",
            f"Over ~{dur/60:.0f} min coolant only reached ~{ect_max:.0f}F "
            f"(want >{dc.thermostat_min_f:.0f}). A stuck-open or missing thermostat is "
            "the usual cause - and cruise/VE data while cold isn't valid to tune on.",
            ["stuck-open or missing thermostat", "wrong/!low-temp thermostat",
             "gauge/sensor reading low"],
            ["Fit a proper thermostat and confirm it reaches temp before tuning.",
             "Re-log once it holds operating temperature."], "medium"))

    # Warmup AFR (wideband) -- too rich loads up/fouls; too lean stumbles/stalls.
    afr = _num(df, col, "afr_actual")
    if afr is not None and int(cold_run.sum()) > 30:
        a = afr[cold_run].dropna()
        if len(a) > 20:
            med = float(a.median())
            if med < dc.warmup_rich_afr:
                out.append(Finding(
                    "WARMUP_RICH", "warning", "Warmup is over-rich",
                    f"While warming up the AFR medians ~{med:.1f} - rich enough to foul "
                    "plugs, wash cylinders, and load up.",
                    ["coolant/afterstart enrichment too high or decaying too slowly"],
                    ["Trim the warmup (coolant) enrichment down; taper it out sooner.",
                     "Re-log a cold start and watch AFR climb toward stoich as it warms."],
                    "medium"))
            elif med > dc.warmup_lean_afr:
                out.append(Finding(
                    "WARMUP_LEAN", "warning", "Warmup is lean",
                    f"While warming up the AFR medians ~{med:.1f} - lean enough to "
                    "stumble, hesitate, or stall cold.",
                    ["not enough coolant/afterstart enrichment when cold"],
                    ["Add warmup (coolant) enrichment so cold AFR sits richer (~13-13.5).",
                     "Re-log a cold start to confirm it drives off cleanly."], "medium"))

    # Enrichment still active when fully warm. Two conventions: "% added" where
    # 0 = none, or a multiplier where 100% = neutral (Holley). Detect the neutral
    # baseline so we don't flag a settled 100% as "still enriching".
    warm = running & (ect >= warm_temp)
    for key, label in (("ase", "afterstart"), ("warmup_enr", "warmup/coolant")):
        enr = _num(df, col, key)
        if enr is None:
            continue
        ew = enr[warm].dropna()
        full = enr.dropna()
        if len(ew) <= 20 or len(full) < 30:
            continue
        neutral = 100.0 if float(full.median()) > 50 else 0.0
        margin = 8.0 if neutral == 100.0 else dc.ase_warm_pct
        warm_val = float(ew.median())
        over = warm_val - neutral
        if over > margin:
            disp = f"~{warm_val:.0f}% (neutral is {neutral:.0f}%)"
            out.append(Finding(
                "ENRICH_NOT_DECAYED", "warning",
                f"{label.title()} enrichment still active when warm",
                f"{label.title()} enrichment is still {disp} with the engine warm - "
                "it should have tapered back to neutral.",
                [f"{label} enrichment-vs-temp curve doesn't decay by operating temp"],
                [f"Taper the {label} enrichment to neutral by operating temp so warm "
                 "fueling is just the base/learned table."], "high"))
            break   # report one; they overlap
    return out


# Typical idle RPM by build (HP Academy / HP Tuners): stock LS ~550-600, a healthy
# cammed LS ~800-850. Used to judge high/low idle when no target idle is logged.
EXPECTED_IDLE = {"stock": 600, "mild": 760, "big": 860}


def _idle_findings(df, col, cfg, dc, warm, cam_class=None):
    """Warm idle quality: hunting, off-target RPM, idle AFR, IAC authority,
    idle-timing fighting. Uses warm idle samples only."""
    out = []
    rpm = _num(df, col, "rpm")
    idle = _idle_mask(df, col, warm)
    if rpm is None or idle is None:
        return out
    ridle = rpm[idle].dropna()
    if len(ridle) < 40:
        return out
    idle_rpm = float(ridle.median())

    # Hunting / surging -- trimmed std (drop the 5% tails) so a brief blip or the
    # moment of coming to a stop doesn't read as a hunt.
    lo, hi = ridle.quantile(0.05), ridle.quantile(0.95)
    trimmed = ridle[(ridle >= lo) & (ridle <= hi)]
    std = float(trimmed.std()) if len(trimmed) > 10 else float(ridle.std())
    if std > dc.idle_hunt_std:
        out.append(Finding(
            "IDLE_HUNT", "warning", "Idle is hunting / surging",
            f"Idle RPM swings a lot (std {std:.0f} rpm around ~{idle_rpm:.0f}).",
            ["vacuum leak (lean hunt)", "IAC range / min-air off",
             "idle spark-vs-RPM correction too aggressive", "loading up rich"],
            ["Steady the idle before fueling: set idle airflow target and IAC range.",
             "Smoke-test for leaks; soften idle spark correction while dialing in."],
            "medium"))

    # Actual vs target idle RPM. Prefer a logged target; otherwise infer a typical
    # idle from the cam class (less certain -> info, wider tolerance).
    target, tol, sev, ctx = None, dc.idle_rpm_tol, "warning", ""
    tgt = _num(df, col, "idle_target")
    if tgt is not None:
        tv = tgt[idle].dropna()
        if len(tv) > 20:
            target = float(tv.median())
            ctx = f"a target of ~{target:.0f} rpm"
    if target is None and cam_class in EXPECTED_IDLE:
        target = EXPECTED_IDLE[cam_class]
        tol, sev = 280, "info"
        ctx = f"~{target:.0f} rpm (typical for a {cam_class} build; no target logged)"
    if target is not None:
        gap = idle_rpm - target
        if gap > tol:
            out.append(Finding(
                "IDLE_HIGH", sev, "Idle higher than expected",
                f"Idle sits ~{idle_rpm:.0f} vs {ctx} (+{gap:.0f}).",
                ["vacuum leak / extra unmetered air", "throttle blade stop set too high",
                 "IAC commanded closed but can't pull it down"],
                ["Check for leaks; set the throttle stop / IAC so idle meets target."],
                "medium"))
        elif gap < -tol:
            out.append(Finding(
                "IDLE_LOW", sev, "Idle lower than expected (stall risk)",
                f"Idle sits ~{idle_rpm:.0f} vs {ctx} ({gap:.0f}).",
                ["not enough idle airflow", "IAC out of authority", "idle spark too low"],
                ["Raise idle airflow (IAC/min-air) so it holds target; "
                 "check idle timing isn't too retarded."], "medium"))

    # Idle AFR
    afr = _num(df, col, "afr_actual")
    if afr is not None:
        av = afr[idle].dropna()
        if len(av) > 20:
            a = float(av.median())
            if a < dc.idle_afr_rich:
                out.append(Finding("IDLE_RICH", "info", "Idle is rich",
                    f"Idle AFR ~{a:.1f} - rich; can load up, smell, and foul over time.",
                    ["idle fuel / base table rich at idle"],
                    ["Lean idle fueling toward ~14.0-14.7; watch idle quality."], "medium"))
            elif a > dc.idle_afr_lean:
                out.append(Finding("IDLE_LEAN", "warning", "Idle is lean",
                    f"Idle AFR ~{a:.1f} - lean idle hunts and can stall.",
                    ["vacuum leak", "idle fuel too low"],
                    ["Smoke-test for leaks first; otherwise add idle fuel toward ~14.2."],
                    "medium"))

    # IAC fully closed but idle isn't low -> extra air getting in (leak / base air high)
    iac = _num(df, col, "iac")
    if iac is not None:
        iv = iac[idle].dropna()
        if len(iv) > 20 and float(iv.median()) < 2.0:
            out.append(Finding("IAC_CLOSED", "info",
                "IAC fully closed at idle",
                f"The idle air valve is commanded shut (~{float(iv.median()):.0f}) yet "
                "idle still holds - extra air is getting in somewhere it shouldn't.",
                ["vacuum/throttle leak", "throttle blade cracked open too far"],
                ["Find the unmetered air (smoke test) or close the throttle stop so the "
                 "IAC has room to control idle."], "low"))

    # Idle timing swinging a lot -> idle spark correction fighting RPM
    spk = _num(df, col, "spark")
    if spk is None:
        spk = _num(df, col, "ign")
    if spk is not None:
        sv = spk[idle].dropna()
        if len(sv) > 40 and float(sv.std()) > dc.idle_timing_std:
            fix = ["Soften idle spark correction while you stabilize idle airflow/fuel."]
            if cam_class == "big":
                fix.append("On a big cam, multiply the idle spark-vs-RPM table by ~0.5 to "
                           "stop it over-correcting.")
            out.append(Finding("IDLE_TIMING_SWING", "info",
                "Idle timing is swinging",
                f"Idle spark advance varies a lot (std {float(sv.std()):.1f} deg) - the "
                "idle spark-vs-RPM correction may be amplifying the hunt.",
                ["idle spark correction (rpm error -> timing) too strong"], fix, "low"))
    return out


_DETECTORS = [_d_cruise_trim, _d_bank_imbalance, _d_wb_vs_commanded,
              _d_wot_fueling, _d_injector_duty, _d_trim_clipping, _d_low_voltage,
              _d_low_fuel_pressure, _d_knock, _d_temps, _d_trim_oscillation]


# Detectors that only make sense on the GM/HPTuners model (the wideband is
# external and the ECM controls fuel via a narrowband). On Holley the wideband
# IS the control sensor, so a wideband-vs-target gap is just un-converged Learn,
# already captured by the base-fuel correction grid.
_PLATFORM_SKIP = {"holley": {"WB_VS_NB"}}


def diagnose(df: pd.DataFrame, col: dict, cfg, dc: DiagnosticConfig | None = None,
             platform: str = "gm", profile=None, cam_class=None) -> list:
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
    # context-aware detectors (need cam / profile) + idle + cold-start groups
    try:
        vl = _d_vacuum_leak(df, col, cfg, dc, warm, cam_class)
        if vl is not None and vl.id not in skip:
            findings.append(vl)
    except Exception:        # pragma: no cover - defensive
        pass
    for group, args in ((_idle_findings, (df, col, cfg, dc, warm, cam_class)),
                        (_coldstart_findings, (df, col, cfg, dc)),
                        (_boost_findings, (df, col, cfg, dc, warm, profile))):
        try:
            for f in group(*args):
                if f.id not in skip:
                    findings.append(f)
        except Exception:    # pragma: no cover - defensive
            continue
    conf_rank = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (SEVERITY_RANK.get(f.severity, 9),
                                 conf_rank.get(f.confidence, 9)))
    return findings
