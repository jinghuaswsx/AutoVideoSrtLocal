from __future__ import annotations

import html
import os
import webbrowser
from pathlib import Path
from typing import Any


_OVERALL_LABELS = {
    "done": "已完成",
    "unfinished": "未完成",
    "pass": "通过",
    "review": "待复核",
    "replace": "需替换",
}

_DECISION_LABELS = {
    "pass": "通过",
    "review": "待复核",
    "replace": "需替换",
    "no_text": "无文字",
    "failed": "处理失败",
}

_REFERENCE_STATUS_LABELS = {
    "matched": "已匹配",
    "weak_match": "弱匹配",
    "not_matched": "未匹配",
    "not_provided": "未提供",
}

_BINARY_STATUS_LABELS = {
    "pass": "通过",
    "fail": "未通过",
    "skipped": "未执行",
    "error": "执行失败",
}

_SAME_IMAGE_STATUS_LABELS = {
    "done": "已完成",
    "skipped": "未执行",
    "error": "执行失败",
}

_KIND_LABELS = {
    "detail": "详情图",
    "carousel": "轮播图",
    "cover": "主图",
    "page_image": "页面图",
}

_INLINE_CSS = """
:root {
  --bg: #f4f7fb;
  --panel: #ffffff;
  --border: #d8e2ee;
  --border-strong: #b9cadb;
  --fg: #183b56;
  --muted: #5e748a;
  --accent: #2563eb;
  --accent-soft: #ebf3ff;
  --success-bg: #e8f7ee;
  --success-fg: #166534;
  --warning-bg: #fff6dd;
  --warning-fg: #9a6700;
  --danger-bg: #fdecec;
  --danger-fg: #b42318;
  --info-bg: #eef5ff;
  --info-fg: #2459c3;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  background: var(--bg);
  color: var(--fg);
  font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
  line-height: 1.5;
}

.report-shell {
  max-width: 1560px;
  margin: 0 auto;
  padding: 24px;
}

.panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 20px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}

.panel + .panel {
  margin-top: 18px;
}

.kicker {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 0 10px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.page-head h1 {
  margin: 12px 0 6px;
  font-size: 28px;
  line-height: 1.25;
}

.page-head p {
  margin: 0;
  color: var(--muted);
}

.tip-box {
  min-width: 280px;
  max-width: 360px;
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: linear-gradient(180deg, #eef5ff, #ffffff);
}

.tip-box strong {
  display: block;
  margin-bottom: 6px;
}

.mono {
  font-family: "JetBrains Mono", Consolas, monospace;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-top: 18px;
}

.summary-card {
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 14px 16px;
  background: #fbfdff;
}

.summary-card strong {
  display: block;
  color: var(--muted);
  font-size: 13px;
  font-weight: 600;
}

.summary-card span {
  display: block;
  margin-top: 6px;
  font-size: 22px;
  font-weight: 700;
}

.meta-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 12px;
}

.meta-chip {
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 12px 14px;
  background: #fbfdff;
}

.meta-chip strong {
  display: block;
  margin-bottom: 6px;
  color: var(--muted);
  font-size: 13px;
}

.meta-chip span {
  display: block;
}

.result-list {
  display: grid;
  gap: 14px;
  margin-top: 18px;
}

.result-row {
  display: grid;
  grid-template-columns: 200px 200px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 16px;
  background: #ffffff;
}

.preview-box {
  width: 200px;
  height: 200px;
  border: 1px solid var(--border-strong);
  border-radius: 12px;
  background: linear-gradient(180deg, #f8fbff, #f2f5f8);
  overflow: hidden;
}

.preview-box img {
  width: 200px;
  height: 200px;
  object-fit: contain;
  display: block;
}

.preview-label {
  margin-bottom: 8px;
  color: var(--muted);
  font-size: 13px;
  font-weight: 700;
}

.preview-empty {
  width: 200px;
  height: 200px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--muted);
  font-size: 13px;
  text-align: center;
  padding: 12px;
}

.badge-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.badge {
  display: inline-flex;
  align-items: center;
  min-height: 26px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  border: 1px solid transparent;
}

.badge.info {
  background: var(--info-bg);
  color: var(--info-fg);
}

.badge.success {
  background: var(--success-bg);
  color: var(--success-fg);
}

.badge.warning {
  background: var(--warning-bg);
  color: var(--warning-fg);
}

.badge.danger {
  background: var(--danger-bg);
  color: var(--danger-fg);
}

.info-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-top: 12px;
}

.info-card {
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 12px;
  background: #fbfdff;
}

.info-card strong {
  display: block;
  margin-bottom: 4px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
}

.info-card span {
  display: block;
  font-size: 14px;
  word-break: break-word;
}

.reason-box {
  margin-top: 12px;
  padding: 12px 14px;
  border-radius: 12px;
  background: #f7fafc;
  color: var(--fg);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

details {
  margin-top: 12px;
}

details summary {
  cursor: pointer;
  color: var(--accent);
  font-weight: 600;
}

.details-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 12px;
}

.empty-panel {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 180px;
  border: 1px dashed var(--border-strong);
  border-radius: 14px;
  color: var(--muted);
  background: #fbfdff;
}

@media (max-width: 1280px) {
  .summary-grid,
  .info-grid,
  .details-grid,
  .meta-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 980px) {
  .page-head,
  .result-row {
    grid-template-columns: 1fr;
    display: grid;
  }

  .tip-box,
  .preview-box,
  .preview-box img,
  .preview-empty {
    width: 100%;
    max-width: 420px;
  }

  .preview-box,
  .preview-box img,
  .preview-empty {
    height: 200px;
  }

  .summary-grid,
  .info-grid,
  .details-grid,
  .meta-row {
    grid-template-columns: 1fr;
  }
}
"""


def _escape(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _format_percent(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{value * 100:.1f}%"
    return "-"


def _bool_label(value: object) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return "-"


def _label(mapping: dict[str, str], value: object, default: str = "-") -> str:
    text = "" if value is None else str(value)
    return mapping.get(text, text or default)


def _decision_badge_kind(decision: str) -> str:
    if decision == "pass":
        return "success"
    if decision in {"review", "no_text"}:
        return "warning"
    if decision in {"replace", "failed"}:
        return "danger"
    return "info"


def _reference_badge_kind(status: str) -> str:
    if status == "matched":
        return "success"
    if status in {"weak_match", "not_provided"}:
        return "warning"
    if status == "not_matched":
        return "danger"
    return "info"


def _relative_asset_path(path: str | Path | None, workspace_root: Path) -> str:
    if not path:
        return ""
    asset_path = Path(path).resolve()
    try:
        relative = asset_path.relative_to(workspace_root.resolve())
    except ValueError:
        relative = Path(os.path.relpath(asset_path, workspace_root.resolve()))
    return relative.as_posix()


def _preview_box(title: str, image_path: str, empty_text: str) -> str:
    inner = (
        f'<img src="{_escape(image_path)}" alt="{_escape(title)}">'
        if image_path
        else f'<div class="preview-empty">{_escape(empty_text)}</div>'
    )
    return (
        f'<div><div class="preview-label">{_escape(title)}</div>'
        f'<div class="preview-box">{inner}</div></div>'
    )


def _badge(label: str, kind: str) -> str:
    return f'<span class="badge {kind}">{_escape(label)}</span>'


def _info_card(label: str, value: object, *, mono: bool = False) -> str:
    classes = "info-card"
    value_class = "mono" if mono else ""
    return (
        f'<div class="{classes}"><strong>{_escape(label)}</strong>'
        f'<span class="{value_class}">{_escape(value)}</span></div>'
    )


def _summary_card(label: str, value: object) -> str:
    return (
        f'<div class="summary-card"><strong>{_escape(label)}</strong>'
        f'<span>{_escape(value)}</span></div>'
    )


def _build_reason(item: dict[str, Any]) -> str:
    analysis = item.get("analysis") or {}
    binary = item.get("binary_quick_check") or {}
    same_image = item.get("same_image_llm") or {}
    return (
        analysis.get("quality_reason")
        or binary.get("reason")
        or same_image.get("reason")
        or item.get("error")
        or "-"
    )


def _same_image_value(same_image: dict[str, Any]) -> str:
    if same_image.get("status") == "done":
        return same_image.get("answer") or "-"
    return _label(_SAME_IMAGE_STATUS_LABELS, same_image.get("status"))


def _render_item(item: dict[str, Any], workspace_root: Path, index: int) -> str:
    analysis = item.get("analysis") or {}
    reference = item.get("reference_match") or {}
    binary = item.get("binary_quick_check") or {}
    same_image = item.get("same_image_llm") or {}
    download_evidence = item.get("download_evidence") or {}

    decision = str(analysis.get("decision") or item.get("status") or "")
    reference_status = str(reference.get("status") or "")
    site_image_path = _relative_asset_path(item.get("local_path"), workspace_root)
    reference_image_path = ""
    if reference_status == "matched":
        reference_image_path = _relative_asset_path(reference.get("reference_path"), workspace_root)

    badges = "".join(
        [
            _badge(_label(_KIND_LABELS, item.get("kind")), "info"),
            _badge(f"最终判定：{_label(_DECISION_LABELS, decision)}", _decision_badge_kind(decision)),
            _badge(
                f"参考图：{_label(_REFERENCE_STATUS_LABELS, reference_status)}",
                _reference_badge_kind(reference_status),
            ),
        ]
    )

    info_cards = "".join(
        [
            _info_card("序号", index + 1),
            _info_card("识别语言", analysis.get("detected_language") or "-"),
            _info_card("质量分", analysis.get("quality_score") if analysis.get("quality_score") is not None else "-"),
            _info_card("二值快检", _label(_BINARY_STATUS_LABELS, binary.get("status"))),
            _info_card("二值相似度", _format_percent(binary.get("binary_similarity"))),
            _info_card("前景重合度", _format_percent(binary.get("foreground_overlap"))),
            _info_card("阈值", _format_percent(binary.get("threshold"))),
            _info_card("同图大模型", _same_image_value(same_image)),
            _info_card("参考图文件", reference.get("reference_filename") or "-"),
        ]
    )

    details_cards = "".join(
        [
            _info_card("图片来源", item.get("source_url") or "-", mono=True),
            _info_card("原始下载 URL", download_evidence.get("requested_url") or download_evidence.get("requested_source_url") or "-", mono=True),
            _info_card("最终下载 URL", download_evidence.get("resolved_url") or download_evidence.get("resolved_source_url") or "-", mono=True),
            _info_card("是否保持同一资源", _bool_label(download_evidence.get("preserved_asset") if "preserved_asset" in download_evidence else download_evidence.get("redirect_preserved_asset"))),
            _info_card("是否来自当前 Variant", _bool_label(download_evidence.get("variant_selected"))),
            _info_card("下载说明", download_evidence.get("evidence_reason") or "-"),
            _info_card("同图通道", same_image.get("channel_label") or "-"),
            _info_card("同图模型", same_image.get("model") or "-"),
        ]
    )

    return (
        '<article class="result-row">'
        f'{_preview_box("网站图", site_image_path, "无网站图")}'
        f'{_preview_box("参考图", reference_image_path, "无参考图")}'
        '<div>'
        f'<div class="badge-row">{badges}</div>'
        f'<div class="info-grid">{info_cards}</div>'
        f'<div class="reason-box">{_escape(_build_reason(item))}</div>'
        '<details><summary>展开详情</summary>'
        f'<div class="details-grid">{details_cards}</div>'
        '</details>'
        '</div>'
        '</article>'
    )


def _render_document(result: dict[str, Any], workspace_root: Path) -> str:
    product = result.get("product") or {}
    summary = ((result.get("analysis") or {}).get("summary") or {})
    items = ((result.get("analysis") or {}).get("items") or [])
    page = result.get("page") or {}

    final_url = page.get("final_url") or result.get("normalized_url") or ""
    title = product.get("name") or f"产品 {product.get('id') or '-'}"

    result_rows = (
        "".join(_render_item(item, workspace_root, index) for index, item in enumerate(items))
        if items
        else '<div class="empty-panel">当前任务还没有可展示的图片结果。</div>'
    )

    summary_cards = "".join(
        [
            _summary_card("产品 ID", product.get("id") or "-"),
            _summary_card("总图片数", len(items)),
            _summary_card("通过", summary.get("pass_count", 0)),
            _summary_card("需替换", summary.get("replace_count", 0)),
            _summary_card("待复核", summary.get("review_count", 0)),
            _summary_card("整体结论", _label(_OVERALL_LABELS, summary.get("overall_decision"))),
            _summary_card("目标语言", result.get("target_language_name") or result.get("target_language") or "-"),
            _summary_card("页面语言", page.get("html_lang") or "-"),
        ]
    )

    meta_row = "".join(
        [
            (
                '<div class="meta-chip"><strong>产品名称</strong>'
                f'<span>{_escape(title)}</span></div>'
            ),
            (
                '<div class="meta-chip"><strong>最终链接</strong>'
                f'<span class="mono">{_escape(final_url or "-")}</span></div>'
            ),
        ]
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_escape(title)} - 本地链接检查结果</title>
  <style>{_INLINE_CSS}</style>
</head>
<body>
  <main class="report-shell">
    <section class="panel">
      <div class="page-head">
        <div>
          <span class="kicker">Local Report</span>
          <h1>{_escape(title)}</h1>
          <p>本页为链接检查桌面端生成的本地静态结果页，可直接双击打开复核。</p>
        </div>
        <div class="tip-box">
          <strong>任务目录</strong>
          <span class="mono">{_escape(str(workspace_root))}</span>
        </div>
      </div>
      <div class="summary-grid">{summary_cards}</div>
      <div class="meta-row">{meta_row}</div>
    </section>

    <section class="panel">
      <div>
        <span class="kicker">Results</span>
        <h2>图片结果</h2>
      </div>
      <div class="result-list">{result_rows}</div>
    </section>
  </main>
</body>
</html>"""


def write_report(result: dict[str, Any]) -> Path:
    workspace_root = Path(result["workspace_root"]).resolve()
    report_path = workspace_root / "report.html"
    report_path.write_text(_render_document(result, workspace_root), encoding="utf-8")
    return report_path


def open_report(path: str | Path) -> None:
    report_path = Path(path).resolve()
    try:
        os.startfile(report_path)  # type: ignore[attr-defined]
        return
    except AttributeError:
        pass
    webbrowser.open(report_path.as_uri())
