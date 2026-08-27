# Issue log

Non-trivial issues, their solutions, and the reasons behind those solutions are recorded here, newest first. See [AGENTS.md](AGENTS.md) for the threshold that requires an entry.

## ISSUE-001 — Weekly dividend curve obscured the annual measure

- **Date:** 2026-08-27
- **Status:** Resolved
- **Area:** Dividend-yield calculation and visualization.
- **Symptoms:** The chart repeated the latest reported annual dividend against every weekly price, creating a dense time series even though users primarily compare the total dividend rate by fiscal year.
- **Cause:** The original yield helper carried each annual dividend forward across weekly price observations.
- **Solution:** Produce one record per reported fiscal year using annual dividend/share divided by the last valid close on or before fiscal year-end. Prefer a split-only close from the preceding 14 days and use a recent adjusted close only as a labeled approximation.
- **Why this solution:** A fiscal-year-end denominator matches the annual reported numerator and produces a conventional historical snapshot. An average annual price is less recognizable, while recalculating every year against the current price would not be historical yield.
- **Verification:** Unit and integration tests cover fiscal-year grouping, weekend dates, future and stale price exclusion, split-only preference, adjusted fallback, zero and unavailable yields, and selected ranges.
- **References:** `fastfunds/server.py`, `fastfunds/static/app.js`, and `tests/test_importer.py`.

## Entry template

<!-- When adding the first entry, remove the "No qualifying issues" line above.
## ISSUE-NNN — Short title

- **Date:** YYYY-MM-DD
- **Status:** Open | Mitigated | Resolved | Accepted
- **Area:** Affected component or workflow.
- **Symptoms:** What happened and how it was observed.
- **Cause:** Confirmed root cause, or clearly labeled current hypothesis.
- **Solution:** The implemented fix, mitigation, or accepted constraint.
- **Why this solution:** Decision criteria, tradeoffs, and important alternatives rejected.
- **Verification:** Tests, checks, or observations that validate the outcome.
- **Follow-up:** Remaining work, owner, or review condition. Omit when none.
- **References:** Relevant project-log entry, commit, test, or file links. Omit if none.
-->
