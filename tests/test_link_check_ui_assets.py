from pathlib import Path


def test_link_check_projects_template_only_exposes_task5_surface():
    template = Path("web/templates/link_check.html").read_text(encoding="utf-8")

    assert 'id="linkCheckProjectForm"' in template
    assert 'id="linkCheckProjectList"' in template
    assert 'id="linkCheckError"' in template
    assert 'id="linkCheckStatus"' in template
    assert "link_check_projects.js" in template

    assert 'id="linkCheckSummary"' not in template
    assert 'id="linkCheckResults"' not in template
    assert 'id="linkCheckDetailDialog"' not in template


def test_link_check_projects_script_includes_locale_detection_and_redirect():
    script = Path("web/static/link_check_projects.js").read_text(encoding="utf-8")

    assert "function detectTargetLanguageFromUrl" in script
    assert "window.location.assign" in script
    assert 'form.addEventListener("submit", onSubmit)' in script
    assert 'linkInput.addEventListener("input", syncLanguageFromUrl)' in script
    assert "/fr/" in script
    assert "/fr-fr/" in script


def test_link_check_projects_css_focuses_on_create_and_list_page():
    style = Path("web/static/link_check.css").read_text(encoding="utf-8")

    assert ".lc-project-list" in style
    assert ".lc-project-card" in style
    assert ".lc-form-grid" in style
    assert ".lc-panel-tip" in style

    assert ".lc-result-layout" not in style
    assert ".lc-detail-dialog" not in style
    assert ".lc-detail-panel" not in style
