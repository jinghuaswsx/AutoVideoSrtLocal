"""Meta Ads Manager CDP defaults and cross-process lock helpers.

Docs-anchor:
docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md#meta-ads-manager-cdp-锁
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from appcore.browser_automation_lock import browser_automation_lock

DEFAULT_META_ADS_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_LINUX_META_ADS_LOCK_PATH = Path("/data/autovideosrt/browser/runtime-meta-ads/automation.lock")
DEFAULT_LOCAL_META_ADS_LOCK_PATH = Path("output") / "browser_automation" / "meta_ads_cdp.lock"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def default_meta_ads_cdp_lock_path() -> Path:
    configured = os.environ.get("META_ADS_CDP_LOCK_PATH")
    if configured:
        return Path(configured)
    if os.name == "nt":
        return Path.cwd() / DEFAULT_LOCAL_META_ADS_LOCK_PATH
    if DEFAULT_LINUX_META_ADS_LOCK_PATH.parent.exists() or Path("/data/autovideosrt").exists():
        return DEFAULT_LINUX_META_ADS_LOCK_PATH
    return Path.cwd() / DEFAULT_LOCAL_META_ADS_LOCK_PATH


def meta_ads_cdp_lock_timeout_seconds() -> int:
    return _int_env(
        "META_ADS_CDP_LOCK_TIMEOUT_SECONDS",
        _int_env("BROWSER_AUTOMATION_LOCK_TIMEOUT_SECONDS", 600),
    )


def meta_ads_cdp_lock_retry_seconds() -> int:
    return _int_env(
        "META_ADS_CDP_LOCK_RETRY_SECONDS",
        _int_env("BROWSER_AUTOMATION_LOCK_RETRY_SECONDS", 5),
    )


def _restore_env(name: str, previous: str | None) -> None:
    if previous is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = previous


@contextmanager
def meta_ads_cdp_lock(
    *,
    task_code: str,
    timeout_seconds: int | None = None,
    retry_seconds: int | None = None,
    command: str | None = None,
    lock_path: str | os.PathLike[str] | None = None,
    disable_child_lock: bool = False,
) -> Iterator[Path]:
    path = Path(lock_path) if lock_path is not None else default_meta_ads_cdp_lock_path()
    if os.environ.get("META_ADS_CDP_LOCK_DISABLED") == "1":
        yield path
        return

    timeout = meta_ads_cdp_lock_timeout_seconds() if timeout_seconds is None else int(timeout_seconds)
    retry = meta_ads_cdp_lock_retry_seconds() if retry_seconds is None else int(retry_seconds)
    with browser_automation_lock(
        task_code=task_code,
        timeout_seconds=timeout,
        retry_seconds=retry,
        command=command,
        lock_path=path,
    ) as acquired_path:
        previous_disabled = os.environ.get("META_ADS_CDP_LOCK_DISABLED")
        if disable_child_lock:
            os.environ["META_ADS_CDP_LOCK_DISABLED"] = "1"
        try:
            yield acquired_path
        finally:
            if disable_child_lock:
                _restore_env("META_ADS_CDP_LOCK_DISABLED", previous_disabled)
