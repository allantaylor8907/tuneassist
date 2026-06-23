"""Generate the README screenshots of the v2 desktop GUI as SVGs.

Like the retired tools/capture_screens.py did for the Textual TUI, this renders
the *current* product into committed, crisp, regenerable images -- but for the
HTML/ECharts GUI we can't headlessly export the live DOM, so we redraw the GUI's
look (its real dark-theme palette + fonts) in SVG and fill the report with REAL
analysis output from tests/fixtures/ride42.csv. Re-run after UI changes:

    python tools/capture_gui.py

Writes docs/images/{garage,setup-axes,report}.svg.
"""
from __future__ import annotations
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("TUNEASSIST_NO_UPDATE_CHECK", "1")

from tuneassist.core import SessionOpts, analyze_log          # noqa: E402
from tuneassist.engine_gm import Config                        # noqa: E402

OUT = os.path.join(ROOT, "docs", "images")
RPM = [400, 800, 1200, 1600, 2000, 2400, 2800, 3200, 3600, 4000,
       4400, 4800, 5200, 5600, 6000, 6400, 6800, 7200, 7600, 8000]
MAP = [15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100, 105]

# --- the GUI's dark theme (tuneassist/gui/static/css/app.css) -----------------
C = dict(bg="#0f1217", bg2="#171b22", bg3="#1f242d", line="#262c36", line2="#333b47",
         text="#e8ebf0", text2="#9aa4b2", text3="#6b7585", accent="#4f8df9",
         accentSoft="#1a2740", crit="#f0564f", warn="#e8a33d", opp="#3fbf6f",
         add="#ffab40", pull="#64b5ff")
FONT = "Segoe UI Variable Text, Segoe UI, system-ui, sans-serif"
MONO = "Cascadia Code, Consolas, monospace"


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def rect(x, y, w, h, fill, r=10, stroke=None, sw=1, op=1):
    s = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
    o = f' opacity="{op}"' if op != 1 else ""
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}"{s}{o}/>'


def txt(x, y, s, fill=None, size=14, weight=400, anchor="start", font=FONT, op=1):
    fill = fill or C["text"]
    o = f' opacity="{op}"' if op != 1 else ""
    return (f'<text x="{x}" y="{y}" fill="{fill}" font-family="{font}" '
            f'font-size="{size}" font-weight="{weight}" text-anchor="{anchor}"{o}>{esc(s)}</text>')


def chip(x, y, label, accent=False):
    w = 16 + len(label) * 7
    bg, fg = (C["accentSoft"], C["accent"]) if accent else (C["bg3"], C["text2"])
    return (rect(x, y, w, 22, bg, r=11) +
            txt(x + w / 2, y + 15, label, fg, 12, 550, "middle")), w + 7


def brand(x, y):
    cells = [(0, 0, C["line2"]), (1, 0, C["line2"]), (2, 0, "#EF9F27"),
             (0, 1, C["line2"]), (1, 1, "#FAC775"), (2, 1, C["line2"]),
             (0, 2, "#378ADD"), (1, 2, C["line2"]), (2, 2, C["line2"])]
    g = [rect(x, y, 34, 34, "#2a3240", r=9)]
    for cx, cy, col in cells:
        g.append(rect(x + 6 + cx * 8, y + 6 + cy * 8, 6, 6, col, r=1.6))
    g.append(txt(x + 44, y + 22, "tune", C["text"], 16, 650))
    g.append(txt(x + 44 + 34, y + 22, "assist", C["text3"], 16, 550))
    return "".join(g)


def sidebar(active):
    items = [("⌂", "Garage", "garage"), ("↗", "Analyze", "analyze"),
             ("⚙", "Settings", "settings")]
    g = [rect(0, 0, 200, 760, C["bg2"]),
         f'<line x1="200" y1="0" x2="200" y2="760" stroke="{C["line"]}"/>',
         brand(18, 18)]
    y = 78
    for ico, label, key in items:
        on = key == active
        if on:
            g.append(rect(12, y, 176, 38, C["accentSoft"], r=8))
        g.append(txt(28, y + 24, ico, C["accent"] if on else C["text3"], 15))
        g.append(txt(52, y + 24, label, C["accent"] if on else C["text2"], 14,
                     600 if on else 520))
        y += 44
    g.append(rect(18, 690, 164, 30, C["bg2"], r=7, stroke=C["line2"]))
    g.append(txt(30, 710, "Expert mode", C["text2"], 12.5))
    g.append(txt(170, 710, "▾", C["text3"], 11, anchor="middle"))
    g.append(txt(20, 742, "v0.1.18", C["text3"], 12))
    return "".join(g)


def frame(body, w=1200, h=760):
    return (f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
            f'font-family="{FONT}">' + rect(0, 0, w, h, C["bg"], r=0) + body + "</svg>")


# ---------------------------------------------------------------- garage ------
def garage_svg():
    g = [sidebar("garage")]
    g.append(txt(232, 46, "Your garage", C["text"], 24, 650))
    g.append(txt(232, 72, "Pick a build to continue its tune, or start fresh — each "
                          "vehicle is remembered between sessions.", C["text2"], 13.5))
    g.append(rect(1010, 28, 150, 34, C["bg2"], r=7, stroke=C["line2"]))
    g.append(txt(1085, 50, "\U0001F9ED  Guided setup", C["text"], 13, 550, "middle"))

    def vcard(x, y, nick, name, chips, frac, stage):
        out = [rect(x, y, 360, 150, C["bg2"], r=10, stroke=C["line"]),
               rect(x + 318, y + 14, 24, 22, C["bg3"], r=6),
               txt(x + 330, y + 29, "✎", C["text3"], 12, anchor="middle"),
               txt(x + 20, y + 36, nick, C["text"], 18, 650),
               txt(x + 20, y + 56, name, C["text2"], 13)]
        cx = x + 20
        for i, (lab, acc) in enumerate(chips):
            c, dw = chip(cx, y + 70, lab, acc)
            out.append(c); cx += dw
        out.append(rect(x + 20, y + 108, 320, 5, C["bg3"], r=3))
        out.append(f'<rect x="{x+20}" y="{y+108}" width="{int(320*frac)}" height="5" rx="3" '
                   f'fill="url(#pg)"/>')
        out.append(txt(x + 20, y + 134, stage, C["text3"], 12))
        return "".join(out)

    g.append('<defs><linearGradient id="pg" x1="0" y1="0" x2="1" y2="0">'
             f'<stop offset="0" stop-color="{C["accent"]}"/>'
             '<stop offset="1" stop-color="#7c4ddc"/></linearGradient></defs>')
    g.append(vcard(232, 96, "Goldie", "5.3 iron truck",
                   [("HP Tuners", True), ("GM", False), ("Gen 3 LS", False), ("VE 20×19", False)],
                   0.55, "Journey: Tune MAF curve"))
    g.append(vcard(612, 96, "Blue GN", "Buick Grand National",
                   [("Holley EFI", True), ("BUICK", False), ("Terminator X", False)],
                   0.25, "Journey: Stabilize idle"))

    def newcard(x, y, label):
        return (f'<rect x="{x}" y="{y}" width="360" height="150" rx="10" fill="none" '
                f'stroke="{C["line2"]}" stroke-width="1.4" stroke-dasharray="6 5"/>' +
                txt(x + 180, y + 80, label, C["text2"], 15, 550, "middle"))
    g.append(newcard(232, 262, "+  New vehicle"))
    g.append(newcard(612, 262, "⚡  Quick scan (no save)"))
    return frame("".join(g))


# ---------------------------------------------------------------- setup -------
def setup_svg():
    g = [sidebar("garage")]
    g.append(txt(232, 46, "New vehicle", C["text"], 24, 650))
    g.append(txt(232, 72, "Asked once — remembered every session after. Everything here "
                          "sharpens the advice.", C["text2"], 13.5))

    # a couple of context fields
    def field(x, y, label, value, w=270):
        return (txt(x, y, label, C["text"], 13.5, 570) +
                rect(x, y + 8, w, 34, C["bg2"], r=7, stroke=C["line2"]) +
                txt(x + 12, y + 30, value, C["text2"], 13))
    g.append(field(232, 96, "Platform", "HP Tuners"))
    g.append(field(520, 96, "Make", "GM"))
    g.append(field(808, 96, "Generation", "Gen 3 LS (1997-2007, 24x)", 352))

    # the headline: Tune table axes
    g.append(rect(232, 162, 928, 488, C["bg2"], r=10, stroke=C["line"]))
    g.append(txt(252, 192, "▾  Tune table axes", C["text"], 14, 600))
    g.append(txt(420, 192, "(optional — line the correction grids up with your tune's tables)",
                 C["text3"], 12.5))
    g.append(rect(252, 210, 888, 420, C["bg3"], r=8, stroke=C["line"]))
    g.append(txt(272, 238, "Your tables' breakpoints rarely match our default bins, so a copied "
                           "correction can land in the wrong cells. Paste a table", C["text2"], 12.5))
    g.append(txt(272, 256, "(VCM Editor: right-click → Copy with Axis) and the grids match it "
                           "cell-for-cell.", C["text2"], 12.5))

    g.append(txt(272, 290, "VE / fuel table", C["text"], 13.5, 570))
    g.append(rect(272, 300, 848, 96, C["bg2"], r=7, stroke=C["line2"]))
    paste = ["%    400   800   1200  1600  2000  2400  ...  8000   rpm",
             "15   38.3  42.5  45.6  46.1  46.7  44.5  ...  52.2",
             "20   41.5  45.8  49.3  49.9  50.4  53.3  ...  63.3",
             "...                                                    kPa"]
    for i, line in enumerate(paste):
        g.append(txt(286, 322 + i * 18, line, C["text2"], 11.5, font=MONO))
    g.append(txt(272, 420, "✓  Read from your table: 20 RPM × 19 MAP = 380 cells — "
                           "the grid and copied TSV will match your table.", C["opp"], 12.5, 540))

    g.append(f'<line x1="272" y1="442" x2="1120" y2="442" stroke="{C["line"]}"/>')
    g.append(txt(272, 470, "Spark / timing table", C["text"], 13.5, 570))
    g.append(txt(420, 470, "(only if you tune spark — its own axes)", C["text3"], 12))
    g.append(rect(272, 482, 848, 80, C["bg2"], r=7, stroke=C["line2"]))
    g.append(txt(286, 510, "Paste your spark/timing table (Copy with Axis). Same idea as VE —",
                 C["text3"], 11.5, font=MONO))
    g.append(txt(286, 528, "the spark table usually has different breakpoints, so paste it separately.",
                 C["text3"], 11.5, font=MONO))
    g.append(txt(272, 596, "+ or enter spark breakpoints manually", C["accent"], 12.5, 540))
    return frame("".join(g))


# ---------------------------------------------------------------- report ------
def _lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _hex(rgb):
    return "#%02x%02x%02x" % rgb


def _hx(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _heat_color(v, lim):
    pull, mid, add = _hx(C["pull"]), _hx(C["bg3"]), _hx(C["add"])
    t = max(0.0, min(1.0, (v + lim) / (2 * lim)))
    return _hex(_lerp(pull, mid, t / 0.5) if t <= 0.5 else _lerp(mid, add, (t - 0.5) / 0.5))


def report_svg(d):
    g = [sidebar("analyze")]
    s, rx = d["summary"], d["prescription"]
    # vehicle bar
    g.append(txt(232, 40, "Goldie", C["text"], 16, 650))
    cx = 300
    for lab, acc in [("HP Tuners", True), ("GM", False), ("Gen 3 LS", False), ("ride42.csv", False)]:
        c, dw = chip(cx, 26, lab, acc); g.append(c); cx += dw

    # verdict card
    g.append(rect(232, 62, 936, 150, C["bg2"], r=10, stroke=C["line"]))
    g.append(rect(252, 80, 118, 24, "#2c1819", r=12))
    g.append(txt(311, 96, "⚠ RUNNING_DRIVE", C["crit"], 11.5, 640, "middle"))
    g.append(txt(252, 132, rx["title"], C["text"], 19, 660))
    lead = (rx["actions"][0] if rx.get("actions") else "")[:96]
    g.append(txt(252, 156, lead + ("…" if len(lead) >= 96 else ""), C["text2"], 13))
    stats = [("Coverage", f'{s["coverage_pct"]}%'), ("Median change", f'{s["median_pct"]}%'),
             ("Worst cell", f'+{s["max_abs_pct"]}%'), ("Wideband", "yes" if s["has_wideband"] else "no")]
    sx = 252
    for k, v in stats:
        g.append(rect(sx, 172, 150, 28, C["bg3"], r=7, stroke=C["line"]))
        g.append(txt(sx + 12, 184, k.upper(), C["text3"], 9.5, 600))
        g.append(txt(sx + 12, 196, v, C["text"], 13, 660))
        sx += 160
    g.append(txt(900, 190, "ⓘ matched to your VE table (20×19)", C["text3"], 11.5))

    # one finding card
    g.append(rect(232, 226, 936, 70, C["bg2"], r=8, stroke=C["line"]))
    g.append(rect(232, 226, 4, 70, C["warn"], r=0))
    g.append(rect(252, 244, 86, 22, "#2b2214", r=11))
    g.append(txt(295, 259, "WARNING", C["warn"], 11.5, 640, "middle"))
    g.append(txt(350, 260, d["findings"][0]["title"], C["text"], 13.5, 600))
    g.append(txt(252, 285, d["findings"][0]["detail"][:104], C["text2"], 12))

    # ---- VE heatmap (real cells) ----
    top = 322
    g.append(rect(232, top, 936, 470, C["bg2"], r=10, stroke=C["line"]))
    g.append(txt(252, top + 28, "VE / fuel correction", C["text"], 15.5, 650))
    g.append(txt(420, top + 28, "% change per cell — matched to your VE table (20×19)",
                 C["text3"], 12.5))
    g.append(rect(1010, top + 12, 138, 26, C["bg3"], r=7, stroke=C["line2"]))
    g.append(txt(1079, top + 29, "Copy for VCM/Holley (TSV)", C["text"], 11.5, 540, "middle"))

    cells = {(c["rpm"], c["map"]): c["value"] for c in d["correction"]["cells"]}
    lim = max(5.0, max(abs(c["value"]) for c in d["correction"]["cells"]))
    rpm_lo_to_hi = [str(r) for r in RPM]            # bottom -> top
    map_l = [str(m) for m in MAP]                   # left -> right
    px0, py0 = 300, top + 52
    pw, ph = 820, 392
    cw, ch = pw / len(map_l), ph / len(rpm_lo_to_hi)
    for ri, r in enumerate(rpm_lo_to_hi):
        yy = py0 + (len(rpm_lo_to_hi) - 1 - ri) * ch
        if ri % 2 == 0:
            g.append(txt(px0 - 8, yy + ch / 2 + 3, r, C["text3"], 9, anchor="end"))
        for ci, m in enumerate(map_l):
            xx = px0 + ci * cw
            v = cells.get((r, m))
            if v is None:
                g.append(rect(xx + 0.5, yy + 0.5, cw - 1, ch - 1, C["bg3"], r=1, op=0.5))
            else:
                g.append(rect(xx + 0.5, yy + 0.5, cw - 1, ch - 1, _heat_color(v, lim), r=1))
                if abs(v) >= 4 and cw >= 40:
                    g.append(txt(xx + cw / 2, yy + ch / 2 + 3,
                                 ("+" if v > 0 else "") + f"{v:.0f}", C["text"], 8.5, anchor="middle"))
    for ci, m in enumerate(map_l):
        if ci % 2 == 0:
            g.append(txt(px0 + ci * cw + cw / 2, py0 + ph + 16, m, C["text3"], 9, anchor="middle"))
    g.append(txt(px0 + pw / 2, py0 + ph + 34, "MAP (kPa)", C["text3"], 11, anchor="middle"))
    g.append(txt(px0 - 34, py0 + ph / 2, "RPM", C["text3"], 11, anchor="middle"))
    # legend
    g.append(txt(1150, py0 + 6, "add", C["text3"], 9.5, anchor="end"))
    for i in range(40):
        v = lim - (2 * lim) * (i / 39)
        g.append(rect(1150, py0 + 14 + i * 4, 10, 4, _heat_color(v, lim), r=0))
    g.append(txt(1150, py0 + 14 + 40 * 4 + 10, "pull", C["text3"], 9.5, anchor="end"))

    # ---- timeline strip with lean shading ----
    ty = top + 484
    g.append(rect(232, ty, 936, 210, C["bg2"], r=10, stroke=C["line"]))
    g.append(txt(252, ty + 28, "Log timeline", C["text"], 15.5, 650))
    g.append(txt(360, ty + 28, "knock + lean/rich shading", C["text3"], 12.5))
    g.append(rect(980, ty + 14, 16, 11, "#3a1d1c", r=3))
    g.append(txt(1002, ty + 24, "lean under load", C["text3"], 11))
    ts = d["timeseries"]
    t = ts["t"]; tmin, tmax = t[0], t[-1]
    plx, ply, plw, plh = 252, ty + 44, 896, 132
    # lean shading bands
    for b in ts.get("bands", []):
        if b["type"] != "lean":
            continue
        x1 = plx + plw * (b["from"] - tmin) / (tmax - tmin)
        x2 = plx + plw * (b["to"] - tmin) / (tmax - tmin)
        g.append(rect(x1, ply, max(2, x2 - x1), plh, C["crit"], r=0, op=0.18))
    # rpm trace (subsampled)
    rpm = ts["traces"]["rpm"]
    rmax = max(x for x in rpm if x is not None) or 1
    step = max(1, len(rpm) // 240)
    pts = []
    for i in range(0, len(rpm), step):
        if rpm[i] is None or t[i] is None:
            continue
        x = plx + plw * (t[i] - tmin) / (tmax - tmin)
        y = ply + plh - plh * (rpm[i] / rmax)
        pts.append(f"{x:.1f},{y:.1f}")
    g.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{C["accent"]}" '
             f'stroke-width="1.2" opacity="0.95"/>')
    # afr trace on its own scale
    afr = ts["traces"].get("afr_actual") or []
    if afr:
        vals = [x for x in afr if x is not None]
        alo, ahi = min(vals), max(vals)
        pts2 = []
        for i in range(0, len(afr), step):
            if afr[i] is None or t[i] is None:
                continue
            x = plx + plw * (t[i] - tmin) / (tmax - tmin)
            y = ply + plh - plh * ((afr[i] - alo) / (ahi - alo + 1e-6))
            pts2.append(f"{x:.1f},{y:.1f}")
        g.append(f'<polyline points="{" ".join(pts2)}" fill="none" stroke="{C["opp"]}" '
                 f'stroke-width="1" opacity="0.85"/>')
    g.append(txt(plx, ply + plh + 18, "0 s", C["text3"], 9))
    g.append(txt(plx + plw, ply + plh + 18, f"{int(tmax)} s", C["text3"], 9, anchor="end"))
    return frame("".join(g), h=1030)


def main():
    cr = analyze_log(os.path.join(ROOT, "tests", "fixtures", "ride42.csv"),
                     SessionOpts(cfg=Config(), tune_spark=True,
                                 ve_axes={"rpm": RPM, "map": MAP}), out_dir=None)
    d = cr.to_dict()
    os.makedirs(OUT, exist_ok=True)
    for name, svg in (("garage", garage_svg()), ("setup-axes", setup_svg()),
                      ("report", report_svg(d))):
        path = os.path.join(OUT, name + ".svg")
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)
        print("wrote", os.path.relpath(path, ROOT))


if __name__ == "__main__":
    main()
