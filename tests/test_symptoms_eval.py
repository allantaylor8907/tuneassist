"""
Eval harness for the symptom classifier (symptoms.match + classify fallback).

Measures, on HELD-OUT phrasings never shown to the matcher:
  - recall of the regex layer alone,
  - recall of regex + the stdlib fuzzy fallback (the lift),
  - false positives on clearly-unrelated text,
  - and, separately, recall on hard SEMANTIC paraphrases (little shared
    vocabulary) -- the gap the future offline embedding backend must close.

Run directly (`python tests/test_symptoms_eval.py`) for the threshold sweep +
scoreboard; imported as tests it asserts a floor so CI catches regressions.
This is the yardstick a Phase-2 embedding model has to beat.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tuneassist import classify, symptoms

# (phrase, expected symptom id) -- realistic, catchable by regex OR fuzzy. None
# of these appears verbatim in symptom_examples.EXAMPLES.
EVAL_POS = [
    ("the idle won't hold steady and jumps around", "rough_idle"),
    ("shaky lumpy idle sitting at the light", "rough_idle"),
    ("big flat spot right when I get on it", "hesitation"),
    ("it bogs for a beat then pulls", "hesitation"),
    ("feels gutless and slow to pull", "no_power"),
    ("no grunt up top anymore", "no_power"),
    ("I hear pinging when I lean on it", "knock"),
    ("rattly noise climbing a grade", "knock"),
    ("it bangs out the pipe on throttle", "backfire"),
    ("spitting and popping when I accelerate", "backfire"),
    ("smells like a rich fuel mixture", "runs_rich"),
    ("sooty black exhaust and a gas smell", "runs_rich"),
    ("the mixture is way too lean", "runs_lean"),
    ("wideband reads lean under throttle", "runs_lean"),
    ("temps climbing into the red", "overheat"),
    ("it gets too hot idling in traffic", "overheat"),
    ("cranks and cranks before it lights", "hard_start"),
    ("takes several tries to fire up", "hard_start"),
    ("stalls out once it's hot", "dies_hot"),
    ("won't crank back up after it heat soaks", "dies_hot"),
    ("it hunts and surges holding a steady speed", "surge_cruise"),
    ("bucking at a constant cruise", "surge_cruise"),
    ("it's missing and breaking up under load", "misfire"),
    ("intermittent stumble like a dead cylinder", "misfire"),
    ("leans right out when boost comes up", "boost_issue"),
    ("falls flat once the turbo is spooled", "boost_issue"),
    ("runs like garbage until it warms", "cold_running"),
    ("stumbly and rough on a cold morning", "cold_running"),
    ("chugs and bogs lugging it in high gear", "lugging"),
    ("no pull down low when I load it", "lugging"),
    ("crackles out the exhaust when I lift", "decel_pop"),
    ("burbling on the overrun off throttle", "decel_pop"),
    ("dies as soon as I drop it into gear", "stalls_load"),
    ("the ac kicks on and it stalls", "stalls_load"),
    ("revs hang and drop slowly after I let off", "idle_hang"),
    ("rpm floats and won't come back down", "idle_hang"),
    ("power drops out at the top of the rev range", "power_cut"),
    ("it hits a wall and quits pulling up high", "power_cut"),
    ("shudders when the converter locks", "shudder"),
    ("vibration cruising at part throttle", "shudder"),
    ("the trans slams into gear hard", "trans_shift"),
    ("won't lock the converter and it slips", "trans_shift"),
    ("check engine light just came on", "cel"),
    ("it set a lean code p0171", "cel"),
    ("fuel economy is terrible now", "bad_mpg"),
    ("it's drinking way too much gas", "bad_mpg"),
    ("the long term fuel trims keep climbing", "trims_drift"),
    ("corrections won't settle down", "trims_drift"),
    ("it floods out on startup", "flooding"),
    ("wet fouled plugs after trying to start", "flooding"),
    ("stalls until it comes up to temperature", "cold_stall"),
    ("have to hold the throttle cold or it dies", "cold_stall"),
]

# Hard SEMANTIC paraphrases -- little/no shared vocabulary with the examples.
# The stdlib fallback will likely MISS most of these; that's the point (this is
# the recall the embedding backend is meant to recover). Reported, not asserted.
EVAL_HARD = [
    ("it's a total slug, all the pep is gone", "no_power"),
    ("there's a hiccup when I ask for throttle", "hesitation"),
    ("can't keep its footing at a stoplight", "rough_idle"),
    ("everything goes sideways once it's on the snail", "boost_issue"),
    ("sounds like a coffee can full of bolts under throttle", "knock"),
    ("it nods off when I nail it", "hesitation"),
    ("guzzles like there's a hole in the tank", "bad_mpg"),
]

# Clearly unrelated -- the classifier must stay quiet (no symptom).
EVAL_NEG = [
    "what oil should I run in it",
    "where do I buy a wideband sensor",
    "how do I export the datalog to csv",
    "the paint is peeling off the hood",
    "my radio stopped working",
    "the cupholder is broken",
    "which spark plugs should I use",
    "the seats are torn up",
    "what gear ratio do I have",
    "how do I add a car to the garage",
]


def _ids(results):
    return {r["id"] for r in results}


def _recall(pairs, fn):
    hit = sum(1 for text, exp in pairs if exp in _ids(fn(text)))
    return hit / len(pairs), hit, len(pairs)


def _measure(threshold=None):
    if threshold is not None:
        classify.FUZZY_THRESHOLD = threshold
    regex = lambda t: [{"id": m["id"]} for m in symptoms.match(t)]
    full = classify.classify
    pos_r = _recall(EVAL_POS, regex)
    pos_f = _recall(EVAL_POS, full)
    hard_f = _recall(EVAL_HARD, full)
    fp = [t for t in EVAL_NEG if full(t)]
    return {"pos_regex": pos_r, "pos_full": pos_f, "hard_full": hard_f,
            "false_pos": fp}


def test_fuzzy_fallback_lifts_recall_without_false_positives():
    from tuneassist.classify import FUZZY_THRESHOLD          # the shipped value
    m = _measure(FUZZY_THRESHOLD)
    pr, pf = m["pos_regex"][0], m["pos_full"][0]
    # the fallback roughly doubles recall on held-out phrasings the regex misses
    assert pf > pr + 0.2, f"insufficient lift: regex {pr:.2f} vs full {pf:.2f}"
    assert pf >= 0.82, f"regex+fuzzy recall too low: {pf:.2f}"
    # the synonym layer + expanded EXAMPLES pull real semantic paraphrases in
    # (this used to be 0.00) -- lock the gain in so a regression can't erase it
    assert m["hard_full"][0] >= 0.30, f"hard-paraphrase recall regressed: {m['hard_full'][0]:.2f}"
    # ...while staying quiet on unrelated text (a soft fuzzy hit shows as a
    # low-confidence "did you mean", so we allow at most one residual)
    assert len(m["false_pos"]) <= 1, f"too many false positives: {m['false_pos']}"


def test_eval_phrases_are_held_out():
    # never train on the test set: no eval phrase may appear verbatim in EXAMPLES
    from tuneassist.symptom_examples import EXAMPLES
    pool = {p.lower().strip() for phrases in EXAMPLES.values() for p in phrases}
    leaked = [t for t, _ in (EVAL_POS + EVAL_HARD) if t.lower().strip() in pool]
    leaked += [t for t in EVAL_NEG if t.lower().strip() in pool]
    assert not leaked, f"eval phrases leaked into EXAMPLES: {leaked}"


def test_every_symptom_has_examples():
    tax = {c["id"] for c in symptoms.TAXONOMY}
    from tuneassist.symptom_examples import EXAMPLES
    assert set(EXAMPLES) == tax, tax.symmetric_difference(set(EXAMPLES))
    assert all(len(v) >= 6 for v in EXAMPLES.values())


if __name__ == "__main__":
    print("threshold sweep (pos = held-out paraphrases, neg = unrelated):\n")
    print(f"{'thr':>5} {'regex':>7} {'+fuzzy':>7} {'lift':>6} {'hard':>6} {'FPs':>5}")
    base = None
    for thr in [0.50, 0.52, 0.54, 0.56, 0.58, 0.60, 0.62, 0.64]:
        m = _measure(thr)
        pr, pf, hf = m["pos_regex"][0], m["pos_full"][0], m["hard_full"][0]
        if base is None:
            base = pr
        print(f"{thr:>5.2f} {pr:>7.2f} {pf:>7.2f} {pf-pr:>+6.2f} {hf:>6.2f} {len(m['false_pos']):>5}")
    print("\nregex-only misses that the fuzzy fallback recovers (at 0.58):")
    _measure(0.58)
    for text, exp in EVAL_POS:
        got_r = exp in _ids([{"id": x["id"]} for x in
                             [{"id": m["id"]} for m in symptoms.match(text)]])
        res = classify.classify(text)
        got_f = exp in _ids(res)
        if got_f and not got_r:
            src = next((r["source"], r["score"]) for r in res if r["id"] == exp)
            print(f"  + {exp:13} {src}  <- {text!r}")
    print("\nhard semantic paraphrases still missed (the embedding backend's job):")
    for text, exp in EVAL_HARD:
        if exp not in _ids(classify.classify(text)):
            print(f"  - {exp:13} <- {text!r}")
