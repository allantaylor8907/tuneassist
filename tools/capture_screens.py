"""Capture SVG screenshots of the Textual UI for the README/docs.

Runs the app headlessly through Textual's pilot harness (no real terminal
needed) and exports crisp SVGs that GitHub renders inline. SVG is the reliable
headless option; for an animated GIF, record a live session locally with a
terminal recorder (see docs/images/README.md).

    python tools/capture_screens.py
"""
import os
import sys
import asyncio
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from textual.widgets import Button, Input, TabbedContent  # noqa: E402

from tuneassist.tui import TuneAssistApp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "images")
RIDE = os.path.join(ROOT, "tests", "fixtures", "ride42.csv")
# Narrower + taller than a typical terminal: when GitHub scales the SVG to the
# README's content width, fewer columns means each character renders LARGER (more
# legible), and the extra rows show more of each screen without scrolling.
SIZE = (110, 50)


def _press(screen, button_id):
    btn = screen.query_one(f"#{button_id}", Button)
    screen.on_button_pressed(Button.Pressed(btn))


async def _capture():
    os.makedirs(OUT, exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        app = TuneAssistApp(garage_path=os.path.join(d, "g.json"))
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            app.save_screenshot("01-garage.svg", OUT)

            _press(app.screen, "new")                 # -> SetupScreen
            await pilot.pause()
            app.screen.query_one("#name", Input).value = "5.3 iron truck"
            app.screen.query_one("#nick", Input).value = "Goldie"
            app.save_screenshot("02-setup.svg", OUT)

            _press(app.screen, "save")                # -> AnalyzeScreen
            await pilot.pause()
            app.save_screenshot("03-analyze-empty.svg", OUT)

            app.screen.query_one("#path", Input).value = RIDE
            _press(app.screen, "analyze")
            await pilot.pause()
            app.save_screenshot("04-report.svg", OUT)

            tabs = app.screen.query_one("#tabs", TabbedContent)
            tabs.active = "tab-grid"
            await pilot.pause()
            app.save_screenshot("05-correction-grid.svg", OUT)

            tabs.active = "tab-cells"
            await pilot.pause()
            app.save_screenshot("06-top-cells.svg", OUT)

    print(f"wrote screenshots to {OUT}")


if __name__ == "__main__":
    asyncio.run(_capture())
