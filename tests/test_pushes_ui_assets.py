from pathlib import Path


def test_pushes_template_contains_mk_id_column():
    template = Path("web/templates/pushes_list.html").read_text(encoding="utf-8")

    assert "<th>mk_id</th>" in template
    assert "<th>产品负责人</th>" in template
    assert "<th>审核信息</th>" in template
    assert 'for="f-owner"' in template
    assert 'id="f-owner"' in template


def test_pushes_template_contains_created_at_sort_control():
    template = Path("web/templates/pushes_list.html").read_text(encoding="utf-8")

    assert 'for="f-sort"' in template
    assert 'id="f-sort"' in template
    assert '<option value="created_at_asc">创建时间升序</option>' in template
    assert '<option value="created_at_desc" selected>创建时间降序</option>' in template


def test_pushes_script_renders_product_link_and_copy_button():
    script = Path("web/static/pushes.js").read_text(encoding="utf-8")

    assert "product_page_url" in script
    assert "product_owner_name" in script
    assert "product-owner-name" in script
    assert "product-name-line" in script
    assert "product-code-row" in script
    assert "mk_id" in script
    assert "data-copy-product-code" in script
    assert "data-copy-modal-product-code" in script
    assert "data-copy-payload-tag" in script
    assert "renderTagList" in script
    assert "navigator.clipboard" in script
    assert "document.execCommand('copy')" in script
    assert "renderAuditCell" in script
    assert "listing_status" in script
    assert "ai_evaluation_result" in script
    assert "ai_evaluation_detail" in script
    assert "AI评估详情" in script
    assert "loadOwners" in script
    assert "/medias/api/users/active" in script
    assert "owner_id" in script
    assert "f-owner" in script


def test_pushes_script_persists_filters_pagination_and_sort_in_url():
    script = Path("web/static/pushes.js").read_text(encoding="utf-8")

    assert "function applyUrlToFilters" in script
    assert "function syncUrlFromFilters" in script
    assert "window.addEventListener('popstate'" in script
    assert "params.set('status', statusSel.value);" in script
    assert "params.set('lang', langSel.value);" in script
    assert "params.set('product', product);" in script
    assert "params.set('keyword', keyword);" in script
    assert "params.set('owner_id', ownerSel ? ownerSel.value : '');" in script
    assert "params.set('date_from', df);" in script
    assert "params.set('date_to', dt);" in script
    assert "params.set('sort', sortSel.value || 'created_at_desc');" in script
    assert "params.set('page', String(state.page));" in script
    assert "history.replaceState" in script
    assert "history.pushState" in script


def test_pushes_css_styles_product_link_and_copy_button():
    css = Path("web/static/pushes.css").read_text(encoding="utf-8")

    assert ".product-link" in css
    assert ".product-name-line" in css
    assert ".product-copy-btn" in css
    assert ".product-code-row" in css
    assert ".pm-inline-copy-row" in css
    assert ".pm-tag-list" in css
    assert ".pm-copy-btn" in css
    assert ".audit-cell" in css
    assert ".audit-detail-pre" in css


def test_pushes_css_expands_ai_evaluation_detail_modal():
    css = Path("web/static/eval_country_table.js").read_text(encoding="utf-8")

    assert ".ect-modal-overlay" in css
    assert "--oc-bg:            oklch(99%  0.004 230);" in css
    assert "--oc-border:        oklch(91%  0.012 230);" in css
    assert "--oc-fg:            oklch(22%  0.020 235);" in css
    assert "calc(100vh - 24px)" in css
    assert "min(1760px, calc(100vw - 48px))" in css
    assert ".ect-modal-body" in css
    assert "flex: 1 1 auto" in css
    assert "max-height: none" in css


def test_eval_country_table_expands_risk_section_but_keeps_meta_collapsed():
    script = Path("web/static/eval_country_table.js").read_text(encoding="utf-8")

    extra_start = script.index("function extraSectionHtml")
    meta_start = script.index("function metaSectionHtml")
    render_start = script.index("function render")
    extra_section = script[extra_start:meta_start]
    meta_section = script[meta_start:render_start]

    assert 'return `<details class="ect-collapsible" open>' in extra_section
    assert 'return `<details class="ect-collapsible">' in meta_section
    assert 'return `<details class="ect-collapsible" open>' not in meta_section


def test_pushes_and_medias_use_shared_ai_evaluation_detail_modal():
    shared = Path("web/static/eval_country_table.js").read_text(encoding="utf-8")
    pushes = Path("web/static/pushes.js").read_text(encoding="utf-8")
    medias = Path("web/static/medias.js").read_text(encoding="utf-8")

    assert "openModal:" in shared
    assert "function openAiEvaluationDetailModal" in shared
    assert "window.EvalCountryTable.openModal(" in pushes
    assert "window.EvalCountryTable.openModal(" in medias
    assert "function openAuditDetailModal" not in pushes
    assert "function openAiEvaluationDetail(product)" not in medias
    assert "aiEvalDetailMask" not in medias
