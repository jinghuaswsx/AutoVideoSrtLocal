from pathlib import Path


def test_link_check_js_locks_submit_until_first_progress_render():
    script = Path("web/static/link_check.js").read_text(encoding="utf-8")

    assert "setSubmitting(" in script
    assert "submitButton.disabled = isSubmitting" in script
    assert "正在创建任务" in script
    assert "正在获取首批进度" in script


def test_link_check_assets_include_compact_result_card_and_detail_dialog():
    template = Path("web/templates/link_check.html").read_text(encoding="utf-8")
    script = Path("web/static/link_check.js").read_text(encoding="utf-8")
    style = Path("web/static/link_check.css").read_text(encoding="utf-8")

    assert 'id="linkCheckDetailDialog"' in template
    assert 'id="linkCheckDetailBody"' in template

    assert "renderDetailDialog(" in script
    assert "linkCheckDetailDialog" in script
    assert "查看任务详情" in script
    assert "lc-result-layout" in script
    assert "lc-meta-grid" in script

    assert ".lc-result-layout" in style
    assert ".lc-preview-frame" in style
    assert "width: 200px;" in style
    assert "height: 200px;" in style
    assert ".lc-meta-grid" in style
    assert "-webkit-line-clamp: 2;" in style
    assert ".lc-detail-dialog" in style


def test_link_check_assets_hide_reference_preview_when_not_matched():
    script = Path("web/static/link_check.js").read_text(encoding="utf-8")

    assert 'reference.status === "matched"' in script
    assert "未匹配到参考图" in script
