# Project log

Major updates to Stock Valuation Lens are recorded here, newest first. See [AGENTS.md](AGENTS.md) for the threshold that requires an entry.

## Entry template

<!--
## YYYY-MM-DD — Short title

- **Summary:** What changed.
- **Impact:** What users or maintainers need to know.
- **Verification:** How the change was checked.
- **References:** Relevant issue, commit, test, or file links. Omit if none.
-->

## 2026-08-27 — Changed dividend yield to an annual view

- **Summary:** Replaced the weekly trailing dividend-yield curve with one fiscal-year-end observation per reported annual dividend, including the annual dividend/share and its reference price metadata.
- **Impact:** Users now see the historical annual dividend rate directly; CSV exports and the annual history table report each fiscal year once. The overlay remains off by default.
- **Verification:** The automated suite covers prior-price selection, adjusted-price fallback, stale and invalid prices, unavailable yields, range filtering, and SEC and supplemental issuers.
- **References:** `fastfunds/server.py`, `fastfunds/static/app.js`, and `tests/test_importer.py`.

## 2026-08-27 — Added project record-keeping policy

- **Summary:** Added dedicated project and issue logs plus repository-wide rules defining when each log must be updated.
- **Impact:** Material changes and non-trivial issue decisions now have a durable, consistent record.
- **Verification:** Confirmed both logs are linked from the README and governed by the root `AGENTS.md` instructions.

## 2026-08-27 — Added optional dividend-yield visualization

- **Summary:** Added an independently controlled dividend-yield curve on a dedicated percentage scale, using reported annual dividends per share and weekly prices.
- **Impact:** Users can compare historical price and valuation metrics with a trailing reported dividend-yield proxy; the overlay remains off by default.
- **Verification:** Covered by the project's automated tests and documented interpretation notes.
- **References:** Commit `b6ee521`.

## 2026-08-27 — Initial Stock Valuation Lens release

- **Summary:** Introduced the local standard-library web application, SQLite import pipeline, SEC and curated issuer support, valuation chart, range controls, and CSV/PNG export.
- **Impact:** Users can compare weekly stock prices with reported per-share fundamentals and transparent formula-based valuation references without installing third-party packages.
- **Verification:** The initial test suite covers the core calculations and import behavior.
- **References:** Commits `48074ff` and `7b0d9dc`.
