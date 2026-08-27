# Project maintenance rules

These instructions apply to the entire repository.

## Required change-log review

Before finishing any change, decide whether it meets one or more of the thresholds below. If it does, update the corresponding log in the same change. Do not add log entries for routine edits that do not meet a threshold.

### Update `PROJECT_LOG.md` when

- a user-visible feature, workflow, or behavior is added, removed, or materially changed;
- a breaking change or compatibility requirement is introduced;
- the architecture, data model, external inputs, dependencies, build, release, security posture, or operating requirements change materially;
- performance, reliability, or data-quality behavior changes enough to affect users or maintainers; or
- repository-wide development or governance policy changes.

Small fixes, comments, formatting, test-only changes, and internal refactors that preserve behavior do not require a project-log entry.

### Update `ISSUE_LOG.md` when

- a non-trivial defect, incident, data-quality problem, security concern, or recurring operational problem is discovered;
- such an issue is resolved or its workaround changes; or
- choosing a solution involved a meaningful tradeoff, rejected alternatives, or reasoning that future maintainers may need.

Do not log typos, transient local tool failures, or ordinary test failures encountered and immediately corrected during development.

### Entry rules

- Put new entries first and date them using `YYYY-MM-DD`.
- Follow the template in the relevant log and be concise but specific.
- Link related issues, commits, pull requests, tests, or files when useful.
- Record verified facts; clearly label unresolved assumptions.
- Preserve historical entries. Add a dated follow-up instead of silently rewriting an old decision.
- If an issue fix also creates a major project change, update both logs: summarize the outcome in `PROJECT_LOG.md` and retain the diagnosis and decision record in `ISSUE_LOG.md`.
