# 2026-06-10 Worktree Python Env Bootstrap Design

## Anchors

- `AGENTS.md`: non-hotfix development happens in isolated worktrees, and verification starts with related pytest.
- `docs/server-environments.md`: production and test services share `/opt/autovideosrt/venv`; dependency changes there can affect online services.
- `docs/superpowers/specs/2026-06-08-targeted-pytest-verification.md`: daily development uses focused pytest instead of the full suite by default.
- `requirements-dev.txt`: development verification dependencies include runtime requirements and pytest.
- `AutoPush/requirements.txt`: full pytest collects AutoPush route tests, so dev verification must include AutoPush dependencies too.
- `requirements-browser.txt`: browser automation requires Playwright.

## Background

Codex worktrees on the server can run under users whose bare `python3` does not have project dependencies such as Flask, Playwright, DBUtils, BeautifulSoup, python-dotenv, or pytest. Installing into the system interpreter is blocked by PEP 668 and should not be bypassed with `--break-system-packages`. Reusing `/opt/autovideosrt/venv` for development installs is also unsafe because production and test services share that virtual environment.

## Goal

Make every new worktree able to run focused pytest and Playwright checks without mutating system Python or the production/test virtual environment.

## Standard Layout

- Base development venv: `${AUTOVIDEOSRT_CODEX_VENV}` when set, otherwise `${HOME}/.cache/autovideosrt/codex-venv-py312`.
- Per-worktree venv entry: `<worktree>/.venv`, a symlink to the base development venv.
- Playwright browser cache: `${PLAYWRIGHT_BROWSERS_PATH}` when set, otherwise `${HOME}/.cache/ms-playwright`.
- Production/test venv: `/opt/autovideosrt/venv`, read-only for development verification and never used as the install target.
- Git ignore rule: both `.venv` and `.venv/` must be ignored so the symlink never pollutes `git status`.

The base venv is per OS user, not per Git worktree. A new worktree only creates a `.venv` symlink, so dependencies and Playwright browser binaries are reused.

## Bootstrap Flow

1. Resolve the repo root with `git rev-parse --show-toplevel`.
2. Create the base development venv if it does not exist.
3. Install or refresh `requirements-dev.txt` inside the base venv, including the root runtime requirements and AutoPush requirements.
4. Install Playwright Chromium into the shared browser cache.
5. Create `<worktree>/.venv` as a symlink to the base development venv.
6. Run `scripts/worktree_env.py check` before pytest or browser verification.

The bootstrap command must refuse `/opt/autovideosrt/venv` and `/opt/autovideosrt-test/venv` as base venv paths.

## Check Flow

`scripts/worktree_env.py check` verifies:

- `.venv/bin/python` is available.
- Required imports work: Flask, Playwright sync API, DBUtils, BeautifulSoup, python-dotenv, pytest.
- `python -m pytest --version` works.
- `python -m playwright --version` works.
- The configured Playwright browser cache contains a Chromium browser.

After a successful check, run focused tests through `.venv/bin/python`, for example:

```bash
.venv/bin/python -m pytest tests/test_meta_hot_posts_routes.py tests/test_xuanpin_routes.py -q
```

## Cleanup Flow

Worktree cleanup happens when the demand ends, the worktree is deleted, or the conversation is closed/archived. Cleanup is conservative by default:

- Remove the worktree-local `.venv` symlink only.
- Remove generated caches: `.pytest_cache`, `__pycache__`, `test-results`, `playwright-report`.
- Keep the shared base venv and shared Playwright browser cache.
- Do not delete `uploads`, `output`, or `media_store` unless the operator explicitly passes `--include-runtime-dirs`.

The base venv can be removed manually only when no active worktree is using it.

## Commands

```bash
python3 scripts/worktree_env.py bootstrap
.venv/bin/python scripts/worktree_env.py check
.venv/bin/python -m pytest <focused test files> -q
python3 scripts/worktree_env.py cleanup
```
