from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


PRODUCTION_BASE_URL = "http://172.16.254.106"
DEFAULT_API_KEY = os.getenv("SHOPIFY_IMAGE_LOCALIZER_API_KEY", "").strip()
DEFAULT_BROWSER_USER_DATA_DIR = r"C:\chrome-shopify-image"
DEFAULT_SHOPIFY_DOMAIN = "newjoyloo.com"
DEFAULT_SHOPIFY_STORE_SLUG = "0ixug9-pv"
# 历史已知 slug：作为缓存未命中时的 fallback。每个 domain 真实 slug 由首次登录后从浏览器
# URL 自动捕获写入 shopify_domain_store_slugs（runtime config）。
DEFAULT_SHOPIFY_STORE_SLUG_BY_DOMAIN = {
    DEFAULT_SHOPIFY_DOMAIN: DEFAULT_SHOPIFY_STORE_SLUG,
}
CONFIG_FILENAME = "shopify_image_localizer_config.json"
DEFAULT_CONFIG_FILENAME = "shopify_image_localizer_default_config.json"

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_STORE_SLUG_RE = re.compile(r"[^a-z0-9-]+")
_STORE_SLUG_VALID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_STORE_URL_RE = re.compile(
    r"^https?://admin\.shopify\.com/store/([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?=[/?#]|$)",
    re.IGNORECASE,
)


def default_base_url(*, packaged: bool | None = None) -> str:
    # This desktop tool is an operations helper for the production Shopify
    # store. It must always use the production OpenAPI endpoint, even when run
    # from source during development.
    _ = packaged
    return PRODUCTION_BASE_URL


DEFAULT_BASE_URL = default_base_url()


def normalize_domain(value: str | None, *, default: str = DEFAULT_SHOPIFY_DOMAIN) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        raw = default
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    domain = (parsed.hostname or parsed.netloc or parsed.path or "").strip().lower()
    if domain.startswith("www."):
        domain = domain[4:]
    domain = domain.rstrip(".")
    if not _DOMAIN_RE.match(domain):
        return default
    return domain


def _normalize_store_slug(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    return raw if _STORE_SLUG_VALID_RE.match(raw) else ""


def extract_store_slug_from_admin_url(url: str | None) -> str:
    """从 https://admin.shopify.com/store/<slug>/... 提取真实 slug。无法解析返回空串。"""
    match = _STORE_URL_RE.match(str(url or "").strip())
    if not match:
        return ""
    return match.group(1).lower()


def _normalize_slug_map(raw: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        domain = normalize_domain(key, default="")
        slug = _normalize_store_slug(value)
        if domain and slug:
            out[domain] = slug
    return out


def cached_store_slug_for_domain(domain: str | None, root: str | Path | None = None) -> str:
    """读 runtime config 里缓存的 store slug。无缓存返回空串。"""
    normalized = normalize_domain(domain)
    cfg = load_runtime_config(root)
    cached = cfg.get("shopify_domain_store_slugs") or {}
    if isinstance(cached, dict):
        return _normalize_store_slug(cached.get(normalized))
    return ""


def cache_store_slug_for_domain(domain: str, slug: str, root: str | Path | None = None) -> bool:
    """把 (domain → slug) 写入 runtime config 的 shopify_domain_store_slugs。返回是否落盘。"""
    normalized_domain = normalize_domain(domain)
    normalized_slug = _normalize_store_slug(slug)
    if not normalized_slug:
        return False
    known_slug = known_store_slug_for_domain(normalized_domain)
    if known_slug and normalized_slug != known_slug:
        return False
    cfg = load_runtime_config(root)
    cached = dict(cfg.get("shopify_domain_store_slugs") or {})
    if cached.get(normalized_domain) == normalized_slug:
        return False
    cached[normalized_domain] = normalized_slug
    save_runtime_config(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        browser_user_data_dir=cfg["browser_user_data_dir"],
        shopify_domain=cfg.get("shopify_domain"),
        store_slug_cache=cached,
        root=root,
    )
    return True


def known_store_slug_for_domain(domain: str | None) -> str:
    normalized = normalize_domain(domain)
    return _normalize_store_slug(DEFAULT_SHOPIFY_STORE_SLUG_BY_DOMAIN.get(normalized))


def shopify_store_slug_for_domain(domain: str | None, root: str | Path | None = None) -> str:
    """优先用内置已知 slug；其它域名用 runtime config 缓存，缺失时退到默认 slug。"""
    normalized = normalize_domain(domain)
    configured = DEFAULT_SHOPIFY_STORE_SLUG_BY_DOMAIN.get(normalized)
    if configured:
        return configured
    cached = cached_store_slug_for_domain(normalized, root=root)
    if cached:
        return cached
    return DEFAULT_SHOPIFY_STORE_SLUG


def browser_user_data_dir_for_domain(base_dir: str, domain: str | None) -> str:
    base = (str(base_dir or "").strip() or DEFAULT_BROWSER_USER_DATA_DIR).rstrip("\\/")
    if not base:
        base = DEFAULT_BROWSER_USER_DATA_DIR
    normalized = normalize_domain(domain)
    if normalized == DEFAULT_SHOPIFY_DOMAIN:
        return base
    suffix = normalized.split(".", 1)[0]
    return f"{base}-{suffix}"


def default_domain_items() -> list[dict[str, str]]:
    return [{"domain": DEFAULT_SHOPIFY_DOMAIN}]


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def config_path(root: str | Path | None = None) -> Path:
    base = Path(root) if root is not None else _runtime_root()
    return base / CONFIG_FILENAME


def default_config_path(root: str | Path | None = None) -> Path:
    base = Path(root) if root is not None else _runtime_root()
    return base / DEFAULT_CONFIG_FILENAME


def _read_config_payload(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_runtime_config_payload(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_runtime_config(root: str | Path | None = None) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "base_url": DEFAULT_BASE_URL,
        "api_key": DEFAULT_API_KEY,
        "browser_user_data_dir": DEFAULT_BROWSER_USER_DATA_DIR,
        "shopify_domain": DEFAULT_SHOPIFY_DOMAIN,
        "shopify_domain_store_slugs": {},
    }
    path = config_path(root)
    default_path = default_config_path(root)
    if not path.is_file() and not default_path.is_file():
        return dict(defaults)

    payload = _read_config_payload(path)
    default_payload = _read_config_payload(default_path)

    api_key_from_file = str(payload.get("api_key") or "").strip()
    browser_dir_from_file = str(payload.get("browser_user_data_dir") or "").strip()
    api_key_from_default = str(default_payload.get("api_key") or "").strip()
    browser_dir_from_default = str(default_payload.get("browser_user_data_dir") or "").strip()
    default_slug_map = _normalize_slug_map(default_payload.get("shopify_domain_store_slugs"))
    runtime_slug_map = _normalize_slug_map(payload.get("shopify_domain_store_slugs"))

    result = {
        "base_url": defaults["base_url"],
        "api_key": api_key_from_file or api_key_from_default or defaults["api_key"],
        "browser_user_data_dir": browser_dir_from_file
        or browser_dir_from_default
        or defaults["browser_user_data_dir"],
        "shopify_domain": normalize_domain(
            payload.get("shopify_domain") or default_payload.get("shopify_domain"),
            default=defaults["shopify_domain"],
        ),
        "shopify_domain_store_slugs": {**default_slug_map, **runtime_slug_map},
    }

    # 如果旧 runtime config 里有字段缺失或为空，直接写回补全后的配置。
    # 不走 save_runtime_config()，避免 load/save 相互递归。
    if (
        path.is_file()
        and (not api_key_from_file or not browser_dir_from_file)
        and result["api_key"]
        and result["browser_user_data_dir"]
    ):
        try:
            _write_runtime_config_payload(
                path,
                {
                    "base_url": DEFAULT_BASE_URL,
                    "api_key": result["api_key"],
                    "browser_user_data_dir": result["browser_user_data_dir"],
                    "shopify_domain": result["shopify_domain"],
                    "shopify_domain_store_slugs": result["shopify_domain_store_slugs"],
                },
            )
        except Exception:
            pass

    return result


_UNSET: Any = object()


def save_runtime_config(
    *,
    base_url: str,
    api_key: str,
    browser_user_data_dir: str,
    shopify_domain: str | None = None,
    store_slug_cache: Any = _UNSET,
    root: str | Path | None = None,
) -> Path:
    """写入 runtime config。
    - store_slug_cache 缺省（_UNSET）时保留磁盘上已有的 slug 缓存。
    - api_key / browser_user_data_dir 传入空字符串时**保留**磁盘已有值，避免 GUI 内存里这两个字段被
      意外清空（init 时序、用户误操作等）后通过 save 把 portable 内嵌的凭据擦掉。
    """
    path = config_path(root)
    existing_cfg = load_runtime_config(root)
    if store_slug_cache is _UNSET:
        existing_cache = existing_cfg.get("shopify_domain_store_slugs") or {}
    else:
        existing_cache = store_slug_cache or {}
    api_key_clean = (api_key or "").strip() or (existing_cfg.get("api_key") or "").strip()
    browser_dir_clean = (browser_user_data_dir or "").strip() or (
        existing_cfg.get("browser_user_data_dir") or ""
    ).strip()
    payload: dict[str, Any] = {
        "base_url": DEFAULT_BASE_URL,
        "api_key": api_key_clean,
        "browser_user_data_dir": browser_dir_clean,
        "shopify_domain": normalize_domain(shopify_domain),
        "shopify_domain_store_slugs": _normalize_slug_map(existing_cache),
    }
    _write_runtime_config_payload(path, payload)
    return path
