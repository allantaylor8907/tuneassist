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

## What the data actually lets you do

- **Add new platforms — yes.** The raw CSV carries the real channel names, units,
  and values from that scanner. One genuine Sniper Dominator / LT Gen-5 / Ford
  export is usually all it takes to write or fix that platform's loader and
  channel patterns. This is the single highest-value thing a submission gives you.
- **Build regression fixtures — yes.** Drop a submitted log into `tests/fixtures/`
  with an expected outcome and you've locked in behavior (see DESIGN.md).
- **Tell *what* was analyzed — yes.** `submission.json` is the telemetry: platform,
  stage, findings, summary, hardware profile.
- **Tell whether the analysis was *right* — only if the human says so.** The log +
  what-the-tool-concluded lets you *spot* suspicious calls, but you can't confirm
  them without ground truth. Capture that in the form (next section). Without it,
  you have telemetry; with it, you have labeled data you can actually tune against.

## Recommended form fields (turn telemetry into ground truth)

Make your Tally/Google form ask the human what the log can't tell you:

1. **File upload** (the submission zip) — required.
2. **What's the car / what were you tuning?** — short text (cam, heads, boost,
   fuel). Context the profile may not capture.
3. **Did the analysis look right?** — single choice: *spot on / mostly / off /
   not sure*. This one field is what makes a submission a labeled example.
4. **If it was off, what did you actually find?** — long text. (e.g. "called it
   lean cruise, it was a vacuum leak at the intake.")
5. **Contact (optional)** — if they want a reply.

The app already attaches what the tool concluded; these fields add the verdict.
Together that's enough to fix a wrong detector with confidence, not guesswork.

## Reviewing what comes in

Each submission is a self-contained zip: the CSV plus `submission.json`. Cross-
reference the JSON's `finding_ids` against the human's "did it look right?" answer
to find the detectors that need work, then promote the log into `tests/fixtures/`
as a regression case — that's the loop that makes the tool smarter over time.
