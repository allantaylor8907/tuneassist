"""
classify.py -- the symptom classifier: regex-primary, pluggable fallback.

`classify(text)` is the single entry point the rest of the app calls. It runs
the fast, exact, deterministic regex matcher (symptoms.match) FIRST; only when
that finds nothing does it consult a fallback backend for the long tail
(paraphrases, misspellings, informal phrasings). Every result carries a
`source` ("pattern" | "fuzzy" | "model") and a `score` so the UI can present a
regex hit as a confident match and a fallback hit as a softer "did you mean".

Design goals (see the offline-only-intelligence memory + Phase plan):
  - FULLY OFFLINE. The stdlib fallback here needs no deps and no model. A future
    embedding backend (ONNX MiniLM) will implement the same `Backend` protocol
    and slot in via set_backend() -- it must never require network or an API.
  - GRACEFUL DEGRADATION. No backend / a broken backend -> regex-only, never an
    error. The closed label set means a fallback can never invent a symptom.
  - The fallback is a PRIOR over the diagnostics engine, exactly like the regex
    layer: it only proposes which symptom the user described, never a finding.
"""

from __future__ import annotations
import re
from difflib import SequenceMatcher, get_close_matches

from . import symptoms
from .symptom_examples import EXAMPLES

# Fallback confidence floor. Below this a fuzzy guess is too weak to show; tuned
# against the eval harness (tests/test_symptoms_eval.py) to keep false positives
# ~zero while still recovering real paraphrases the regex misses.
# Tuned on the held-out eval (tests/test_symptoms_eval.py). With the synonym
# layer + expanded EXAMPLES, at 0.60 the stdlib fallback recovers ~87% of the
# held-out paraphrases the regex misses AND ~0.43 of the hard semantic ones
# (which a general ONNX MiniLM only reached ~0.29 on -- benchmarked), at ~1 soft
# false positive (an instrument-vs-symptom question). Residual misses are rare
# slang; grow EXAMPLES/SYNONYMS rather than reach for a model.
FUZZY_THRESHOLD = 0.60
FUZZY_MARGIN = 0.10          # keep runners-up within this of the top score
FUZZY_MAX = 3                # never propose more than this many soft guesses

# Non-informative words dropped before scoring. Deliberately SHORT -- domain
# words that look like stopwords ("hot", "cold", "low", "up", "top", "hard")
# carry real meaning here and must survive.
_STOP = frozenset("""
a an the it its i im and or but to of my me that this there so just really very
kind sorta like when if then get gets getting got some any bit been was were are
am be he she they you car truck engine motor thing something seems seem think
guess about into with your his her at on in for as is do does did has have had
how what which whats where why who should shall would could can cant use uses
using used buy need needs want wants add install put set help
""".split())

_WORD = re.compile(r"[a-z0-9]+")

# Slang / synonym -> a canonical domain word that appears in the EXAMPLES. This
# is the cheap "semantics" layer: difflib canonicalization only catches spelling
# variants (close letters), so genuinely different words for the same idea
# ("snail"->turbo, "pep"->power) need this map. Applied to BOTH the example
# vocabulary and the query, so the two sides always canonicalize the same way.
SYNONYMS = {
    # down-on-power slang
    "pep": "power", "grunt": "power", "guts": "power", "gutless": "power",
    "oomph": "power", "gusto": "power", "get-up": "power", "giddyup": "power",
    # forced induction slang -> the token boost_issue examples carry
    "snail": "turbo", "huffer": "turbo", "blower": "turbo", "boosted": "boost",
    # fuel
    "gas": "fuel", "gasoline": "fuel", "petrol": "fuel", "economy": "mileage",
    "milage": "mileage",
    # rpm / driveline
    "revs": "rpm", "rev": "rpm", "tach": "rpm",
    "tranny": "transmission", "gearbox": "transmission", "trans": "transmission",
    "slushbox": "transmission", "converter": "lockup",
    # idle quality
    "lopey": "lumpy", "lope": "lumpy", "lopy": "lumpy", "choppy": "rough",
    "loping": "lumpy",
    # knock
    "pinging": "knock", "ping": "knock", "pinking": "knock", "pings": "knock",
    "detonation": "knock", "detonating": "knock", "detonate": "knock",
    # other symptom slang
    "hiccup": "stumble", "hiccups": "stumble", "stumbles": "stumble",
    "sputtering": "misfire", "sputters": "misfire", "surges": "surge",
    "overheating": "overheat", "boiling": "overheat", "cooking": "overheat",
    "cel": "code", "mil": "code", "dtc": "code", "dtcs": "code", "codes": "code",
    "floods": "flood", "flooded": "flood", "flooding": "flood",
}


def _tokens(text: str) -> list[str]:
    """Normalize -> informative tokens. Lowercase, collapse emphatic repeats
    (reusing the regex layer's rule so "rouuugh"->"rough"), drop apostrophes so
    "won't"->"wont", split on non-alphanumerics, drop stopwords and 1-char noise,
    then map slang/synonyms onto their canonical domain word."""
    t = symptoms._REPEAT.sub(r"\1", text.lower()).replace("'", "")
    return [SYNONYMS.get(w, w) for w in _WORD.findall(t)
            if w not in _STOP and len(w) > 1]


class _FuzzyBackend:
    """Stdlib fallback: IDF-weighted keyword recall over the labeled EXAMPLES,
    blended with a whole-phrase difflib ratio. Catches misspellings and word
    reorderings that share vocabulary; it does NOT do true semantics (no shared
    words -> no match) -- that gap is exactly what the embedding backend is for,
    and the eval harness measures it."""

    def __init__(self, examples: dict[str, list[str]]):
        self.ids = list(examples)
        self.phrases = examples
        # per-symptom token sets + global IDF (a token in few symptoms is more
        # discriminative than one every symptom shares, e.g. "boost" >> "runs").
        self._toks: dict[str, set[str]] = {}
        df: dict[str, int] = {}
        for sid, phrases in examples.items():
            toks: set[str] = set()
            for p in phrases:
                toks.update(_tokens(p))
            self._toks[sid] = toks
            for w in toks:
                df[w] = df.get(w, 0) + 1
        import math
        n = max(1, len(self.ids))
        self._idf = {w: math.log(n / (1 + c)) + 1.0 for w, c in df.items()}
        self._vocab = list(df)

    def _canon(self, tok: str) -> str | None:
        """Map a query token onto the nearest known vocab token so a misspelling
        ('detonaton') still lands on the real one ('detonation'). None if nothing
        is close enough to trust."""
        if tok in self._idf:
            return tok
        near = get_close_matches(tok, self._vocab, n=1, cutoff=0.82)
        return near[0] if near else None

    # weight for a query word we don't recognize -- it still counts in the
    # denominator so a query that's mostly unknown words ("what gear ratio do I
    # have") can't score high off a single domain keyword.
    _UNKNOWN_W = 2.0

    def score(self, text: str) -> dict[str, float]:
        qtoks = _tokens(text)
        if not qtoks:
            return {}
        canon = {t: self._canon(t) for t in qtoks}
        weight = {t: (self._idf.get(canon[t], self._UNKNOWN_W) if canon[t] else self._UNKNOWN_W)
                  for t in qtoks}
        total = sum(weight.values())                 # includes UNRECOGNIZED words
        norm = " ".join(qtoks)
        out: dict[str, float] = {}
        for sid in self.ids:
            toks = self._toks[sid]
            num = sum(weight[t] for t in qtoks if canon[t] in toks)
            recall = num / total if total else 0.0
            # phrase-shape agreement: best difflib ratio against this symptom's
            # examples (rewards near-duplicate wording / order)
            phrase = max((SequenceMatcher(None, norm, " ".join(_tokens(p))).ratio()
                          for p in self.phrases[sid]), default=0.0)
            out[sid] = round(0.75 * recall + 0.25 * phrase, 4)
        return out


# The active fallback backend. Swap via set_backend() when the ONNX embedding
# model is bundled (Phase 2); None disables the fallback (regex-only).
_backend: object = _FuzzyBackend(EXAMPLES)


def set_backend(backend) -> None:
    """Install the fallback backend (must expose .score(text)->{id: float}) or
    None to disable the fallback entirely (regex-only)."""
    global _backend
    _backend = backend


def _label(sid: str) -> str:
    for c in symptoms.TAXONOMY:
        if c["id"] == sid:
            return c["label"]
    return sid


def classify(text, use_fallback: bool = True) -> list[dict]:
    """Recognize the complaint. Returns [{id, label, source, score}] in priority
    order: exact regex hits (source='pattern', score 1.0) first; if there are
    none and a fallback is available, its best guesses (source='fuzzy'/'model')
    above the confidence floor. Empty when nothing is recognized -- the caller
    says so honestly."""
    hits = symptoms.match(text)
    if hits:
        return [{"id": h["id"], "label": h["label"], "source": "pattern",
                 "score": 1.0} for h in hits]
    if not use_fallback or _backend is None or not isinstance(text, str) or not text.strip():
        return []
    try:
        scores = _backend.score(text)
    except Exception:                       # pragma: no cover - defensive; degrade to regex-only
        return []
    if not scores:
        return []
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top = ranked[0][1]
    if top < FUZZY_THRESHOLD:
        return []
    src = "model" if _is_model_backend() else "fuzzy"
    out = []
    for sid, sc in ranked[:FUZZY_MAX]:
        if sc >= FUZZY_THRESHOLD and sc >= top - FUZZY_MARGIN:
            out.append({"id": sid, "label": _label(sid), "source": src,
                        "score": round(float(sc), 3)})
    return out


def _is_model_backend() -> bool:
    return _backend is not None and type(_backend).__name__ != "_FuzzyBackend"
