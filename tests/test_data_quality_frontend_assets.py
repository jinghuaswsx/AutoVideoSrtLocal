"""数据质量状态条前端资产静态校验。

Docs-anchor: docs/analytics-data-quality-guardrails.md
Docs-anchor: docs/superpowers/specs/2026-05-29-data-quality-bar-compact-disclosure.md
"""
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def bar_template() -> str:
    return Path("web/templates/_data_quality_bar.html").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "page",
    [
        "web/templates/order_profit_dashboard.html",
        "web/templates/product_profit_dashboard.html",
        "web/templates/order_analytics.html",
    ],
)
def test_data_quality_bar_is_included_on_pages(page: str) -> None:
    body = Path(page).read_text(encoding="utf-8")
    assert '{% include "_data_quality_bar.html" %}' in body, f"missing include in {page}"


def test_data_quality_bar_renders_unknown_when_payload_missing(bar_template: str) -> None:
    # 默认状态必须是 unknown，不能给用户一个看似 ok 的表象
    assert "is-unknown" in bar_template
    assert "STATUS_LABELS" in bar_template
    # 五种状态枚举都覆盖
    for status in ("ok", "warning", "stale", "mismatch", "error", "unknown"):
        assert f"is-{status}" in bar_template


def test_data_quality_bar_uses_design_tokens_only(bar_template: str) -> None:
    # 不允许引入硬编码紫色
    forbidden_hues = ["260", "270", "290", "300", "310", "330"]
    for hue in forbidden_hues:
        assert hue not in bar_template, f"hue {hue} 不允许出现"
    # 必须用 token
    for token in ("--success-bg", "--warning-bg", "--danger-bg", "--bg-muted"):
        assert token in bar_template


def test_data_quality_bar_exposes_render_helper(bar_template: str) -> None:
    assert "window.renderDataQualityBar" in bar_template


def test_data_quality_bar_defaults_to_compact_disclosure(bar_template: str) -> None:
    assert 'data-dq-summary' in bar_template
    assert 'aria-expanded="false"' in bar_template
    assert 'data-dq-details hidden' in bar_template
    assert 'data-dq-toggle-icon' in bar_template
    assert 'data-dq-sub' not in bar_template
    assert 'details.hidden = false' not in bar_template


def test_pages_invoke_render_data_quality_bar() -> None:
    op = Path("web/templates/order_profit_dashboard.html").read_text(encoding="utf-8")
    pp = Path("web/templates/product_profit_dashboard.html").read_text(encoding="utf-8")
    oa = Path("web/templates/order_analytics.html").read_text(encoding="utf-8")
    for body in (op, pp, oa):
        assert "window.renderDataQualityBar" in body
