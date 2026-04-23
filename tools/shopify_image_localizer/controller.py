from __future__ import annotations

from typing import Callable

from tools.shopify_image_localizer import api_client, downloader, storage
from tools.shopify_image_localizer.browser import run_shopify_localizer as run_browser_localizer


StatusCallback = Callable[[str], None]


def _noop(_message: str) -> None:
    return None


def run_shopify_localizer(
    *,
    base_url: str,
    api_key: str,
    browser_user_data_dir: str,
    product_code: str,
    lang: str,
    status_cb: StatusCallback | None = None,
) -> dict:
    user_reporter = status_cb or _noop

    user_reporter("正在拉取任务数据")
    bootstrap = api_client.fetch_bootstrap(base_url, api_key, product_code, lang)

    workspace = storage.create_workspace(product_code, lang)

    def reporter(message: str) -> None:
        storage.append_log(workspace.log_path, message)
        user_reporter(message)

    reporter("已创建本地工作目录")
    reporter("正在下载英文参考图")
    reference_images = downloader.download_images(
        bootstrap.get("reference_images") or [],
        workspace.source_en_dir,
        status_cb=reporter,
    )
    reporter("正在下载目标语言图片")
    localized_images = downloader.download_images(
        bootstrap.get("localized_images") or [],
        workspace.source_localized_dir,
        status_cb=reporter,
    )

    reporter("正在启动 Shopify 浏览器")
    browser_result = run_browser_localizer(
        browser_user_data_dir=browser_user_data_dir,
        bootstrap=bootstrap,
        reference_images=reference_images,
        localized_images=localized_images,
        workspace=workspace,
        status_cb=reporter,
    )
    manifest = {
        "product_code": product_code,
        "lang": lang,
        "bootstrap": bootstrap,
        "reference_images": reference_images,
        "localized_images": localized_images,
        "browser_result": browser_result,
    }
    storage.write_json(workspace.manifest_path, manifest)
    reporter("已写入 manifest.json")
    return {
        **browser_result,
        "workspace_root": str(workspace.root),
        "manifest_path": str(workspace.manifest_path),
    }
