from pathlib import Path


SCRIPT = Path("AutoPush/static/app.js").read_text(encoding="utf-8")


def test_autopush_static_app_sanitizes_preview_media_sources():
    payload_block = SCRIPT[
        SCRIPT.index("function renderPayloadView"):
        SCRIPT.index("function validatePayload")
    ]
    list_block = SCRIPT[
        SCRIPT.index("function renderBody()"):
        SCRIPT.index("// 产品 / 素材", SCRIPT.index("function renderBody()"))
    ]
    create_block = SCRIPT[
        SCRIPT.index("state.videos.forEach"):
        SCRIPT.index("async function doFetch")
    ]

    assert "function safeMediaSrc(url)" in SCRIPT
    assert "const coverSrc = safeMediaSrc(v.image_url);" in payload_block
    assert "const videoSrc = safeMediaSrc(v.url);" in payload_block
    assert "src: coverSrc" in payload_block
    assert "src: videoSrc" in payload_block
    assert "poster: coverSrc" in payload_block
    assert "const coverUrl = safeMediaSrc(item.cover_url);" in list_block
    assert "src: coverUrl" in list_block
    assert "const coverSrc = safeMediaSrc(v.image_url);" in create_block
    assert "const videoSrc = safeMediaSrc(v.url);" in create_block
    assert "poster: coverSrc" in create_block
    assert "src: v.url" not in create_block


def test_autopush_material_push_starts_copywriting_workflow_after_mkid():
    assert "推送素材并自动推文案" in SCRIPT
    assert "function runMaterialAndCopywritingWorkflow" in SCRIPT
    assert "async function pushMaterialStep" in SCRIPT
    assert "async function pushCopywritingStep" in SCRIPT
    assert "const mkId = resolveWorkflowMkId" in SCRIPT
    assert SCRIPT.index("await pushMaterialStep()") < SCRIPT.index("await pushCopywritingStep()")


def test_autopush_workflow_logs_requests_and_responses():
    assert "function appendWorkflowLog" in SCRIPT
    assert "素材推送请求" in SCRIPT
    assert "素材推送响应" in SCRIPT
    assert "小语种文案推送请求" in SCRIPT
    assert "小语种文案推送响应" in SCRIPT
    assert "ap-workflow-log" in SCRIPT


def test_autopush_manual_localized_text_tabs_remain_available():
    assert "推送小语种文案" in SCRIPT
    assert "小语种文案JSON预览" in SCRIPT
    assert "retryCopywritingOnly" in SCRIPT
    assert "重试文案推送" in SCRIPT
