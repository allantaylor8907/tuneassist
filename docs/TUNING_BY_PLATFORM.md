# Tuning differences by platform & architecture

Research notes that turn our detections into *platform-correct* remedies. Pairs
with docs/PLATFORMS.md (the three axes). Sources at the bottom.

The headline: **our default journey (tune VE in speed-density with the MAF off,
then the MAF curve) is specifically a GEN 3 workflow.** Gen 4 pros tune MAF-only;
Gen 5 is direct-injection + torque-targeted. So the architecture axis must change
the *prescribed workflow*, not just labels.

---

## Holley (platform: holley)

Base calibration is **VE-based**: the ECU computes fuel from displacement + MAT
to hit a **Target AFR**, then a **Learn** table self-corrects, with closed-loop
compensation on the integrated wideband. There are no GM-style STFT/LTFT.

- **Sniper** — throttle-body (TBI), handheld, designed to self-tune; the simplest
  case. Cruise AFR target ~13.5–15.5 (richer for big cams). Get actual within
  ~1–1.5 AFR of target at startup, then let it learn.
- **Terminator X / Max** — multiport, deeper table access, laptop-tunable; same
  VE+Learn model with more control (idle, timing, transmission, boost).

**Remedy mapping (what we detect → what to do):**
- lean/rich cruise → don't chase trims; let **Learn** converge, then **Transfer
  Learn to Base Fuel (no smoothing)** and reset Learn. (Already our Holley path.)
- idle hunt / lopey cam → richer idle Target AFR, IAC/throttle stop, idle timing.
- knock → pull timing (Holley spark currently borrows the GM logic).

---

## HP Tuners + GM, by architecture

### gm_gen3_ls — P01 / P59 / 0411 (E40 transitional), ~1997–2006, 24x
- Airflow: **MAF + a real speed-density VE table (RPM × MAP)** you edit directly.
- **Workflow = our default:** disable MAF → dial the **Main VE table** in SD →
  re-enable MAF → tune the **MAF curve (Hz)**.
- Caveats: P01 ECMs are fragile; the E40 has a split MAF table and a ~64 lb/hr
  injector limit.
- Remedy: lean/rich cruise → Main VE (multiply-by-trim%), then MAF; WOT → PE /
  commanded AFR + injector flow; knock → high-octane spark table.

### gm_gen4_ls — E38 / E67 / E78, ~2006–2016, 58x, DBW, VVT/AFM, torque-based
- Airflow: **Virtual VE (VVE)** — a coefficient/polynomial model, *not* a simple
  table — plus **dynamic airflow** blending MAF and SD.
- **Pro approach = MAF-only** (especially on a cammed engine): set **Dynamic
  Airflow High-RPM Disable = 0** to take VVE out of the loop, run MAF-only, map
  **AFR error vs MAF frequency** (low/high ranges), iterate to ~0–3% error, then
  re-enable closed loop with VE still off. Don't try to back-calculate VVE.
- Gen 4 **likes less timing** (~23° max, CR-dependent) and a slightly **leaner
  WOT (~12.5)** vs Gen 3's ~12.8–12.9.
- Torque management (virtual torque / max-torque tables) can cap power.
- Remedy: lean/rich cruise → **tune the MAF curve**, not the VE table. (Our
  "VE-first" prescription is WRONG for Gen 4 — it should prescribe MAF-only +
  disable dynamic airflow.)

### gm_gen5_lt — E92 / E99, 2016+, direct injection
- **Direct injection**: high-pressure fuel system + DI injector model + rail
  pressure; fueling isn't a VE/MAF paste the same way. Stock LT1 injectors ~600
  rwhp, LT4 ~800.
- **Driver-demand torque targeting** actively *fights* mods — it pulls torque
  back toward stock, so torque-management / driver-demand tables must be addressed
  or the tune "undoes" itself.
- More knock-sensitive; DI timing is its own table.
- Remedy: VE/MAF correction still informs airflow, but expect to also raise
  torque limits / driver-demand and respect the DI fuel-system ceiling.

---

## Detection -> remedy, by architecture (the payoff)

| We detect | Gen 3 (VE table) | Gen 4 (VVE/MAF) | Gen 5 (LT/DI) | Holley |
|---|---|---|---|---|
| Lean/rich cruise | edit Main VE, then MAF | tune MAF curve (VVE off) | airflow + check DI ceiling | Transfer Learn → Base Fuel |
| WOT lean/shortfall | PE / cmd AFR, injectors | PE, leaner ~12.5 | DI rail/injectors, torque limit | Base Fuel + Target AFR |
| Knock | high-oct spark | less timing (~23° cap) | DI-sensitive, pull + cool | pull timing |
| Idle hunt (cam) | idle VE/air/spark | idle MAF + spark | idle airflow + torque | idle Target AFR + IAC |
| Power capped | n/a | max-torque tables | driver-demand/torque mgmt | rev/torque limits |

This table is the spec for the per-architecture strategy modules in Phase 2.

## Sources
- The Tuning School — Differences & Applications of GM ECMs (Gen III/IV/V):
  https://thetuningschool.com/blogs/news/differences-and-applications-of-gm-ecms
- HP Tuners forum — Gen 4 vs Gen 3 tuning:
  https://forum.hptuners.com/showthread.php?26196
- HP Tuners forum — "Starting over Again" (Gen 4 MAF-only, dynamic-airflow disable):
  https://forum.hptuners.com/showthread.php?24809
- High Performance Academy — GM Gen V LT torque-based tuning:
  https://www.hpacademy.com/blog/gm-gen-v-lt-torque-based-tuning-course/
- Holley / EFISystemPro / JEGS — Sniper vs Terminator X self-tuning & targets:
  https://www.jegs.com/tech-articles/holley-sniper-efi-vs-terminator-efi-whats-the-difference/
