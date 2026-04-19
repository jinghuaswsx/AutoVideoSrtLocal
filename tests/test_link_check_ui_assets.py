from pathlib import Path


def test_link_check_js_locks_submit_until_first_progress_render():
    script = Path("web/static/link_check.js").read_text(encoding="utf-8")

    assert "setSubmitting(" in script
    assert "submitButton.disabled = isSubmitting" in script
    assert 'body: new FormData($("linkCheckForm"))' in script
    assert "await pollTask(state.taskId)" in script


def test_link_check_assets_include_compact_result_card_and_detail_dialog():
    template = Path("web/templates/link_check.html").read_text(encoding="utf-8")
    script = Path("web/static/link_check.js").read_text(encoding="utf-8")
    style = Path("web/static/link_check.css").read_text(encoding="utf-8")

    assert 'id="linkCheckDetailDialog"' in template
    assert 'id="linkCheckDetailBody"' in template

    assert "renderDetailDialog(" in script
    assert "linkCheckDetailDialog" in script
    assert "lc-detail-trigger" in script
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
    assert 'if (reference.status === "not_matched")' in script


def test_link_check_projects_assets_include_project_form_list_and_redirect_script():
    template = Path("web/templates/link_check.html").read_text(encoding="utf-8")
    script = Path("web/static/link_check_projects.js").read_text(encoding="utf-8")

    assert 'id="linkCheckProjectForm"' in template
    assert 'id="linkCheckProjectList"' in template
    assert "link_check_projects.js" in template

    assert "function detectTargetLanguageFromUrl" in script
    assert "window.location.assign" in script
    assert "/fr/" in script
    assert "/fr-fr/" in script


def test_link_check_projects_css_includes_project_list_cards():
    style = Path("web/static/link_check.css").read_text(encoding="utf-8")

    assert ".lc-project-list" in style
    assert ".lc-project-card" in style
