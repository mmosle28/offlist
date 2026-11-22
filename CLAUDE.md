# Working on offlist

Notes for Claude (or anyone) making changes. The owner is Micah, who mostly works from his phone and asks Claude to make edits.

## What this is
A daily job scraper plus a static site. `offlist/run.py` reads the three files in `config/`, fetches jobs, filters them, and writes JSON to `docs/data/`. `docs/index.html` is the whole site (one file, no build step) and reads those JSON files. GitHub Pages serves `docs/` from `main`.

## Common requests and where they go
- "Add company X": `config/companies.yaml`. Prefer a pasted careers `url`. If unknown, use `guess: <lowercase name>`. Workday links look like `https://TENANT.wdN.myworkdayjobs.com/SITE`.
- "A company shows an error": check the Companies tab / `docs/data/status.json`, then fix its link in `companies.yaml`.
- "Look for title Y" / "stop showing Z": `config/roles.yaml` (`titles` per family, `exclude_titles` to drop).
- "Add a city": `config/locations.yaml`.
- Site look or features: `docs/index.html`. Resume builder: `docs/resume.html` (one plan per role family).
- "Change what's on my resume": `docs/data/profile.json`. Only facts Micah has actually stated. Never add the email or phone there.
- Discovered companies live in `docs/data/discovered.json` (written by the Action). To stop watching one, set `"found": false` and a recent `last_try`.

## Rules
- Run `python -m unittest -v` before committing. Add a test when changing matching rules.
- Never commit invented or sample jobs to `docs/data/`. `--dry` writes to a temp folder on purpose. Real data only comes from the GitHub Action.
- Don't hand-edit `docs/data/seen.json` or `ats_cache.json` except to clear a wrong cache entry.
- Adzuna is called only on scheduled and manual runs (not on pushes) to save its small free quota.
- Keep dependencies to the standard library plus PyYAML.
- In user-facing text (site copy, README), avoid em dashes and words joined with hyphens where a plain phrase works.
- Matching is word-boundary based (`filters._phrase_regex`), so "lead" does not drop "leadership".

## Pushing changes
Commit to `main`. A push that touches `config/` or `offlist/` triggers the Action, which refreshes the data within a few minutes.
