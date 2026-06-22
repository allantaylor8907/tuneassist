# UI images

Screenshots of the v2 **desktop GUI** used by the top-level README.

The cleanest captures come straight from the real app window on Windows. Run it,
get to each screen, and grab a screenshot (Win+Shift+S). Save them here with
these exact names so the README picks them up:

| file | screen to capture |
|---|---|
| `garage.png`     | the garage — a couple of cars (Edit + delete buttons), "New vehicle", "Quick scan" |
| `setup-axes.png` | a vehicle setup with **Tune table axes** expanded and a table pasted (the green "✓ Read from your table: N RPM × M MAP" showing) |
| `report.png`     | a full analysis — verdict + next move, findings, the VE heatmap matched to the table, and the log timeline with lean/rich shading (Expert mode) |

Tips for good shots: dark theme (default), window ~1380×900, and analyze a log
that actually has a correction (e.g. `tests/fixtures/ride42.csv`) so the heatmap
and timeline are populated.

> The old `01-garage.svg` … `06-top-cells.svg` were headless captures of the
> retired Textual TUI and have been removed.
