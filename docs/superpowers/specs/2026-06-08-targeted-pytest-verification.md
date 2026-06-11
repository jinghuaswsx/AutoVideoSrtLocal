# 2026-06-08 Targeted Pytest Verification

## Anchors

- `AGENTS.md`: change verification starts with related `pytest <files> -q`.
- `pytest.ini`: default collection is limited to `tests/` and excludes local virtualenvs, generated, e2e, manual, and build directories.
- `tests/conftest.py`: live-DB, external-service, e2e, and manual tests are gated behind explicit environment flags.
- `docs/superpowers/specs/2026-06-07-test-suite-cleanup.md`: full default pytest was accepted as deterministic but expensive, with `7572 passed` in `576.79s`.

## Goal

Keep daily development fast while preserving meaningful regression coverage. Small, local changes must not wait for the full default pytest suite unless the change actually affects broad test infrastructure or release readiness.

## AutoVideoSrtLocal pytest 最小化规则（强制）

- 默认不跑全量 `pytest -q`；按仓库 `docs/superpowers/specs/2026-06-08-targeted-pytest-verification.md` 选择改动相关测试。
- 首选 `python3 scripts/pytest_related.py --base origin/master --run`；Windows 若只有 `python` 则用 `python scripts/pytest_related.py --base origin/master --run`。
- 没有脚本时人工列 `pytest <相关 files> -q`。
- 脚本无目标时说明“无直接 pytest 覆盖”，改跑最小必要非 pytest 验证，不得自动退回全量。
- 只有发布/合并/用户明确要求、pytest 配置/fixture/依赖变更、跨模块重构，或 schema/auth/deploy/scheduler/LLM/storage/billing 等广影响改动时跑全量。
- 最终汇报必须说明全量是否跳过、理由，以及实际运行的 focused tests 或替代验证。

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

Local virtualenv directories and symlinks such as `.venv` / `venv` must stay in `pytest.ini` `norecursedirs`; focused file targets should not fail because pytest tries to inspect an unreadable environment directory in the repo root.

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
