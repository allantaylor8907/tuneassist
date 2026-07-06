# packaging/

- `entry.py` — the PyInstaller entry point (absolute import of `cli.main`).
- `icon.ico` — the exe/taskbar icon: the brand heatmap mark at
  16/24/32/48/64 (BMP entries) + 256 (PNG entry). Windows build passes
  `--icon packaging/icon.ico`.
- `splash.png` — the boot splash (480×260, dark card + mark + wordmark) shown
  by the PyInstaller bootloader while the onefile bundle extracts
  (`--splash packaging/splash.png`). `cli._close_splash()` /
  `gui.app._close_splash()` dismiss it.

Both images render the same mark as `tuneassist/gui/static/favicon.svg`
(rounded 56×56 tile, 3×3 cells, amber/light-amber/blue accents). To regenerate,
draw that mark on an HTML canvas (supersample 4× for the small sizes), export
PNG/raw-RGBA, and assemble the ICO (BMP DIBs for ≤64px, PNG for 256) — or just
edit the SVG and re-do the same. Keep the splash text short; it shows for the
~1–3 s the bundle takes to unpack.
