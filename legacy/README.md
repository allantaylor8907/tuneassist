# legacy/ — historical code retention

Per the project's retention policy: when a presentation layer is replaced, the
old code is preserved here rather than deleted.

## Status

The v2 GUI (`tuneassist/gui/`, see docs/V2.md) is in its **parallel phase**:
the Textual TUI is still the shipping default, so `tui.py`, `panels.py`, and
`demo.py` remain in place and functional.

**At cutover** (when the GUI reaches parity and becomes the no-args default),
those files move here verbatim:

- `tuneassist/tui.py`   → `legacy/tui.py`
- `tuneassist/panels.py`→ `legacy/panels.py`
- `tuneassist/demo.py`  → `legacy/demo.py`

along with their tests, and the `textual` dependency becomes optional. Until
then, git history plus this note is the record. Nothing has been deleted.
