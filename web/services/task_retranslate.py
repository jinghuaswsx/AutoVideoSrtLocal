"""Task retranslation workflow."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass

from appcore import ai_billing
from appcore.api_keys import get_key
from appcore.runtime import _VALID_TRANSLATE_PREFS, _llm_request_payload, _llm_response_payload
from web import store
from web.services.task_llm import resolve_translate_billing_provider
from web.services.task_prompts import resolve_task_prompt_text


@dataclass(frozen=True)
class TaskRetranslateOutcome:
    payload: dict
    status_code: int = 200


def _default_translate_pref(user_id: int | None) -> str | None:
    if user_id is None:
        return None
    return get_key(user_id, "translate_pref")


def retranslate_task(
    task_id: str,
    task: dict,
    body: Mapping[str, object],
    *,
    user_id: int | None,
    resolve_prompt_text: Callable[..., str] = resolve_task_prompt_text,
    valid_translate_prefs: Collection[str] = _VALID_TRANSLATE_PREFS,
    get_user_translate_pref: Callable[[int | None], str | None] = _default_translate_pref,
    resolve_billing_provider: Callable[[str], str] = resolve_translate_billing_provider,
    build_source_full_text: Callable[..., str] | None = None,
    generate_translation: Callable[..., dict] | None = None,
    get_model_display_name: Callable[..., str] | None = None,
    log_ai_request: Callable[..., object] | None = None,
    update_task: Callable[..., object] = store.update,
    llm_request_payload: Callable[..., dict] = _llm_request_payload,
    llm_response_payload: Callable[..., dict] = _llm_response_payload,
) -> TaskRetranslateOutcome:
    step_status = (task.get("steps") or {}).get("translate")
    if step_status not in ("done", "error"):
        return TaskRetranslateOutcome({"error": "翻译步骤尚未完成，无法重新翻译"}, 400)

    prompt_id = body.get("prompt_id")
    prompt_text = resolve_prompt_text(
        str(body.get("prompt_text") or "").strip(),
        prompt_id,
        user_id=user_id,
    )
    model_provider = str(body.get("model_provider") or "").strip()

    if not prompt_text:
        return TaskRetranslateOutcome({"error": "需要提供 prompt_text 或有效的 prompt_id"}, 400)

    if model_provider not in valid_translate_prefs:
        model_provider = get_user_translate_pref(user_id) or "openrouter"

    if build_source_full_text is None:
        from pipeline.localization import build_source_full_text_zh

        build_source_full_text = build_source_full_text_zh
    if generate_translation is None:
        from pipeline.translate import generate_localized_translation

        generate_translation = generate_localized_translation
    if get_model_display_name is None:
        from pipeline.translate import get_model_display_name as _get_model_display_name

        get_model_display_name = _get_model_display_name
    if log_ai_request is None:
        log_ai_request = ai_billing.log_request

    script_segments = task.get("script_segments") or []
    source_full_text_zh = build_source_full_text(script_segments)
    billing_provider = resolve_billing_provider(model_provider)
    resolved_model = get_model_display_name(model_provider, user_id)

    try:
        result = generate_translation(
            source_full_text_zh,
            script_segments,
            variant="normal",
            custom_system_prompt=prompt_text,
            provider=model_provider,
            user_id=user_id,
            use_case="video_translate.localize",
            project_id=task_id,
        )
        usage = result.get("_usage") or {}
        log_ai_request(
            use_case_code="video_translate.localize",
            user_id=user_id,
            project_id=task_id,
            provider=billing_provider,
            model=resolved_model,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            units_type="tokens",
            response_cost_cny=usage.get("cost_cny"),
            success=True,
            extra={"source": "task.retranslate"},
            request_payload=llm_request_payload(
                result, model_provider, "video_translate.localize"
            ),
            response_payload=llm_response_payload(result),
        )
    except Exception as exc:
        log_ai_request(
            use_case_code="video_translate.localize",
            user_id=user_id,
            project_id=task_id,
            provider=billing_provider,
            model=resolved_model,
            units_type="tokens",
            success=False,
            extra={"source": "task.retranslate", "error": str(exc)[:500]},
            request_payload={
                "type": "chat",
                "use_case_code": "video_translate.localize",
                "provider": model_provider,
                "source_full_text": source_full_text_zh,
                "script_segments": script_segments,
                "custom_system_prompt": prompt_text,
            },
            response_payload={"error": str(exc)[:500]},
        )
        return TaskRetranslateOutcome({"error": f"翻译失败: {exc}"}, 500)

    translation_history = list(task.get("translation_history") or [])
    translation_history.append(
        {
            "prompt_text": prompt_text,
            "prompt_id": prompt_id,
            "model_provider": model_provider,
            "result": result,
        }
    )
    if len(translation_history) > 3:
        translation_history = translation_history[-3:]

    update_task(task_id, translation_history=translation_history)

    return TaskRetranslateOutcome(
        {
            "translation": result,
            "history_index": len(translation_history) - 1,
            "translation_history": translation_history,
        }
    )
