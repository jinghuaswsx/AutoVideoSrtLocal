from pathlib import Path


SCRIPT = Path("web/static/bulk_translate_ui.js").read_text(encoding="utf-8")


def test_bulk_translate_estimate_errors_are_escaped_before_inner_html():
    assert "const detail = escapeHtml(err.error || resp.status);" in SCRIPT
    assert 'box.innerHTML = `<span class="bt-warn">预估失败: ${detail}</span>`;' in SCRIPT
    assert 'box.innerHTML = `<span class="bt-warn">网络错误: ${escapeHtml(e.message || e)}</span>`;' in SCRIPT

    assert 'box.innerHTML = `<span class="bt-warn">预估失败: ${err.error || resp.status}</span>`;' not in SCRIPT
    assert 'box.innerHTML = `<span class="bt-warn">网络错误: ${e.message}</span>`;' not in SCRIPT
