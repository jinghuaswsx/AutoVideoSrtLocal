from __future__ import annotations

import argparse
import contextlib
import io
import json
import time
import urllib.request
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from tools.shopify_image_localizer import (
    api_client,
    cancellation,
    domain_image_mapping,
    ext_bridge,
    locales,
    settings,
    storage,
)
from tools.shopify_image_localizer.browser import session
from tools.shopify_image_localizer.rpa import ez_cdp, run_product_cdp


SHOPIFY_ADMIN_ROOT_URL = "https://admin.shopify.com/"
SHOPIFY_ADMIN_STORE_URL_CONTAINS = "admin.shopify.com/store/"
BROWSER_URL_CAPTURE_TIMEOUT_S = 8.0


StatusCallback = Callable[[str], None]
ShopifyProductIdCallback = Callable[[str], None]
VisualPairConfirmCallback = Callable[[list[dict]], bool]


def _noop(_message: str) -> None:
    return None


class _StatusWriter(io.TextIOBase):
    def __init__(self, emit: StatusCallback) -> None:
        self._emit = emit
        self._buffer = ""

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        if not text:
            return 0
        value = str(text)
        self._buffer += value
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.rstrip()
            if line:
                self._emit(line)
        return len(value)

    def flush(self) -> None:
        line = self._buffer.strip()
        self._buffer = ""
        if line:
            self._emit(line)


def _build_batch_args(
    *,
    product_code: str,
    lang: str,
    shopify_product_id: str,
    shopify_domain: str = settings.DEFAULT_SHOPIFY_DOMAIN,
    browser_user_data_dir: str = settings.DEFAULT_BROWSER_USER_DATA_DIR,
    shopify_language_name: str = "",
    shop_locale: str = "",
) -> argparse.Namespace:
    normalized_lang = str(lang or "").strip().lower()
    normalized_domain = settings.normalize_domain(shopify_domain)
    language_name = str(shopify_language_name or "").strip() or locales.english_name_for(normalized_lang)
    taa_shop_locale = locales.translate_and_adapt_locale_for(shop_locale or normalized_lang)
    return argparse.Namespace(
        product_code=str(product_code or "").strip().lower(),
        lang=normalized_lang,
        shop_locale=normalized_lang,
        taa_shop_locale=taa_shop_locale,
        language=language_name,
        product_id=str(shopify_product_id or "").strip(),
        store_domain=normalized_domain,
        store_slug=settings.shopify_store_slug_for_domain(normalized_domain),
        browser_user_data_dir=str(browser_user_data_dir or "").strip(),
        bootstrap_timeout_s=600,
        port=run_product_cdp.ez_cdp.DEFAULT_CDP_PORT,
        carousel_limit=0,
        skip_carousel=False,
        skip_detail=False,
        skip_existing_carousel=False,
        source_index_map="",
        replace_shopify_cdn=True,
        no_preserve_detail_size=False,
        no_original_detail_fallback=False,
        no_detail_reload_verify=False,
    )


def run_shopify_localizer(
    *,
    base_url: str,
    api_key: str,
    browser_user_data_dir: str,
    product_code: str,
    lang: str,
    shopify_product_id: str = "",
    shopify_domain: str = settings.DEFAULT_SHOPIFY_DOMAIN,
    shopify_language_name: str = "",
    shop_locale: str = "",
    status_cb: StatusCallback | None = None,
    shopify_product_id_cb: ShopifyProductIdCallback | None = None,
    visual_pair_confirm_cb: VisualPairConfirmCallback | None = None,
    cancel_token: cancellation.CancellationToken | None = None,
    skip_kill_chrome: bool = True,
) -> dict:
    reporter = status_cb or _noop
    cancellation.throw_if_cancelled(cancel_token)
    normalized_domain = settings.normalize_domain(shopify_domain)
    effective_browser_dir = settings.browser_user_data_dir_for_domain(browser_user_data_dir, normalized_domain)
    workspace = storage.create_workspace(product_code, lang)

    def emit(message: str) -> None:
        storage.append_log(workspace.log_path, message)
        reporter(message)

    emit("正在保存运行配置")
    settings.save_runtime_config(
        base_url=base_url,
        api_key=api_key,
        browser_user_data_dir=browser_user_data_dir,
        shopify_domain=normalized_domain,
    )
    if skip_kill_chrome:
        emit("复用现有浏览器会话（仅在浏览器不可用或 profile 不匹配时由 CDP 初始化流程恢复）")
    else:
        emit("正在清理旧 Chrome 浏览器进程")
        session.kill_chrome_for_profile(effective_browser_dir)

    args = _build_batch_args(
        product_code=product_code,
        lang=lang,
        shopify_product_id=shopify_product_id,
        shopify_domain=normalized_domain,
        browser_user_data_dir=effective_browser_dir,
        shopify_language_name=shopify_language_name,
        shop_locale=shop_locale,
    )
    cancellation.throw_if_cancelled(cancel_token)
    resolved_product_id = resolve_shopify_product_id(
        base_url=base_url,
        api_key=api_key,
        product_code=args.product_code,
        lang=args.lang,
        shopify_product_id=args.product_id,
        shopify_domain=normalized_domain,
    )
    args.product_id = resolved_product_id
    if shopify_product_id_cb is not None:
        shopify_product_id_cb(resolved_product_id)
    if args.product_id:
        emit(f"使用手动 Shopify ID: {args.product_id}")
    emit("开始连续替换流程：先替换轮播图，再替换详情图")

    writer = _StatusWriter(emit)
    try:
        with contextlib.redirect_stdout(writer):
            run_kwargs = {"cancel_token": cancel_token}
            if visual_pair_confirm_cb is not None:
                run_kwargs["visual_pair_confirm_cb"] = visual_pair_confirm_cb
            result = run_product_cdp.run(args, **run_kwargs)
    finally:
        writer.flush()
    cancellation.throw_if_cancelled(cancel_token)

    output_path = Path(str(result.get("workspace") or workspace.root)) / f"shopify_batch_{args.lang}_result.json"
    emit(f"执行完成，结果文件：{output_path}")
    return {
        **result,
        "status": "done",
        "mode": "batch_cdp",
        "shopify_domain": normalized_domain,
        "browser_user_data_dir": effective_browser_dir,
        "workspace_root": str(result.get("workspace") or workspace.root),
        "manifest_path": str(output_path),
    }


def _task_domain_rows(task: dict) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in task.get("link_urls") or []:
        if not isinstance(item, dict):
            continue
        domain = settings.normalize_domain(item.get("domain") or item.get("url"))
        if domain in seen:
            continue
        seen.add(domain)
        rows.append({"domain": domain, "url": str(item.get("url") or "").strip()})
    if not rows:
        domain = settings.normalize_domain(task.get("link_url"))
        rows.append({"domain": domain, "url": str(task.get("link_url") or "").strip()})
    return rows


def run_worker_once(
    *,
    base_url: str,
    api_key: str,
    browser_user_data_dir: str,
    worker_id: str,
    status_cb: StatusCallback | None = None,
) -> dict:
    reporter = status_cb or _noop
    claimed = api_client.claim_task(
        base_url,
        api_key,
        worker_id=worker_id,
    )
    task = claimed.get("task")
    if not task:
        reporter("当前没有待处理任务")
        return {"status": "idle"}

    reporter(
        f"领取任务 #{task.get('id')}: {task.get('product_code')} / {task.get('lang')}"
    )
    link_urls = task.get("link_urls") or []
    if link_urls:
        reporter(f"任务关联 {len(link_urls)} 个商品域名链接")
    try:
        domain_rows = _task_domain_rows(task)
        domain_results: list[dict] = []
        for row in domain_rows:
            domain = row["domain"]
            reporter(f"开始处理域名店铺：{domain}")
            task_shopify_product_id = (
                task.get("shopify_product_id") or ""
                if domain == settings.DEFAULT_SHOPIFY_DOMAIN
                else ""
            )
            domain_result = run_shopify_localizer(
                base_url=base_url,
                api_key=api_key,
                browser_user_data_dir=browser_user_data_dir,
                product_code=task["product_code"],
                lang=task["lang"],
                shopify_product_id=task_shopify_product_id,
                shopify_domain=domain,
                status_cb=reporter,
            )
            domain_results.append({
                "domain": domain,
                "url": row.get("url") or "",
                "result": domain_result,
            })
        result = dict(domain_results[-1]["result"]) if domain_results else {"status": "done"}
        if domain_results:
            result["shopify_domain"] = domain_results[-1]["domain"]
            result["domains_processed"] = [row["domain"] for row in domain_results]
            result["domain_results"] = domain_results
    except Exception as exc:
        api_client.fail_task(
            base_url,
            api_key,
            int(task["id"]),
            error_code=exc.__class__.__name__,
            error_message=str(exc),
        )
        return {"status": "failed", "task": task, "error": str(exc)}

    if link_urls:
        result["link_urls"] = link_urls
        result["link_url"] = link_urls[0].get("url") or task.get("link_url") or ""
    elif task.get("link_url"):
        result["link_url"] = task.get("link_url")
    api_client.complete_task(
        base_url,
        api_key,
        int(task["id"]),
        result=result,
    )
    return {"status": "completed", "task": task, "result": result}


def _save_resolved_shopify_id(
    *,
    base_url: str,
    api_key: str,
    product_code: str,
    domain: str,
    shopify_product_id: str,
) -> None:
    """将实时解析到的 Shopify product ID 异步存档到服务端 per-domain 缓存。"""
    try:
        api_client.save_shopify_product_id(
            base_url,
            api_key,
            product_code=product_code,
            domain=domain,
            shopify_product_id=shopify_product_id,
        )
    except Exception:
        pass


def _fetch_storefront_id(
    product_code: str,
    domain: str,
) -> str:
    """从 Storefront API 实时获取 Shopify product ID。失败返回空串。"""
    try:
        product = run_product_cdp.fetch_storefront_product(
            product_code,
            store_domain=domain,
        )
        return str(product.get("id") or "").strip()
    except Exception:
        return ""


def resolve_shopify_product_id(
    *,
    base_url: str,
    api_key: str,
    product_code: str,
    lang: str,
    shopify_product_id: str = "",
    shopify_domain: str = settings.DEFAULT_SHOPIFY_DOMAIN,
) -> str:
    """解析 Shopify product ID。

    优先级：
    1. 用户手动填写 → 直接使用
    2. 目标域名 Storefront API → 实时抓取（权威），成功则存档到服务端
    3. 服务端 per-domain 缓存 → 兜底
    4. 全部失败 → 报错
    """
    manual_id = str(shopify_product_id or "").strip()
    if manual_id:
        return manual_id

    normalized_product_code = str(product_code or "").strip().lower()
    normalized_lang = str(lang or "").strip().lower()
    normalized_domain = settings.normalize_domain(shopify_domain)

    # Step 1: 目标域名 Storefront API 实时抓取
    resolved = _fetch_storefront_id(normalized_product_code, normalized_domain)
    if resolved:
        _save_resolved_shopify_id(
            base_url=base_url,
            api_key=api_key,
            product_code=normalized_product_code,
            domain=normalized_domain,
            shopify_product_id=resolved,
        )
        return resolved

    # Step 2: 服务端 per-domain 缓存（带 domain 参数查 media_product_shopify_ids）
    try:
        payload = api_client.fetch_bootstrap(
            base_url,
            api_key,
            normalized_product_code,
            normalized_lang,
            domain=normalized_domain,
        )
        product = payload.get("product") or {}
        cached = str(product.get("shopify_product_id") or "").strip()
        if cached:
            return cached
    except api_client.ApiError as exc:
        if exc.status_code != 409:
            raise

    raise RuntimeError("未能解析 Shopify ID，请手动填写 Shopify ID 后再打开。")


def preview_domain_image_mapping(
    *,
    product_code: str,
    shopify_domain: str = settings.DEFAULT_SHOPIFY_DOMAIN,
) -> dict:
    """生成当前域名相对默认域名的图片映射预览。

    这是桌面端“映射管理”的只读入口：非默认域名只建立 alias，不下载、
    不翻译、不保存第二份图片。
    """
    normalized_product_code = str(product_code or "").strip().lower()
    if not normalized_product_code:
        raise RuntimeError("商品 ID 不能为空")

    target_domain = settings.normalize_domain(shopify_domain)
    canonical_domain = settings.DEFAULT_SHOPIFY_DOMAIN
    if target_domain == canonical_domain:
        return {
            "status": "default_domain",
            "product_code": normalized_product_code,
            "canonical_domain": canonical_domain,
            "target_domain": target_domain,
            "message": "默认域名无需跨域图片映射",
            "summary": domain_image_mapping.summarize_domain_image_mapping(
                domain_image_mapping.DomainImageMapping(
                    canonical_domain=canonical_domain,
                    target_domain=target_domain,
                )
            ),
        }

    canonical_product = run_product_cdp.fetch_storefront_product(
        normalized_product_code,
        store_domain=canonical_domain,
    )
    target_product = run_product_cdp.fetch_storefront_product(
        normalized_product_code,
        store_domain=target_domain,
    )
    mapping = domain_image_mapping.build_domain_image_mapping(
        canonical_product=canonical_product,
        target_product=target_product,
        canonical_detail_product=canonical_product,
        target_detail_product=target_product,
        canonical_domain=canonical_domain,
        target_domain=target_domain,
    )
    return {
        "status": "mapped",
        "product_code": normalized_product_code,
        "canonical_domain": canonical_domain,
        "target_domain": target_domain,
        "canonical_product_id": str(canonical_product.get("id") or "").strip(),
        "target_product_id": str(target_product.get("id") or "").strip(),
        "summary": domain_image_mapping.summarize_domain_image_mapping(mapping),
    }


def build_shopify_target_url(
    *,
    target: str,
    shopify_product_id: str,
    lang: str,
    shopify_domain: str = settings.DEFAULT_SHOPIFY_DOMAIN,
) -> str:
    normalized_target = str(target or "").strip().lower()
    product_id = str(shopify_product_id or "").strip()
    if not product_id:
        raise RuntimeError("Shopify ID 不能为空")
    store_slug = settings.shopify_store_slug_for_domain(shopify_domain)
    if normalized_target == "ez":
        url = session.build_ez_url(product_id, store_slug=store_slug)
    elif normalized_target == "detail":
        url = session.build_translate_url(product_id, str(lang or "").strip(), store_slug=store_slug)
    else:
        raise ValueError(f"unsupported Shopify target: {target}")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "admin.shopify.com" or not parsed.path.startswith("/store/"):
        raise RuntimeError(f"生成的 Shopify 管理后台 URL 非法，必须是 admin.shopify.com/store 页面，已阻止打开：{url}")
    return url


def open_shopify_target(
    *,
    target: str,
    base_url: str,
    api_key: str,
    browser_user_data_dir: str,
    product_code: str,
    lang: str,
    shopify_product_id: str = "",
    shop_locale: str = "",
    shopify_domain: str = settings.DEFAULT_SHOPIFY_DOMAIN,
) -> dict:
    normalized_domain = settings.normalize_domain(shopify_domain)
    effective_browser_dir = settings.browser_user_data_dir_for_domain(browser_user_data_dir, normalized_domain)
    settings.save_runtime_config(
        base_url=base_url,
        api_key=api_key,
        browser_user_data_dir=browser_user_data_dir,
        shopify_domain=normalized_domain,
    )
    product_id = resolve_shopify_product_id(
        base_url=base_url,
        api_key=api_key,
        product_code=product_code,
        lang=lang,
        shopify_product_id=shopify_product_id,
        shopify_domain=normalized_domain,
    )
    url = build_shopify_target_url(
        target=target,
        shopify_product_id=product_id,
        lang=shop_locale or lang,
        shopify_domain=normalized_domain,
    )
    ez_cdp.open_managed_tab(user_data_dir=effective_browser_dir, target_url=url)
    return {
        "status": "opened",
        "target": str(target or "").strip().lower(),
        "shopify_product_id": product_id,
        "lang": str(lang or "").strip().lower(),
        "shopify_domain": normalized_domain,
        "browser_user_data_dir": effective_browser_dir,
        "url": url,
    }


def open_shopify_login_page(
    *,
    base_url: str,
    api_key: str,
    browser_user_data_dir: str,
    shopify_domain: str = settings.DEFAULT_SHOPIFY_DOMAIN,
) -> dict:
    """确保同一个 CDP Chrome 已启动，保留第一 tab 为 Google，并把 Shopify 登录页开到后续 tab。"""
    normalized_domain = settings.normalize_domain(shopify_domain)
    effective_browser_dir = settings.browser_user_data_dir_for_domain(browser_user_data_dir, normalized_domain)
    settings.save_runtime_config(
        base_url=base_url,
        api_key=api_key,
        browser_user_data_dir=browser_user_data_dir,
        shopify_domain=normalized_domain,
    )
    ez_cdp.open_managed_tab(user_data_dir=effective_browser_dir, target_url=SHOPIFY_ADMIN_ROOT_URL)
    return {
        "status": "opened",
        "target": "shopify_login",
        "shopify_domain": normalized_domain,
        "browser_user_data_dir": effective_browser_dir,
        "url": SHOPIFY_ADMIN_ROOT_URL,
    }


def _domain_title_token(shopify_domain: str | None) -> str:
    domain = settings.normalize_domain(shopify_domain, default="")
    token = domain.split(".", 1)[0].strip().lower()
    return token


def _tab_matches_shopify_domain(tab: dict, shopify_domain: str | None) -> bool:
    token = _domain_title_token(shopify_domain)
    if not token:
        return False
    haystack = f"{tab.get('title') or ''} {tab.get('url') or ''}".lower()
    return token in haystack


def _select_current_admin_store_url_from_tabs(
    tabs: list[dict] | None,
    *,
    shopify_domain: str | None = None,
    require_domain_match: bool = False,
) -> tuple[str, str]:
    """Pick the current Shopify admin store URL from live Chrome tabs."""
    candidates: list[tuple[int, int, float, int, str]] = []
    for tab in tabs or []:
        if not isinstance(tab, dict):
            continue
        url = str(tab.get("url") or "").strip()
        if not settings.extract_store_slug_from_admin_url(url):
            continue
        domain_match = 1 if _tab_matches_shopify_domain(tab, shopify_domain) else 0
        if require_domain_match and not domain_match:
            continue
        try:
            last_accessed = float(tab.get("lastAccessed") or 0)
        except (TypeError, ValueError):
            last_accessed = 0
        try:
            tab_id = int(tab.get("id") or 0)
        except (TypeError, ValueError):
            tab_id = 0
        candidates.append((domain_match, 1 if tab.get("active") else 0, last_accessed, tab_id, url))
    if not candidates:
        return "", ""
    _domain_match, active, _last_accessed, _tab_id, url = max(
        candidates,
        key=lambda item: (item[0], item[1], item[2], item[3]),
    )
    return url, "active_tab" if active else "open_tab"


def _read_current_admin_store_url_from_cdp(
    port: int = ez_cdp.DEFAULT_CDP_PORT,
    *,
    expected_browser_user_data_dir: str | None = None,
    shopify_domain: str | None = None,
) -> tuple[str, str]:
    """Read live Chrome tabs from CDP /json without touching Chrome History."""
    if expected_browser_user_data_dir and not ez_cdp._cdp_port_matches_profile(
        port,
        expected_browser_user_data_dir,
    ):
        return "", "cdp_profile_mismatch"
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/json", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return "", "cdp_unavailable"
    if not isinstance(payload, list):
        return "", "cdp_tabs_not_found"
    tabs: list[dict] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            continue
        if item.get("type") and item.get("type") != "page":
            continue
        tabs.append({
            "id": index + 1,
            "url": item.get("url"),
            "title": item.get("title"),
            "active": False,
            "lastAccessed": 0,
        })
    require_domain_match = bool(_domain_title_token(shopify_domain))
    url, _source = _select_current_admin_store_url_from_tabs(
        tabs,
        shopify_domain=shopify_domain,
        require_domain_match=require_domain_match,
    )
    if url:
        return url, "cdp_tab"
    if require_domain_match:
        return "", "cdp_domain_not_found"
    return "", "cdp_tabs_not_found"


def _fallback_current_admin_store_url(
    previous_source: str,
    *,
    expected_browser_user_data_dir: str | None = None,
    shopify_domain: str | None = None,
) -> tuple[str, str]:
    url, source = _read_current_admin_store_url_from_cdp(
        expected_browser_user_data_dir=expected_browser_user_data_dir,
        shopify_domain=shopify_domain,
    )
    if url:
        return url, source
    if source in {"cdp_profile_mismatch", "cdp_domain_not_found"}:
        return "", source
    return "", previous_source or source


def _read_current_admin_store_url_from_browser(
    timeout_s: float = BROWSER_URL_CAPTURE_TIMEOUT_S,
    *,
    expected_browser_user_data_dir: str | None = None,
    shopify_domain: str | None = None,
) -> tuple[str, str]:
    """Read the current URL from Chrome tabs via the bundled extension bridge.

    The "already logged in" button must not use Chrome History: while Chrome is
    still running, History can lag behind the visible tab and return an old slug.
    """
    bridge = ext_bridge.ExtensionBridge()
    deadline = time.monotonic() + max(1.0, float(timeout_s or 0))
    try:
        bridge.start()
    except OSError:
        return _fallback_current_admin_store_url(
            "extension_bridge_unavailable",
            expected_browser_user_data_dir=expected_browser_user_data_dir,
            shopify_domain=shopify_domain,
        )
    try:
        if not bridge.wait_client(timeout_s=min(4.0, max(0.5, deadline - time.monotonic()))):
            return _fallback_current_admin_store_url(
                "extension_not_connected",
                expected_browser_user_data_dir=expected_browser_user_data_dir,
                shopify_domain=shopify_domain,
            )
        queries = [
            {
                "url_contains": SHOPIFY_ADMIN_STORE_URL_CONTAINS,
                "active": True,
                "last_focused_window": True,
            },
            {"url_contains": SHOPIFY_ADMIN_STORE_URL_CONTAINS},
        ]
        for params in queries:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                tabs = bridge.call("list_tabs", params, timeout_s=max(0.5, remaining))
            except Exception:
                tabs = []
            url, source = _select_current_admin_store_url_from_tabs(
                tabs if isinstance(tabs, list) else [],
                shopify_domain=shopify_domain,
            )
            if url:
                return url, source
        return _fallback_current_admin_store_url(
            "browser_tabs_not_found",
            expected_browser_user_data_dir=expected_browser_user_data_dir,
            shopify_domain=shopify_domain,
        )
    finally:
        bridge.stop()


def confirm_shopify_login_capture_slug_from_url(
    *,
    browser_user_data_dir: str,
    shopify_domain: str,
    admin_url: str,
) -> dict:
    """Manual fallback for the login dialog: parse a pasted live admin URL and cache its slug."""
    normalized_domain = settings.normalize_domain(shopify_domain)
    effective_browser_dir = settings.browser_user_data_dir_for_domain(browser_user_data_dir, normalized_domain)
    url = str(admin_url or "").strip()
    slug = settings.extract_store_slug_from_admin_url(url)
    if not slug:
        return {
            "status": "not_found",
            "shopify_domain": normalized_domain,
            "browser_user_data_dir": effective_browser_dir,
            "url": url,
            "slug": "",
            "source": "manual_url",
            "message": (
                "输入的 URL 不是 admin.shopify.com/store/<slug>/ 形式；"
                "请复制浏览器地址栏里当前 Shopify 后台店铺主页 URL 后再保存。"
            ),
        }
    settings.cache_store_slug_for_domain(normalized_domain, slug)
    return {
        "status": "captured",
        "shopify_domain": normalized_domain,
        "browser_user_data_dir": effective_browser_dir,
        "url": url,
        "slug": slug,
        "source": "manual_url",
    }


def confirm_shopify_login_capture_slug(
    *,
    browser_user_data_dir: str,
    shopify_domain: str,
) -> dict:
    """用户点「已登录」后调用：从当前 Chrome 标签页读取目标店铺 URL，提取 slug 写缓存。"""
    normalized_domain = settings.normalize_domain(shopify_domain)
    effective_browser_dir = settings.browser_user_data_dir_for_domain(browser_user_data_dir, normalized_domain)
    best_url, source = _read_current_admin_store_url_from_browser(
        expected_browser_user_data_dir=effective_browser_dir,
        shopify_domain=normalized_domain,
    )
    slug = settings.extract_store_slug_from_admin_url(best_url) if best_url else ""
    if not slug:
        return {
            "status": "not_found",
            "shopify_domain": normalized_domain,
            "browser_user_data_dir": effective_browser_dir,
            "url": best_url,
            "slug": "",
            "source": source,
            "message": (
                "未从当前 Chrome 浏览器标签页读取到 admin.shopify.com/store/<slug>/ 形式的 URL；"
                "请确认浏览器已登录并停留在目标店铺主页，再点「已登录」。"
            ),
        }
    settings.cache_store_slug_for_domain(normalized_domain, slug)
    return {
        "status": "captured",
        "shopify_domain": normalized_domain,
        "browser_user_data_dir": effective_browser_dir,
        "url": best_url,
        "slug": slug,
        "source": source,
    }
