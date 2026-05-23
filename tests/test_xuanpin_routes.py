from __future__ import annotations


def _assert_unified_xuanpin_tabs(body: str, active_href: str, active_label: str) -> None:
    assert '<nav class="xuanpin-tabs" role="tablist" aria-label="选品中心类型">' in body
    assert f'<a class="xuanpin-tab active" href="{active_href}" role="tab" aria-selected="true">{active_label}</a>' in body
    assert 'href="/xuanpin/mk"' in body
    assert 'href="/xuanpin/meta-hot-posts"' in body
    assert 'href="/xuanpin/tabcut"' in body
    assert 'href="/xuanpin/today-recommendations"' in body
    assert 'href="/xuanpin/new-products"' in body


def _patch_new_product_review_list_deps(monkeypatch):
    monkeypatch.setattr(
        "appcore.new_product_review.list_pending",
        lambda **kw: [
            {
                "id": 1,
                "name": "Test Product",
                "product_code": "P001",
                "product_link": "https://example.com",
                "main_image": None,
                "translator_id": 10,
                "translator_name": "Alice",
                "cover_object_key": None,
                "mk_id": 123,
                "ai_score": 85.0,
                "ai_evaluation_result": "ok",
                "ai_evaluation_detail": None,
                "npr_decision_status": None,
                "npr_decided_countries": None,
                "npr_decided_at": None,
                "npr_eval_clip_path": None,
                "created_at": "2026-04-01 10:00:00",
                "updated_at": "2026-04-01 10:00:00",
            }
        ],
    )
    monkeypatch.setattr(
        "appcore.medias.list_enabled_languages_kv",
        lambda: [("de", "German"), ("fr", "French")],
    )
    monkeypatch.setattr(
        "web.routes.new_product_review._list_translators",
        lambda: [{"id": 10, "username": "Alice"}],
    )


def test_xuanpin_root_redirects_to_mk(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/")

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/xuanpin/mk")


def test_xuanpin_root_redirects_to_meta_hot_posts_when_mk_hidden(monkeypatch):
    import json

    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.db.query", lambda *args, **kwargs: [])
    monkeypatch.setattr("appcore.db.query_one", lambda *args, **kwargs: None)
    from web.app import create_app

    fake_user = {
        "id": 7,
        "username": "meta-worker",
        "role": "user",
        "is_active": 1,
        "permissions": json.dumps({"meta_hot_posts": True, "mk_selection": False}),
    }
    monkeypatch.setattr("web.auth.get_by_id", lambda user_id: fake_user if int(user_id) == 7 else None)
    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "7"
        session["_fresh"] = True

    resp = client.get("/xuanpin/")

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/xuanpin/meta-hot-posts")


def test_xuanpin_mk_page_uses_xuanpin_tabs_and_api(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    _assert_unified_xuanpin_tabs(body, "/xuanpin/mk", "明空选品")
    assert "oc-page-tabs" not in body
    assert "oc-page-tab" not in body
    assert "/xuanpin/api/mk-selection" in body
    assert "/xuanpin/api/mk-selection/snapshots" in body
    assert 'aria-label="明空选品库类型"' in body
    assert "产品库" in body
    assert "视频素材库" in body
    assert "昨天消耗前100" in body
    assert "/xuanpin/api/mk-material-library" in body
    assert "/xuanpin/api/mk-yesterday-top100" in body


def test_xuanpin_mk_video_cards_clamp_copy_and_hide_missing_sales(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert ".mk-line-clamp--2" in body
    assert ".mk-line-clamp--3" in body
    assert ".mk-video-product-name" in body
    assert ".mk-video-product-code" in body
    assert 'class="mk-video-card-title mk-line-clamp mk-line-clamp--3"' in body
    assert "· 销量 ${Number(r.sales_count || 0)}" not in body


def test_xuanpin_mk_video_cards_copy_code_and_show_first_mk_product_link(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "renderMkProductCodeCopyButton(rawProductHandle)" in body
    assert "const productLinkRaw = String(r.mk_product_link || r.product_url || '').trim();" in body
    assert 'class="mk-video-product-link-row"' in body
    assert 'class="mk-video-product-link mk-line-clamp mk-line-clamp--1"' in body
    assert "renderMkProductLinkCopyButton(productLinkRaw)" in body


def test_xuanpin_mk_video_cards_link_to_material_detail_page(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "function renderMkMaterialDetailHref(r)" in body
    assert "encodeURIComponent(r.material_key || '')" in body
    assert 'class="mk-video-detail-link"' in body
    assert 'target="_blank" rel="noopener noreferrer"' in body
    assert ".mk-video-title-actions { display:inline-flex; flex-direction:column;" in body
    assert "${detailLinkHtml}${filenameCopyHtml}</span>" in body


def test_xuanpin_mk_video_material_detail_page_renders_preview_and_history(
    authed_client_no_db,
    monkeypatch,
):
    material_key = "a" * 64

    monkeypatch.setattr(
        "appcore.mingkong_materials.get_material_detail",
        lambda key: {
            "material": {
                "material_key": key,
                "product_code": "cool-widget",
                "rank_position": 7,
                "mk_product_name": "MK Cool",
                "mk_product_link": "https://shop.example/products/cool-widget-rjc",
                "video_name": "winner.mp4",
                "video_path": "uploads2/winner.mp4",
                "video_image_path": "uploads2/winner.jpg",
                "local_cover_url": "",
                "video_spends": 12800.0,
                "video_spends_text": "1.28万",
                "video_ads_count": 16,
                "video_author": "Alice",
                "video_upload_time": "2026-05-20T10:00:00",
                "snapshot_at": "2026-05-22 05:00:02",
            },
            "history": [
                {
                    "snapshot_at": "2026-05-21 05:00:02",
                    "snapshot_slot": "0500",
                    "cumulative_90_spend": 12000.0,
                    "spend_delta": 0.0,
                    "video_ads_count": 12,
                },
                {
                    "snapshot_at": "2026-05-22 05:00:02",
                    "snapshot_slot": "0500",
                    "cumulative_90_spend": 12800.0,
                    "spend_delta": 800.0,
                    "video_ads_count": 16,
                },
            ],
            "summary": {"history_count": 2},
        }
        if key == material_key
        else None,
    )

    resp = authed_client_no_db.get(f"/xuanpin/mk/videos/{material_key}")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "明空视频素材详情" in body
    assert "winner.mp4" in body
    assert "mk-detail-media-grid" in body
    assert "mk-detail-cover-frame" in body
    assert "mk-detail-video-frame" in body
    assert body.count("<style>") == 1
    assert "--mkd-media-frame-w: 270px;" in body
    assert "--mkd-media-frame-h: 480px;" in body
    assert ".mk-detail-media-panel { border:1px solid var(--mkd-border); border-radius:var(--mkd-radius); background:#fff; padding:12px; display:flex; flex-direction:column; align-items:center;" in body
    assert ".mk-detail-cover-frame, .mk-detail-video-frame { width:var(--mkd-media-frame-w); max-width:100%; height:var(--mkd-media-frame-h);" in body
    assert "margin:0 auto;" in body
    assert ".mk-detail-video-frame video { width:100%; height:100%; object-fit:contain;" in body
    assert "/xuanpin/api/mk-media?path=uploads2/winner.jpg" in body
    assert "/xuanpin/api/mk-video?path=uploads2/winner.mp4" in body
    assert "历史同步消耗" in body
    assert ".mk-detail-history-table { width:min(100%, 980px);" in body
    assert ".mk-detail-history-table th.num, .mk-detail-history-table td.num" in body
    assert "2026-05-22 05:00:02" in body
    assert "800" in body


def test_xuanpin_mk_video_material_detail_page_404_when_missing(
    authed_client_no_db,
    monkeypatch,
):
    monkeypatch.setattr("appcore.mingkong_materials.get_material_detail", lambda key: None)

    resp = authed_client_no_db.get(f"/xuanpin/mk/videos/{'b' * 64}")

    assert resp.status_code == 404


def test_xuanpin_mk_video_import_metadata_includes_mk_paths(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-mki-video-path="${escapeHtml(videoPath)}"' in body
    assert 'data-mki-cover-path="${escapeHtml(coverPath)}"' in body
    assert "video_path: btn.dataset.mkiVideoPath || null" in body
    assert "cover_path: btn.dataset.mkiCoverPath || null" in body


def test_xuanpin_mk_video_cards_use_backend_material_status_for_import_button(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "const isMaterialImported = Boolean(r.has_local_material_in_library);" in body
    assert "${isMaterialImported ? '已入库' : '加入素材库'}" in body
    assert "${isMaterialImported ? 'disabled' : ''}" in body


def test_xuanpin_mk_material_import_modal_shows_fine_ai_soft_advice(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "mkiFineAiEvaluationFromStatus(productStatus)" in body
    assert "const hasFineAiEvaluationResult = mkiFineAiHasUsableResult(fineAiResult);" in body
    assert "data-mki-fine-ai-has-result" in body
    assert "function mkiImportProgressRenderFineAiPanel(btn)" in body
    assert "mkiImportProgressRenderFineAiPanel(mkiImportProgressButton);" in body
    assert "mki-progress-fine-ai-table" in body
    assert "mkiImportProgressOpenFineAi()" in body
    assert "建议先完成 AI 精细评估" in body
    assert "if (!mkiFineAiCanImportMaterial(btn))" not in body


def test_xuanpin_mk_material_import_fine_ai_country_columns_are_tinted_by_decision(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "function mkiFineAiCellClassForImport(row, baseClass)" in body
    assert "mkiFineAiCellClassForImport(row, 'mki-progress-fine-ai-cell')" in body
    assert "mkiFineAiCellClassForImport(row, 'fine-ai-result-cell')" in body
    assert ".mki-progress-fine-ai-cell.is-go" in body
    assert ".mki-progress-fine-ai-cell.is-test" in body
    assert ".mki-progress-fine-ai-cell.is-hold" in body
    assert ".fine-ai-result-cell.is-go" in body
    assert ".fine-ai-result-cell.is-test" in body
    assert ".fine-ai-result-cell.is-hold" in body


def test_xuanpin_mk_video_cards_show_product_icon_for_product_library_status(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "const hasProductInLibrary = Boolean(r.has_local_product_in_library || (r.product_ad_status && r.product_ad_status.has_local_match));" in body
    assert "if (hasProductInLibrary)" in body
    assert "产品已在素材库" in body


def test_xuanpin_mk_import_progress_includes_publish_domain_step(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "{key: 'domains', title: '选择发布域名'" in body
    assert "mkiImportProgressDomains" in body
    assert "mkiImportProgressRenderDomainRows" in body
    assert "function mkiImportProgressDefaultDomainIds()" in body
    assert "function mkiImportProgressEffectiveDomainIds()" in body
    assert "product-link-domains" in body
    assert "enabled_domain_ids" in body
    assert "确定发布域名" in body
    assert "未选择发布域名，已按默认域名继续" in body


def test_xuanpin_mk_import_progress_includes_product_owner_before_domains(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "{key: 'productOwner', title: '选择产品负责人'" in body
    assert body.index("{key: 'prepare'") < body.index("{key: 'productOwner'")
    assert body.index("{key: 'productOwner'") < body.index("{key: 'product', title: '检查产品与链接'")
    assert body.index("{key: 'productOwner'") < body.index("{key: 'domains'")
    assert "product_owner_id" in body
    assert "mkiImportProgressProductOwnerId" in body
    assert 'id="mkiTranslatorModal"' not in body


def test_xuanpin_mk_import_progress_keeps_actions_in_step_cards(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "function mkiImportProgressStepActionHtml(stepKey)" in body
    assert 'data-mki-progress-action="${escapeHtml(step.key)}"' in body
    assert "mki-progress-actions" not in body
    assert "mkiImportProgressCancelProductOwner" not in body
    assert 'id="mkiImportProgressProductOwner"' in body
    assert 'id="mkiImportProgressDomains"' in body
    assert 'id="mkiImportProgressNextActions"' in body
    assert body.index('id="mkiImportProgressProductOwner"') < body.index('id="mkiImportProgressDomains"')
    assert body.index('id="mkiImportProgressDomains"') < body.index('id="mkiImportProgressNextActions"')


def test_xuanpin_mk_import_progress_logs_product_record_visibility(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "function mkiImportProgressAppendStepLog(stepKey, message, kind = '')" in body
    assert "data-mki-progress-log-list" in body
    assert "检测到产品记录已存在，素材管理已可见" in body
    assert "服务端确认复用已有产品" in body
    assert "服务端已创建新产品" in body
    assert body.index("检测到产品记录已存在，素材管理已可见") < body.index("fetch('/mk-import/video'")


def test_xuanpin_mk_import_payload_sends_known_local_product_id(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "const localProductId = Number(btn.dataset.mkiLocalProductId || 0);" in body
    assert "media_product_id: localProductId > 0 ? localProductId : null" in body


def test_xuanpin_mk_import_progress_waits_for_domain_save_before_next_actions(
    authed_client_no_db,
):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "function mkiImportProgressHideNextActions()" in body
    assert "function mkiImportProgressShowNextActions()" in body
    assert "mkiImportProgressHideNextActions();" in body
    assert "mkiImportProgressSetStep('domains', 'done', domainDetail);" in body
    assert "mkiImportProgressSetStep('next', 'done', '发布域名已确认，可以创建小语种任务');" in body


def test_xuanpin_mk_uses_create_small_language_task_labels(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "创建小语种翻译任务" in body
    assert "下一步：创建小语种任务" in body
    assert "小语种翻译负责人" in body
    assert "产品负责人用于素材归属" in body
    assert "可与产品负责人不同" in body
    assert "继续做小语种任务" not in body
    assert "mkiXiaoLangLabel" in body
    assert "中文名 + 国家代码" not in body


def test_xuanpin_mk_small_language_task_requires_imported_material(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "const canCreateSmallLangTask = isMaterialImported && localProductId && localItemId;" in body
    assert "data-mki-local-product-id" in body
    assert "data-mki-local-item-id" in body
    assert "先加入素材库后才能创建小语种任务" in body
    assert "mkiXiaoCreateFromImportedMaterial" in body


def test_xuanpin_mk_cards_include_ai_evaluation_button_and_language_hints(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "eval_country_table.js" in body
    assert "mkiAiEvaluateFromCard" in body
    assert "mki-ai-btn" in body
    assert "AI评估" in body
    assert "data-mki-ai-product-id" in body
    assert "data-mki-product-link" in body
    assert "product_link: btn.dataset.mkiProductLink || null" in body
    assert "AI建议：" in body
    assert "mkiXiaoLoadAiSuggestions" in body
    assert "mkiXiaoApplyAiSuggestions" in body
    assert "mkiAiProductEndpoint(productId, '/evaluate/request-preview', itemId, productLink)" in body


def test_xuanpin_mk_small_language_ai_suggestions_are_card_hints(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "mki-xiao-lang-card" in body
    assert "mki-xiao-lang-card--high" in body
    assert "mki-xiao-lang-card--medium" in body
    assert "mki-xiao-lang-card--low" in body
    assert "function mkiXiaoAiSuggestionTone(row)" in body
    assert "function mkiXiaoAiSuggestionText(row, tone)" in body
    assert "AI建议：高评分" in body
    assert "AI建议：中间档" in body
    assert "AI建议：低分不可做" in body
    assert '<span class="mki-xiao-lang-pill-box" data-mki-ai-lang="' in body
    assert '<small class="mki-xiao-ai-suggestion">AI建议：暂无</small>' in body


def test_xuanpin_mk_small_language_uses_fine_ai_country_decisions(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "const MKI_FINE_AI_RESULT_CACHE = new Map();" in body
    assert "function mkiFineAiCountryIndex(detail)" in body
    assert "function mkiXiaoFineAiSuggestionTone(row)" in body
    assert "function mkiXiaoFineAiSuggestionText(row, tone)" in body
    assert "GO" in body
    assert "TEST" in body
    assert "HOLD" in body
    assert "fineAiResult: mkiFineAiResultFromButton(btn)" in body
    assert "mkiXiaoApplyAiSuggestions(options.fineAiResult)" in body


def test_xuanpin_mk_small_language_modal_shows_fine_ai_top_panel(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'id="mkiXiaoFineAi"' in body
    assert 'aria-label="AI 精细评估建议"' in body
    assert "function mkiXiaoRenderFineAiPanel(result)" in body
    assert "mkiXiaoRenderFineAiPanel(options.fineAiResult || null);" in body
    assert "mkiImportProgressFineAiTable(result)" in body
    assert "mkiXiaoOpenFineAi()" in body
    assert "sourceButton: btn" in body
    assert "sourceButton: mkiImportProgressButton" in body


def test_xuanpin_mk_ai_evaluation_result_button_opens_result_and_can_rerun(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "function mkiAiButtonLabel" in body
    assert "data-mki-ai-has-result" in body
    assert "\u0041\u0049\u8bc4\u4f30\u7ed3\u679c" in body
    assert "function mkiAiShowExistingResult" in body
    assert "function mkiAiRunEvaluation" in body
    assert "function mkiAiAttachReevaluateButton" in body
    assert "data-mki-ai-reevaluate" in body
    assert "\u91cd\u65b0\u8bc4\u4f30" in body
    assert "if (context.hasExistingResult) {" in body


def test_xuanpin_fine_ai_external_link_routes_delegate_to_service(authed_client_no_db, monkeypatch):
    calls = []

    class FakeService:
        def create_external_link_run(self, **kwargs):
            calls.append(("create_external", kwargs))
            return {
                "evaluation_run_id": "eval_external",
                "product_id": "0",
                "status": "queued",
                "countries": ["DE", "FR", "IT", "ES", "JP"],
                "created_at": "2026-05-22T00:00:00Z",
            }

        def start_run_async(self, evaluation_run_id):
            calls.append(("start", evaluation_run_id))
            return True

        def get_status(self, product_id, evaluation_run_id):
            calls.append(("status", product_id, evaluation_run_id))
            return {"evaluation_run_id": evaluation_run_id, "product_id": str(product_id), "status": "running"}

        def get_result(self, product_id, evaluation_run_id):
            calls.append(("result", product_id, evaluation_run_id))
            return {"evaluation_run_id": evaluation_run_id, "product_id": str(product_id), "status": "completed"}

        def get_latest_external_link_result(self, product_link, **kwargs):
            calls.append(("latest_external", product_link, kwargs))
            return {
                "evaluation_run_id": "eval_external",
                "product_id": "0",
                "status": "completed",
                "metadata": {
                    "external_product_link": product_link,
                    "external_card_video": {"path": kwargs.get("card_video_path")},
                },
            }

        def rerun_country(self, product_id, evaluation_run_id, country_code, **kwargs):
            calls.append(("rerun", product_id, evaluation_run_id, country_code, kwargs))
            return {
                "evaluation_run_id": evaluation_run_id,
                "product_id": str(product_id),
                "country_code": country_code,
                "status": "running",
            }

    monkeypatch.setattr("web.routes.xuanpin.get_fine_ai_evaluation_service", lambda: FakeService())
    monkeypatch.setattr("web.routes.medias._normalize_mk_media_path", lambda media_path: media_path.strip())
    monkeypatch.setattr(
        "web.routes.medias._cache_mk_video",
        lambda media_path: f"mk/videos/cached-{media_path.rsplit('/', 1)[-1]}",
    )
    monkeypatch.setattr(
        "web.services.fine_ai_product_link_check.link_availability.probe",
        lambda url: {"ok": True, "http_status": 200, "error": None, "elapsed_ms": 1},
    )

    post = authed_client_no_db.post(
        "/xuanpin/api/fine-ai-evaluation",
        json={
            "product_link": "https://example.test/products/new-idea",
            "product_name": "New Idea",
            "product_code": "new-idea",
            "card_video_path": "uploads2/selected-card.mp4",
            "card_video_url": "/xuanpin/api/mk-video?path=uploads2%2Fselected-card.mp4",
            "card_video_name": "selected-card.mp4",
            "card_video_duration_seconds": 18.5,
            "countries": ["DE", "FR", "IT", "ES", "JP"],
        },
    )
    status = authed_client_no_db.get("/xuanpin/api/fine-ai-evaluation/eval_external/status")
    result = authed_client_no_db.get("/xuanpin/api/fine-ai-evaluation/eval_external")
    latest = authed_client_no_db.get(
        "/xuanpin/api/fine-ai-evaluation/latest",
        query_string={
            "product_link": "https://example.test/products/new-idea",
            "card_video_path": "uploads2/selected-card.mp4",
        },
    )
    rerun = authed_client_no_db.post(
        "/xuanpin/api/fine-ai-evaluation/eval_external/countries/DE/rerun",
        json={"force_refresh": True},
    )

    assert post.status_code == 202
    assert status.status_code == 200
    assert result.status_code == 200
    assert latest.status_code == 200
    assert rerun.status_code == 202
    assert post.get_json()["data"]["product_id"] == "0"
    assert latest.get_json()["data"]["evaluation_run_id"] == "eval_external"
    assert calls[0][0] == "create_external"
    create_kwargs = calls[0][1]
    assert create_kwargs["product_link"] == "https://example.test/products/new-idea"
    assert create_kwargs["product_name"] == "New Idea"
    assert create_kwargs["product_code"] == "new-idea"
    assert create_kwargs["link_check_result"]["ok"] is True
    assert create_kwargs["link_check_result"]["selected_link"] == "https://example.test/products/new-idea"
    assert create_kwargs["card_video_path"] == "uploads2/selected-card.mp4"
    assert create_kwargs["card_video_url"] == "/xuanpin/api/mk-video?path=uploads2%2Fselected-card.mp4"
    assert create_kwargs["card_video_name"] == "selected-card.mp4"
    assert create_kwargs["card_video_duration_seconds"] == 18.5
    assert create_kwargs["card_video_object_key"] == "mk/videos/cached-selected-card.mp4"
    assert create_kwargs["countries"] == ["DE", "FR", "IT", "ES", "JP"]
    assert create_kwargs["force_refresh"] is True
    assert create_kwargs["locale"] == "zh-CN"
    assert ("start", "eval_external") in calls
    assert ("status", 0, "eval_external") in calls
    assert (
        "latest_external",
        "https://example.test/products/new-idea",
        {
            "card_video_object_key": "",
            "card_video_path": "uploads2/selected-card.mp4",
            "card_video_url": "",
            "card_video_name": "",
        },
    ) in calls


def test_xuanpin_fine_ai_external_link_replaces_unavailable_link_from_mingkong_candidates(
    authed_client_no_db,
    monkeypatch,
):
    from types import SimpleNamespace

    calls = []
    bad_link = "https://shop.example/products/bad"
    good_link = "https://shop.example/products/good"

    class FakeService:
        def create_external_link_run(self, **kwargs):
            calls.append(("create_external", kwargs))
            return {
                "evaluation_run_id": "eval_external",
                "product_id": "0",
                "status": "queued",
                "countries": ["DE", "FR", "IT", "ES", "JP"],
                "created_at": "2026-05-22T00:00:00Z",
                "link_check": kwargs.get("link_check_result"),
            }

        def start_run_async(self, evaluation_run_id):
            calls.append(("start", evaluation_run_id))
            return True

    probe_calls = []

    def fake_probe(url: str):
        probe_calls.append(url)
        return {
            "ok": url == good_link,
            "http_status": 200 if url == good_link else 404,
            "error": None if url == good_link else "http 404",
            "elapsed_ms": 9,
        }

    monkeypatch.setattr("web.routes.xuanpin.get_fine_ai_evaluation_service", lambda: FakeService())
    monkeypatch.setattr("web.routes.medias._normalize_mk_media_path", lambda media_path: media_path.strip())
    monkeypatch.setattr(
        "web.routes.medias._cache_mk_video",
        lambda media_path: f"mk/videos/cached-{media_path.rsplit('/', 1)[-1]}",
    )
    monkeypatch.setattr(
        "web.routes.medias._build_mk_detail_response",
        lambda mk_id: SimpleNamespace(
            status_code=200,
            payload={
                "data": {
                    "item": {
                        "product_links": [
                            bad_link,
                            good_link,
                        ]
                    }
                }
            },
        ),
    )
    monkeypatch.setattr("web.services.fine_ai_product_link_check.link_availability.probe", fake_probe)

    post = authed_client_no_db.post(
        "/xuanpin/api/fine-ai-evaluation",
        json={
            "product_link": bad_link,
            "product_name": "Link Candidate Product",
            "product_code": "candidate-product",
            "mk_product_id": 7788,
            "card_video_path": "uploads2/selected-card.mp4",
            "countries": ["DE", "FR", "IT", "ES", "JP"],
        },
    )

    assert post.status_code == 202
    payload = post.get_json()["data"]
    assert payload["link_check"]["selected_link"] == good_link
    assert payload["link_check"]["status"] == "replaced"
    assert calls[0][1]["product_link"] == good_link
    assert calls[0][1]["link_check_result"]["original_link"] == bad_link
    assert probe_calls == [bad_link, good_link]
    assert ("start", "eval_external") in calls


def test_xuanpin_fine_ai_external_link_requires_product_link(authed_client_no_db):
    resp = authed_client_no_db.post("/xuanpin/api/fine-ai-evaluation", json={"product_link": ""})

    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "PRODUCT_LINK_REQUIRED"


def test_xuanpin_fine_ai_external_link_requires_current_card_video(authed_client_no_db):
    resp = authed_client_no_db.post(
        "/xuanpin/api/fine-ai-evaluation",
        json={"product_link": "https://example.test/products/new-idea"},
    )

    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "CARD_VIDEO_REQUIRED"


def test_xuanpin_fine_ai_external_detail_page_renders_independent_shell(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/fine-ai-evaluation/eval_external")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "AI精细评估独立页" in body
    assert "fine_ai_evaluation_detail.js" in body
    assert '"mode": "external"' in body
    assert '"/xuanpin/api/fine-ai-evaluation/eval_external/status"' in body
    assert '"/xuanpin/api/fine-ai-evaluation/eval_external"' in body
    assert '"/xuanpin/api/fine-ai-evaluation/eval_external/countries/{country}/rerun"' in body


def test_xuanpin_mk_uses_translation_work_user_api(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "const MKI_ACTIVE_USERS_API = '/tasks/api/translation-work-users';" in body
    assert "/medias/api/users/active" not in body
    assert "没有可用翻译工作用户" in body


def test_xuanpin_mk_video_cards_pause_other_videos_on_play(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "function pauseOtherMkVideos(activeVideo)" in body
    assert "document.querySelectorAll('.mk-video-source').forEach(video => {" in body
    assert "video !== activeVideo && !video.paused && !video.ended" in body
    assert "function handleMkVideoPlay(event)" in body
    assert "video.classList.contains('mk-video-source')" in body
    assert "document.addEventListener('play', handleMkVideoPlay, true);" in body


def test_xuanpin_meta_hot_posts_keeps_single_video_playback_guard(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/meta-hot-posts")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    _assert_unified_xuanpin_tabs(body, "/xuanpin/meta-hot-posts", "Meta热帖")
    assert "function pauseOtherMetaHotVideos(activeVideo)" in body
    assert "document.querySelectorAll('.meta-hot-page video').forEach(video => {" in body
    assert "function handleMetaHotVideoPlay(event)" in body
    assert "document.addEventListener('play', handleMetaHotVideoPlay, true);" in body


def test_xuanpin_tabcut_page_uses_xuanpin_tabs_and_api(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/tabcut")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    _assert_unified_xuanpin_tabs(body, "/xuanpin/tabcut", "TABCUT")
    assert "tabcut-tabs" not in body
    assert "tabcut-tab-link" not in body
    assert "/xuanpin/api/tabcut/videos" in body
    assert "/xuanpin/api/tabcut/goods" in body
    assert "/xuanpin/api/tabcut/categories" in body
    assert '<select class="tabcut-select" id="categoryL1">' in body
    assert '<input class="tabcut-input" id="minPrice" type="number"' in body
    assert '<input class="tabcut-input" id="maxPrice" type="number"' in body
    assert '<input class="tabcut-input" id="minGoodsSales" type="number"' in body
    assert '<input class="tabcut-input" id="maxGoodsSales" type="number"' in body
    assert 'id="markStatus"' in body
    assert '<option value="ok">行</option>' in body
    assert '<option value="bad">不行</option>' in body
    assert 'params.set(tabcutView === "videos" ? "min_item_price" : "min_price", qs("minPrice").value)' in body
    assert 'params.set(tabcutView === "videos" ? "max_item_price" : "max_price", qs("maxPrice").value)' in body
    assert 'params.set(tabcutView === "videos" ? "min_goods_sales_7d" : "min_sales_7d", qs("minGoodsSales").value)' in body
    assert 'params.set(tabcutView === "videos" ? "max_goods_sales_7d" : "max_sales_7d", qs("maxGoodsSales").value)' in body
    assert 'params.set("mark_status", qs("markStatus").value)' in body
    assert "tabcut-video-grid" in body
    assert "function normalizeTabcutGotoPage(raw, totalPages)" in body
    assert 'class="tabcut-pager-goto"' in body


def test_xuanpin_new_products_page_uses_xuanpin_tabs_and_api(
    authed_client_no_db,
    monkeypatch,
):
    _patch_new_product_review_list_deps(monkeypatch)

    resp = authed_client_no_db.get("/xuanpin/new-products")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    _assert_unified_xuanpin_tabs(body, "/xuanpin/new-products", "新品选择")
    assert "oc-page-tabs" not in body
    assert "oc-page-tab" not in body
    assert "/xuanpin/api/new-products/list" in body


def test_xuanpin_today_recommendations_page_uses_tab_and_api(
    authed_client_no_db,
    monkeypatch,
):
    monkeypatch.setattr(
        "appcore.today_recommendations.list_recommendations",
        lambda **kw: [
            {
                "id": 1,
                "product_name": "Test Product",
                "product_url": "https://example.com/products/test",
                "product_recommendation_rank": 1,
                "material_rank": 1,
                "rank_position": 5,
                "sales_count": 10,
                "order_count": 9,
                "mk_product_name": "Test MK",
                "product_handle": "test",
                "product_key": "test",
                "video_image_path": "",
                "video_path": "videos/test.mp4",
                "video_name": "test.mp4",
                "video_spends": 100,
                "video_ads_count": 2,
                "recommended_countries": ["de", "fr"],
                "ai_reason": "good fit",
                "status": "pending",
            }
        ],
    )
    monkeypatch.setattr(
        "appcore.today_recommendations.latest_run_summary",
        lambda: {
            "recommendation_date": "2026-05-12",
            "ranking_snapshot_date": "2026-05-11",
            "status": "success",
        },
    )
    monkeypatch.setattr(
        "appcore.users.list_translators",
        lambda: [{"id": 10, "username": "Alice"}],
    )

    resp = authed_client_no_db.get("/xuanpin/today-recommendations")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    _assert_unified_xuanpin_tabs(body, "/xuanpin/today-recommendations", "今日推荐")
    assert 'class="tr-tabs"' not in body
    assert '<a class="tr-tab' not in body
    assert "/xuanpin/api/today-recommendations/adopt" in body
    assert "Test Product" in body


def test_legacy_selection_pages_redirect_to_xuanpin(authed_client_no_db):
    responses = {
        "/medias/mk-selection": "/xuanpin/mk",
        "/medias/tabcut-selection": "/xuanpin/tabcut",
        "/new-product-review/": "/xuanpin/new-products",
    }

    for old_path, new_path in responses.items():
        resp = authed_client_no_db.get(old_path)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(new_path)


def test_xuanpin_mk_api_alias_delegates_after_admin_gate(
    authed_client_no_db,
    monkeypatch,
):
    from web.services.media_mk_selection import MkSelectionResponse

    captured = {}

    def fake_build(args):
        captured["keyword"] = args.get("keyword")
        return MkSelectionResponse(
            {"items": [{"rank": 1}], "total": 1, "page": 1, "page_size": 50},
            200,
        )

    monkeypatch.setattr("web.routes.medias._build_mk_selection_response", fake_build)

    resp = authed_client_no_db.get("/xuanpin/api/mk-selection?keyword=tooth")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"rank": 1}]
    assert captured["keyword"] == "tooth"


def test_xuanpin_mk_selection_snapshots_api_alias_delegates_after_admin_gate(
    authed_client_no_db,
    monkeypatch,
):
    from web.services.media_mk_selection import MkSelectionResponse

    captured = {}

    def fake_build(args):
        captured["limit"] = args.get("limit")
        return MkSelectionResponse(
            {"items": [{"snapshot": "2026-05-18"}], "default_snapshot": "2026-05-18"},
            200,
        )

    monkeypatch.setattr("web.routes.medias._build_mk_selection_snapshots_response", fake_build)

    resp = authed_client_no_db.get("/xuanpin/api/mk-selection/snapshots?limit=7")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"snapshot": "2026-05-18"}]
    assert captured["limit"] == "7"


def test_xuanpin_mk_video_materials_api_delegates_after_admin_gate(
    authed_client_no_db,
    monkeypatch,
):
    from web.services.media_mk_selection import MkSelectionResponse

    captured = {}

    def fake_build(args):
        captured["keyword"] = args.get("keyword")
        captured["product_code"] = args.get("product_code")
        return MkSelectionResponse(
            {"items": [{"video_name": "winner.mp4"}], "page": 1, "page_size": 24},
            200,
        )

    monkeypatch.setattr("web.routes.medias._build_mk_video_materials_response", fake_build)

    resp = authed_client_no_db.get("/xuanpin/api/mk-video-materials?keyword=tooth&product_code=cool-widget")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"video_name": "winner.mp4"}]
    assert captured["keyword"] == "tooth"
    assert captured["product_code"] == "cool-widget"


def test_xuanpin_mk_material_library_api_reads_local_archive(
    authed_client_no_db,
    monkeypatch,
):
    captured = {}

    def fake_list_material_library(**kwargs):
        captured.update(kwargs)
        return {
            "items": [{"video_name": "winner.mp4"}],
            "snapshot": "2026-05-18",
            "total": 1,
        }

    monkeypatch.setattr(
        "appcore.mingkong_materials.list_material_library",
        fake_list_material_library,
    )

    resp = authed_client_no_db.get(
        "/xuanpin/api/mk-material-library?keyword=tooth&page=2&page_size=24&snapshot=2026-05-18&range=this_week"
    )

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"video_name": "winner.mp4"}]
    assert captured == {
        "snapshot_date": "2026-05-18",
        "snapshot_at": None,
        "range_key": "this_week",
        "keyword": "tooth",
        "page": "2",
        "page_size": "24",
    }


def test_xuanpin_mk_yesterday_top100_api_reads_archive(
    authed_client_no_db,
    monkeypatch,
):
    captured = {}

    def fake_list_yesterday_top100(**kwargs):
        captured.update(kwargs)
        return {
            "items": [{"video_name": "fresh.mp4", "is_new_top100_entry": True}],
            "snapshot": "2026-05-18",
            "previous_snapshot": "2026-05-17",
            "total": 1,
        }

    monkeypatch.setattr(
        "appcore.mingkong_materials.list_yesterday_top100",
        fake_list_yesterday_top100,
    )

    resp = authed_client_no_db.get(
        "/xuanpin/api/mk-yesterday-top100?page=1&page_size=100&snapshot=2026-05-18&keyword=baseball"
    )

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"video_name": "fresh.mp4", "is_new_top100_entry": True}]
    assert captured == {
        "snapshot_date": "2026-05-18",
        "snapshot_at": None,
        "keyword": "baseball",
        "page": "1",
        "page_size": "100",
    }


def test_xuanpin_tabcut_api_alias_delegates(authed_client_no_db, monkeypatch):
    from appcore.tabcut_selection.service import TabcutResponse

    monkeypatch.setattr(
        "appcore.tabcut_selection.service.build_videos_response",
        lambda args: TabcutResponse({"items": [{"video_id": "v1"}], "total": 1}),
    )

    resp = authed_client_no_db.get("/xuanpin/api/tabcut/videos?sort=score")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"video_id": "v1"}]


def test_xuanpin_tabcut_categories_api_alias_delegates(authed_client_no_db, monkeypatch):
    from appcore.tabcut_selection.service import TabcutResponse

    monkeypatch.setattr(
        "appcore.tabcut_selection.service.build_category_options_response",
        lambda args: TabcutResponse({"items": [{"value": "Beauty", "label": "Beauty"}]}),
    )

    resp = authed_client_no_db.get("/xuanpin/api/tabcut/categories")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"value": "Beauty", "label": "Beauty"}]


def test_xuanpin_tabcut_mark_api_alias_delegates(authed_client_no_db, monkeypatch):
    from appcore.tabcut_selection.service import TabcutResponse

    captured = {}

    def fake_build(entity_type, entity_id, payload, *, user_id=None):
        captured.update({"entity_type": entity_type, "entity_id": entity_id, "payload": payload})
        return TabcutResponse({"ok": True, "mark_status": "ok"})

    monkeypatch.setattr(
        "appcore.tabcut_selection.service.build_mark_response",
        fake_build,
    )

    resp = authed_client_no_db.post(
        "/xuanpin/api/tabcut/videos/v1/mark",
        json={"mark_status": "ok"},
    )

    assert resp.status_code == 200
    assert resp.get_json()["mark_status"] == "ok"
    assert captured == {
        "entity_type": "video",
        "entity_id": "v1",
        "payload": {"mark_status": "ok"},
    }


def test_xuanpin_new_product_api_alias_delegates(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "appcore.new_product_review.evaluate_product",
        lambda product_id, actor_user_id: {
            "status": "evaluated",
            "product_id": product_id,
            "ai_score": 85.0,
            "ai_evaluation_result": "ok",
            "detail": {},
        },
    )

    resp = authed_client_no_db.post("/xuanpin/api/new-products/1/evaluate")

    assert resp.status_code == 200
    assert resp.get_json()["product_id"] == 1


def test_xuanpin_today_recommendations_api_aliases(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "appcore.today_recommendations.list_recommendations",
        lambda **kw: [{"id": 1, "status": "pending"}],
    )
    monkeypatch.setattr("appcore.today_recommendations.latest_run_summary", lambda: {"status": "success"})
    monkeypatch.setattr(
        "appcore.today_recommendations.adopt_recommendations",
        lambda **kw: {"adopted": [{"id": 1}], "skipped": [], "failed": []},
    )

    list_resp = authed_client_no_db.get("/xuanpin/api/today-recommendations/list")
    adopt_resp = authed_client_no_db.post(
        "/xuanpin/api/today-recommendations/adopt",
        json={"recommendation_ids": [1], "translator_id": 10},
    )

    assert list_resp.status_code == 200
    assert list_resp.get_json()["items"] == [{"id": 1, "status": "pending"}]
    assert adopt_resp.status_code == 200
    assert adopt_resp.get_json()["adopted"] == [{"id": 1}]
