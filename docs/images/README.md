# UI images

These SVGs are generated headlessly from the Textual UI — no real terminal or
screen recorder needed. GitHub renders SVG inline, so they stay crisp at any
zoom and the file sizes are small.

## Regenerate the screenshots

    python tools/capture_screens.py

This drives the app through Textual's pilot harness, analyzes the bundled
`tests/fixtures/ride42.csv`, and writes:

| file | screen |
|---|---|
| `01-garage.svg`           | garage (pick / new / quick-scan) |
| `02-setup.svg`            | setup form (engine preset, mods, cam) |
| `03-analyze-empty.svg`    | analysis screen before a log is loaded |
| `04-report.svg`           | full report: journey bar + diagnosis + next step |
| `05-correction-grid.svg`  | interactive RPM × MAP correction grid |
| `06-top-cells.svg`        | sortable "top cells" table |

The theme shown is the default (**textual-dark**); press **Ctrl+T** in the app
to try the others.

## Recording an animated GIF

SVG covers static views. For a short demo GIF, record a *live* session locally
(the pilot harness can't drive a real terminal's redraw timing):

1. Run the app in a terminal sized ~132×44:

       python -m tuneassist.cli --tui

2. Record with a terminal recorder, e.g. [asciinema](https://asciinema.org)
   plus [agg](https://github.com/asciinema/agg):

       asciinema rec demo.cast       # do a short walkthrough, then exit
       agg demo.cast demo.gif

   or [termtosvg](https://github.com/nbedos/termtosvg) for an animated SVG.

3. Drop the result here as `demo.gif` and reference it from the top-level README.
