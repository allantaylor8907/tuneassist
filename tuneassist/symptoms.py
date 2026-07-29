"""
symptoms.py -- "what's it doing?" free-text complaint -> analysis focus.

Fully OFFLINE by design: a curated symptom taxonomy with synonym patterns, no
network, no model, no API. The user types (or dictates -- Windows Win+H works
in any text box) what the car is doing in their own words; we recognize the
complaint, pin the diagnostic findings that speak to it, and -- the important
part -- say when the LOG doesn't even cover the situation they're describing
("you said it stumbles at WOT, but this log never gets above 60 kPa").

This is a prior over the existing diagnostics engine, not a new one: matching
never invents a finding, it only reorders/relates what the detectors produced
and points out coverage gaps. Unrecognized text is fine -- we say so and run
the normal analysis untouched.
"""

from __future__ import annotations
import re

import pandas as pd

# Cheap typo tolerance: collapse 3+ repeated LETTERS to one ("boggg" -> "bog",
# "rouuugh" -> "rough") so emphatic spellings still hit the stems (English has
# essentially no real triple letters). Digits are left alone so "3000 rpm"
# survives. Real misspellings (transpositions like "stumbel") are out of scope
# for the regex layer -- that's where a future local embedding model would earn
# its keep.
_REPEAT = re.compile(r"([a-z])\1{2,}")

# Negation guard. Unambiguous negators ONLY, and only when they sit immediately
# before the match ("doesn't ping", "no longer stalls") or a "gone" marker
# follows ("pings ... anymore"). A missed negation merely over-pins a finding
# that's still in the report; over-negating would DROP a real complaint, so we
# stay conservative -- note there is no bare "no"/"none" (would eat "no power").
_NEG_BEFORE = re.compile(
    r"\b(?:not|never|no longer|hardly|barely|used to|stopped|quit|"
    r"doesn'?t|didn'?t|don'?t|isn'?t|wasn'?t|aren'?t|won'?t|can'?t|"
    r"hasn'?t|haven'?t|without)\b\W*$")
_NEG_AFTER = re.compile(r"^\W*(?:anymore|no more|went away|all gone|fixed now)\b")


def _negated(text: str, start: int, end: int) -> bool:
    """True if the match at [start:end] is inside a negation. Small windows keep
    'no matter'/'not sure if' from over-firing -- the negator must be the last
    token before the hit, or a 'gone' marker the first token after it."""
    before = text[max(0, start - 20):start]
    after = text[end:end + 14]
    return bool(_NEG_BEFORE.search(before)) or bool(_NEG_AFTER.match(after))


# Each symptom: how users say it (patterns, case-insensitive), which diagnostic
# finding ids speak to it, which log REGION must be covered to investigate it,
# and what to capture when that region is missing. Multiple symptoms can match
# one complaint ("idles rough and smells rich") -- all of them apply.
TAXONOMY = [
    {"id": "rough_idle", "label": "rough / hunting idle",
     "patterns": [r"rough(?:\s+\w+)? idle", r"idle[^.]*\b(?:rough|hunt\w*|surg\w*|bounc\w*|erratic|unstable|all over)",
                  r"\bhunt(?:s|ing)?\b", r"lop\w+ (?:bad|too much|like crazy)",
                  r"won'?t (?:hold an? )?idle", r"(?:dies|stalls?)[^.]*\b(?:idle|at (?:a )?(?:stop|light|red))",
                  r"idle[^.]*\b(?:dies|stalls?)"],
     "finding_ids": ["IDLE_HUNT", "IDLE_LOW", "IDLE_HIGH", "IDLE_LEAN", "IDLE_RICH",
                     "IDLE_AIRFLOW_OFF", "IAC_CLOSED", "IDLE_TIMING_SWING",
                     "VACUUM_LEAK", "ROLLING_IDLE_HANG"],
     "region": "idle",
     "capture": "let it sit and idle fully warmed up for 2-3 minutes in the log"},

    {"id": "hesitation", "label": "bog / stumble on throttle",
     "patterns": [r"\bbogs?\b", r"\bbogg\w+", r"stumbl\w+", r"hesitat\w+",
                  r"falls? (?:on|flat on) its face", r"dead spot", r"tip.?in",
                  r"when i (?:floor|stab|hit|punch) (?:it|the)"],
     "finding_ids": ["WOT_LEAN", "WOT_RICH", "LEAN_CRUISE", "VACUUM_LEAK",
                     "TRIM_CLIPPING", "LOW_FUEL_PRESSURE"],
     "region": "wot",
     "capture": "capture a few brisk accelerations and at least one full pull"},

    {"id": "no_power", "label": "down on power",
     "patterns": [r"down on power", r"no power", r"low on power", r"sluggish",
                  r"won'?t rev", r"runs? out of (?:steam|breath)", r"soft (?:up top|on top)",
                  r"falls? flat", r"doesn'?t pull"],
     "finding_ids": ["WOT_SHORTFALL", "WOT_RICH", "WOT_LEAN", "TIMING_BELOW_COMMAND",
                     "INJ_DUTY", "TRANS_SHIFT_EARLY"],
     "region": "wot",
     "capture": "capture a wide-open-throttle pull through the rev range"},

    {"id": "knock", "label": "knock / ping",
     "patterns": [r"knock\w*", r"\bping\w*", r"detonat\w*", r"rattl\w+",
                  r"(?:sounds? like )?marbles"],
     "finding_ids": ["KNOCK", "TIMING_BELOW_COMMAND", "HIGH_IAT", "WOT_LEAN", "BOOST_IAT"],
     "region": "wot",
     "capture": "log knock retard and capture the load/RPM where you hear it"},

    {"id": "backfire", "label": "backfires / pops",
     "patterns": [r"backfir\w*", r"afterfir\w*", r"\bpops?\b", r"popping",
                  r"\bbangs?\b(?!\s+(?:into|in)\b)", r"sputter\w*", r"spits? (?:and|back)"],
     "finding_ids": ["LEAN_CRUISE", "WOT_LEAN", "IDLE_LEAN", "VACUUM_LEAK", "RICH_CRUISE"],
     "region": None, "capture": None},

    {"id": "runs_rich", "label": "running rich",
     "patterns": [r"smells? (?:like )?(?:gas|fuel|rich)", r"black smoke",
                  r"foul\w* (?:the )?plugs?", r"eyes (?:burn|water)", r"\brich\b"],
     "finding_ids": ["RICH_CRUISE", "IDLE_RICH", "WOT_RICH", "WARMUP_RICH",
                     "ENRICH_NOT_DECAYED"],
     "region": None, "capture": None},

    {"id": "runs_lean", "label": "running lean",
     "patterns": [r"\blean\b", r"white plugs?"],
     "finding_ids": ["LEAN_CRUISE", "IDLE_LEAN", "WOT_LEAN", "VACUUM_LEAK", "BOOST_LEAN"],
     "region": None, "capture": None},

    {"id": "overheat", "label": "running hot",
     "patterns": [r"overheat\w*", r"runs? hot", r"runn\w+ hot",
                  r"temp\w*[^.]*\b(?:climb|creep|high|pegg?)", r"coolant[^.]*hot"],
     "finding_ids": ["OVERHEAT", "THERMOSTAT", "HIGH_IAT"],
     "region": None, "capture": None},

    {"id": "hard_start", "label": "hard starting / no start",
     "patterns": [r"hard (?:to )?start\w*", r"long crank\w*",
                  r"(?:won'?t|will not|wont|can'?t|cannot) (?:start|fire|catch|crank|turn over)",
                  r"cranks? but", r"no.?start", r"slow crank\w*",
                  r"takes? (?:forever|a while|a few tries) to (?:start|fire|catch)"],
     "finding_ids": ["NOSTART_NO_INJECTION", "NOSTART_LOW_FUEL_PRESSURE", "NOSTART_FLOODED",
                     "NOSTART_STARVED", "NOSTART_SYNC_SUSPECT", "NOSTART_SLOW_CRANK",
                     "NOSTART_LOG_MORE", "STARTUP_FLARE", "STARTUP_SAG", "LOW_FUEL_PRESSURE"],
     "region": "crank",
     "capture": "log from key-ON through the whole start attempt (don't start logging after it's running)"},

    {"id": "dies_hot", "label": "stalls / won't restart when hot",
     "patterns": [r"(?:dies|stalls?)[^.]*\b(?:hot|heat)\b",
                  r"(?:dies|stalls?)[^.]*\b(?:once|when|after)[^.]*\bwarm(?:ed)?\b",
                  r"hot.?start",
                  r"won'?t (?:re)?start[^.]*hot", r"(?:when|once) (?:it'?s )?hot[^.]*\b(?:dies|stalls?)"],
     "finding_ids": ["HIGH_IAT", "LOW_FUEL_PRESSURE", "IDLE_LOW", "IAC_CLOSED", "LOW_VOLTAGE"],
     "region": None, "capture": None},

    {"id": "surge_cruise", "label": "surging / bucking at cruise",
     "patterns": [r"surg\w+[^.]*\b(?:cruise|speed|highway|freeway|steady)",
                  r"(?:cruise|highway|steady)[^.]*surg\w+", r"buck\w+", r"jerk\w+"],
     "finding_ids": ["TRIM_OSCILLATION", "LEAN_CRUISE", "WB_VS_NB", "TCC_SLIP",
                     "TCC_NOT_LOCKING"],
     "region": "cruise",
     "capture": "hold a steady cruise (light throttle, constant speed) for a minute or two"},

    {"id": "misfire", "label": "misfire / breaking up",
     "patterns": [r"misfir\w*", r"\bmisses\b", r"\bmiss\b", r"breaks? up|breaking up",
                  r"skips?\b", r"stutter\w*", r"cuts? out"],
     "finding_ids": ["BANK_IMBALANCE", "LOW_VOLTAGE", "WOT_LEAN", "TRIM_CLIPPING",
                     "BOOST_LEAN"],
     "region": None, "capture": None},

    {"id": "boost_issue", "label": "trouble under boost",
     "patterns": [r"(?:under|in|on|into) boost", r"boost[^.]*\b(?:falls?|flat|breaks?|cuts?|lean)",
                  r"spark blow.?out"],
     "finding_ids": ["BOOST_LEAN", "FUEL_PRESSURE_DROP", "MAP_SENSOR_RANGE",
                     "CL_IN_BOOST", "BOOST_IAT"],
     "region": "boost",
     "capture": "capture a pull that actually gets into boost (MAP above ~105 kPa)"},

    {"id": "cold_running", "label": "runs poorly cold / warming up",
     "patterns": [r"(?:when|until|till|while) (?:it'?s )?(?:cold|warm\w+ up)",
                  r"cold.?start\w*", r"warm.?up", r"first (?:start|thing) in the morning"],
     "finding_ids": ["WARMUP_RICH", "WARMUP_LEAN", "ENRICH_NOT_DECAYED",
                     "STARTUP_FLARE", "STARTUP_SAG", "THERMOSTAT"],
     "region": "warmup",
     "capture": "log a cold start from key-on and let it warm up on camera (in the log)"},

    {"id": "lugging", "label": "lugging / chuggy under light load",
     "patterns": [r"\blug\w*", r"chug\w*", r"loads? up", r"loading up",
                  r"won'?t pull (?:from|down low|off idle|in \w+ gear)",
                  r"doesn'?t (?:want to )?pull (?:down low|from (?:down )?low)",
                  r"(?:flat|soft|dead)[^.]*\b(?:down low|low.?end|bottom end)"],
     "finding_ids": ["LEAN_CRUISE", "RICH_CRUISE", "TIMING_BELOW_COMMAND", "KNOCK"],
     "region": "cruise",
     "capture": "log some light-throttle low-RPM lugging (high gear, gentle load)"},

    {"id": "decel_pop", "label": "pops / crackles on decel",
     "patterns": [r"decel\w*[^.]*\b(?:pop|crackl|burbl|bang|snap)",
                  r"(?:pop|crackl|burbl|snap)\w*[^.]*\b(?:decel|let(?:ting)? off|lift\w* off|off.?throttle|coast\w*|overrun)",
                  r"(?:exhaust|it) (?:pops?|crackl\w+|burbl\w+)[^.]*\b(?:let off|decel|coast|off throttle)",
                  r"overrun (?:pop|fuel|crackl)"],
     "finding_ids": ["LEAN_CRUISE", "ENRICH_NOT_DECAYED", "VACUUM_LEAK"],
     "region": None, "capture": None},

    {"id": "stalls_load", "label": "stalls in gear / with AC / at a stop",
     "patterns": [r"(?:stalls?|dies|quits?)[^.]*\b(?:in gear|in drive|in reverse|coming to (?:a )?stop|at a (?:stop|light)|when i (?:brake|stop|slow|turn)|braking|turning|put it in)",
                  r"(?:a/?c|air ?conditioning)[^.]*\b(?:stall|dies|kills?|drops? (?:the )?idle|kills? (?:the )?idle)",
                  r"(?:stalls?|dies)[^.]*\b(?:a/?c|air ?conditioning) on",
                  r"drops? (?:the )?idle (?:in gear|with (?:the )?a/?c|when)"],
     "finding_ids": ["IDLE_LOW", "IAC_CLOSED", "IDLE_AIRFLOW_OFF", "IDLE_HUNT",
                     "ROLLING_IDLE_HANG"],
     "region": "idle",
     "capture": "log idle in gear (brake held) and with the A/C on, warmed up"},

    {"id": "idle_hang", "label": "RPM hangs / slow to return to idle",
     "patterns": [r"(?:rpm|idle|revs?)[^.]*\b(?:hang\w*|stays? up|won'?t (?:come|settle|drop) (?:down|back)|slow to (?:come down|return|drop|settle))",
                  r"hangs? (?:up )?(?:at|around|near) \d{3,4}",
                  r"won'?t idle (?:back )?down", r"revs? hang\w*",
                  r"slow to (?:return|come back|drop|settle)[^.]*idle"],
     "finding_ids": ["ROLLING_IDLE_HANG", "IDLE_HIGH", "IDLE_TIMING_SWING", "IAC_CLOSED"],
     "region": "idle",
     "capture": "blip the throttle and let it settle in the log (watch RPM come back down)"},

    {"id": "power_cut", "label": "cuts out / hits a wall up top",
     "patterns": [r"cuts? out[^.]*\b(?:at|when|under|wide open|wot|high rpm|up top|\d)",
                  r"(?:power|fuel|spark|ignition) cut\w*", r"limp (?:mode|home)",
                  r"hits? (?:a )?(?:wall|brick wall|rev limit\w*|limiter)",
                  r"(?:falls?|drops?|noses?) (?:on its face|over|off)[^.]*\b(?:at|around|past|up top|\d)",
                  r"(?:breaks? up|breaking up)[^.]*\b(?:up top|high rpm|top end|at \d)",
                  r"won'?t (?:pull|go|rev) past \d"],
     "finding_ids": ["INJ_DUTY", "WOT_LEAN", "TIMING_BELOW_COMMAND",
                     "LOW_FUEL_PRESSURE", "FUEL_PRESSURE_DROP"],
     "region": "wot",
     "capture": "log a full pull to where it cuts (catch fuel pressure + injector duty)"},

    {"id": "shudder", "label": "shudder / vibration at cruise",
     "patterns": [r"shudder\w*", r"judder\w*", r"lock.?up shudder",
                  r"tcc (?:shudder|slip\w*)",
                  r"(?:shakes?|shaking|vibrat\w+|shimm\w+)[^.]*\b(?:cruise|light throttle|part throttle|lock.?up|highway|steady|at speed|\d\d ?mph|under load)"],
     "finding_ids": ["TCC_SLIP", "TCC_NOT_LOCKING", "LEAN_CRUISE", "BANK_IMBALANCE"],
     "region": "cruise",
     "capture": "log a steady light-throttle cruise where the converter is locked"},

    {"id": "trans_shift", "label": "harsh / wrong shifts, no lockup",
     "patterns": [r"shifts?(?: \w+){0,2} (?:hard|harsh|early|late|weird|erratic|rough|firm|soft|mushy)",
                  r"(?:bangs?|slams?|clunks?) (?:into )?(?:gear|shifts?|\dnd|\drd)",
                  r"(?:harsh|hard|firm|soft|mushy|slipping|flar\w+) shift\w*",
                  r"won'?t (?:lock ?up|lock the (?:torque )?converter)",
                  r"(?:trans\w*|transmission|gearbox|converter)[^.]*\b(?:slip\w*|shift\w*|hunt\w*|flar\w+|won'?t lock|unlock\w*)"],
     "finding_ids": ["TRANS_SHIFT_EARLY", "TCC_NOT_LOCKING", "TCC_SLIP", "LINE_PRESSURE_FLAT"],
     "region": "cruise",
     "capture": "log some normal-throttle up/down shifts and a highway cruise (converter lock)"},

    {"id": "cel", "label": "check engine light / codes",
     "patterns": [r"check engine", r"\bcel\b", r"\bmil\b", r"engine light",
                  r"service engine", r"(?:throw\w*|threw|set\w*|has|got|pending) (?:a )?(?:code|codes|dtc)",
                  r"\bp0\d{3}\b"],
     "finding_ids": ["LOGGING_TIPS", "LEAN_CRUISE", "RICH_CRUISE", "TRIM_CLIPPING"],
     "region": None, "capture": None},

    {"id": "bad_mpg", "label": "bad fuel economy",
     "patterns": [r"(?:bad|terrible|awful|poor|horrible|crap\w*|worse|worst|low|lousy) (?:gas ?)?(?:mileage|mpg|economy|fuel economy)",
                  r"drink\w+ (?:gas|fuel)", r"guzzl\w+",
                  r"(?:eats?|eating|burns?|burning|going through|goes through) (?:a lot of |through )?(?:gas|fuel)",
                  r"\d\d?\s?mpg"],
     "finding_ids": ["RICH_CRUISE", "IDLE_RICH", "WOT_RICH", "ENRICH_NOT_DECAYED"],
     "region": "cruise",
     "capture": "log a steady cruise (that's where fuel economy lives)"},

    {"id": "trims_drift", "label": "fuel trims drifting / won't settle",
     "patterns": [r"(?:trims?|ltft|stft|fuel trims?)[^.]*\b(?:drift\w*|climb\w*|swing\w*|jump\w*|all over|keep\w* (?:changing|moving)|maxed|pegg\w+|won'?t settle|never settle)",
                  r"(?:long|short).?term (?:fuel )?trims?[^.]*\b(?:high|low|negative|maxed|-?\d\d)",
                  r"(?:re-?learn\w*|relearn\w*)[^.]*\b(?:bad|worse|off|wrong|rich|lean)",
                  r"trims? (?:won'?t|never|don'?t) settle"],
     "finding_ids": ["TRIM_OSCILLATION", "TRIM_CLIPPING", "WB_VS_NB", "LEAN_CRUISE"],
     "region": "cruise",
     "capture": "log a long steady cruise so the trims have time to move"},

    {"id": "flooding", "label": "floods / loads up on start",
     "patterns": [r"flood\w+", r"smells? flooded",
                  r"(?:wet|fouled|black|soaked) plugs? (?:after|when|on) (?:crank|start)",
                  r"(?:dumps?|dumping) fuel (?:on|at|during) (?:start|crank)",
                  r"washes? (?:down )?(?:the )?(?:cylinders?|bores?|walls?)"],
     "finding_ids": ["NOSTART_FLOODED", "WARMUP_RICH", "IDLE_RICH"],
     "region": "crank",
     "capture": "log from key-ON through the start attempt (catch injector pulse + AFR)"},

    {"id": "cold_stall", "label": "stalls / won't run until warm",
     "patterns": [r"(?:stalls?|dies|quits?)[^.]*\b(?:until (?:it'?s? )?warm\w*|when (?:it'?s? )?cold|dead cold|first start\w*)",
                  r"(?:won'?t|has to|have to|gotta) (?:idle|stay running|run|keep it running)[^.]*\b(?:until (?:it'?s? )?warm\w*|when cold|cold)",
                  r"(?:have to|has to|gotta) (?:feather|hold|pump|nurse|two.?foot) (?:the )?(?:throttle|gas|pedal)[^.]*\b(?:cold|until (?:it'?s? )?warm)",
                  r"(?:stalls?|dies)[^.]*\bcold\b"],
     "finding_ids": ["WARMUP_LEAN", "IDLE_LOW", "THERMOSTAT", "STARTUP_SAG", "ENRICH_NOT_DECAYED"],
     "region": "warmup",
     "capture": "log a cold start from key-on and keep logging until it idles on its own"},
]

REGION_LABEL = {"idle": "warm idle", "wot": "wide-open throttle / high load",
                "cruise": "steady cruise", "crank": "the crank/start window",
                "boost": "boost", "warmup": "a cold start / warmup"}


def match(text) -> list:
    """Recognize symptoms in free text. Returns [{'id','label'}] in taxonomy
    order (empty when nothing matches -- the caller says so, honestly). Emphatic
    typos are normalized, and a negated mention ('doesn't ping anymore') does not
    count -- a symptom needs at least one NON-negated hit to be recognized."""
    if not isinstance(text, str) or not text.strip():
        return []
    t = " " + _REPEAT.sub(r"\1", text.lower().strip()) + " "
    out = []
    for s in TAXONOMY:
        for p in s["patterns"]:
            if any(not _negated(t, m.start(), m.end()) for m in re.finditer(p, t)):
                out.append({"id": s["id"], "label": s["label"]})
                break
    return out


def region_coverage(df, col, cfg) -> dict:
    """Which log regions have enough samples to investigate. Coarse on purpose:
    this powers 'your log doesn't cover what you described', not the analysis."""
    rpm = pd.to_numeric(df[col["rpm"]], errors="coerce") if "rpm" in col else None
    mp = pd.to_numeric(df[col["map"]], errors="coerce") if "map" in col else None
    tps = pd.to_numeric(df[col["tps"]], errors="coerce") if "tps" in col else None
    ect_key = "ect" if "ect" in col else ("cts" if "cts" in col else None)
    ect = pd.to_numeric(df[col[ect_key]], errors="coerce") if ect_key else None
    wot_min = getattr(cfg, "wot_map_min", 80.0)

    cov = {}
    if rpm is None:
        return {k: False for k in REGION_LABEL}
    idle = (rpm > 400) & (rpm < 1300)
    if tps is not None:
        idle &= tps < 8
    cov["idle"] = int(idle.sum()) >= 50
    cov["wot"] = mp is not None and int((mp >= wot_min).sum()) >= 20
    cruise = (rpm >= 1300) & (rpm <= 3200)
    if mp is not None:
        cruise &= (mp >= 25) & (mp <= 75)
    cov["cruise"] = int(cruise.sum()) >= 100
    cov["crank"] = int(((rpm > 40) & (rpm < 400)).sum()) >= 5
    cov["boost"] = mp is not None and int((mp > 105).sum()) >= 20
    # warmup needs the log to START cold -- a warm median start can't show it
    cov["warmup"] = ect is not None and len(ect.dropna()) > 10 \
        and float(ect.dropna().iloc[:50].median()) < 140.0
    return cov


def relate(matched: list, findings: list, coverage: dict):
    """(related finding ids present in THIS analysis, coverage-gap messages)."""
    by_id = {s["id"]: s for s in TAXONOMY}
    present = [f.id for f in findings]
    related, gaps = [], []
    for m in matched:
        s = by_id.get(m["id"])
        if not s:
            continue
        for fid in s["finding_ids"]:
            if fid in present and fid not in related:
                related.append(fid)
        region = s.get("region")
        if region and not coverage.get(region, False):
            gaps.append(f"You described {s['label']}, but this log has little to no "
                        f"{REGION_LABEL[region]} in it -- {s['capture']}, then re-analyze.")
    return related, gaps


def reorder(findings: list, related_ids: list) -> list:
    """Pin the findings that speak to the complaint to the front (stable: they
    keep their severity order among themselves, as do the rest)."""
    if not related_ids:
        return findings
    rel = [f for f in findings if f.id in related_ids]
    rest = [f for f in findings if f.id not in related_ids]
    return rel + rest
