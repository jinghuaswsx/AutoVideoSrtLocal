from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_medias_js_renders_source_video_as_two_line_deep_link():
    script = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "function edFindSourceEnglishItem" in script
    assert "function edBuildSourceVideoHref" in script
    assert "params.set('focus', 'source_video')" in script
    assert 'class="vsource-label">来源视频</span>' in script
    assert 'class="vsource-name"' in script
    assert 'target="_blank" rel="noopener noreferrer"' in script


def test_medias_deep_link_focus_works_without_from_task_and_uses_highlight_class():
    template = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")

    assert "if (!fromTask && !productId) return;" in template
    assert "if (fromTask) {" in template
    assert "oc-deeplink-highlight" in template
    assert "mbridgeHighlight(document.getElementById('edItemsSection'))" in template
