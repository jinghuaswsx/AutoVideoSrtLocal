"""一次性评估脚本：对照 Claude Sonnet 4.6 / Gemini 3.1 Pro / Gemini 3 Flash 在
video_translate.localize 步骤的翻译质量。

直接调 pipeline.translate.generate_localized_translation，不写 ai_billing
（避免污染计费统计）。

服务器上执行：
    cd /opt/autovideosrt && python tools/translate_quality_eval.py \
        --asr /tmp/eval/A_ice_ball.json --label A_ice_ball \
        --asr /tmp/eval/B_utility_knife.json --label B_utility_knife \
        --output /tmp/eval/results.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

TARGET_LANGS = ["de", "es", "fr", "it", "ja", "nl", "pt", "sv"]
# (key, display_name, provider_override, model_override) —— 走 invoke_chat
# binding 路径，evaluator 跳过 binding 默认值直接指定 provider+model 跑 A/B。
MODELS = [
    ("claude_sonnet", "Claude Sonnet 4.6", "openrouter", "anthropic/claude-sonnet-4.6"),
    ("gemini_31_pro", "Gemini 3.1 Pro", "openrouter", "google/gemini-3.1-pro-preview"),
    ("gemini_3_flash", "Gemini 3 Flash", "openrouter", "google/gemini-3-flash-preview"),
]


def asr_to_inputs(asr_path: Path) -> dict:
    data = json.loads(asr_path.read_text(encoding="utf-8"))
    utterances = data.get("utterances", [])
    segments = []
    for i, u in enumerate(utterances):
        text = (u.get("text") or "").strip()
        if not text:
            continue
        segments.append({
            "index": i,
            "text": text,
            "start_time": float(u.get("start_time", 0.0)),
            "end_time": float(u.get("end_time", 0.0)),
        })
    full = " ".join(seg["text"] for seg in segments)
    return {"source_full_text": full, "script_segments": segments}


def build_system_prompt(lang: str) -> str:
    from appcore.llm_prompt_configs import resolve_prompt_config
    base = resolve_prompt_config("base_translation", lang)
    plugin = resolve_prompt_config("ecommerce_plugin", None)
    return f"{base['content']}\n\n---\n\n{plugin['content']}"


def run_one(source_full_text: str, script_segments: list[dict],
            lang: str, provider_override: str, model_override: str) -> dict:
    from pipeline.translate import generate_localized_translation
    system_prompt = build_system_prompt(lang)
    t0 = time.time()
    try:
        result = generate_localized_translation(
            source_full_text, script_segments, variant="normal",
            custom_system_prompt=system_prompt,
            user_id=None,  # tools 评测脚本不写 ai_billing
            use_case="video_translate.localize",
            provider_override=provider_override,
            model_override=model_override,
        )
        return {
            "ok": True,
            "elapsed_s": round(time.time() - t0, 2),
            "full_text": result.get("full_text"),
            "sentences": result.get("sentences"),
            "usage": result.get("_usage"),
            "error": None,
        }
    except Exception as e:
        return {
            "ok": False,
            "elapsed_s": round(time.time() - t0, 2),
            "full_text": None,
            "sentences": None,
            "usage": None,
            "error": f"{type(e).__name__}: {str(e)[:300]}",
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asr", action="append", required=True,
                        help="asr_result.json 路径（可多次传）")
    parser.add_argument("--label", action="append", required=True,
                        help="对应每段 asr 的 label（数量与 --asr 一致）")
    parser.add_argument("--output", required=True, help="输出 results.json 路径")
    parser.add_argument("--langs", default=",".join(TARGET_LANGS),
                        help="逗号分隔目标语种，默认 de,es,fr,it,ja,nl,pt,sv")
    parser.add_argument("--smoke", action="store_true",
                        help="冒烟模式：只跑第 1 个 asr × 第 1 个 lang × 第 1 个 model")
    args = parser.parse_args()

    if len(args.asr) != len(args.label):
        sys.exit("--asr 和 --label 数量必须一致")

    langs = [l.strip() for l in args.langs.split(",") if l.strip()]
    models = MODELS[:1] if args.smoke else MODELS
    if args.smoke:
        langs = langs[:1]

    output = {"sources": [], "results": []}
    pairs = list(zip(args.asr, args.label))
    if args.smoke:
        pairs = pairs[:1]

    for asr_path, label in pairs:
        inputs = asr_to_inputs(Path(asr_path))
        output["sources"].append({
            "label": label,
            "asr_path": asr_path,
            "source_full_text": inputs["source_full_text"],
            "n_segments": len(inputs["script_segments"]),
        })
        print(f"[{label}] {len(inputs['script_segments'])} segments, "
              f"{len(inputs['source_full_text'])} chars / "
              f"{len(inputs['source_full_text'].split())} words", flush=True)

        for lang in langs:
            for prov_key, prov_name, prov_override, mod_override in models:
                key = f"{label}/{lang}/{prov_key}"
                print(f"  → {key} ...", end=" ", flush=True)
                r = run_one(inputs["source_full_text"],
                            inputs["script_segments"],
                            lang, prov_override, mod_override)
                output["results"].append({
                    "source_label": label,
                    "target_lang": lang,
                    "provider_key": prov_key,
                    "provider_name": prov_name,
                    "provider_override": prov_override,
                    "model_override": mod_override,
                    **r,
                })
                if r["ok"]:
                    u = r["usage"] or {}
                    print(f"OK {r['elapsed_s']}s "
                          f"in={u.get('input_tokens')} out={u.get('output_tokens')}",
                          flush=True)
                else:
                    print(f"FAIL {r['error']}", flush=True)

                Path(args.output).write_text(
                    json.dumps(output, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

    print(f"\nDone. Output: {args.output}", flush=True)


if __name__ == "__main__":
    main()
