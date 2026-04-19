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


def test_link_check_projects_script_checks_full_segment_before_primary_subtag_fallback():
    script = Path("web/static/link_check_projects.js").read_text(encoding="utf-8")

    assert "if (enabledLanguages.has(segment))" in script
    assert 'if (segment.includes("-"))' in script
    assert 'const primary = segment.split("-", 1)[0];' in script
    assert "if (enabledLanguages.has(primary))" in script


def test_link_check_projects_script_does_not_restrict_locale_detection_with_length_regex():
    script = Path("web/static/link_check_projects.js").read_text(encoding="utf-8")

    assert "isLocaleCode" not in script
    assert "isLanguageCountryPair" not in script
    assert "^[a-z]{2,3}$" not in script
    assert "^[a-z]{2,3}-[a-z]{2,3}$" not in script


def test_link_check_projects_css_focuses_on_create_and_list_page():
    style = Path("web/static/link_check.css").read_text(encoding="utf-8")

    assert ".lc-project-list" in style
    assert ".lc-project-card" in style
    assert ".lc-form-grid" in style
    assert ".lc-panel-tip" in style

    assert ".lc-result-card--alert" in style
    assert ".lc-meta-card--alert" in style
    assert ".lc-issue-summary" in style


def test_link_check_detail_template_bootstraps_persisted_task_for_detail_page():
    template = Path("web/templates/link_check_detail.html").read_text(encoding="utf-8")

    assert 'id="linkCheckDetailPage"' in template
    assert "__LINK_CHECK_TASK__" in template
    assert 'id="linkCheckSummary"' in template
    assert 'id="linkCheckResults"' in template
    assert "link_check.css" in template
    assert "link_check.js" in template


def test_link_check_detail_script_supports_bootstrap_and_issue_alert_rendering():
    script = Path("web/static/link_check.js").read_text(encoding="utf-8")

    assert "function getBootstrappedTask" in script
    assert 'if (window.__LINK_CHECK_TASK__ && typeof window.__LINK_CHECK_TASK__ === "object")' in script
    assert 'const node = $("linkCheckInitialTask")' in script
    assert "JSON.parse(node.textContent)" in script
    assert "function collectIssueSummary(" in script
    assert "lc-result-card--alert" in script
    assert "lc-meta-card--alert" in script
    assert 'binary.status === "error"' in script
    assert 'sameImage.status === "error"' in script
    assert 'analysis.decision === "review"' in script
    assert 'analysis.decision === "no_text"' in script


def test_link_check_detail_script_keeps_polling_after_transient_failures_until_threshold():
    script = Path("web/static/link_check.js").read_text(encoding="utf-8")

    assert "const MAX_POLL_FAILURES = 3" in script
    assert "consecutivePollFailures" in script
    assert "state.consecutivePollFailures = 0;" in script
    assert "state.consecutivePollFailures += 1;" in script
    assert "if (state.consecutivePollFailures >= MAX_POLL_FAILURES)" in script
    assert 'showError(error.message || "轮询失败")' in script
    assert 'if (!task || !task.id || TERMINAL_STATUSES.has(task.status))' in script


def test_link_check_detail_script_marks_key_non_pass_states_as_alerts():
    script = Path("web/static/link_check.js").read_text(encoding="utf-8")

    assert "function isNonPassDecision" in script
    assert '["review", "replace", "no_text", "failed"]' in script
    assert 'label: "最终判定"' in script
    assert "isAlert: isNonPassDecision(decision)" in script
    assert 'decision === "review"' in script
    assert 'decision === "no_text"' in script
