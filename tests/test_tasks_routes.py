import io


def test_index_renders_for_admin(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    assert rsp.status_code == 200
    assert "任务中心".encode("utf-8") in rsp.data


def test_task_detail_route_renders_task_center_with_initial_detail_id(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/detail/44")
    assert rsp.status_code == 200
    body = rsp.data.decode("utf-8")

    assert "小语种视频翻译" in body
    assert "const TC_INITIAL_DETAIL_TASK_ID = 44;" in body
    assert "tcOpenDetail(dlTaskId)" in body


def test_task_detail_route_requires_login(authed_client_no_db):
    client = authed_client_no_db.application.test_client()
    rsp = client.get("/tasks/detail/44", follow_redirects=False)

    assert rsp.status_code in (302, 401)


def test_task_center_row_actions_navigate_to_detail_route(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    assert rsp.status_code == 200
    body = rsp.data.decode("utf-8")

    assert "function tcTaskDetailUrl(id)" in body
    assert "window.location.href = tcTaskDetailUrl(id);" in body
    start = body.index("function tcOpenDetailAction")
    end = body.index("function tcActiveTaskAction", start)
    action_helper = body[start:end]

    assert "tcTaskDetailUrl(id)" in action_helper
    assert "tcOpenDetail(" not in action_helper


def test_task_detail_drawer_sticks_header_and_refreshes_latest_status(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    assert ".tc-detail-sticky { position:sticky; top:0;" in body
    assert "刷新最新状态" in body
    assert "function tcRefreshDetail" in body
    assert "tcLoadDetail(id, {forceRefresh: true" in body
    assert "tcLoadArtifacts(id)" in body
    assert "tcLoadReadiness(id, task)" in body


def test_task_center_child_translate_jump_uses_product_code_search(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")
    assert "function tcMediasUrl" in body
    assert "params.set('q', productCode)" in body
    assert "params.set('from_task', String(taskId || ''))" in body
    assert "tcChildJumpTranslate(taskId, country, productId, productCode)" in body


def test_task_center_renders_backend_product_actions(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    assert "function tcRenderActionLinks" in body
    assert "tc-action-link--primary" in body
    assert "check.actions" in body
    assert "it.actions" in body


def test_task_detail_child_readiness_renders_manual_submit_modal_without_confirm_buttons(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    assert "function tcRenderReadinessManualActions" in body
    assert "手动提交" in body
    assert "tcOpenManualSubmit" in body
    assert "tcManualSubmitModal" in body
    assert "确认完成" not in body
    assert "tcConfirmChildStep" not in body
    assert "/steps/' + encodeURIComponent(stepKey) + '/confirm" not in body


def test_task_center_list_localizes_status_and_uses_action_entry_labels(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    assert "function tcStatusLabel(status)" in body
    assert "blocked: '等待 去字幕原始素材'" in body
    assert "assigned: '待处理'" in body
    assert "raw_in_progress: '去字幕原始视频素材处理中'" in body
    assert "function tcTaskStatusLabel(task)" in body
    assert "管理员已拒绝" in body
    assert '<span class="tc-badge tc-badge--${tcEsc(it.high_level)}">${tcEsc(tcTaskStatusLabel(it))}</span>' in body
    assert "function tcTaskTypeLabel(task)" in body
    assert "const kind = tcTaskTypeLabel(it);" in body
    assert "task && task.parent_task_id ? '小语种翻译' : '去字幕'" in body
    assert "function tcTaskStatusCell(it)" in body
    assert "<td>${tcTaskStatusCell(it)}</td>" in body
    assert "tcActiveTaskAction(parentId, '去查看等待的任务', false)" in body
    assert "function tcTaskRowAction(it)" in body
    assert "tcDisabledTaskAction('等待 去字幕原始素材')" in body
    assert "function tcBlockedTaskAction(it)" not in body
    assert "if (status === 'blocked') return tcDisabledTaskAction('等待 去字幕原始素材');" in body
    assert "tcOpenDetail(id)" in body
    assert "去处理" in body
    assert "处理去字幕原始视频" in body
    assert "认领处理" not in body
    assert "const kind = it.parent_task_id ? '子任务' : '父任务';" not in body
    assert "等待前置完成" not in body
    assert "查看结果" in body
    assert "查看记录" in body
    assert ">详情</button>" not in body
    assert "<td>${tcEsc(it.status)}</td>" not in body


def test_task_center_raw_review_self_actions_render_in_step(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    assert "function tcRenderRawSelfReviewActions" in body
    assert "审核通过，结果视频入库" in body
    assert "手动提交修改后的结果视频" in body
    assert "tcOpenManualRawResult" in body
    assert "tcManualRawResultModal" in body
    assert "manual_result" in body
    assert "去字幕原始视频素材处理" in body


def test_task_center_niuma_step_uses_status_only_and_centered_detail_button(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    assert ".tc-btn { display:inline-flex; align-items:center; justify-content:center;" in body
    assert "text-align:center;" in body
    assert "text-decoration:none;" in body
    assert "结果反馈" in body
    assert "错误摘要" not in body
    assert "function tcShouldShowSubtitleRemovalComparison" in body
    assert "String(event && event.event_type || '') !== 'raw_niuma_submitted'" in body


def test_task_center_parent_detail_exposes_force_niuma_rerun_button(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    assert "强制重跑" in body
    assert "tcCanForceRerunNiuma" in body
    assert "tcForceRerunNiuma" in body
    assert "force_niuma_rerun" in body


def test_task_center_does_not_jump_to_legacy_raw_pool_page(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    assert "function tcParentUploadDone" not in body
    assert "window.open('/raw-video-pool/'" not in body


def test_task_center_overview_uses_status_subtabs_and_pagination(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    assert "任务总览" in body
    assert 'data-section-tab="overview"' in body
    assert 'data-bucket="todo"' in body
    assert 'data-bucket="review"' in body
    assert 'data-bucket="done"' in body
    assert "待处理任务" in body
    assert "待审核任务" in body
    assert "已完成任务" in body
    assert "function tcRenderTaskPager" in body
    assert "TC_TASK_PAGE_SIZE" in body
    assert "const TC_TASK_PAGE_SIZE = 50;" in body
    assert "page_size: String(TC_TASK_PAGE_SIZE)" in body
    assert 'class="tc-pager__summary"' in body
    assert "共 ${total} 条" in body
    assert "每页 ${pageSize} 条" in body
    assert "第 ${page} / ${totalPages} 页" in body
    assert "第一页" in body
    assert "最后一页" in body
    assert "function tcTaskGotoPage" in body
    assert "function tcTaskJumpPage" in body
    assert "function tcNormalizeTaskPage" in body
    assert "Number.isFinite(parsed)" in body
    assert "<th>任务</th><th>类型</th><th>语言</th><th>状态</th><th>负责人</th><th>创建时间</th><th>操作</th>" in body


def test_task_center_overview_has_task_type_filter_before_refresh(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    select_pos = body.index('id="tcTaskTypeFilter"')
    refresh_pos = body.index('id="tcRefresh"')
    assert select_pos < refresh_pos
    assert '<option value="all" selected>全部</option>' in body
    assert '<option value="raw">去字幕任务</option>' in body
    assert '<option value="translate">翻译任务</option>' in body
    assert "tcTaskTypeFilter" in body
    assert "task_type: taskType" in body
    assert "tcTaskTypeFilter').addEventListener('keyup'" in body


def test_task_center_overview_has_assignee_filter_after_task_type(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    type_pos = body.index('id="tcTaskTypeFilter"')
    assignee_pos = body.index('id="tcAssigneeFilter"')
    refresh_pos = body.index('id="tcRefresh"')
    assert type_pos < assignee_pos < refresh_pos
    assert "tcLoadAssigneeFilterUsers" in body
    assert "/tasks/api/translation-work-users" in body
    assert "assignee_id: assigneeId" in body
    assert "tcAssigneeFilter').addEventListener('change'" in body


def test_task_center_hides_dispatch_pool_menu(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    assert "待派单素材" not in body
    assert 'data-section-tab="dispatch"' not in body
    assert "tcTabDispatch" not in body


def test_task_detail_drawer_uses_half_screen_chinese_process_view(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    assert "--tc-detail-drawer-w: 70vw;" in body
    assert "width: var(--tc-detail-drawer-w)" in body
    assert "function tcRenderEventTimeline" in body
    assert "function tcHumanEvent" in body
    assert "审核流程" in body
    assert "牛马去字幕失败" in body
    assert "手动上传原始视频" in body
    assert "技术详情" in body
    assert "function tcEventPayloadUserDisplay" in body
    assert "actor_display_name" in body
    assert "payload_context" in body
    assert "翻译员 ID" not in body
    assert "add('翻译员'," in body
    assert "/tasks/api/translation-work-users" in body
    assert "push_material_approved" in body
    assert "管理员审核通过" in body
    assert "已推送" in body
    assert "管理员审核拒绝" in body
    assert "继续完善素材内容" in body
    assert "问题点" in body
    assert "管理员批注" in body


def test_task_center_formats_language_codes_with_chinese_labels(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    assert "const TC_LANGUAGE_FALLBACKS" in body
    assert "function tcLanguageLabel" in body
    assert "function tcLoadLanguageLabels" in body
    assert "payload.countries.map(tcLanguageLabel).join('、')" in body
    assert "tcLanguageLabel(payload.country)" in body
    assert "const country = it.country_code ? tcEsc(tcLanguageLabel(it.country_code)) : '—';" in body
    assert "tcEsc(tcTaskTypeLabel(task))" in body
    assert "'（' + tcEsc(tcLanguageLabel(task.country_code)) + '）'" in body
    assert "翻译产物状态 (${tcEsc(tcLanguageLabel(task.country_code))})" in body


def test_task_detail_header_shows_source_filename_and_product_code_copy_actions(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    assert "function tcCopyText" in body
    assert "function tcRenderDetailIdentityRows" in body
    assert "素材文件名" in body
    assert "Product code" in body
    assert "source_media_filename" in body
    assert "tcCopyButton(task.source_media_filename" in body
    assert "tcCopyButton(task.product_code" in body


def test_task_detail_readiness_exposes_inline_ad_language_controls(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    assert "tc-lang-enable-strip" in body
    assert "function tcRenderAdSupportedLangControls" in body
    assert "function tcToggleAdSupportedLang" in body
    assert "link-availability/' + encodeURIComponent(lang)" in body
    assert "ad_supported_langs: selected.join(',')" in body
    assert "tcLoadReadiness(TC_DETAIL_TASK_ID, TC_DETAIL_CURRENT_TASK)" in body
    assert ".tc-lang-checkbox.is-current { border-color:var(--tc-danger); outline:2px solid var(--tc-danger);" in body
    assert "background:color-mix(in oklch, var(--tc-accent-subtle) 62%, var(--tc-accent) 38%);" in body


def test_task_detail_readiness_embeds_product_link_manager(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    assert "function tcRenderProductLinkManagerShell" in body
    assert "data-tc-product-link-manager" in body
    assert "function tcLoadProductLinkManager" in body
    assert "function tcProductLinkAction" in body
    assert "data-tc-pl-action=\"confirm-link\"" in body
    assert "manual_confirm: true" in body
    assert "manual_abnormal: true" in body
    assert "'/shopify-image/' + encodeURIComponent(lang) + '/confirm'" in body
    assert "'/shopify-image/' + encodeURIComponent(lang) + '/clear'" in body
    assert "'/shopify-image/' + encodeURIComponent(lang) + '/requeue'" not in body
    assert "data-tc-pl-action=\"shopify-clear\"" in body
    assert "data-tc-pl-action=\"shopify-requeue\"" not in body
    assert "标记图片未替换" in body
    assert "重新排队换图" not in body
    assert "tcLoadProductLinkManager(data, task)" in body


def test_task_detail_product_link_manager_collapses_shopify_image_status_to_two_states(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    labels = body[
        body.index("const TC_SHOPIFY_IMAGE_REPLACE_LABELS"):
        body.index("function tcEsc")
    ]
    shopify_row = body[
        body.index("function tcRenderProductLinkShopifyRow"):
        body.index("function tcRenderProductLinkRow")
    ]

    assert "图片正常" in labels
    assert "未替换" in labels
    assert "已排队" not in labels
    assert "替换中" not in labels
    assert "自动替换完成" not in labels
    assert "替换失败" not in labels
    assert "已确认" not in labels
    assert "tcShopifyImageReplaceLabel(status)" in shopify_row
    assert "status.link_status" not in shopify_row


def test_task_detail_readiness_groups_product_link_checks_into_manager_card(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    assert ".tc-product-link-combo" in body
    assert ".tc-readiness-evidence--domain-list" in body
    assert "function tcIsProductLinkCombinedCheck" in body
    assert "function tcRenderProductLinkCombinedCard" in body
    assert "tcRenderReadinessEvidence(check.evidence || [], {domainList: opts.mode === 'product-link-combo'})" in body
    assert "tcIsProductLinkCombinedCheck(check)" in body
    assert "tcRenderProductLinkCombinedCard(data, task, linkCombinedChecks)" in body
    assert "tcRenderProductLinkManagerShell(data, task)" in body
    assert "}).join('') + tcRenderProductLinkManagerShell(data, task)" not in body


def test_task_detail_product_link_combo_hides_duplicate_reason_and_uses_status_card_tones(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    assert ".tc-product-link-combo.is-ok" in body
    assert ".tc-product-link-combo.is-bad" in body
    assert ".tc-product-link-combo-check.is-ok" in body
    assert ".tc-product-link-combo-check.is-bad" in body
    assert "function tcReadinessCheckOk" in body
    assert "opts.mode !== 'product-link-combo' && check.reason" in body
    assert "tcProductLinkComboStateClass(linkCombinedChecks)" in body
    assert "'tc-product-link-combo-check' + stateClass" in body


def test_task_detail_readiness_moves_ad_language_after_product_link_combo(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    assert "const trailingChecks = [];" in body
    assert "String(check.key || '') === 'language_supported'" in body
    assert "const hint = check.hint" in body
    assert "hint + reason + links + evidence + langControls" in body
    assert (
        "wrap.innerHTML = jump + rows.join('') + tcRenderProductLinkCombinedCard(data, task, linkCombinedChecks) + trailingRows.join('');"
        in body
    )


def test_task_detail_readiness_uses_card_tones_for_regular_checks(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    assert ".tc-readiness-check-card.is-ok" in body
    assert ".tc-readiness-check-card.is-bad" in body
    assert ".tc-readiness-check-card.is-wait" in body
    assert "function tcReadinessCheckStateClass" in body
    assert "tcReadinessCheckStateClass(check)" in body
    assert "'tc-readiness-check-card' + stateClass" in body


def test_task_create_modal_supports_per_language_assignments_and_owner_hint(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    assert "const transOpts = translators.map" in body
    assert "const rawOpts = rawProcessors.map" in body
    assert "原负责人：" in body
    assert "仅提示，不会自动带入" in body
    assert "翻译工作范围" in body
    assert "can_process_raw_video 权限" not in body
    assert "tcCreateTranslator_" in body
    assert "language_assignments[country] = translatorId;" in body
    assert "translator_id, raw_processor_id" not in body
    assert "isOldProduct ? 'selected' : ''" not in body


def test_index_requires_login(authed_client_no_db):
    client = authed_client_no_db.application.test_client()
    rsp = client.get("/tasks/", follow_redirects=False)
    assert rsp.status_code in (302, 401)


def test_api_list_returns_empty_for_fresh_db(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/api/list?tab=all")
    # Without DB, the query may fail or return empty; we accept 200 OR 500
    # The point of this smoke test is the route is registered
    assert rsp.status_code in (200, 500)


def test_api_list_my_tasks_filters_by_assignee(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/api/list?tab=mine")
    assert rsp.status_code in (200, 500)


def test_api_list_delegates_to_tasks_service_for_mine(authed_user_client_no_db, monkeypatch):
    captured = {}

    def fail_query_all(*args, **kwargs):
        raise AssertionError("route should delegate task list queries")

    def fake_list_task_center_items(**kwargs):
        captured.update(kwargs)
        return {
            "items": [{"id": 11, "status": "pending", "high_level": "in_progress"}],
            "page": kwargs["page"],
            "page_size": kwargs["page_size"],
        }

    monkeypatch.setattr("appcore.db.query_all", fail_query_all)
    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.list_task_center_items",
        fake_list_task_center_items,
        raising=False,
    )

    rsp = authed_user_client_no_db.get(
        "/tasks/api/list?tab=mine&keyword=abc&status=in_progress&bucket=todo&page=3&page_size=150&task_id=44"
    )

    assert rsp.status_code == 200
    assert rsp.get_json() == {
        "items": [{"id": 11, "status": "pending", "high_level": "in_progress"}],
        "page": 3,
        "page_size": 100,
    }
    assert captured == {
        "tab": "mine",
        "user_id": 2,
        "can_process_raw_video": False,
        "keyword": "abc",
        "high_status": "in_progress",
        "bucket": "todo",
        "page": 3,
        "page_size": 100,
        "task_id": 44,
        "task_type": "",
        "assignee_id": None,
    }


def test_api_list_delegates_task_type_filter(authed_user_client_no_db, monkeypatch):
    captured = {}

    def fake_list_task_center_items(**kwargs):
        captured.update(kwargs)
        return {"items": [], "page": kwargs["page"], "page_size": kwargs["page_size"]}

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.list_task_center_items",
        fake_list_task_center_items,
        raising=False,
    )

    rsp = authed_user_client_no_db.get("/tasks/api/list?tab=mine&task_type=translate")

    assert rsp.status_code == 200
    assert captured["task_type"] == "translate"


def test_api_list_defaults_to_50_items_per_page(authed_user_client_no_db, monkeypatch):
    captured = {}

    def fake_list_task_center_items(**kwargs):
        captured.update(kwargs)
        return {
            "items": [],
            "page": kwargs["page"],
            "page_size": kwargs["page_size"],
            "total": 0,
            "total_pages": 1,
        }

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.list_task_center_items",
        fake_list_task_center_items,
        raising=False,
    )

    rsp = authed_user_client_no_db.get("/tasks/api/list?tab=mine")

    assert rsp.status_code == 200
    assert captured["page_size"] == 50
    assert rsp.get_json()["page_size"] == 50

def test_api_list_delegates_assignee_filter(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_list_task_center_items(**kwargs):
        captured.update(kwargs)
        return {"items": [], "page": kwargs["page"], "page_size": kwargs["page_size"]}

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.list_task_center_items",
        fake_list_task_center_items,
        raising=False,
    )

    rsp = authed_client_no_db.get("/tasks/api/list?tab=all&assignee_id=7")

    assert rsp.status_code == 200
    assert captured["assignee_id"] == 7


def test_api_list_rejects_invalid_assignee_filter(authed_client_no_db, monkeypatch):
    def fake_list_task_center_items(**kwargs):
        raise AssertionError("invalid assignee_id should be rejected before service call")

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.list_task_center_items",
        fake_list_task_center_items,
        raising=False,
    )

    rsp = authed_client_no_db.get("/tasks/api/list?tab=all&assignee_id=abc")

    assert rsp.status_code == 400
    assert rsp.get_json()["error"] == "invalid assignee_id"


def test_api_list_rejects_invalid_task_type(authed_user_client_no_db, monkeypatch):
    def fake_list_task_center_items(**kwargs):
        raise AssertionError("invalid task_type should be rejected before service call")

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.list_task_center_items",
        fake_list_task_center_items,
        raising=False,
    )

    rsp = authed_user_client_no_db.get("/tasks/api/list?tab=mine&task_type=unknown")

    assert rsp.status_code == 400
    assert rsp.get_json()["error"] == "invalid task_type"


def test_api_list_accepts_all_bucket_as_unfiltered_overview(authed_user_client_no_db, monkeypatch):
    captured = {}

    def fake_list_task_center_items(**kwargs):
        captured.update(kwargs)
        return {"items": [], "page": kwargs["page"], "page_size": kwargs["page_size"]}

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.list_task_center_items",
        fake_list_task_center_items,
        raising=False,
    )

    rsp = authed_user_client_no_db.get("/tasks/api/list?tab=mine&bucket=all")

    assert rsp.status_code == 200
    assert captured["bucket"] == ""


def test_task_detail_deep_link_fetches_exact_task_before_fallback(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    assert "task_id=' + encodeURIComponent(String(id || ''))" in body
    assert "'&task_id='" in body


def test_task_detail_drawer_checks_task_before_secondary_fetches(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")

    start = body.index("async function tcLoadDetail")
    end = body.index("function tcCloseDetail", start)
    fn = body[start:end]

    missing_task_check = fn.index("if (!task)")
    assert missing_task_check < fn.index("'/tasks/api/' + id + '/events'")
    assert missing_task_check < fn.index("'/tasks/api/' + id + '/review-assets'")


def test_api_list_rejects_unknown_tab_without_querying_db(authed_user_client_no_db, monkeypatch):
    captured = []

    def fail_query_all(*args, **kwargs):
        raise AssertionError("invalid tab should not query the database")

    def fake_list_task_center_items(**kwargs):
        captured.append(kwargs)
        return {"items": [], "page": 1, "page_size": 20}

    monkeypatch.setattr("appcore.db.query_all", fail_query_all)
    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.list_task_center_items",
        fake_list_task_center_items,
        raising=False,
    )

    rsp = authed_user_client_no_db.get("/tasks/api/list?tab=unexpected")

    assert rsp.status_code == 400
    assert "error" in rsp.get_json()
    assert captured == []


def test_api_dispatch_pool_admin_only(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/api/dispatch_pool")
    # Without DB the SQL query may 500; we accept 200 OR 500 for smoke
    assert rsp.status_code in (200, 500)


def test_api_dispatch_pool_delegates_to_tasks_service(authed_client_no_db, monkeypatch):
    captured = []
    expected_items = [
        {
            "product_id": 9,
            "product_name": "Product A",
            "owner_id": 3,
            "en_item_count": 2,
        }
    ]

    def fake_list_dispatch_pool_products():
        captured.append(True)
        return expected_items

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.list_dispatch_pool_products",
        fake_list_dispatch_pool_products,
    )

    rsp = authed_client_no_db.get("/tasks/api/dispatch_pool")

    assert rsp.status_code == 200
    assert rsp.get_json() == {"items": expected_items}
    assert captured == [True]


def test_api_dispatch_pool_forbidden_for_non_admin(authed_user_client_no_db):
    rsp = authed_user_client_no_db.get("/tasks/api/dispatch_pool")
    assert rsp.status_code == 403


def test_create_parent_task_route_registered(authed_client_no_db):
    # GET on a POST-only endpoint → 405 Method Not Allowed confirms the route exists
    rsp = authed_client_no_db.get("/tasks/api/parent")
    assert rsp.status_code == 405


def test_create_parent_task_missing_params(authed_client_no_db):
    # POST with empty body → 400 bad request (missing required keys)
    rsp = authed_client_no_db.post("/tasks/api/parent", json={})
    assert rsp.status_code == 400
    assert "error" in rsp.get_json()


def test_translation_work_users_route_returns_users(authed_client_no_db, monkeypatch):
    expected = [{"id": 10, "username": "gq", "display_name": "顾倩"}]
    monkeypatch.setattr("web.routes.tasks.list_translation_work_users", lambda: expected)

    resp = authed_client_no_db.get("/tasks/api/translation-work-users")

    assert resp.status_code == 200
    assert resp.get_json() == {"users": expected}


def test_raw_processors_route_uses_translation_work_user_scope(authed_client_no_db, monkeypatch):
    expected = [{"id": 10, "username": "gq", "display_name": "顾倩"}]
    monkeypatch.setattr("web.routes.tasks.list_translation_work_users", lambda: expected)
    monkeypatch.setattr(
        "web.routes.tasks.list_raw_processors",
        lambda: (_ for _ in ()).throw(AssertionError("raw processor scope should not be used")),
        raising=False,
    )

    resp = authed_client_no_db.get("/tasks/api/raw-processors")

    assert resp.status_code == 200
    assert resp.get_json() == {"users": expected}


def test_create_parent_validates_raw_processor_with_translation_work_scope(authed_client_no_db, monkeypatch):
    ensure_calls = []
    audit_calls = []

    monkeypatch.setattr(
        "web.routes.tasks.ensure_translation_work_user",
        lambda user_id: ensure_calls.append(user_id) or None,
    )
    monkeypatch.setattr(
        "web.routes.tasks.ensure_raw_processor_user",
        lambda user_id: (_ for _ in ()).throw(AssertionError("raw processor permission should not be used")),
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.create_parent_task",
        lambda **kwargs: 123,
    )
    monkeypatch.setattr(
        "web.routes.tasks._audit_task_action",
        lambda task_id, action, detail=None: audit_calls.append((task_id, action, detail)),
    )
    monkeypatch.setattr(
        "appcore.task_raw_video_processing.start_niuma_processing_for_parent_task",
        lambda **kwargs: {"status": "submitted"},
    )

    resp = authed_client_no_db.post(
        "/tasks/api/parent",
        json={
            "media_product_id": 1,
            "media_item_id": 2,
            "product_link": "https://mingkong.example/item/imported",
            "countries": ["DE"],
            "translator_id": 9,
            "raw_processor_id": 8,
        },
    )

    assert resp.status_code == 200
    assert ensure_calls == [9, 8]
    assert audit_calls[0][2]["raw_processor_id"] == 8


def test_create_parent_rejects_non_translation_work_user(authed_client_no_db, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "web.routes.tasks.ensure_translation_work_user",
        lambda user_id: (_ for _ in ()).throw(ValueError("该用户不在翻译工作范围")),
    )
    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.create_parent_task",
        lambda **kwargs: calls.append(kwargs) or 123,
    )

    resp = authed_client_no_db.post(
        "/tasks/api/parent",
        json={
            "media_product_id": 1,
            "media_item_id": 2,
            "product_link": "https://mingkong.example/item/imported",
            "countries": ["DE"],
            "translator_id": 9,
            "raw_processor_id": 8,
        },
    )

    assert resp.status_code == 400
    assert "翻译工作范围" in resp.get_json()["error"]
    assert calls == []


def test_create_parent_accepts_language_assignments(authed_client_no_db, monkeypatch):
    ensure_calls = []
    captured = {}
    audit_calls = []

    monkeypatch.setattr(
        "web.routes.tasks.ensure_translation_work_user",
        lambda user_id: ensure_calls.append(user_id) or None,
    )
    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.create_parent_task",
        lambda **kwargs: captured.update(kwargs) or 321,
    )
    monkeypatch.setattr(
        "appcore.task_raw_video_processing.start_niuma_processing_for_parent_task",
        lambda **kwargs: {"status": "submitted"},
    )
    monkeypatch.setattr(
        "web.routes.tasks._audit_task_action",
        lambda task_id, action, detail=None: audit_calls.append((task_id, action, detail)),
    )

    resp = authed_client_no_db.post(
        "/tasks/api/parent",
        json={
            "media_product_id": 1,
            "media_item_id": 2,
            "countries": ["DE", "FR"],
            "raw_processor_id": 8,
            "language_assignments": {"de": 9, "FR": 10},
        },
    )

    assert resp.status_code == 200
    assert captured == {
        "media_product_id": 1,
        "media_item_id": 2,
        "countries": ["DE", "FR"],
        "translator_id": None,
        "language_assignments": {"DE": 9, "FR": 10},
        "raw_processor_id": 8,
        "created_by": 1,
    }
    assert sorted(ensure_calls) == [8, 9, 10]
    assert audit_calls == [
        (
            321,
            "task_parent_created",
            {
                "media_product_id": 1,
                "media_item_id": 2,
                "countries": ["DE", "FR"],
                "translator_id": None,
                "raw_processor_id": 8,
                "language_assignments": {"DE": 9, "FR": 10},
            },
        )
    ]


def test_create_parent_rejects_missing_language_assignments(authed_client_no_db, monkeypatch):
    resp = authed_client_no_db.post(
        "/tasks/api/parent",
        json={
            "media_product_id": 1,
            "media_item_id": 2,
            "countries": ["DE", "FR"],
            "raw_processor_id": 8,
            "language_assignments": {"DE": 9},
        },
    )

    assert resp.status_code == 400
    assert "language_assignments" in resp.get_json()["error"]


def test_import_and_create_rejects_non_translation_work_user(authed_client_no_db, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "web.routes.tasks.ensure_translation_work_user",
        lambda user_id: (_ for _ in ()).throw(ValueError("该用户不在翻译工作范围")),
    )
    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.import_and_create_task",
        lambda **kwargs: calls.append(kwargs) or {"parent_task_id": 1},
    )

    resp = authed_client_no_db.post(
        "/tasks/api/import-and-create",
        json={
            "mk_video_metadata": {"filename": "x.mp4"},
            "countries": ["DE"],
            "translator_id": 9,
        },
    )

    assert resp.status_code == 400
    assert "翻译工作范围" in resp.get_json()["error"]
    assert calls == []


def test_import_and_create_accepts_language_assignments(authed_client_no_db, monkeypatch):
    ensure_calls = []
    evaluation_calls = []
    captured = {}

    monkeypatch.setattr(
        "web.routes.tasks.ensure_translation_work_user",
        lambda user_id: ensure_calls.append(user_id) or None,
    )
    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.import_and_create_task",
        lambda **kwargs: captured.update(kwargs) or {
            "parent_task_id": 1,
            "media_product_id": 2,
            "media_item_id": 3,
            "is_new_product": False,
        },
    )
    monkeypatch.setattr(
        "web.routes.tasks._trigger_material_evaluation",
        lambda **kwargs: evaluation_calls.append(kwargs) or True,
    )

    resp = authed_client_no_db.post(
        "/tasks/api/import-and-create",
        json={
            "mk_video_metadata": {
                "filename": "x.mp4",
                "product_link": "https://mingkong.example/item/import-and-create",
            },
            "countries": ["DE", "FR"],
            "language_assignments": {"DE": 9, "fr": 10},
        },
    )

    assert resp.status_code == 200
    assert captured == {
        "mk_video_metadata": {
            "filename": "x.mp4",
            "product_link": "https://mingkong.example/item/import-and-create",
        },
        "translator_id": None,
        "countries": ["DE", "FR"],
        "language_assignments": {"DE": 9, "FR": 10},
        "actor_user_id": 1,
    }
    assert ensure_calls == [9, 10]
    assert evaluation_calls == [
        {
            "product_id": 2,
            "media_item_id": 3,
            "force": False,
            "manual": False,
            "product_url_override": "https://mingkong.example/item/import-and-create",
        }
    ]


def test_create_parent_requires_raw_processor_id(authed_client_no_db, monkeypatch):
    called = []
    monkeypatch.setattr("web.routes.tasks.ensure_translation_work_user", lambda user_id: None)
    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.create_parent_task",
        lambda **kwargs: called.append(kwargs) or 123,
    )

    resp = authed_client_no_db.post(
        "/tasks/api/parent",
        json={
            "media_product_id": 1,
            "media_item_id": 2,
            "countries": ["DE"],
            "translator_id": 9,
        },
    )

    assert resp.status_code == 400
    assert "raw_processor_id" in resp.get_json()["error"]
    assert called == []


def test_create_parent_starts_raw_niuma_processing(authed_client_no_db, monkeypatch):
    audit_calls = []
    ensure_calls = []
    evaluation_calls = []

    monkeypatch.setattr("web.routes.tasks.ensure_translation_work_user", lambda user_id: ensure_calls.append(("translation_work", user_id)) or None)
    monkeypatch.setattr(
        "web.routes.tasks.ensure_raw_processor_user",
        lambda user_id: (_ for _ in ()).throw(AssertionError("raw processor permission should not be used")),
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.create_parent_task",
        lambda **kwargs: 123,
    )
    monkeypatch.setattr(
        "web.routes.tasks._audit_task_action",
        lambda task_id, action, detail=None: audit_calls.append((task_id, action, detail)),
    )
    monkeypatch.setattr(
        "appcore.task_raw_video_processing.start_niuma_processing_for_parent_task",
        lambda **kwargs: {"status": "submitted", "subtitle_task_id": "tcraw-123"},
    )
    monkeypatch.setattr(
        "web.routes.tasks._trigger_material_evaluation",
        lambda **kwargs: evaluation_calls.append(kwargs) or True,
    )

    resp = authed_client_no_db.post(
        "/tasks/api/parent",
        json={
            "media_product_id": 1,
            "media_item_id": 2,
            "product_link": "https://mingkong.example/item/imported",
            "countries": ["DE"],
            "translator_id": 9,
            "raw_processor_id": 8,
        },
    )

    assert resp.status_code == 200
    assert resp.get_json() == {
        "parent_task_id": 123,
        "raw_processing": {"status": "submitted", "subtitle_task_id": "tcraw-123"},
    }
    assert ensure_calls == [("translation_work", 9), ("translation_work", 8)]
    assert audit_calls == [
        (123, "task_parent_created", {
            "media_product_id": 1,
            "media_item_id": 2,
            "product_link": "https://mingkong.example/item/imported",
            "countries": ["DE"],
            "translator_id": 9,
            "raw_processor_id": 8,
        })
    ]
    assert evaluation_calls == [
        {
            "product_id": 1,
            "media_item_id": 2,
            "force": False,
            "manual": False,
            "product_url_override": "https://mingkong.example/item/imported",
        }
    ]


def test_create_parent_skips_raw_niuma_when_raw_source_ready(authed_client_no_db, monkeypatch):
    captured = {}

    monkeypatch.setattr("web.routes.tasks.ensure_translation_work_user", lambda user_id: None)
    monkeypatch.setattr(
        "appcore.task_raw_source_bridge.find_ready_raw_source_for_media_item",
        lambda item_id: {"id": 301, "product_id": 1, "display_name": "demo.mp4"},
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.create_parent_task",
        lambda **kwargs: captured.update(kwargs) or 123,
    )
    monkeypatch.setattr(
        "appcore.task_raw_video_processing.start_niuma_processing_for_parent_task",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("raw processing should be skipped")),
    )
    monkeypatch.setattr("web.routes.tasks._audit_task_action", lambda *args, **kwargs: None)

    resp = authed_client_no_db.post(
        "/tasks/api/parent",
        json={
            "media_product_id": 1,
            "media_item_id": 2,
            "countries": ["DE"],
            "translator_id": 9,
            "raw_processor_id": 8,
        },
    )

    assert resp.status_code == 200
    assert captured["reused_raw_source_id"] == 301
    assert resp.get_json() == {
        "parent_task_id": 123,
        "raw_processing": {
            "status": "skipped",
            "reason": "raw_source_ready",
            "raw_source_id": 301,
        },
    }


def test_import_and_create_returns_product_link_warnings(authed_client_no_db, monkeypatch):
    warnings = [{"type": "product_link_unavailable", "detail": "HTTP 404"}]

    monkeypatch.setattr("web.routes.tasks.ensure_translation_work_user", lambda user_id: None)
    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.import_and_create_task",
        lambda **kwargs: {
            "parent_task_id": 11,
            "media_product_id": 22,
            "media_item_id": 33,
            "is_new_product": True,
            "warnings": warnings,
        },
    )

    rsp = authed_client_no_db.post(
        "/tasks/api/import-and-create",
        json={
            "mk_video_metadata": {"filename": "demo.mp4"},
            "translator_id": 2,
            "countries": ["DE"],
        },
    )

    assert rsp.status_code == 200
    assert rsp.get_json()["warnings"] == warnings


def test_parent_action_routes_registered_admin(authed_client_no_db):
    """All admin parent endpoints reachable (will 4xx/5xx without real DB; smoke only)."""
    # claim — capability required (admin has all caps)
    rsp = authed_client_no_db.post("/tasks/api/parent/9999/claim")
    assert rsp.status_code in (200, 400, 404, 409, 500)

    # upload_done
    rsp = authed_client_no_db.post("/tasks/api/parent/9999/upload_done")
    assert rsp.status_code in (200, 400, 500)

    # approve
    rsp = authed_client_no_db.post("/tasks/api/parent/9999/approve")
    assert rsp.status_code in (200, 400, 500)

    # reject — needs reason
    rsp = authed_client_no_db.post("/tasks/api/parent/9999/reject", json={"reason": "this is a long enough reason"})
    assert rsp.status_code in (200, 400, 500)

    # cancel
    rsp = authed_client_no_db.post("/tasks/api/parent/9999/cancel", json={"reason": "cancel reason long enough"})
    assert rsp.status_code in (200, 400, 500)

    # bind_item — needs DB; just verify route registered
    rsp = authed_client_no_db.patch("/tasks/api/parent/9999/bind_item", json={"media_item_id": 1})
    assert rsp.status_code in (200, 400, 403, 404, 500)


def test_parent_force_niuma_rerun_delegates_to_processing_service(
    authed_client_no_db,
    monkeypatch,
):
    captured = {}
    audit_calls = []

    monkeypatch.setattr(
        "appcore.task_raw_video_processing.force_rerun_niuma_processing_for_parent_task",
        lambda **kwargs: captured.update(kwargs) or {
            "status": "submitted",
            "subtitle_task_id": "tcraw-new",
        },
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.tasks._audit_task_action",
        lambda task_id, action, detail=None: audit_calls.append((task_id, action, detail)),
    )

    rsp = authed_client_no_db.post("/tasks/api/parent/44/force_niuma_rerun")

    assert rsp.status_code == 200
    assert rsp.get_json() == {
        "ok": True,
        "raw_processing": {"status": "submitted", "subtitle_task_id": "tcraw-new"},
    }
    assert captured == {"task_id": 44, "actor_user_id": 1, "is_admin": True}
    assert audit_calls == [
        (
            44,
            "task_parent_force_niuma_rerun",
            {"subtitle_task_id": "tcraw-new", "status": "submitted"},
        )
    ]


def test_parent_approve_allows_non_admin_service_authorized_assignee(
    authed_user_client_no_db,
    monkeypatch,
):
    captured = {}
    audit_calls = []

    def fake_approve_raw(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.approve_raw",
        fake_approve_raw,
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.tasks._audit_task_action",
        lambda task_id, action, detail=None: audit_calls.append((task_id, action, detail)),
    )

    rsp = authed_user_client_no_db.post("/tasks/api/parent/44/approve")

    assert rsp.status_code == 200
    assert rsp.get_json() == {"ok": True}
    assert captured == {
        "task_id": 44,
        "actor_user_id": 2,
        "is_admin": False,
    }
    assert audit_calls == [(44, "task_parent_approved", None)]


def test_parent_approve_maps_service_permission_denied_for_non_assignee(
    authed_user_client_no_db,
    monkeypatch,
):
    def fake_approve_raw(**kwargs):
        raise PermissionError("only assignee or admin can approve")

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.approve_raw",
        fake_approve_raw,
        raising=False,
    )

    rsp = authed_user_client_no_db.post("/tasks/api/parent/44/approve")

    assert rsp.status_code == 403
    assert rsp.get_json() == {"error": "only assignee or admin can approve"}


def test_parent_manual_result_uploads_video_and_auto_approves(
    authed_user_client_no_db,
    monkeypatch,
):
    calls = {"upload": [], "approve": [], "audit": []}

    def fake_replace_processed_video(**kwargs):
        calls["upload"].append(kwargs)
        return 12

    def fake_approve_raw(**kwargs):
        calls["approve"].append(kwargs)

    monkeypatch.setattr(
        "web.routes.tasks.rvp_svc.replace_processed_video",
        fake_replace_processed_video,
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.approve_raw",
        fake_approve_raw,
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.tasks._audit_task_action",
        lambda task_id, action, detail=None: calls["audit"].append((task_id, action, detail)),
    )

    rsp = authed_user_client_no_db.post(
        "/tasks/api/parent/44/manual_result",
        data={"file": (io.BytesIO(b"fixed-video"), "fixed.mp4")},
        content_type="multipart/form-data",
    )

    assert rsp.status_code == 200
    assert rsp.get_json() == {"ok": True, "new_size": 12, "approved": True}
    assert calls["upload"][0]["task_id"] == 44
    assert calls["upload"][0]["actor_user_id"] == 2
    assert calls["upload"][0]["uploaded_file"].filename == "fixed.mp4"
    assert calls["upload"][0]["allowed_statuses"] == ("raw_review",)
    assert calls["upload"][0]["mark_uploaded_after"] is False
    assert calls["approve"] == [
        {"task_id": 44, "actor_user_id": 2, "is_admin": False}
    ]
    assert calls["audit"] == [
        (44, "task_parent_manual_result_uploaded", {"new_size": 12}),
        (44, "task_parent_approved", {"source": "manual_result"}),
    ]


def test_parent_admin_endpoints_forbid_non_admin(authed_user_client_no_db):
    """Non-admin user gets 403 on admin-only endpoints."""
    rsp = authed_user_client_no_db.post("/tasks/api/parent/9999/reject", json={"reason": "x"})
    assert rsp.status_code == 403
    rsp = authed_user_client_no_db.post("/tasks/api/parent/9999/cancel", json={"reason": "x"})
    assert rsp.status_code == 403


def test_parent_bind_item_delegates_to_tasks_service(authed_client_no_db, monkeypatch):
    captured = {}
    audit_calls = []

    def fail_query_one(*args, **kwargs):
        raise AssertionError("route should delegate bind_item database work")

    def fail_execute(*args, **kwargs):
        raise AssertionError("route should delegate bind_item database work")

    def fake_bind_parent_media_item(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("appcore.db.query_one", fail_query_one)
    monkeypatch.setattr("appcore.db.execute", fail_execute)
    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.bind_parent_media_item",
        fake_bind_parent_media_item,
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.tasks._audit_task_action",
        lambda task_id, action, detail=None: audit_calls.append((task_id, action, detail)),
    )

    rsp = authed_client_no_db.patch(
        "/tasks/api/parent/44/bind_item",
        json={"media_item_id": 5},
    )

    assert rsp.status_code == 200
    assert rsp.get_json() == {"ok": True}
    assert captured == {
        "task_id": 44,
        "media_item_id": 5,
        "actor_user_id": 1,
        "is_admin": True,
    }
    assert audit_calls == [
        (44, "task_parent_bound_item", {"media_item_id": 5})
    ]


def test_parent_bind_item_maps_service_errors(authed_client_no_db, monkeypatch):
    def fail_query_one(*args, **kwargs):
        raise AssertionError("route should delegate bind_item database work")

    monkeypatch.setattr("appcore.db.query_one", fail_query_one)

    def fake_bind_parent_media_item(**kwargs):
        from appcore import tasks

        raise tasks.StateError("task not found")

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.bind_parent_media_item",
        fake_bind_parent_media_item,
        raising=False,
    )
    rsp = authed_client_no_db.patch(
        "/tasks/api/parent/44/bind_item",
        json={"media_item_id": 5},
    )
    assert rsp.status_code == 404
    assert rsp.get_json() == {"error": "task not found"}

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.bind_parent_media_item",
        lambda **kwargs: (_ for _ in ()).throw(PermissionError("forbidden")),
        raising=False,
    )
    rsp = authed_client_no_db.patch(
        "/tasks/api/parent/44/bind_item",
        json={"media_item_id": 5},
    )
    assert rsp.status_code == 403
    assert rsp.get_json() == {"error": "forbidden"}

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.bind_parent_media_item",
        lambda **kwargs: (_ for _ in ()).throw(
            ValueError("media_item not found or not under this product")
        ),
        raising=False,
    )
    rsp = authed_client_no_db.patch(
        "/tasks/api/parent/44/bind_item",
        json={"media_item_id": 5},
    )
    assert rsp.status_code == 400
    assert rsp.get_json() == {"error": "media_item not found or not under this product"}


def test_parent_claim_requires_capability(authed_user_client_no_db):
    """Non-admin user without can_process_raw_video gets 403."""
    rsp = authed_user_client_no_db.post("/tasks/api/parent/9999/claim")
    assert rsp.status_code == 403


def test_child_action_routes_registered_admin(authed_client_no_db):
    """All child endpoints reachable as admin."""
    rsp = authed_client_no_db.post("/tasks/api/child/9999/submit")
    assert rsp.status_code in (200, 400, 422, 500)

    rsp = authed_client_no_db.post("/tasks/api/child/9999/approve")
    assert rsp.status_code in (200, 400, 500)

    rsp = authed_client_no_db.post("/tasks/api/child/9999/reject", json={"reason": "valid reject reason"})
    assert rsp.status_code in (200, 400, 500)

    rsp = authed_client_no_db.post("/tasks/api/child/9999/cancel", json={"reason": "valid cancel reason"})
    assert rsp.status_code in (200, 400, 500)


def test_child_admin_endpoints_forbid_non_admin(authed_user_client_no_db):
    """Non-admin user gets 403 on admin-only child endpoints."""
    rsp = authed_user_client_no_db.post("/tasks/api/child/9999/approve")
    assert rsp.status_code == 403
    rsp = authed_user_client_no_db.post("/tasks/api/child/9999/reject", json={"reason": "x"})
    assert rsp.status_code == 403
    rsp = authed_user_client_no_db.post("/tasks/api/child/9999/cancel", json={"reason": "x"})
    assert rsp.status_code == 403


def test_events_endpoint_registered(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/api/9999/events")
    assert rsp.status_code in (200, 500)


def test_api_events_delegates_to_tasks_service(authed_client_no_db, monkeypatch):
    captured = []
    expected_events = [
        {
            "id": 1,
            "task_id": 44,
            "event_type": "created",
            "actor_user_id": None,
            "actor_username": None,
            "payload_json": None,
            "created_at": None,
        }
    ]

    def fake_list_task_events(task_id):
        captured.append(task_id)
        return expected_events

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.list_task_events",
        fake_list_task_events,
    )

    rsp = authed_client_no_db.get("/tasks/api/44/events")

    assert rsp.status_code == 200
    assert rsp.get_json() == {"events": expected_events}


def test_api_review_assets_delegates_to_tasks_service(authed_client_no_db, monkeypatch):
    expected = {
        "current_review": {
            "event_type": "submitted",
            "title": "当前待审核：翻译产物",
            "asset_count": 1,
        },
        "steps": [
            {
                "event_type": "submitted",
                "review_target": True,
                "assets": [{"type": "video", "url": "/medias/object?object_key=x"}],
            }
        ],
    }

    def fake_get_task_review_assets(task_id):
        assert task_id == 44
        return expected

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.get_task_review_assets",
        fake_get_task_review_assets,
        raising=False,
    )

    rsp = authed_client_no_db.get("/tasks/api/44/review-assets")

    assert rsp.status_code == 200
    assert rsp.get_json() == expected


def test_api_review_assets_maps_missing_task_to_404(authed_client_no_db, monkeypatch):
    from web.routes.tasks import tasks_svc
    captured = []

    def fake_get_task_review_assets(task_id):
        captured.append(task_id)
        raise tasks_svc.StateError("task not found")

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.get_task_review_assets",
        fake_get_task_review_assets,
        raising=False,
    )

    rsp = authed_client_no_db.get("/tasks/api/44/review-assets")

    assert rsp.status_code == 404
    assert rsp.get_json()["error"] == "task not found"
    assert captured == [44]


def test_index_html_contains_tab_buttons(authed_client_no_db):
    """Verify the rendered tasks_list.html bootstraps the tab UI + JS."""
    rsp = authed_client_no_db.get("/tasks/")
    body = rsp.data.decode("utf-8")
    assert 'data-section-tab="overview"' in body
    assert "let TC_CURRENT_BUCKET = 'all';" in body
    assert body.index('data-bucket="all"') < body.index('data-bucket="todo"')
    assert '>任务总览</button>' in body
    assert 'data-bucket="todo"' in body
    assert 'data-bucket="review"' in body
    assert 'data-bucket="done"' in body
    assert "<th>创建时间</th>" in body
    assert "<th>更新时间</th>" not in body
    assert "tcRender" in body  # JS bootstrapped
    assert "tcCreateRawProcessor" in body
    assert "原视频处理人" in body
    assert ">认领</button>" not in body


def test_create_modal_supporting_endpoints_registered(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/api/translators")
    assert rsp.status_code in (200, 500)
    rsp = authed_client_no_db.get("/tasks/api/languages")
    assert rsp.status_code in (200, 500)
    rsp = authed_client_no_db.get("/tasks/api/product/9999/en_items")
    assert rsp.status_code in (200, 500)


def test_api_translators_delegates_to_users_dao(authed_client_no_db, monkeypatch):
    captured = []

    def fake_list_translators():
        captured.append(True)
        return [{"id": 7, "username": "translator"}]

    monkeypatch.setattr("web.routes.tasks.list_translators", fake_list_translators)

    rsp = authed_client_no_db.get("/tasks/api/translators")

    assert rsp.status_code == 200
    assert rsp.get_json() == {"translators": [{"id": 7, "username": "translator"}]}
    assert captured == [True]


def test_api_raw_processors_forbidden_for_non_admin(authed_user_client_no_db):
    rsp = authed_user_client_no_db.get("/tasks/api/raw-processors")
    assert rsp.status_code == 403


def test_api_languages_delegates_to_tasks_service(authed_client_no_db, monkeypatch):
    captured = []

    def fake_list_enabled_target_languages():
        captured.append(True)
        return [
            {"code": "DE", "name_zh": "德语", "label": "德语 (DE)"},
            {"code": "JA", "name_zh": "日语", "label": "日语 (JA)"},
        ]

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.list_enabled_target_languages",
        fake_list_enabled_target_languages,
    )

    rsp = authed_client_no_db.get("/tasks/api/languages")

    assert rsp.status_code == 200
    assert rsp.get_json() == {
        "languages": [
            {"code": "DE", "name_zh": "德语", "label": "德语 (DE)"},
            {"code": "JA", "name_zh": "日语", "label": "日语 (JA)"},
        ]
    }
    assert captured == [True]


def test_api_product_en_items_delegates_to_tasks_service(authed_client_no_db, monkeypatch):
    captured = []

    def fake_list_product_english_items(product_id):
        captured.append(product_id)
        return [{"id": 11, "filename": "source.mp4"}]

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.list_product_english_items",
        fake_list_product_english_items,
    )

    rsp = authed_client_no_db.get("/tasks/api/product/417/en_items")

    assert rsp.status_code == 200
    assert rsp.get_json() == {"items": [{"id": 11, "filename": "source.mp4"}]}
    assert captured == [417]


def test_child_readiness_endpoint_smoke(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/api/child/9999/readiness")
    assert rsp.status_code in (200, 404, 500)


def test_child_readiness_delegates_to_tasks_service(authed_client_no_db, monkeypatch):
    captured = []
    expected = {
        "ready": False,
        "missing": ["cover"],
        "readiness": {"cover": False},
        "country_code": "DE",
        "media_item_id": 5,
    }

    def fail_query_one(*args, **kwargs):
        raise AssertionError("route should delegate child readiness queries")

    def fake_get_child_readiness(task_id):
        captured.append(task_id)
        return expected

    monkeypatch.setattr("appcore.db.query_one", fail_query_one)
    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.get_child_readiness",
        fake_get_child_readiness,
        raising=False,
    )

    rsp = authed_client_no_db.get("/tasks/api/child/44/readiness")

    assert rsp.status_code == 200
    assert rsp.get_json() == expected
    assert captured == [44]


def test_child_readiness_maps_missing_child_to_404(authed_client_no_db, monkeypatch):
    def fail_query_one(*args, **kwargs):
        raise AssertionError("route should delegate child readiness queries")

    def fake_get_child_readiness(task_id):
        from appcore import tasks

        raise tasks.StateError("child task not found")

    monkeypatch.setattr("appcore.db.query_one", fail_query_one)
    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.get_child_readiness",
        fake_get_child_readiness,
        raising=False,
    )

    rsp = authed_client_no_db.get("/tasks/api/child/44/readiness")

    assert rsp.status_code == 404
    assert rsp.get_json() == {"error": "child task not found"}


def test_child_step_confirm_route_delegates_to_tasks_service(authed_client_no_db, monkeypatch):
    captured = {}
    audit_calls = []

    def fake_confirm_child_step(**kwargs):
        captured.update(kwargs)
        return {"step_key": "detail_images"}

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.confirm_child_step",
        fake_confirm_child_step,
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.tasks._audit_task_action",
        lambda task_id, action, detail=None: audit_calls.append((task_id, action, detail)),
    )

    rsp = authed_client_no_db.post("/tasks/api/child/44/steps/detail_images/confirm")

    assert rsp.status_code == 200
    assert rsp.get_json() == {"ok": True, "step_key": "detail_images"}
    assert captured == {
        "task_id": 44,
        "step_key": "detail_images",
        "actor_user_id": 1,
        "is_admin": True,
    }
    assert audit_calls == [
        (44, "task_child_step_confirmed", {"step_key": "detail_images"})
    ]


def test_child_step_confirm_route_maps_service_errors(authed_client_no_db, monkeypatch):
    from appcore import tasks

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.confirm_child_step",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("unknown step")),
        raising=False,
    )
    rsp = authed_client_no_db.post("/tasks/api/child/44/steps/bad_step/confirm")
    assert rsp.status_code == 400
    assert rsp.get_json() == {"error": "unknown step"}

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.confirm_child_step",
        lambda **kwargs: (_ for _ in ()).throw(PermissionError("forbidden")),
        raising=False,
    )
    rsp = authed_client_no_db.post("/tasks/api/child/44/steps/detail_images/confirm")
    assert rsp.status_code == 403
    assert rsp.get_json() == {"error": "forbidden"}

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.confirm_child_step",
        lambda **kwargs: (_ for _ in ()).throw(tasks.StateError("child task not found")),
        raising=False,
    )
    rsp = authed_client_no_db.post("/tasks/api/child/44/steps/detail_images/confirm")
    assert rsp.status_code == 404
    assert rsp.get_json() == {"error": "child task not found"}


def test_child_step_manual_output_route_delegates_text_payload(authed_client_no_db, monkeypatch):
    captured = {}
    audit_calls = []

    def fake_submit_child_step_manual_output(**kwargs):
        captured.update(kwargs)
        return {
            "step_key": "translated_copywriting",
            "kind": "text",
            "manual": True,
        }

    monkeypatch.setattr(
        "web.routes.tasks.tasks_svc.submit_child_step_manual_output",
        fake_submit_child_step_manual_output,
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.tasks._audit_task_action",
        lambda task_id, action, detail=None: audit_calls.append((task_id, action, detail)),
    )

    rsp = authed_client_no_db.post(
        "/tasks/api/child/44/steps/translated_copywriting/manual-output",
        json={"title": "Titel", "message": "Beschreibung", "description": "Detail"},
    )

    assert rsp.status_code == 200
    assert rsp.get_json() == {
        "ok": True,
        "step_key": "translated_copywriting",
        "kind": "text",
        "manual": True,
    }
    assert captured == {
        "task_id": 44,
        "step_key": "translated_copywriting",
        "actor_user_id": 1,
        "is_admin": True,
        "text": {"title": "Titel", "message": "Beschreibung", "description": "Detail"},
        "files": [],
    }
    assert audit_calls == [
        (44, "task_child_step_manual_output_submitted", {"step_key": "translated_copywriting", "kind": "text"})
    ]


def test_child_step_manual_output_route_rejects_status_only_step(authed_client_no_db):
    rsp = authed_client_no_db.post(
        "/tasks/api/child/44/steps/product_listed/manual-output",
        json={},
    )

    assert rsp.status_code == 400
    assert rsp.get_json() == {"error": "step does not accept manual output"}
