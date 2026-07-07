# Tune Assist — web demo

Run the full Textual app **in a browser** so anyone can try it with zero install.

## Try it locally (terminal)
```bash
python -m tuneassist.demo        # or: tuneassist-demo
```

## Serve it on the web
```bash
pip install ".[serve]"           # installs textual-serve
python demo/serve.py             # open http://localhost:8000
# (equivalent: textual serve "python -m tuneassist.demo")
```
Each browser session gets its own isolated app process. Set `HOST`/`PORT` env
vars to change where it binds.

## What "demo mode" does
The app runs **on the server**, so the demo build is locked down:
- File access is **confined to `demo/samples/`** — the bundled logs only. The
  native file picker and arbitrary folder browsing are hidden, and analyzing any
  path outside the samples is refused.
- Each session uses a **throwaway garage** (temp file), so visitors never share
  or clobber each other's vehicles.

## Bundled sample logs (`demo/samples/`)
| File | Shows off |
| --- | --- |
| `gm_hptuners_cruise.csv` | GM/HPTuners VE correction + diagnosis (a warm cruise slice) |
| `holley_terminatorx.csv` | Holley base-fuel correction + a "richen WOT for power" opportunity |
| `boosted_gm_pull.csv` | Forced-induction findings: boost-lean, fuel-pressure drop, knock, hot IAT |

To point at a different folder of samples, set `TUNEASSIST_DEMO_SAMPLES=/path`.

> Demo only. Real tuning uses the local binary or `pipx install .` so your own
> logs and per-vehicle garage stay on your machine.
