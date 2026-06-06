# Collecting user-submitted logs (optional)

tuneassist can ask users — after an analysis, opt-in, defaulting to **no** — to
share the log they just ran so you can improve the detectors on real-world data.
It's off until you point it at a place to receive files. Nothing is ever sent
automatically; the app bundles the file locally and opens an upload page in the
user's browser.

## What gets shared (and what doesn't)

When a user says yes, the app writes a `.zip` to `~/.tuneassist/submissions/`
containing:

- the **log CSV** they just analyzed, and
- `submission.json` — a short, non-identifying summary: tuneassist version,
  platform, triage state, journey stage, the engine profile (block / compression
  / displacement / power-adder / bolt-ons), the correction summary numbers, the
  finding IDs, and **only** the note/contact the user chooses to type.

It never includes the garage, the vehicle name, or the nickname.

## Pick a free place to receive files

You need a form with a **file-upload field**. Recommended, free, no account
required for the submitter:

1. **[Tally.so](https://tally.so)** *(recommended)* — free plan includes file
   uploads, unlimited submissions, and the submitter doesn't need an account.
   Create a form with: a File Upload field, an optional Email field, an optional
   "notes" field. Publish it and copy the share link (looks like
   `https://tally.so/r/xxxxxx`).
2. **Google Forms** — free, file uploads land in your Google Drive, *but* the
   submitter must be signed into a Google account. Fine if your audience already
   is.
3. **GitHub Issues** — zero setup, but the user needs a GitHub account and has to
   drag the file into an issue. Best for technical users; link them to a "submit
   a log" issue template instead of the in-app flow.

## Turn it on

Edit `tuneassist/submit.py` and set:

```python
SUBMIT_URL = "https://tally.so/r/xxxxxx"   # your form
```

That's it. With a URL set:

- the **TUI** shows a "Share log" button (and the `s` key) after an analysis,
- the **wizard** asks once per log whether to share,

both behind a clear confirm that explains what's included. Leave `SUBMIT_URL`
blank and the feature stays completely hidden.

## Reviewing what comes in

Each submission is a self-contained zip: the CSV plus `submission.json`. Drop new
logs that expose a wrong call into `tests/fixtures/` as regression fixtures (see
DESIGN.md for what each known log should produce) — that's the loop that makes
the tool smarter over time.
