"""Rewrite 候选质量守门：忠实度 + 首句钩子 + 尾句收尾三项快评。

Spec: docs/superpowers/specs/2026-06-12-omni-quality-block3-convergence-guard-design.md

定位：在时长收敛内循环里，对"字数已落入窗口"的 rewrite 候选做一道轻量 LLM
质量评估。绝不放宽任何时长约束；fail-open——LLM 异常/非 JSON 一律放行
（passed=True, guard_error=True），守门故障不阻塞生产。
"""
from __future__ import annotations

import json
import logging

import config
from appcore import llm_client
from appcore.llm_debug_payloads import build_chat_request_payload, prompt_file_payload

log = logging.getLogger(__name__)
_USE_CASE = "video_translate.rewrite_guard"

_SYSTEM = """You are a translation quality gatekeeper for short-form commerce video scripts.
Compare CANDIDATE (a length-adjusted rewrite) against REFERENCE (the approved initial translation) and SOURCE (the original video transcript).
Return strict JSON only: {"fidelity": 0-100, "hook_ok": true/false, "ending_ok": true/false, "issues": ["..."]}
- fidelity: does CANDIDATE preserve the meaning of REFERENCE/SOURCE? No invented claims, no dropped key selling points. 100 = fully faithful.
- hook_ok: does CANDIDATE's FIRST sentence still work as a strong 3-second hook (clear outcome / benefit / curiosity / contrast)? It does not need to match REFERENCE word-for-word.
- ending_ok: does CANDIDATE's FINAL sentence preserve the closing / CTA intent of REFERENCE's ending? If REFERENCE ends with a wrap-up or CTA and CANDIDATE drops it, this is false.
- issues: up to 3 short Simplified-Chinese phrases describing concrete problems."""

_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "rewrite_guard",
        "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "fidelity": {"type": "integer", "minimum": 0, "maximum": 100},
                "hook_ok": {"type": "boolean"},
                "ending_ok": {"type": "boolean"},
                "issues": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["fidelity", "hook_ok", "ending_ok", "issues"],
        },
    },
}


def assess_rewrite_candidate(*, source_full_text: str, reference_translation_text: str,
                             candidate_text: str, target_lang: str,
                             task_id: str, user_id: int | None) -> dict:
    user_content = (
        f"TARGET LANGUAGE: {target_lang}\n\n"
        f"SOURCE (original transcript):\n{source_full_text}\n\n"
        f"REFERENCE (approved initial translation):\n{reference_translation_text}\n\n"
        f"CANDIDATE (length-adjusted rewrite to judge):\n{candidate_text}"
    )
    messages = [{"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_content}]
    debug_call = prompt_file_payload(
        phase="rewrite_guard", label="重写质量守门", use_case_code=_USE_CASE,
        provider=None, model=None, messages=messages,
        request_payload=build_chat_request_payload(
            use_case_code=_USE_CASE, provider=None, model=None,
            messages=messages, response_format=_RESPONSE_FORMAT,
            temperature=0.0, max_tokens=1000,
        ),
    )
    min_fidelity = int(getattr(config, "OMNI_REWRITE_GUARD_MIN_FIDELITY", 75))
    try:
        result = llm_client.invoke_chat(
            _USE_CASE, messages=messages, response_format=_RESPONSE_FORMAT,
            temperature=0.0, max_tokens=1000, user_id=user_id, project_id=task_id,
        )
        payload = result.get("json") or json.loads((result.get("text") or "").strip())
        fidelity = int(payload["fidelity"])
        hook_ok = bool(payload["hook_ok"])
        ending_ok = bool(payload["ending_ok"])
        issues = [str(x) for x in (payload.get("issues") or [])][:3]
    except Exception as exc:
        log.warning("[rewrite_guard] task=%s fail-open: %s", task_id, exc, exc_info=True)
        debug_call["error"] = str(exc)
        return {"fidelity": -1, "hook_ok": True, "ending_ok": True, "issues": [],
                "passed": True, "guard_error": True, "_llm_debug_call": debug_call}
    debug_call["response_preview"] = json.dumps(payload, ensure_ascii=False)[:2000]
    passed = fidelity >= min_fidelity and hook_ok and ending_ok
    return {"fidelity": fidelity, "hook_ok": hook_ok, "ending_ok": ending_ok,
            "issues": issues, "passed": passed, "guard_error": False,
            "_llm_debug_call": debug_call}
