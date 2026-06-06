<!-- Thanks for contributing! Anyone can open a PR; the owner reviews + merges. -->

## What this does

<!-- One or two sentences. What changes, and why. -->

## Related issue

<!-- e.g. Closes #12 -->

## How I tested it

<!-- e.g. ran `for t in tests/test_*.py; do python "$t"; done` -- all pass.
     If it's a detector/platform change, mention the log(s) you checked against. -->

## Checklist

- [ ] I added or updated a test in `tests/` (real logs welcome in `tests/fixtures/`)
- [ ] The test suite passes locally
- [ ] Output stays ASCII-safe (runs in legacy Windows terminals)
- [ ] No new heavy dependencies
- [ ] This does **not** add any feature that writes a tune file / touches the ECU
      (tuneassist is recommendation-only by design — see DESIGN.md)
