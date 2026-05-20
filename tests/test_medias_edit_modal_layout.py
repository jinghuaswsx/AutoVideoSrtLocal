import re
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
    assert 'id="edShopifyId"' in html
    assert "Shopify ID" in html


def test_edit_modal_shopifyid_field_is_editable_input():
    html = (ROOT / "web" / "templates" / "_medias_edit_detail_modal.html").read_text(
        encoding="utf-8"
    )

    section_start = html.index('id="edShopifyIdSection"')
    section_end = html.index("</section>", section_start)
    section = html[section_start:section_end]

    assert 'id="edShopifyIdValue"' not in section
    assert "仅展示不可编辑" not in section
    assert 'id="edShopifyId"' in section
    assert 'inputmode="numeric"' in section
    assert 'aria-labelledby="edShopifyIdSectionTitle"' in section


def test_edit_copywriting_textarea_autosizes_without_vertical_scroll():
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    script = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    rule_start = html.index('.oc-cw-grid .oc-cw textarea[data-field="body"]')
    rule_end = html.index("}", rule_start)
    textarea_rule = html[rule_start:rule_end]

    assert "height:calc(1.55em * 4 + 16px);" in textarea_rule
    assert "overflow-x:auto;" in textarea_rule
    assert "overflow-y:hidden;" in textarea_rule
    assert "function edAutosizeCopywritingTextarea" in script
    assert "textarea.rows = 4;" in script
    assert "textarea.addEventListener('input', () => edAutosizeCopywritingTextarea(textarea));" in script


def test_medias_list_uses_two_column_grid_for_row_actions():
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")

    assert ".oc-row-actions { display:grid;" in html
    assert "grid-template-columns:repeat(2, minmax(0, max-content));" in html


def test_medias_listing_status_pill_keeps_text_horizontal():
    script = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")

    assert '<col style="width:80px">\n        <col style="width:88px">\n        <col style="width:56px">' in script
    assert ".listing-status-cell { text-align:center; white-space:nowrap; }" in html
    assert ".oc-listing-pill { display:inline-flex; align-items:center; justify-content:center; box-sizing:border-box; min-width:44px; height:32px; padding:0 10px; border-radius:9999px; font-size:13px; font-weight:500; line-height:1.25; text-align:center; white-space:nowrap; }" in html


def test_medias_product_table_leaves_space_between_cover_and_name_columns():
    script = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    table_start = script.index('<table class="oc-table"')
    colgroup_start = script.index("<colgroup>", table_start)
    colgroup_end = script.index("</colgroup>", colgroup_start)
    colgroup = script[colgroup_start:colgroup_end]
    widths = [int(value) for value in re.findall(r'<col style="width:(\d+)px">', colgroup)]

    assert widths[1] >= 128
    assert widths[2] >= 130
    assert '<td class="name wrap">${nameCell}</td>' in script


def test_detail_images_support_multi_select_delete_assets():
    script = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")

    assert "selectedDetailImageIds = new Set()" in script
    assert "deleteSelectedDetailImages" in script
    assert 'class="oc-detail-image-select"' in script
    assert 'querySelectorAll(\'.oc-detail-image-select\')' in script
    assert "确定删除选中的 ${ids.length} 张详情图？" in script
    assert "for (const imgId of ids)" in script
    assert "selectedDetailImageIds.has(Number(it.id))" in script
    assert ".oc-detail-images-toolbar" in html
    assert ".oc-detail-image.is-selected" in html


def test_medias_js_uses_current_cache_buster():
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")

    assert "filename='medias.js', v='main-image-name-spacing-20260520'" in html


def test_edit_video_material_cards_support_inline_filename_edit():
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    script = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "oc-vitem-name-editor" in html
    assert 'data-act="name-edit"' in script
    assert 'data-act="name-save"' in script
    assert 'data-act="name-cancel"' in script
    assert "修改文件名" in script
    assert "保存" in script
    assert "取消" in script
    assert "edStartItemNameEdit" in script
    assert "edSaveItemNameEdit" in script
    assert "edCancelItemNameEdit" in script
    assert 'method: "PATCH"' in script or "method: 'PATCH'" in script


def test_edit_detail_translate_task_links_sanitize_internal_hrefs():
    script = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    history_block = script[
        script.index("function edRenderDetailTranslateHistory"):
        script.index("const LINK_CHECK_STATUS_LABELS")
    ]
    state_block = script[
        script.index("function edRenderDetailTranslateState"):
        script.index("async function edRefreshDetailImagesPanel")
    ]
    submit_block = script[
        script.index("async function edSubmitDetailTranslate"):
        script.index("function edCloseLinkCheckModal")
    ]

    assert "function safeInternalHref(url, fallback)" in script
    assert "safeInternalHref(task.detail_url" in history_block
    assert "safeInternalHref(appliedTask.detail_url" in state_block
    assert "safeInternalHref(latest.detail_url" in state_block
    assert "link.href = safeInternalHref(data.detail_url" in submit_block
    assert "escapeHtml(task.detail_url ||" not in history_block
    assert "link.href = data.detail_url ||" not in submit_block


def test_edit_video_play_url_sanitizes_media_src_protocols():
    script = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    load_block = script[
        script.index("async function edEnsureVideoLoaded"):
        script.index("function edPickItemCover")
    ]

    assert "function safeMediaSrc(url)" in script
    assert "const playUrl = safeMediaSrc(r.url);" in load_block
    assert 'src="${escapeHtml(playUrl)}"' in load_block
    assert 'src="${escapeHtml(r.url)}"' not in load_block


def test_medias_js_product_and_detail_image_previews_sanitize_media_src_protocols():
    script = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    detail_images = script[
        script.index("function renderItemHTML"):
        script.index("function renderInto")
    ]
    product_row = script[
        script.index("function rowHTML(p)"):
        script.index("function renderProductLinksPushList")
    ]
    add_items = script[
        script.index("function renderItems(items)"):
        script.index("async function removeItem")
    ]

    assert "function safeMediaSrc(url)" in script
    assert "const imageUrl = safeMediaSrc(it.thumbnail_url);" in detail_images
    assert 'src="${escapeHtml(imageUrl)}"' in detail_images
    assert "const coverUrl = safeMediaSrc(p.cover_thumbnail_url);" in product_row
    assert 'src="${escapeHtml(coverUrl)}"' in product_row
    assert "const coverUrl = safeMediaSrc(it.cover_url);" in add_items
    assert 'src="${escapeHtml(coverUrl)}"' in add_items
    assert 'src="${escapeHtml(it.thumbnail_url)}"' not in detail_images
    assert 'src="${escapeHtml(p.cover_thumbnail_url)}"' not in product_row
    assert 'src="${escapeHtml(it.cover_url)}"' not in add_items


def test_medias_js_edit_modal_preview_setters_sanitize_media_src_protocols_but_keep_blob_previews():
    script = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    add_cover_setter = script[
        script.index("function setCover"):
        script.index("// ---- Item cover")
    ]
    add_item_cover_setter = script[
        script.index("function setItemCover"):
        script.index("async function uploadItemCover")
    ]
    cover_setter = script[
        script.index("function edSetCoverUI"):
        script.index("async function edUploadCover")
    ]
    item_cover_setter = script[
        script.index("function edSetItemCover"):
        script.index("function edSetPickedVideo")
    ]
    edit_items = script[
        script.index("function edRenderItems"):
        script.index("function edSetItemNameSaving")
    ]

    assert "const safeUrl = safeMediaSrc(url);" in add_cover_setter
    assert "img.src = safeUrl;" in add_cover_setter
    assert "const safeUrl = String(url || '').startsWith('blob:') ? String(url) : safeMediaSrc(url);" in add_item_cover_setter
    assert "img.src = safeUrl;" in add_item_cover_setter
    assert "const safeUrl = safeMediaSrc(url);" in cover_setter
    assert "img.src = safeUrl;" in cover_setter
    assert "const safeUrl = String(url || '').startsWith('blob:') ? String(url) : safeMediaSrc(url);" in item_cover_setter
    assert "img.src = safeUrl;" in item_cover_setter
    assert "const coverUrl = safeMediaSrc(it.cover_url);" in edit_items
    assert "const coverSrc = coverUrl ? withCacheBuster(coverUrl) : '';" in edit_items
    assert 'src="${escapeHtml(coverSrc)}"' in edit_items
    assert "img.src = url;" not in add_cover_setter
    assert "img.src = url;" not in add_item_cover_setter
    assert "img.src = url;" not in cover_setter
    assert "img.src = url;" not in item_cover_setter


def test_medias_js_link_check_detail_preview_images_sanitize_media_src_protocols():
    script = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    link_check_modal = script[
        script.index("function edRenderLinkCheckModal"):
        script.index("function edCloseLinkCheckModal")
    ]

    assert "const previewUrl = safeMediaSrc(ref.preview_url || '');" in link_check_modal
    assert 'src="${escapeHtml(previewUrl)}"' in link_check_modal
    assert "const itemPreviewUrl = safeMediaSrc(item.site_preview_url);" in link_check_modal
    assert 'src="${escapeHtml(itemPreviewUrl)}"' in link_check_modal
    assert 'src="${escapeHtml(ref.preview_url || \'\')}"' not in link_check_modal
    assert 'src="${escapeHtml(item.site_preview_url)}"' not in link_check_modal


def test_medias_js_raw_source_cards_sanitize_cover_and_video_media_src_protocols():
    script = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    raw_source_card = script[
        script.index("function renderRawSourceCard"):
        script.index("function getRawSourceCardTitle")
    ]
    raw_source_video = script[
        script.index("function ensureRawSourceVideoLoaded"):
        script.index("function bindRawSourceCard")
    ]

    assert "const coverUrl = safeMediaSrc(it.cover_url);" in raw_source_card
    assert "const videoUrl = safeMediaSrc(it.video_url);" in raw_source_card
    assert 'src="${escapeHtml(coverUrl)}"' in raw_source_card
    assert 'data-video-url="${escapeHtml(videoUrl)}"' in raw_source_card
    assert "const videoUrl = safeMediaSrc(card.dataset.videoUrl || '');" in raw_source_video
    assert "video.src = videoUrl;" in raw_source_video
    assert 'src="${escapeHtml(it.cover_url)}"' not in raw_source_card
    assert 'data-video-url="${escapeHtml(it.video_url || \'\')}"' not in raw_source_card
