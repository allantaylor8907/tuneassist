"""Textual UI smoke tests via the pilot harness (no real terminal needed).

These drive the app the way a user would and assert the screens compose and the
analysis renders + persists. Numeric correctness is covered by test_core/test_*;
here we prove the UI wires to the core."""
import sys, os, tempfile, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from textual.widgets import Button, Input, DataTable, Static

from tuneassist.tui import TuneAssistApp, GarageScreen, AnalyzeScreen, SetupScreen
from tuneassist.panels import build_report
from tuneassist.core import analyze_log, SessionOpts
from tuneassist.engine_gm import Config
from tuneassist import garage

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
RIDE = os.path.join(FIX, "ride42.csv")


def _run(coro):
    asyncio.run(coro)


def _press(screen, button_id):
    """Activate a button by invoking its handler (visibility-independent)."""
    btn = screen.query_one(f"#{button_id}", Button)
    screen.on_button_pressed(Button.Pressed(btn))


def test_app_boots_to_garage():
    async def go():
        with tempfile.TemporaryDirectory() as d:
            app = TuneAssistApp(garage_path=os.path.join(d, "g.json"))
            async with app.run_test() as pilot:
                await pilot.pause()
                assert isinstance(app.screen, GarageScreen)
    _run(go())


def test_garage_lists_saved_vehicles():
    async def go():
        with tempfile.TemporaryDirectory() as d:
            gp = os.path.join(d, "g.json")
            data = garage.load(gp)
            garage.upsert(data, "5.7 LS6", {"platform": "gm", "nickname": "Bandit",
                                            "stage": "TUNE_MAF", "updated": "2026-06-01"})
            garage.save(data, gp)
            app = TuneAssistApp(garage_path=gp)
            async with app.run_test() as pilot:
                await pilot.pause()
                dt = app.screen.query_one("#vehicles", DataTable)
                assert dt.row_count == 1
    _run(go())


def test_new_vehicle_setup_to_analyze_and_persist():
    async def go():
        with tempfile.TemporaryDirectory() as d:
            gp = os.path.join(d, "g.json")
            app = TuneAssistApp(garage_path=gp)
            async with app.run_test() as pilot:
                await pilot.pause()
                _press(app.screen, "new")              # GarageScreen -> SetupScreen
                await pilot.pause()
                assert isinstance(app.screen, SetupScreen)
                app.screen.query_one("#name", Input).value = "5.3 truck"
                app.screen.query_one("#nick", Input).value = "Goldie"
                _press(app.screen, "save")             # -> AnalyzeScreen
                await pilot.pause()
                assert isinstance(app.screen, AnalyzeScreen)

                app.screen.query_one("#path", Input).value = RIDE
                _press(app.screen, "analyze")
                await pilot.pause()

            data = garage.load(gp)
            assert "5.3 truck" in garage.list_vehicles(data)
            rec = garage.get(data, "5.3 truck")
            assert rec["nickname"] == "Goldie"
            assert rec["stage"] == "TUNE_VE_SD"        # ride42 lands here
            assert len(rec.get("history", [])) == 1
    _run(go())


def test_grid_and_cells_tables_populate_and_sort():
    async def go():
        with tempfile.TemporaryDirectory() as d:
            app = TuneAssistApp(garage_path=os.path.join(d, "g.json"))
            async with app.run_test(size=(120, 50)) as pilot:
                await pilot.pause()
                _press(app.screen, "new")
                await pilot.pause()
                app.screen.query_one("#name", Input).value = "rt"
                _press(app.screen, "save")
                await pilot.pause()
                app.screen.query_one("#path", Input).value = RIDE
                _press(app.screen, "analyze")
                await pilot.pause()
                grid = app.screen.query_one("#grid", DataTable)
                cells = app.screen.query_one("#cells", DataTable)
                assert grid.row_count > 0          # RPM x MAP grid rendered
                assert cells.row_count == 34        # one row per confident cell
                # cell-detail updates when a cell is highlighted
                app.screen.on_data_table_cell_highlighted(
                    type("E", (), {"data_table": grid,
                                   "coordinate": type("C", (), {"row": 2, "column": 1})()})())
                detail = app.screen.query_one("#celldetail", Static)
                assert "MAP" in str(detail.render())
                # header click sorts the flat table without error
                app.screen.on_data_table_header_selected(
                    type("E", (), {"data_table": cells, "column_key": "n"})())
    asyncio.run(go())


def test_build_report_renders_without_error():
    from rich.console import Console
    cr = analyze_log(RIDE, SessionOpts(cfg=Config(), tune_spark=True))
    Console(file=open(os.devnull, "w"), width=120).print(
        build_report(cr, history=[], show_spark=True))   # must not raise


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("all tui tests passed")
