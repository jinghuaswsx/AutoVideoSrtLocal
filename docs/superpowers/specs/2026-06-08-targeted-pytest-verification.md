# 2026-06-08 Targeted Pytest Verification

## Anchors

- `AGENTS.md`: change verification starts with related `pytest <files> -q`.
- `pytest.ini`: default collection is limited to `tests/` and excludes generated, e2e, manual, and build directories.
- `tests/conftest.py`: live-DB, external-service, e2e, and manual tests are gated behind explicit environment flags.
- `docs/superpowers/specs/2026-06-07-test-suite-cleanup.md`: full default pytest was accepted as deterministic but expensive, with `7572 passed` in `576.79s`.

## Goal

Keep daily development fast while preserving meaningful regression coverage. Small, local changes must not wait for the full default pytest suite unless the change actually affects broad test infrastructure or release readiness.

## Default Rule

For ordinary development, run only changed-area pytest:

```bash
python3 scripts/pytest_related.py --base origin/master --run
```

If the helper cannot identify a direct pytest target, report that there is no direct pytest coverage and run the smallest useful non-pytest verification instead, such as `python3 -m compileall`, a route smoke, or a service-level check. Do not run the full suite merely because no direct test file was found.

## Selection Policy

Include tests when any of these are true:

- The changed file is itself a pytest file.
- A test imports the changed module directly or through a package prefix.
- A test name matches the changed module, route, service, tool, or feature tokens.
- The changed file is cross-cutting and has a fixed guard set:
  - `pytest.ini`, `tests/conftest.py`, or `scripts/pytest_related.py`: run the selector tests plus collection-oriented tests when available.
  - `web/app.py`, route registration, auth, permissions, CSRF, or template layout changes: run the related route tests and web guard tests.
  - `appcore/db.py`, migrations, scheduled tasks, LLM provider routing, task lifecycle, storage deletion, or billing: run the owning service tests and the relevant architecture/guard tests.

The selector should prefer a focused file list over `-k` expressions because file targets are easier to audit and copy into the final report.

## Full Suite Gate

Run full default `pytest -q` only for:

- Release, merge, or final acceptance requested by the user.
- Changes to pytest collection, global fixtures, dependency versions, or test infrastructure.
- Broad refactors touching shared contracts across multiple product areas.
- Schema, auth/permission, deployment, queue/scheduler, LLM provider, storage deletion, or billing changes whose blast radius is intentionally broad.
- Explicit user request for full regression.

When full pytest is skipped, say so explicitly in the final report and list the focused tests or alternate checks that were run.

## Agent Sync Requirement

This rule must be synchronized to the global agent defaults used by:

- Windows local `admin`: Codex, Antigravity/Gemini, and Claude Code.
- Server `cjh`: Codex and Claude Code.
- Server `root`: Codex and Claude Code.

Project-local `AGENTS.md` remains the authoritative AutoVideoSrtLocal rule; global files carry the same default so new sessions do not fall back to full pytest by habit.
