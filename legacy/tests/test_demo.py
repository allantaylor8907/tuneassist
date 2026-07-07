"""Tests for the locked-down demo mode (textual serve)."""
import sys, os, asyncio, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from textual.widgets import Input, Button
from legacy.tui import TuneAssistApp, GarageScreen, SetupScreen, AnalyzeScreen
from legacy.demo import _samples_dir

SAMPLES = _samples_dir()


def _press(screen, bid):
    btn = screen.query_one(f"#{bid}", Button)
    screen.on_button_pressed(Button.Pressed(btn))


def test_samples_dir_found():
    assert SAMPLES and os.path.isdir(SAMPLES)
    assert any(f.endswith(".csv") for f in os.listdir(SAMPLES))


def test_demo_mode_hides_picker_and_confines_files():
    async def go():
        with tempfile.TemporaryDirectory() as d:
            app = TuneAssistApp(garage_path=os.path.join(d, "g.json"),
                                demo=True, samples_dir=SAMPLES)
            async with app.run_test() as pilot:
                await pilot.pause()
                _press(app.screen, "quick"); await pilot.pause()    # SetupScreen
                _press(app.screen, "save"); await pilot.pause()      # AnalyzeScreen
                assert isinstance(app.screen, AnalyzeScreen)
                # native picker button is hidden in demo mode
                assert not app.screen.query("#pick")
                # a path OUTSIDE the samples dir is refused
                outside = os.path.join(d, "secret.csv")
                open(outside, "w").write("x")
                app.screen.query_one("#path", Input).value = outside
                _press(app.screen, "analyze"); await pilot.pause()
                assert app.screen.query_one("#journey")  # still on analyze, no crash
                # a bundled sample analyzes fine
                sample = os.path.join(SAMPLES, "gm_hptuners_cruise.csv")
                app.screen.query_one("#path", Input).value = sample
                _press(app.screen, "analyze"); await pilot.pause()
                assert app.screen._cr is not None and app.screen._cr.has_grid
    asyncio.run(go())


def test_non_demo_mode_keeps_picker():
    async def go():
        with tempfile.TemporaryDirectory() as d:
            app = TuneAssistApp(garage_path=os.path.join(d, "g.json"))
            async with app.run_test() as pilot:
                await pilot.pause()
                _press(app.screen, "quick"); await pilot.pause()
                _press(app.screen, "save"); await pilot.pause()
                assert app.screen.query("#pick")     # Browse button present normally
    asyncio.run(go())


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("all demo tests passed")
