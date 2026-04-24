from __future__ import annotations

import argparse
import contextlib
import io
from pathlib import Path
from typing import Callable

from tools.shopify_image_localizer import api_client, settings, storage
from tools.shopify_image_localizer.rpa import run_product_cdp


StatusCallback = Callable[[str], None]


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
) -> argparse.Namespace:
    normalized_lang = str(lang or "").strip().lower()
    return argparse.Namespace(
        product_code=str(product_code or "").strip().lower(),
        lang=normalized_lang,
        shop_locale=normalized_lang,
        language=run_product_cdp.LANGUAGE_LABELS.get(normalized_lang, normalized_lang),
        product_id=str(shopify_product_id or "").strip(),
        store_domain=run_product_cdp.DEFAULT_STORE_DOMAIN,
        bootstrap_timeout_s=120,
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
    status_cb: StatusCallback | None = None,
) -> dict:
    reporter = status_cb or _noop
    workspace = storage.create_workspace(product_code, lang)

    def emit(message: str) -> None:
        storage.append_log(workspace.log_path, message)
        reporter(message)

    emit("正在保存运行配置")
    settings.save_runtime_config(
        base_url=base_url,
        api_key=api_key,
        browser_user_data_dir=browser_user_data_dir,
    )

    args = _build_batch_args(
        product_code=product_code,
        lang=lang,
        shopify_product_id=shopify_product_id,
    )
    if args.product_id:
        emit(f"使用手动 Shopify ID: {args.product_id}")
    emit("开始连续替换流程：先替换轮播图，再替换详情图")

    writer = _StatusWriter(emit)
    try:
        with contextlib.redirect_stdout(writer):
            result = run_product_cdp.run(args)
    finally:
        writer.flush()

    output_path = Path(str(result.get("workspace") or workspace.root)) / f"shopify_batch_{args.lang}_result.json"
    emit(f"执行完成，结果文件：{output_path}")
    return {
        **result,
        "status": "done",
        "mode": "batch_cdp",
        "workspace_root": str(result.get("workspace") or workspace.root),
        "manifest_path": str(output_path),
    }


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
    try:
        result = run_shopify_localizer(
            base_url=base_url,
            api_key=api_key,
            browser_user_data_dir=browser_user_data_dir,
            product_code=task["product_code"],
            lang=task["lang"],
            shopify_product_id=task.get("shopify_product_id") or "",
            status_cb=reporter,
        )
    except Exception as exc:
        api_client.fail_task(
            base_url,
            api_key,
            int(task["id"]),
            error_code=exc.__class__.__name__,
            error_message=str(exc),
        )
        return {"status": "failed", "task": task, "error": str(exc)}

    api_client.complete_task(
        base_url,
        api_key,
        int(task["id"]),
        result=result,
    )
    return {"status": "completed", "task": task, "result": result}
