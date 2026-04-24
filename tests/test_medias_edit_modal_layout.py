from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_edit_language_tabs_are_outside_scrollable_body():
    html = (ROOT / "web" / "templates" / "_medias_edit_detail_modal.html").read_text(
        encoding="utf-8"
    )

    body_start = html.index('<div class="oc-modal-body oc-edit-form">')
    langbar_start = html.index('<div class="oc-modal-langbar">')
    tabs_index = html.index('id="edLangTabs"')

    assert langbar_start < tabs_index < body_start


def test_edit_modal_does_not_require_english_cover_to_save():
    html = (ROOT / "web" / "templates" / "_medias_edit_detail_modal.html").read_text(
        encoding="utf-8"
    )
    script = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "edEnCoverWarn" not in html
    assert "必须先上传英文" not in html
    assert "saveBtn.disabled = !hasEn" not in script
    assert "必须先上传英文" not in script


def test_edit_mk_id_section_has_no_duplicate_field_label():
    html = (ROOT / "web" / "templates" / "_medias_edit_detail_modal.html").read_text(
        encoding="utf-8"
    )

    section_start = html.index('id="edMkIdSection"')
    section_end = html.index("<!-- 从 URL 一键下载", section_start)
    section = html[section_start:section_end]

    assert section.count("明空 ID") == 1
    assert 'for="edMkId"' not in section
    assert 'id="edMkIdSectionTitle"' in section
    assert 'id="edMkId"' in section
    assert 'aria-labelledby="edMkIdSectionTitle"' in section


def test_edit_modal_places_shopifyid_section_after_mk_id():
    html = (ROOT / "web" / "templates" / "_medias_edit_detail_modal.html").read_text(
        encoding="utf-8"
    )

    mk_id_index = html.index('id="edMkIdSection"')
    shopify_id_index = html.index('id="edShopifyIdSection"')

    assert mk_id_index < shopify_id_index
    assert 'id="edShopifyIdValue"' in html
    assert "Shopify ID" in html


def test_medias_list_uses_two_column_grid_for_row_actions():
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")

    assert ".oc-row-actions { display:grid;" in html
    assert "grid-template-columns:repeat(2, minmax(0, max-content));" in html
