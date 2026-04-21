from __future__ import annotations

from typing import Any

import requests

from link_check_desktop import (
    analysis,
    bootstrap_api,
    browser_worker,
    report,
    result_schema,
    settings,
    storage,
)


DEFAULT_BASE_URL = settings.DEFAULT_BASE_URL
DEFAULT_API_KEY = settings.DEFAULT_API_KEY


def _noop(_message: str) -> None:
    return None


def _download_references(reference_images: list[dict[str, Any]], workspace, status_cb) -> list[dict[str, Any]]:
    downloaded: list[dict[str, Any]] = []
    for index, item in enumerate(reference_images, start=1):
        status_cb(f"正在下载参考图 {index}/{len(reference_images)}")
        response = requests.get(item["download_url"], timeout=30)
        response.raise_for_status()

        filename = item.get("filename") or f"reference-{index:03d}.jpg"
        output_path = workspace.reference_dir / filename
        output_path.write_bytes(response.content)

        downloaded.append({
            **item,
            "local_path": str(output_path),
        })
    return downloaded


def run_link_check(
    *,
    base_url: str = DEFAULT_BASE_URL,
    api_key: str = DEFAULT_API_KEY,
    target_url: str,
    status_cb=None,
) -> dict[str, Any]:
    reporter = status_cb or _noop

    reporter("正在解析产品和语种")
    bootstrap = bootstrap_api.fetch_bootstrap(base_url, api_key, target_url)
    workspace = storage.create_workspace(bootstrap["product"]["id"])

    reference_images = _download_references(
        bootstrap.get("reference_images") or [],
        workspace,
        reporter,
    )

    reporter("正在通过浏览器锁定目标页")
    page_result = browser_worker.capture_page(
        target_url=target_url,
        target_language=bootstrap["target_language"],
        workspace=workspace,
        status_cb=reporter,
    )
    if not page_result.get("locked"):
        raise RuntimeError("target page was not locked before download")

    reporter("正在分析图片")
    analyzed = analysis.analyze_downloaded_images(
        downloaded_images=page_result.get("downloaded_images") or [],
        reference_images=reference_images,
        target_language=bootstrap["target_language"],
        target_language_name=bootstrap["target_language_name"],
    )

    result = {
        "product": dict(bootstrap["product"]),
        "target_language": bootstrap["target_language"],
        "target_language_name": bootstrap["target_language_name"],
        "matched_by": bootstrap.get("matched_by") or "",
        "normalized_url": bootstrap.get("normalized_url") or target_url,
        "workspace_root": str(workspace.root),
        "reference_images": reference_images,
        "page": page_result,
        "analysis": analyzed,
    }

    storage.write_json(
        workspace.root / "task.json",
        result_schema.build_task_manifest(target_url, bootstrap, workspace),
    )
    storage.write_json(workspace.root / "page_info.json", page_result)
    storage.write_json(workspace.compare_dir / "result.json", analyzed)
    result["report_html_path"] = str(report.write_report(result))

    return result
