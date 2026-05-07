"""临时烟囱测试：渲染 multi/omni 详情页确认 asr_normalize 卡片合并改造生效。

改造内容：
- 删除原本浮在 detail_extra 末尾的独立 ``<section class="card asr-normalize-card">``。
- multi 详情页：把 metadata + 原文/英文预览 details + token 行搬进 ``_task_workbench.html``
  的 ``step-asr_normalize`` 步骤卡片内（仅在 ``state.asr_normalize_artifact`` 为真时渲染）。
- omni 详情页：workbench 历史上展示的是 ``step-asr_clean``（位置 3），asr_normalize 仅以
  artifact 形式存在；故把同一份 metadata + 「重选语言」按钮 + 浮层搬进 ``step-asr_clean``。
- 用户可见的 step 名 / JS step name map / llm_debug STEP_LABELS 全部从「原文标准化」
  统一改写为「原文标准化和ASR结果纯净化」（仅 multi 那个 step 改名；omni 仍叫「原文纯净化」）。
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest


_ARTIFACT = {
    "route": "en_skip",
    "detection_source": "user_specified",
    "detected_source_language": "en",
    "confidence": 1.0,
    "is_mixed": False,
    "elapsed_ms": 120,
    "input": {
        "language_label": "英语",
        "full_text_preview": "Bring the joy back to your everyday wardrobe.",
    },
    "output": {"full_text_preview": ""},
    "tokens": {
        "detect": {"input_tokens": 0, "output_tokens": 0},
        "translate": {"input_tokens": 0, "output_tokens": 0},
    },
}


_OMNI_DYNAMIC_CFG = {
    "asr_post": "asr_normalize",
    "shot_decompose": True,
    "translate_algo": "shot_char_limit",
    "source_anchored": False,
    "tts_strategy": "five_round_rewrite",
    "subtitle": "asr_realign",
    "voice_separation": True,
    "loudness_match": True,
    "av_sync_audit": "report_only",
}


def _fake_project(task_id: str, project_type: str, extra_state: dict | None = None) -> dict:
    state = {
        "target_lang": "pt",
        "source_language": "en",
        "detected_source_language": "en",
        "asr_normalize_artifact": _ARTIFACT,
    }
    if extra_state:
        state.update(extra_state)
    return {
        "id": task_id,
        "user_id": 1,
        "type": project_type,
        "display_name": "asr_normalize 烟囱",
        "original_filename": "demo.mp4",
        "status": "done",
        "deleted_at": None,
        "state_json": json.dumps(state, ensure_ascii=False),
    }


@pytest.mark.parametrize(
    "url_prefix,project_type,route_module",
    [
        ("/multi-translate", "multi_translate", "web.routes.multi_translate"),
        ("/omni-translate", "omni_translate", "web.routes.omni_translate"),
    ],
    ids=["multi", "omni"],
)
def test_asr_normalize_render_merged(
    authed_client_no_db, url_prefix, project_type, route_module
):
    task_id = f"asr-norm-smoke-{project_type}"
    project = _fake_project(task_id, project_type)

    with patch(f"{route_module}.db_query_one", return_value=project), patch(
        f"{route_module}.recover_project_if_needed"
    ), patch("appcore.api_keys.get_key", return_value="openrouter"):
        resp = authed_client_no_db.get(f"{url_prefix}/{task_id}")

    assert resp.status_code == 200, resp.status_code
    html = resp.data.decode("utf-8")

    # 1. 旧的独立卡片彻底消失
    assert "asr-normalize-card" not in html, "standalone card markup still present"

    # 2. 新的 inline metadata block 出现
    assert "asr-normalize-detail" in html, "merged metadata block missing"

    # 3. 重命名后的 step 名称在页面里出现（至少在 step name map / step name 上）
    assert "原文标准化和ASR结果纯净化" in html, "renamed step label missing"

    # 4. 不同 detail 模板 host 的 step 不同：multi → step-asr_normalize；omni → step-asr_clean
    if project_type == "multi_translate":
        assert (
            '<span class="step-name">原文标准化和ASR结果纯净化</span>' in html
        ), "multi workbench step name was not renamed"
        host_id = "step-asr_normalize"
    else:
        # omni 的 asr_clean step 名称保持「原文纯净化」（不属于本次重命名范围）
        assert (
            '<span class="step-name">原文纯净化</span>' in html
        ), "omni's asr_clean step name should remain 原文纯净化"
        host_id = "step-asr_clean"

    start = html.find(f'id="{host_id}"')
    assert start != -1, f"{host_id} div missing"
    chunk = html[start : start + 8000]
    assert "asr-normalize-detail" in chunk, (
        f"metadata block is not nested inside {host_id}"
    )

    if project_type == "omni_translate":
        assert 'id="relangPanel"' in chunk, "omni relang panel missing inside step"
        assert 'id="btnReselectLang"' in chunk, "omni relang button missing inside step"
        assert "data-task-id" in chunk, "relang panel data-task-id wiring missing"


def test_omni_detail_renders_dynamic_pipeline_steps(authed_client_no_db):
    task_id = "omni-dynamic-steps"
    project = _fake_project(
        task_id,
        "omni_translate",
        {"plugin_config": _OMNI_DYNAMIC_CFG},
    )

    with patch("web.routes.omni_translate.db_query_one", return_value=project), patch(
        "web.routes.omni_translate.recover_project_if_needed"
    ), patch("appcore.api_keys.get_key", return_value="openrouter"):
        resp = authed_client_no_db.get(f"/omni-translate/{task_id}")

    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert 'id="step-asr_normalize"' in html
    assert 'id="step-asr_clean"' not in html
    assert 'id="step-shot_decompose"' in html
    assert 'id="step-av_sync_audit"' in html
    assert '"shot_decompose"' in html
    assert '"asr_normalize"' in html
    assert '"av_sync_audit"' in html


def test_workbench_preview_renderer_skips_non_visual_steps():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "templates" / "_task_workbench_scripts.html").read_text(
        encoding="utf-8-sig"
    )
    assert "voice_match" in script
    assert (
        "const msgEl = document.getElementById(`msg-${step}`);\n"
        "      if (!stepEl || !iconEl || !msgEl) return;"
    ) in script
    assert (
        "const previewEl = document.getElementById(`preview-${step}`);\n"
        "      if (!previewEl) return;"
    ) in script
