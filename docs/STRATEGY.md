# Where to take this — notes on monetization, open source, and community

Written as a starting point for a decision, not a decision. Read it over coffee.

## Where it stands today

- Validated on six real-world configs: HP Tuners P01/P59 + LS2-era, Holley
  Terminator X, Sniper V1 and V2 — with and without trans/wideband/closed-loop.
- Ships as a one-file binary for Windows/macOS/Linux, builds in CI on a tag,
  self-updates, installs a desktop shortcut. A non-technical person can run it.
- Offline, private, recommendation-only. That's not a limitation to apologize
  for — it's the whole pitch.

## The honest competitive read

TuneView.io is the obvious comparison: cloud upload, HP-Tuners/Gen-5-leaning,
black-box output that (on the ride42 log) contradicted itself. tuneassist wins on
the things a self-tuner actually cares about:

- **Holley + swaps + older GM**, not just modern HP Tuners.
- **Transparent** — every recommendation traces to a documented reason in
  DESIGN.md, not a mystery score.
- **Offline and private** — street tuners don't want their logs on someone's
  server, and a lot of them tune where there's no signal.
- **Free.**

Don't try to out-cloud the cloud product. Lean into offline + transparent +
community-built. That's a moat they can't copy without abandoning their model.

## Monetization options, least to most aggressive

1. **Free + donations (recommended starting point).** MIT, GitHub Sponsors /
   Ko-fi button. The car community shares freely and rewards people who give
   first. This builds reputation and an audience before you ask for anything.
   Revenue is small but the goodwill compounds. FUNDING.yml is already in.

2. **Open-core / freemium.** Keep the DIY core free forever (the guy tuning his
   own truck never pays). Charge for features aimed at people who tune *for
   others* or want convenience:
   - printable/branded client reports (a shop hands the customer a PDF),
   - batch convergence across many logs / multi-car fleet view,
   - LT (Gen 5 DI) and Global B support when built,
   - optional encrypted cloud sync between a laptop and a shop machine.
   This monetizes pro/shop use without paywalling the hobbyist — which is the
   only way the community won't turn on you.

3. **Pay-what-you-want binaries / one-time license.** Source stays free; the
   convenience binary asks for an optional or fixed price. Lower goodwill than
   open-core, simpler to run. TunerStudio is the precedent here (~$60–100) and
   nobody resents it.

4. **Paid support / consulting.** "Buy an hour, I'll look at your logs." Scales
   with your time, not the product. Fine as a side stream, not a strategy.

**My read:** do #1 now (free + Sponsors), design toward #2 later if it gets
traction. Never paywall the core analysis. The moment a hobbyist hits a paywall
on "what should I change," the trust advantage over TuneView evaporates.

## Open-sourcing logistics (mostly done)

- MIT license, public-ready code, tests, DESIGN/CLAUDE docs: done.
- FUNDING.yml (Sponsor button): added.
- Still worth adding before a real launch:
  - `CONTRIBUTING.md` (how to add a platform/detector, how to drop a fixture log).
  - Issue templates — especially a "this log got a wrong call, here's the CSV"
    template, since real logs are how the detectors improve.
  - A hosted web-demo link (textual serve behind a small host) so people can try
    it without downloading anything.
  - Code signing for the binaries — unsigned triggers SmartScreen (Windows) and
    Gatekeeper (macOS) warnings that scare off non-technical users. This is the
    biggest adoption friction right now.

## Launch / community

Show, don't sell. The framing that works: *"Free, offline tool that reads your
HP Tuners or Holley log and tells you what to change — built on Kyle's Gen 3 LSx
methodology, validated on real logs."* Lead with a short screen recording.

Channels: LS1Tech, the HP Tuners forum, Holley forums, r/projectcar, r/Cartalk,
r/LSswap, and a heads-up to the YouTube tuners whose methodology it encodes
(Goat Rope Garage especially — credit them loudly; ask, don't assume). The credit
is both the right thing to do and the best marketing — "the tool that
implements Kyle's playbook" sells itself if Kyle nods at it.

## Near-term roadmap (technical)

- **Code-sign the binaries** (biggest adoption blocker).
- LT (Gen 5, DI) airflow model + Holley LT — different airflow model, deferred.
- Sniper/Dominator label variants; Holley spark on its own patterns (not GM's).
- Bump the deprecated CI actions to the Node-24-compatible versions.
- Record the demo GIF (script + instructions are in docs/images/README.md).
- Opt-in "pro tips" (pops/bangs, torque-management un-limiting) behind a clear
  "you asked for this" gate.

## One thing not to do

Don't add a "write the tune" feature, ever — not as a paid tier, not by
popular demand. The recommendation-only line is the legal and safety foundation
the whole thing stands on. It's also, quietly, the trust pitch.
