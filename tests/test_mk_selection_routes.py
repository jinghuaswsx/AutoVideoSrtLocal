from __future__ import annotations

from pathlib import Path


def _assert_unified_selection_tabs(body: str, active_href: str, active_label: str) -> None:
    assert '<nav class="xuanpin-tabs" role="tablist" aria-label="选品中心类型">' in body
    assert f'<a class="xuanpin-tab active" href="{active_href}" role="tab" aria-selected="true">{active_label}</a>' in body
    assert '<a class="xuanpin-tab" href="/xuanpin/mk" role="tab" aria-selected="false">明空选品</a>' in body or active_href == "/xuanpin/mk"
    assert '<a class="xuanpin-tab" href="/xuanpin/meta-hot-posts" role="tab" aria-selected="false">Meta热帖</a>' in body or active_href == "/xuanpin/meta-hot-posts"
    assert '<a class="xuanpin-tab" href="/xuanpin/tabcut" role="tab" aria-selected="false">TABCUT</a>' in body or active_href == "/xuanpin/tabcut"
    assert '<a class="xuanpin-tab" href="/xuanpin/today-recommendations" role="tab" aria-selected="false">今日推荐</a>' in body or active_href == "/xuanpin/today-recommendations"
    assert '<a class="xuanpin-tab" href="/xuanpin/new-products" role="tab" aria-selected="false">新品选择</a>' in body or active_href == "/xuanpin/new-products"


def test_mk_selection_token_has_no_hardcoded_fallback(monkeypatch, tmp_path):
    from web.routes.medias import mk_selection

    monkeypatch.delenv("MK_API_TOKEN", raising=False)
    monkeypatch.setattr(
        mk_selection,
        "_MK_TOKEN_FILE",
        tmp_path / "missing-mk-token.txt",
        raising=False,
    )

    assert mk_selection._get_mk_token() == ""


def test_mk_selection_source_does_not_embed_jwt_fallback():
    source = Path("web/routes/medias/mk_selection.py").read_text(encoding="utf-8")

    assert "eyJhbGci" not in source


def test_selection_center_sidebar_label_and_mk_page_tabs(authed_client_no_db):
    response = authed_client_no_db.get("/xuanpin/mk")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert '<span class="nav-icon">🔍</span> 选品中心' in body
    assert "<title>选品中心 - AutoVideoSrt</title>" in body
    assert "{% block page_title %}" not in body
    assert '<span class="selection-center-title">选品中心</span>' in body
    assert '<span class="selection-center-title-note">' in body
    assert "店小秘近7天有销量全量归档" in body
    assert '<h1 class="title">选品中心</h1>' not in body
    _assert_unified_selection_tabs(body, "/xuanpin/mk", "明空选品")
    assert '<div class="mk-library-tabs" role="tablist" aria-label="明空选品库类型">' in body
    video_tab = 'class="mk-library-tab active" type="button" role="tab" aria-selected="true" onclick="switchMkLibraryTab(\'videos\')" data-mk-library-tab="videos">视频素材库'
    yesterday_tab = 'data-mk-library-tab="yesterday-top100">昨天消耗前100'
    products_tab = 'data-mk-library-tab="products">产品库'
    assert video_tab in body
    assert yesterday_tab in body
    assert products_tab in body
    assert body.index(video_tab) < body.index(yesterday_tab) < body.index(products_tab)
    assert 'id="snapshotSelect"' in body
    assert 'id="snapshotRangeSelect"' in body
    assert "loadMkSelectionSnapshots" in body
    assert "oc-page-tabs" not in body
    assert "oc-page-tab" not in body
    assert "明控选品" not in body


def test_selection_center_tabs_and_heading_on_related_pages():
    mk_template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")
    npr_template = Path("web/templates/new_product_review_list.html").read_text(encoding="utf-8")

    assert "{% block title %}选品中心 - AutoVideoSrt{% endblock %}" in mk_template
    assert '<span class="selection-center-title">选品中心</span>' in mk_template
    assert "店小秘近7天有销量全量归档" in mk_template
    assert '<h1 class="title">选品中心</h1>' not in mk_template
    assert '{% set active = "mk" %}' in mk_template
    assert '{% include "_xuanpin_tabs.html" %}' in mk_template
    assert "/xuanpin/api/mk-selection/snapshots" in mk_template
    assert "snapshotRangeSelect" in mk_template
    assert "mkRangeQueryParam" in mk_template
    assert "本周" in mk_template
    assert "上周" in mk_template
    assert "本月" in mk_template
    assert "上月" in mk_template
    assert "selectedMkSnapshot" in mk_template
    assert "oc-page-tabs" not in mk_template
    assert "oc-page-tab" not in mk_template
    assert "{% block title %}选品中心 - AutoVideoSrt{% endblock %}" in npr_template
    assert '<span class="selection-center-title">选品中心</span>' in npr_template
    assert "明空入库新品 AI 评估矩阵" in npr_template
    assert '<h1 class="title">选品中心</h1>' not in npr_template
    assert '{% set active = "new-products" %}' in npr_template
    assert '{% include "_xuanpin_tabs.html" %}' in npr_template
    assert "oc-page-tabs" not in npr_template
    assert "oc-page-tab" not in npr_template
    assert "明控选品" not in mk_template
    assert "明控选品" not in npr_template
    assert "新品审核" not in mk_template
    assert "新品审核" not in npr_template


def test_new_product_review_cover_preview_sanitizes_media_sources():
    template = Path("web/templates/new_product_review_list.html").read_text(encoding="utf-8")

    assert "function safeMediaSrc(url)" in template
    assert "const coverUrl = safeMediaSrc(p.main_image || '');" in template
    assert "const coverHtml = coverUrl" in template
    assert '`<img class="npr-cover" src="${nprAttr(coverUrl)}"' in template
    assert 'src="${nprAttr(p.main_image)}"' not in template


def test_mk_selection_video_cards_use_single_preview_with_metrics():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "--mk-video-media-w:" in template
    assert "--mk-video-media-h:" in template
    assert "repeat(auto-fill, minmax(248px, 248px))" in template
    assert "mk-video-card-title" in template
    assert "mk-video-summary-row" in template
    assert "mk-video-tabs" in template
    assert "mk-video-frame" in template
    assert "mk-video-cover-frame" in template
    assert "mk-video-source-frame" not in template
    assert "mk-video-media-frame" not in template
    assert "投放热度" in template
    assert "90天消耗" in template
    assert "昨日消耗" in template


def test_mk_selection_import_success_warnings_are_toasted():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert ".mki-toast--warning" in template
    assert "function mkiWarningMessageFromResponse(data)" in template
    assert "mkiToast('warning', warningMessage)" in template
    assert "product_link_unavailable" in template
    assert "firstWarning.message || firstWarning.type" in template


def test_mk_import_progress_modal_present():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert 'id="mkiImportProgressModal"' in template
    assert "function mkiImportProgressOpen(meta)" in template
    assert "function mkiImportProgressSetStep(stepKey, status, detail)" in template
    assert "function mkiImportProgressFail(message)" in template
    assert "function mkiImportProgressComplete(data, btn)" in template
    assert 'id="mkiImportProgressError"' in template
    for label in ("准备素材信息", "检查产品与链接", "下载明空原视频", "写入素材库", "后续任务入口"):
        assert label in template
    assert "下一步：创建小语种任务" in template
    assert "继续做小语种任务" not in template
    assert "创建小语种翻译任务" in template
    assert "去任务中心" in template
    assert "去素材管理" in template
    assert "原视频处理人指派后会自动提交牛马去字幕" in template
    assert "原视频处理人认领后会自动提交牛马去字幕" not in template


def test_mk_import_progress_modal_width_is_expanded_to_150_percent():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert ".mki-progress-modal" in template
    assert "width: min(840px, 94vw)" in template


def test_mk_import_progress_uses_product_owner_step_before_domains():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "{key: 'productOwner', title: '选择产品负责人'" in template
    assert template.index("{key: 'prepare'") < template.index("{key: 'productOwner'")
    assert template.index("{key: 'productOwner'") < template.index("{key: 'product', title: '检查产品与链接'")
    assert template.index("{key: 'productOwner'") < template.index("{key: 'domains'")
    assert "product_owner_id" in template
    assert "mkiImportProgressProductOwnerId" in template
    assert 'id="mkiTranslatorModal"' not in template
    assert "function mkiOpenTranslatorModal" not in template
    assert "指定翻译员" not in template


def test_mk_import_progress_actions_live_inside_matching_step_cards():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "function mkiImportProgressStepActionHtml(stepKey)" in template
    assert 'data-mki-progress-action="${escapeHtml(step.key)}"' in template
    assert "mki-progress-actions" not in template
    assert "mkiImportProgressCancelProductOwner" not in template
    assert 'onclick="mkiImportProgressCancelProductOwner()"' not in template
    assert 'id="mkiImportProgressProductOwner"' in template
    assert 'id="mkiImportProgressDomains"' in template
    assert 'id="mkiImportProgressNextActions"' in template
    assert template.index('id="mkiImportProgressProductOwner"') < template.index('id="mkiImportProgressDomains"')
    assert template.index('id="mkiImportProgressDomains"') < template.index('id="mkiImportProgressNextActions"')
    assert 'data-mki-progress-retry-step="${escapeHtml(stepKey)}"' in template


def test_mk_import_progress_logs_product_record_visibility_in_product_step():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "function mkiImportProgressAppendStepLog(stepKey, message, kind = '')" in template
    assert "data-mki-progress-log-list" in template
    assert "检测到产品记录已存在，素材管理已可见" in template
    assert "服务端确认复用已有产品" in template
    assert "服务端已创建新产品" in template
    assert "mkiImportProgressAppendStepLog('product'," in template
    assert template.index("检测到产品记录已存在，素材管理已可见") < template.index("fetch('/mk-import/video'")


def test_mk_small_language_modal_distinguishes_product_owner_from_translation_owner():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "小语种翻译负责人" in template
    assert "产品负责人用于素材归属" in template
    assert "可与产品负责人不同" in template
    assert "翻译员：" not in template


def test_mk_selection_import_modals_use_active_user_display_names():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "/tasks/api/translation-work-users" in template
    assert "/medias/api/users/active" not in template
    assert "/tasks/api/translators" not in template
    assert "data.users || []" in template
    assert "function mkiUserLabel(user)" in template
    assert "user.display_name || user.username" in template


def test_mk_selection_modal_preview_tokens_available_globally():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert ":root {" in template
    assert "--mk-video-card-w:" in template
    assert "--mk-video-media-h:" in template
    assert 'id="detailPanel"' in template


def test_mk_selection_library_subtabs_match_meta_hot_posts_placement_and_state_logic():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert ".mk-library-tabs { display:flex;" in template
    assert "width:max-content" in template
    assert "function normalizeMkLibraryTab(tab)" in template
    assert "return ['products', 'videos', 'yesterday-top100'].includes(tab) ? tab : 'videos';" in template
    assert "function initMkLibraryTabFromHash()" in template
    assert "location.hash = currentMkLibraryTab;" in template
    assert "let currentMkLibraryTab = 'videos';" in template
    assert "loadMkLocalMaterialLibrary" in template
    assert "loadMkYesterdayTop100" in template
    assert "mkSnapshotQueryParam()" in template
    assert "mkRangeQueryParam()" in template
    assert "if (initialTab === 'videos')" in template
    assert "if (initialTab === 'yesterday-top100')" in template
    assert "/xuanpin/api/mk-material-library" in template
    assert "/xuanpin/api/mk-yesterday-top100" in template
    assert "/xuanpin/api/mk-video-materials" not in template


def test_mk_selection_material_archive_tabs_do_not_use_product_snapshot_selector():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert (
        "const url = `/xuanpin/api/mk-selection?page=${page}&page_size=${PAGE_SIZE}"
        "&keyword=${encodeURIComponent(kw)}${mkSnapshotQueryParam()}`;"
    ) in template
    assert (
        "const url = `/xuanpin/api/mk-material-library?page=${page}"
        "&page_size=${MK_VIDEO_PAGE_SIZE}${keywordParam}${mkRangeQueryParam()}`;"
    ) in template
    assert (
        "const url = `/xuanpin/api/mk-yesterday-top100?page=${page}"
        "&page_size=${MK_VIDEO_PAGE_SIZE}`;"
    ) in template
    assert "/xuanpin/api/mk-yesterday-top100?page=${page}&page_size=${MK_VIDEO_PAGE_SIZE}${mkSnapshotQueryParam()}" not in template


def test_mk_selection_material_archive_tabs_have_top_pagers_and_page100():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "const MK_VIDEO_PAGE_SIZE = 100;" in template
    assert "function normalizeMkGotoPage(raw, totalPages)" in template
    assert "function handleMkGotoPage(event, loaderName, totalPages)" in template
    assert 'class="oc-pager-goto"' in template
    assert "onkeydown=\"handleMkGotoPage(event, 'loadData', ${totalPages})\"" in template
    assert "onkeydown=\"handleMkGotoPage(event, '${loaderName}', ${totalPages})\"" in template
    assert 'id="mkVideoPagerTop"' in template
    assert 'id="mkVideoPagerBottom"' in template
    assert 'id="mkYesterdayTop100PagerTop"' in template
    assert 'id="mkYesterdayTop100PagerBottom"' in template
    assert "function renderMkArchivePager(containerIds, loaderName, page, total)" in template
    assert "`<button onclick=\"${loaderName}(1)\" ${pageNum <= 1 ? 'disabled' : ''}>首页</button>`" in template
    assert "`<button onclick=\"${loaderName}(${totalPages})\" ${pageNum >= totalPages ? 'disabled' : ''}>末页</button>`" in template
    assert template.index(">首页</button>") < template.index(">上一页</button>")
    assert template.index(">下一页</button>") < template.index(">末页</button>")
    assert "renderMkArchivePager(['mkVideoPagerTop', 'mkVideoPagerBottom'], 'loadMkLocalMaterialLibrary', page, total);" in template
    assert "renderMkArchivePager(['mkYesterdayTop100PagerTop', 'mkYesterdayTop100PagerBottom'], 'loadMkYesterdayTop100', page, total);" in template


def test_mk_selection_video_search_placeholder_lists_supported_fields():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert 'placeholder="搜索产品名 / product code / 视频文件名"' in template


def test_mk_selection_manual_video_search_clears_active_product_code_before_loading():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "function runMkSearch(options = {})" in template
    assert "if (currentMkLibraryTab === 'videos' && options.preserveProductCode !== true) {" in template
    assert "activeMkProductCode = '';" in template
    assert "switchMkLibraryTab('videos', {preserveProductCode: true});" in template


def test_mk_material_search_index_migration_is_idempotent():
    sql = Path("db/migrations/2026_05_22_mingkong_material_search_indexes.sql").read_text(encoding="utf-8")

    assert "idx_mk_material_search_at_product_code" in sql
    assert "idx_mk_material_search_at_video_name" in sql
    assert "idx_mk_material_search_at_product_name" in sql
    assert "idx_mk_material_search_at_mk_product_name" in sql
    assert "idx_mk_material_search_at_video_path" in sql
    assert "information_schema.STATISTICS" in sql
    assert "PREPARE stmt FROM @ddl" in sql


def test_mk_selection_video_cards_prefer_local_cover_url():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "const localCoverUrl = safeMediaSrc(r.local_cover_url || '');" in template
    assert "const coverUrl = localCoverUrl ||" in template
    assert "/xuanpin/api/mk-media?path=" in template


def test_mk_selection_import_buttons_pass_local_asset_object_keys():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "data-mki-cover-object-key" in template
    assert "data-mki-main-image-object-key" in template
    assert "cover_object_key: btn.dataset.mkiCoverObjectKey || null" in template
    assert "main_image_object_key: btn.dataset.mkiMainImageObjectKey || null" in template


def test_mk_selection_import_success_marks_all_matching_buttons():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "let MKI_IMPORTED_FILENAMES = new Set();" in template
    assert "function mkiMarkImportedFilename(filename)" in template
    assert "document.querySelectorAll('.mki-btn--add')" in template
    assert "MKI_IMPORTED_FILENAMES.has(fn)" in template
    assert "String(btn.dataset.mkiFilename || '').trim() === fn" in template
    assert "mkiMarkImportedButton(btn);" in template
    assert "mkiMarkImportedFilename(btn.dataset.mkiFilename);" in template
    assert template.index("mkiMarkImportedButton(btn);") < template.index("mkiMarkImportedFilename(btn.dataset.mkiFilename);")
    assert "importSucceeded = true;" in template
    assert "if (!importSucceeded)" in template


def test_mk_selection_imported_check_uses_post_json_to_avoid_long_urls():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "async function mkiFetchImportedFilenames(chunk)" in template
    assert "fetch('/mk-import/check', {" in template
    assert "method: 'POST'" in template
    assert "JSON.stringify({filenames: chunk})" in template
    assert "X-CSRFToken" in template
    assert "fetch('/mk-import/check?filenames='" not in template


def test_mk_import_progress_medias_button_searches_product_code():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "function mkiProductCodeWithRjc(value)" in template
    assert "function mkiMediaSearchHrefFromButton(btn)" in template
    assert "`/medias/?q=${encodeURIComponent(code)}`" in template
    assert "function mkiImportProgressOpenMedias()" in template
    assert "mkiImportProgressSetMediasHref(btn);" in template
    assert 'onclick="mkiImportProgressOpenMedias()"' in template


def test_mk_import_progress_footer_actions_open_new_tabs():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "function mkiImportProgressOpenTab(href)" in template
    assert "window.open(targetHref, '_blank', 'noopener,noreferrer')" in template
    assert "function mkiImportProgressOpenTasks()" in template
    assert "function mkiImportProgressContinueTask()" in template
    assert "async function mkiImportProgressCreateSmallLangTask()" in template
    assert "const endpoint = '/tasks/api/parent';" in template
    assert "fetch(endpoint" in template
    assert "media_product_id: mkiImportProgressProductId" in template
    assert "media_item_id: mkiImportProgressItemId" in template
    assert "translator_id: selection.translatorId" in template
    assert "raw_processor_id: selection.rawProcessorId" in template
    assert "language_assignments: mkiXiaoLanguageAssignments(selection)" in template
    assert "mkiXiaoOpenModal({title: '创建小语种翻译任务', endpoint: endpoint})" in template
    assert "mkiImportProgressTranslatorId" not in template
    assert 'onclick="mkiImportProgressContinueTask()"' in template
    assert 'onclick="mkiImportProgressOpenTasks()"' in template
    assert 'onclick="mkiImportProgressOpenMedias()"' in template
    assert 'onclick="window.location.href=\'/tasks/\'"' not in template
    assert "window.location.href = '/tasks/'" not in template


def test_mk_selection_small_language_modal_matches_task_parent_contract():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "const MKI_RAW_PROCESSORS_API" not in template
    assert "let mkiXiaoRawProcessorsCache = null;" in template
    assert "mkiXiaoRawProcessorsCache = mkiXiaoTranslatorsCache;" in template
    assert "mkiFetchRawProcessors" not in template
    assert 'id="mkiXiaoRawProcessor"' in template
    assert "请选择原视频处理人" in template
    assert "function mkiXiaoLanguageAssignments(selection)" in template
    assert "raw_processor_id: selection.rawProcessorId" in template
    assert "language_assignments: mkiXiaoLanguageAssignments(selection)" in template


def test_mk_selection_small_language_modal_keeps_task_creation_feedback_inline():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert 'id="mkiXiaoStatus"' in template
    assert 'id="mkiXiaoConfirm"' in template
    assert 'id="mkiXiaoCancel"' in template
    assert "function mkiXiaoSetBusy(isBusy)" in template
    assert "function mkiXiaoSetStatus(kind, html)" in template
    assert "function mkiXiaoRequestError(endpoint, rsp, data)" in template
    assert "function mkiXiaoTaskHref(taskId)" in template
    assert "function mkiXiaoShowCreateSuccess(taskId)" in template
    assert "function mkiXiaoShowCreateFailure(endpoint, error)" in template
    assert "mkiXiaoCurrentEndpoint = '/tasks/api/parent'" in template
    assert "正在请求 " in template
    assert "任务已创建" in template
    assert "打开任务" in template
    assert "请求失败" in template
    assert "document.getElementById('mkiXiaoModal').style.display = 'none';" not in template.split("function mkiXiaoOK()", 1)[1].split("function mkiXiaoCancel()", 1)[0]


def test_mk_selection_video_cards_include_local_video_preview():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "mk-video-source" in template
    assert "data-mk-video-src" in template
    assert "mk-video-play-btn" in template
    assert "data-mk-video-play" in template
    assert "activateMkVideoTab" in template
    assert "function playMkVideoFromButton(button)" in template
    assert "activateMkVideoTab(videoTab, {play: true})" in template
    assert "/xuanpin/api/mk-video?path=" in template
    assert "controls" in template
    assert "loading=\"lazy\"" in template
    assert "const playResult = video.play();" in template
    assert "playResult.catch(() => {})" in template
    assert "e.stopPropagation();" in template


def test_mk_selection_video_cards_include_cached_ad_status_icons_and_media_search_link():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "function renderMkStatusIconCluster(r)" in template
    assert "mk-video-status-icons" in template
    assert "has_local_product_running_ad" in template
    assert "has_local_material_in_library" in template
    assert "mk-status-icon--inactive" not in template
    assert "mk-status-icon--product" in template
    assert "mkStatusIconSvg('product')" in template
    assert "mk-status-icon-emoji" in template
    assert "📦" in template
    assert "mk-status-icon--video" in template
    assert "mkStatusIconSvg('video')" in template
    assert "const hasProductInLibrary = Boolean(r.has_local_product_in_library" in template
    assert "if (hasProductInLibrary)" in template
    assert "r.has_local_product_running_ad ? '产品已在素材库且有消耗广告计划' : '产品已在素材库'" in template
    assert "if (r.has_local_material_in_library)" in template
    assert "产品已在素材库且有消耗广告计划" in template
    assert "视频素材已在素材库" in template
    assert "视频已在素材库且有投放计划" not in template
    assert "未命中素材库投放" not in template
    assert "return icons.length ? `<div class=\"mk-video-status-icons\">${icons.join('')}</div>` : '';" in template
    assert "function renderMkMediaSearchLink(r)" in template
    assert "media_search_url" in template
    assert "mk-media-search-link" in template
    assert "/medias/?q=" in template


def test_mk_selection_product_rows_include_material_library_button():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "function productCodeFromUrl(value)" in template
    assert "function openProductMaterialLibrary(productCode, productName)" in template
    assert "data-mk-material-button" in template
    assert "data-product-code" in template
    assert "const productCode = String(r.product_code || productCodeFromUrl(r.product_url) || '').trim();" in template
    assert "renderProductMaterialButton(productCode, rawProductName, linked)" in template
    assert "openProductMaterialLibrary(materialButton.dataset.productCode || '', materialButton.dataset.productName || '')" in template
    assert "activeMkProductCode ? `&keyword=${encodeURIComponent(activeMkProductCode)}`" in template


def test_mk_selection_product_rows_show_library_status_and_color_classes():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert '<col class="mk-col-status">' in template
    assert "<th>产品状态</th>" in template
    assert 'colspan="13"' in template
    assert ".mk-library-row--green td" in template
    assert ".mk-library-row--red td" in template
    assert ".mk-library-row--yellow td" in template
    assert "function libraryStatusTitle(status)" in template
    assert "function libraryStatusMetricLine(status)" in template
    assert "function renderLibraryStatusCell(status)" in template
    assert "近一个月消耗" in template
    assert "保本ROAS" in template
    assert "const libraryStatus = r.library_status || {};" in template
    assert "const rowStatusClass = libraryStatus.card_status && libraryStatus.card_status !== 'none'" in template
    assert "const linked = libraryStatus.in_library" in template
    assert "${renderLibraryStatusCell(libraryStatus)}</td>" in template


def test_mk_selection_product_rows_show_200_cover_and_copy_buttons():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert '<col class="mk-col-image">' in template
    assert "--mk-product-image-size: 200px;" in template
    assert ".mk-product-image-frame { width:var(--mk-product-image-size); height:var(--mk-product-image-size);" in template
    assert "function renderProductImageCell(row)" in template
    assert "const coverSrc = safeMediaSrc(row.product_main_image_local_url || row.product_main_image_url || '');" in template
    assert "function copyMkText(value, button)" in template
    assert "renderTwoLineCopyBlock(rawProductName, r.product_url" in template
    assert "renderTwoLineCopyBlock(productCode || handle, ''" in template
    assert "data-copy-text" in template
    assert "product_cn_name" in template


def test_mk_selection_products_table_has_secondary_screen_compact_layout():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "2026-05-20-xuanpin-secondary-screen-columns-design.md" in template
    assert '<table class="oc-table mk-products-table" id="mainTable">' in template
    assert '<col class="mk-col-rank">' in template
    assert '<col class="mk-col-action">' in template
    assert "--mk-products-table-min-w: 1690px;" in template
    assert "@media (min-width: 769px) and (max-width: 2100px)" in template
    assert "@media (min-width: 769px) and (orientation: portrait)" in template
    assert ".sidebar { transform:translateX(-100%); }" in template
    assert "body.sidebar-open .sidebar { transform:translateX(0); }" in template
    assert ".main-wrap { margin-left:0; max-width:100vw; }" in template
    assert "--mk-col-action: 58px;" in template
    assert ".mk-products-table-shell { overflow-x:visible;" in template
    assert "mk-detail-btn" in template


def test_mk_selection_products_table_has_secondary_half_screen_density_band():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "@media (min-width: 769px) and (max-width: 2100px)" in template
    assert "--mk-product-image-size: 128px;" in template
    assert "--mk-col-name: 200px;" in template
    assert "--mk-col-action: 68px;" in template


def test_mk_selection_dynamic_html_escapes_api_fields():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "function safeExternalHref(value)" in template
    assert 'title="${escapeHtml(text)}"' in template
    assert 'href="${safeHref}"' in template
    assert "${escapeHtml(text)}</a>" in template
    assert 'data-copy-text="${escapeHtml(text)}"' in template
    assert 'alt="${escapeHtml(alt)}"' in template
    assert "${escapeHtml(cnNameShort)}" in template
    assert "${escapeHtml(r.store || '--')}" in template
    assert "${escapeHtml(r.revenue_main || '--')}" in template
    assert "${escapeHtml(item.product_name || '--')}" in template
    assert "${escapeHtml(item.admin_name || '--')}" in template
    assert "${escapeHtml(t.title || '')}" in template
    assert "${escapeHtml(t.message || '')}" in template
    assert 'href="${href}"' in template
    assert '加载失败: ${escapeHtml(e.message)}' in template
    assert '<option value="${escapeHtml(user.id)}">${escapeHtml(mkiUserLabel(user))}</option>' in template

    assert 'title="${r.product_name}"' not in template
    assert 'href="${r.product_url}"' not in template
    assert "${productName}</a>" not in template
    assert "${cnNameShort}</td>" not in template
    assert "<td>${r.store}</td>" not in template
    assert "${item.product_name}</div>" not in template
    assert '<a href="${l}"' not in template
    assert "${t.title}</div>" not in template
    assert "${t.message}</div>" not in template
    assert '加载失败: ${e.message}' not in template
    assert '>${t.username}</option>' not in template


def test_mk_selection_api_handles_legacy_rankings_schema_without_mk_columns(
    authed_client_no_db,
    monkeypatch,
):
    from web.routes import medias as route_mod

    route_mod._dianxiaomi_rankings_columns.cache_clear()

    def fake_db_query(sql, args=()):
        if sql == "SHOW COLUMNS FROM dianxiaomi_rankings":
            return [
                {"Field": "id"},
                {"Field": "product_id"},
                {"Field": "product_name"},
                {"Field": "product_url"},
                {"Field": "store"},
                {"Field": "sales_count"},
                {"Field": "order_count"},
                {"Field": "revenue_main"},
                {"Field": "revenue_split"},
                {"Field": "media_product_id"},
                {"Field": "snapshot_date"},
                {"Field": "rank_position"},
            ]
        if "SELECT MAX(snapshot_date) AS snapshot_date" in sql:
            assert args == []
            return [{"snapshot_date": "2026-04-23"}]
        if "SELECT COUNT(*) AS cnt" in sql:
            assert "mk_product_name" not in sql
            assert args == ["2026-04-23", "%tooth%"]
            return [{"cnt": 0}]
        if "FROM dianxiaomi_rankings dr" in sql:
            assert "mingkong_material_products" in sql
            assert "COALESCE(mps.mk_product_id, NULL) AS mk_product_id" in sql
            assert "COALESCE(mps.mk_product_name, NULL) AS mk_product_name" in sql
            assert "COALESCE(mps.total_90_spend, 0) AS mk_total_spends" in sql
            assert "COALESCE(mps.video_count, 0) AS mk_video_count" in sql
            assert "COALESCE(mps.total_ads, 0) AS mk_total_ads" in sql
            assert "ORDER BY COALESCE(mps.total_90_spend" in sql
            return []
        raise AssertionError(sql)

    monkeypatch.setattr(route_mod, "db_query", fake_db_query)

    response = authed_client_no_db.get("/medias/api/mk-selection?keyword=tooth")

    assert response.status_code == 200
    assert response.get_json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 50,
        "snapshot": "2026-04-23",
    }

    route_mod._dianxiaomi_rankings_columns.cache_clear()


def test_mk_selection_api_delegates_response_building_after_admin_gate(
    authed_client_no_db,
    monkeypatch,
):
    from web.routes import medias as route_mod
    from web.services.media_mk_selection import MkSelectionResponse

    captured = {}

    def fake_build(args):
        captured["keyword"] = args.get("keyword")
        return MkSelectionResponse(
            {"items": [{"rank": 1}], "total": 1, "page": 1, "page_size": 50},
            200,
        )

    monkeypatch.setattr(route_mod, "_build_mk_selection_response", fake_build)
    monkeypatch.setattr(
        route_mod,
        "db_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("route should delegate query/response building")
        ),
    )

    response = authed_client_no_db.get("/medias/api/mk-selection?keyword=tooth")

    assert response.status_code == 200
    assert response.get_json()["items"] == [{"rank": 1}]
    assert captured["keyword"] == "tooth"


def test_mk_selection_snapshots_api_delegates_response_building_after_admin_gate(
    authed_client_no_db,
    monkeypatch,
):
    from web.routes import medias as route_mod
    from web.services.media_mk_selection import MkSelectionResponse

    captured = {}

    def fake_build(args):
        captured["limit"] = args.get("limit")
        return MkSelectionResponse(
            {"items": [{"snapshot": "2026-05-18"}], "default_snapshot": "2026-05-18"},
            200,
        )

    monkeypatch.setattr(route_mod, "_build_mk_selection_snapshots_response", fake_build)

    response = authed_client_no_db.get("/medias/api/mk-selection/snapshots?limit=7")

    assert response.status_code == 200
    assert response.get_json()["items"] == [{"snapshot": "2026-05-18"}]
    assert captured["limit"] == "7"


def test_mk_selection_refresh_delegates_response_building_after_admin_gate(
    authed_client_no_db,
    monkeypatch,
):
    from web.routes import medias as route_mod
    from web.services.media_mk_selection import MkSelectionResponse

    calls = []
    monkeypatch.setattr(
        route_mod,
        "_build_mk_selection_refresh_response",
        lambda: calls.append("refresh") or MkSelectionResponse(
            {"ok": False, "error": "not_implemented"},
            501,
        ),
    )

    response = authed_client_no_db.post("/medias/api/mk-selection/refresh")

    assert response.status_code == 501
    assert response.get_json() == {"ok": False, "error": "not_implemented"}
    assert calls == ["refresh"]


def test_mk_selection_admin_only_routes_delegate_forbidden_response(
    authed_user_client_no_db,
    monkeypatch,
):
    from web.routes import medias as route_mod

    calls = []
    monkeypatch.setattr(
        route_mod,
        "_mk_admin_required_response",
        lambda: calls.append("forbidden") or ({"error": "forbidden-from-builder"}, 403),
    )
    monkeypatch.setattr(
        route_mod,
        "_build_mk_selection_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("selection builder should not run for non-admin")
        ),
    )
    monkeypatch.setattr(
        route_mod,
        "_build_mk_selection_snapshots_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("snapshots builder should not run for non-admin")
        ),
    )
    monkeypatch.setattr(
        route_mod,
        "_build_mk_selection_refresh_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("refresh builder should not run for non-admin")
        ),
    )
    monkeypatch.setattr(
        route_mod,
        "_build_mk_detail_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("detail builder should not run for non-admin")
        ),
    )
    monkeypatch.setattr(
        route_mod,
        "_build_mk_media_proxy_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("media proxy builder should not run for non-admin")
        ),
    )
    monkeypatch.setattr(
        route_mod,
        "_build_mk_video_proxy_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("video proxy builder should not run for non-admin")
        ),
    )

    responses = [
        authed_user_client_no_db.get("/medias/api/mk-selection"),
        authed_user_client_no_db.get("/medias/api/mk-selection/snapshots"),
        authed_user_client_no_db.post("/medias/api/mk-selection/refresh"),
        authed_user_client_no_db.get("/medias/api/mk-detail/3719"),
        authed_user_client_no_db.get("/medias/api/mk-media?path=uploads2/demo.jpg"),
        authed_user_client_no_db.get("/medias/api/mk-video?path=uploads2/demo.mp4"),
    ]

    assert [response.status_code for response in responses] == [403, 403, 403, 403, 403, 403]
    assert [response.get_json() for response in responses] == [
        {"error": "forbidden-from-builder"},
        {"error": "forbidden-from-builder"},
        {"error": "forbidden-from-builder"},
        {"error": "forbidden-from-builder"},
        {"error": "forbidden-from-builder"},
        {"error": "forbidden-from-builder"},
    ]
    assert calls == ["forbidden", "forbidden", "forbidden", "forbidden", "forbidden", "forbidden"]


def test_mk_media_proxy_fetches_wedev_media_with_server_credentials(
    authed_client_no_db,
    monkeypatch,
):
    captured = {}

    class FakeResponse:
        status_code = 200
        content = b"image-bytes"
        headers = {"content-type": "image/jpeg"}

    monkeypatch.setattr(
        "web.routes.medias.pushes.get_localized_texts_base_url",
        lambda: "https://wedev.example",
    )
    monkeypatch.setattr(
        "web.routes.medias.pushes.build_localized_texts_headers",
        lambda: {
            "Authorization": "Bearer synced-token",
            "Cookie": "token=synced-token; x-hng=lang=zh-CN&domain=os.wedev.vip",
            "Content-Type": "application/json",
        },
    )

    def fake_get(url, *, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("web.routes.medias.requests.get", fake_get)

    response = authed_client_no_db.get(
        "/medias/api/mk-media?path=./medias/uploads2/202505/1747910543.jpg"
    )

    assert response.status_code == 200
    assert response.data == b"image-bytes"
    assert response.content_type == "image/jpeg"
    assert captured["url"] == "https://wedev.example/medias/uploads2/202505/1747910543.jpg"
    assert captured["headers"]["Authorization"] == "Bearer synced-token"
    assert captured["headers"]["Cookie"] == "token=synced-token; x-hng=lang=zh-CN&domain=os.wedev.vip"
    assert captured["headers"]["Accept"] == "image/*,*/*;q=0.8"
    assert "Content-Type" not in captured["headers"]
    assert captured["timeout"] == 20


def test_mk_media_proxy_rejects_missing_wedev_credentials_without_request(
    authed_client_no_db,
    monkeypatch,
):
    import requests
    from web.routes import medias as route_mod
    from web.routes.medias import mk_selection

    monkeypatch.setattr(mk_selection.pushes, "build_localized_texts_headers", lambda: {})
    monkeypatch.setattr(mk_selection, "_get_mk_token", lambda: "")
    monkeypatch.setattr(mk_selection, "_get_mk_api_base_url", lambda: "https://wedev.example")

    def fail_get(*_args, **_kwargs):
        raise requests.ConnectionError("should not request wedev without credentials")

    monkeypatch.setattr(route_mod.requests, "get", fail_get)

    response = authed_client_no_db.get(
        "/medias/api/mk-media?path=./medias/uploads2/202505/1747910543.jpg"
    )

    assert response.status_code == 500
    assert response.get_json()["error"] == "明空凭据未配置，请先在设置页同步 wedev 凭据"


def test_mk_media_proxy_delegates_response_building_after_admin_gate(
    authed_client_no_db,
    monkeypatch,
):
    from web.routes import medias as route_mod
    from web.services.media_mk_selection import MkMediaProxyResponse

    captured = {}

    def fake_build(media_path):
        captured["media_path"] = media_path
        return MkMediaProxyResponse(
            status_code=200,
            content=b"image-bytes",
            content_type="image/jpeg",
            cache_control="private, max-age=3600",
        )

    monkeypatch.setattr(route_mod, "_build_mk_media_proxy_response", fake_build)
    monkeypatch.setattr(
        route_mod.requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("route should delegate mk media request handling")
        ),
    )

    response = authed_client_no_db.get(
        "/medias/api/mk-media?path=./medias/uploads2/202505/1747910543.jpg"
    )

    assert response.status_code == 200
    assert response.data == b"image-bytes"
    assert response.content_type == "image/jpeg"
    assert response.headers["Cache-Control"] == "private, max-age=3600"
    assert captured["media_path"] == "uploads2/202505/1747910543.jpg"


def test_mk_http_get_adapter_uses_medias_package_request_dependency(monkeypatch):
    from web.routes import medias as route_mod
    from web.routes.medias import mk_selection

    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return "ok"

    monkeypatch.setattr(route_mod.requests, "get", fake_get)

    result = mk_selection._mk_http_get("https://wedev.example/media.jpg", timeout=20)

    assert result == "ok"
    assert captured == {
        "url": "https://wedev.example/media.jpg",
        "kwargs": {"timeout": 20},
    }


def test_mk_video_proxy_caches_wedev_video_for_local_preview(
    authed_client_no_db,
    monkeypatch,
    tmp_path,
):
    from appcore import local_media_storage

    captured = {"calls": 0}
    payload = b"\x00\x00\x00\x20ftypisom-video-bytes"

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "video/mp4", "content-length": str(len(payload))}

        @staticmethod
        def iter_content(chunk_size=1024 * 1024):
            del chunk_size
            yield payload[:10]
            yield payload[10:]

    monkeypatch.setattr(local_media_storage, "MEDIA_STORE_DIR", tmp_path / "media_store")
    monkeypatch.setattr(
        "web.routes.medias.pushes.get_localized_texts_base_url",
        lambda: "https://wedev.example",
    )
    monkeypatch.setattr(
        "web.routes.medias.pushes.build_localized_texts_headers",
        lambda: {
            "Authorization": "Bearer synced-token",
            "Cookie": "token=synced-token; x-hng=lang=zh-CN&domain=os.wedev.vip",
            "Content-Type": "application/json",
        },
    )

    def fake_get(url, *, headers=None, timeout=None, stream=False):
        captured["calls"] += 1
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        captured["stream"] = stream
        return FakeResponse()

    monkeypatch.setattr("web.routes.medias.requests.get", fake_get)

    response = authed_client_no_db.get(
        "/medias/api/mk-video?path=./medias/uploads2/202505/1747910543.mp4"
    )

    assert response.status_code == 200
    assert response.data == payload
    assert response.mimetype == "video/mp4"
    assert captured["calls"] == 1
    assert captured["url"] == "https://wedev.example/medias/uploads2/202505/1747910543.mp4"
    assert captured["headers"]["Accept"] == "video/*,*/*;q=0.8"
    assert "Content-Type" not in captured["headers"]
    assert captured["stream"] is True

    def fail_get(*_args, **_kwargs):
        raise AssertionError("cached video should be served without refetching")

    monkeypatch.setattr("web.routes.medias.requests.get", fail_get)

    cached_response = authed_client_no_db.get(
        "/medias/api/mk-video?path=medias/uploads2/202505/1747910543.mp4"
    )

    assert cached_response.status_code == 200
    assert cached_response.data == payload


def test_mk_video_proxy_rejects_missing_wedev_credentials_without_request(
    authed_client_no_db,
    monkeypatch,
    tmp_path,
):
    import requests
    from appcore import local_media_storage
    from web.routes import medias as route_mod
    from web.routes.medias import mk_selection

    monkeypatch.setattr(local_media_storage, "MEDIA_STORE_DIR", tmp_path / "media_store")
    monkeypatch.setattr(mk_selection.pushes, "build_localized_texts_headers", lambda: {})
    monkeypatch.setattr(mk_selection, "_get_mk_token", lambda: "")
    monkeypatch.setattr(mk_selection, "_get_mk_api_base_url", lambda: "https://wedev.example")

    def fail_get(*_args, **_kwargs):
        raise requests.ConnectionError("should not request wedev without credentials")

    monkeypatch.setattr(route_mod.requests, "get", fail_get)

    response = authed_client_no_db.get(
        "/medias/api/mk-video?path=./medias/uploads2/202505/1747910543.mp4"
    )

    assert response.status_code == 500
    assert response.get_json()["error"] == "明空凭据未配置，请先在设置页同步 wedev 凭据"


def test_mk_video_proxy_rejects_local_media_path_escape(
    authed_client_no_db,
    monkeypatch,
    tmp_path,
):
    from web.routes import medias as r
    from web.routes.medias import mk_selection

    media_store = tmp_path / "media_store"
    media_store.mkdir()
    outside_file = tmp_path / "outside.mp4"
    outside_file.write_bytes(b"outside-video")
    object_key = "mk-selection/videos/demo.mp4"

    monkeypatch.setattr(r.local_media_storage, "MEDIA_STORE_DIR", media_store)
    monkeypatch.setattr(mk_selection, "_cache_mk_video", lambda media_path: object_key)
    monkeypatch.setattr(r.local_media_storage, "local_path_for", lambda key: outside_file)

    response = authed_client_no_db.get(
        "/medias/api/mk-video?path=medias/uploads2/202505/1747910543.mp4"
    )

    assert response.status_code == 404


def test_mk_video_proxy_delegates_response_building_after_admin_gate(
    authed_client_no_db,
    monkeypatch,
    tmp_path,
):
    from web.routes import medias as route_mod
    from web.services.media_mk_selection import MkVideoProxyResponse

    payload = b"\x00\x00\x00\x20ftypisom-video-bytes"
    local_path = tmp_path / "cached.mp4"
    local_path.write_bytes(payload)
    captured = {}

    def fake_build(media_path, guessed_type):
        captured["media_path"] = media_path
        captured["guessed_type"] = guessed_type
        return MkVideoProxyResponse(status_code=200, local_path=local_path, mimetype="video/mp4")

    monkeypatch.setattr(route_mod, "_build_mk_video_proxy_response", fake_build)
    monkeypatch.setattr(
        route_mod.requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("route should delegate mk video request/cache handling")
        ),
    )

    response = authed_client_no_db.get(
        "/medias/api/mk-video?path=./medias/uploads2/202505/1747910543.mp4"
    )

    assert response.status_code == 200
    assert response.data == payload
    assert response.mimetype == "video/mp4"
    assert captured == {
        "media_path": "uploads2/202505/1747910543.mp4",
        "guessed_type": "video/mp4",
    }


def test_mk_detail_proxy_uses_server_side_wedev_credentials(
    authed_client_no_db,
    monkeypatch,
):
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"data": {"item": {"id": 3719, "videos": []}}}

    monkeypatch.setattr(
        "web.routes.medias.pushes.get_localized_texts_base_url",
        lambda: "https://wedev.example",
    )
    monkeypatch.setattr(
        "web.routes.medias.pushes.build_localized_texts_headers",
        lambda: {
            "Authorization": "Bearer synced-token",
            "Cookie": "token=synced-token; x-hng=lang=zh-CN&domain=os.wedev.vip",
            "Content-Type": "application/json",
        },
    )

    def fake_get(url, *, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("web.routes.medias.requests.get", fake_get)

    response = authed_client_no_db.get("/medias/api/mk-detail/3719")

    assert response.status_code == 200
    assert response.get_json() == {"data": {"item": {"id": 3719, "videos": []}}}
    assert captured["url"] == "https://wedev.example/api/marketing/medias/3719"
    assert captured["headers"]["Authorization"] == "Bearer synced-token"
    assert captured["headers"]["Cookie"] == "token=synced-token; x-hng=lang=zh-CN&domain=os.wedev.vip"
    assert captured["headers"]["Accept"] == "application/json"
    assert captured["timeout"] == 15


def test_mk_detail_proxy_delegates_response_building_after_admin_gate(
    authed_client_no_db,
    monkeypatch,
):
    from web.routes import medias as route_mod
    from web.services.media_mk_selection import MkDetailResponse

    captured = {}

    def fake_build(mk_id):
        captured["mk_id"] = mk_id
        return MkDetailResponse({"data": {"item": {"id": mk_id}}}, 200)

    monkeypatch.setattr(route_mod, "_build_mk_detail_response", fake_build)
    monkeypatch.setattr(
        route_mod.requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("route should delegate mk detail request handling")
        ),
    )

    response = authed_client_no_db.get("/medias/api/mk-detail/3719")

    assert response.status_code == 200
    assert response.get_json() == {"data": {"item": {"id": 3719}}}
    assert captured["mk_id"] == 3719
