"""一次性评估脚本：对照 Claude Sonnet 4.6 / Gemini 3 Flash 在
video_translate.tts_script 步骤的切分质量。

跟 translate_quality_eval.py 类似，多一层准备阶段：
  Phase 1: Claude localize 4 段 × 8 语种 = 32 次（拿 tts_script 输入）
  Phase 2: 32 段本地化结果 × 2 模型 tts_script = 64 次
  Phase 3: 自动统计 schema 合规、词级 diff、切分长度分布

服务器上执行：
    cd /opt/autovideosrt && PYTHONPATH=/opt/autovideosrt \\
        /opt/autovideosrt/venv/bin/python /tmp/eval/tts_script_run.py \\
        --asr /opt/autovideosrt/output/<task_a>/asr_result.json --label A \\
        --asr /opt/autovideosrt/output/<task_b>/asr_result.json --label B \\
        --asr /opt/autovideosrt/output/<task_c>/asr_result.json --label C \\
        --asr /opt/autovideosrt/output/<task_d>/asr_result.json --label D \\
        --output /tmp/eval/tts_results.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

TARGET_LANGS = ["de", "es", "fr", "it", "ja", "nl", "pt", "sv"]
LOCALIZE_PROVIDER = "claude_sonnet"   # Phase 1 固定用 Claude（公平起点）
TTS_MODELS = [
    ("claude_sonnet", "Claude Sonnet 4.6"),
    ("gemini_3_flash", "Gemini 3 Flash"),
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


def build_localize_prompt(lang: str) -> str:
    from appcore.llm_prompt_configs import resolve_prompt_config
    base = resolve_prompt_config("base_translation", lang)
    plugin = resolve_prompt_config("ecommerce_plugin", None)
    return f"{base['content']}\n\n---\n\n{plugin['content']}"


def run_localize(source_full_text: str, script_segments: list[dict],
                 lang: str) -> dict:
    """Phase 1: Claude 跑 localize 拿 tts_script 输入。"""
    from pipeline.translate import generate_localized_translation
    system_prompt = build_localize_prompt(lang)
    t0 = time.time()
    try:
        result = generate_localized_translation(
            source_full_text, script_segments, variant="normal",
            custom_system_prompt=system_prompt,
            provider=LOCALIZE_PROVIDER, user_id=None,
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
            "error": f"{type(e).__name__}: {str(e)[:300]}",
        }


def run_tts_script(localized_translation: dict, provider_key: str) -> dict:
    """Phase 2: 给 localized 结果做 tts_script 切分。"""
    from pipeline.translate import generate_tts_script
    # tts_script 需要纯净的 localized_translation（不能带 _usage / _messages）
    clean = {
        "full_text": localized_translation.get("full_text"),
        "sentences": localized_translation.get("sentences"),
    }
    t0 = time.time()
    try:
        result = generate_tts_script(
            clean, provider=provider_key, user_id=None,
        )
        return {
            "ok": True,
            "elapsed_s": round(time.time() - t0, 2),
            "full_text": result.get("full_text"),
            "blocks": result.get("blocks"),
            "subtitle_chunks": result.get("subtitle_chunks"),
            "usage": result.get("_usage"),
            "error": None,
        }
    except Exception as e:
        return {
            "ok": False,
            "elapsed_s": round(time.time() - t0, 2),
            "error": f"{type(e).__name__}: {str(e)[:300]}",
        }


# Phase 3 自动统计 ---------------------------------------------------------

_WORD_RE = re.compile(r"\S+")


def word_tokens(s: str) -> list[str]:
    """简单按空格切词，统一小写，去标点尾巴。"""
    if not s:
        return []
    out = []
    for tok in _WORD_RE.findall(s):
        cleaned = tok.strip(".,!?:;\"'()[]{}").lower()
        if cleaned:
            out.append(cleaned)
    return out


def levenshtein_words(a: list[str], b: list[str]) -> int:
    """词级编辑距离。"""
    n, m = len(a), len(b)
    if n == 0:
        return m
    if m == 0:
        return n
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        curr = [i] + [0] * m
        for j in range(1, m + 1):
            curr[j] = min(
                prev[j] + 1,        # delete
                curr[j - 1] + 1,    # insert
                prev[j - 1] + (0 if a[i - 1] == b[j - 1] else 1),
            )
        prev = curr
    return prev[m]


def assess_one(localize_full_text: str, tts_result: dict) -> dict:
    """一条 tts_script 输出的自动评估。"""
    if not tts_result.get("ok"):
        return {"schema_ok": False, "diff_ratio": None, "chunk_stats": None}

    blocks = tts_result.get("blocks") or []
    chunks = tts_result.get("subtitle_chunks") or []
    full_text = tts_result.get("full_text") or ""

    # schema: 必填字段 + 索引引用合法
    schema_ok = bool(blocks) and bool(chunks) and bool(full_text)
    if schema_ok:
        # blocks 索引应连续 0..n-1
        block_indices = {b.get("index") for b in blocks}
        for c in chunks:
            for bi in (c.get("block_indices") or []):
                if bi not in block_indices:
                    schema_ok = False
                    break
            if not schema_ok:
                break

    # 文本不变性：blocks.text 拼起来 vs localize 的 full_text，做词级 diff
    blocks_concat = " ".join((b.get("text") or "") for b in blocks)
    a = word_tokens(localize_full_text)
    b = word_tokens(blocks_concat)
    if a:
        dist = levenshtein_words(a, b)
        diff_ratio = round(dist / max(len(a), 1), 4)
    else:
        diff_ratio = None

    # 切分长度分布
    chunk_lens = [len(word_tokens(c.get("text") or "")) for c in chunks]
    if chunk_lens:
        stats = {
            "n": len(chunk_lens),
            "mean": round(sum(chunk_lens) / len(chunk_lens), 1),
            "min": min(chunk_lens),
            "max": max(chunk_lens),
            "in_5_10": round(sum(1 for x in chunk_lens if 5 <= x <= 10) / len(chunk_lens), 3),
            "tiny_1_3": round(sum(1 for x in chunk_lens if x <= 3) / len(chunk_lens), 3),
            "huge_12plus": round(sum(1 for x in chunk_lens if x >= 12) / len(chunk_lens), 3),
        }
    else:
        stats = None

    return {
        "schema_ok": schema_ok,
        "diff_ratio": diff_ratio,
        "chunk_stats": stats,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asr", action="append", required=True)
    parser.add_argument("--label", action="append", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--langs", default=",".join(TARGET_LANGS))
    parser.add_argument("--smoke", action="store_true",
                        help="冒烟模式：只第 1 段 × 第 1 语种 × 第 1 模型")
    args = parser.parse_args()

    if len(args.asr) != len(args.label):
        sys.exit("--asr 和 --label 数量必须一致")

    langs = [l.strip() for l in args.langs.split(",") if l.strip()]
    pairs = list(zip(args.asr, args.label))
    if args.smoke:
        pairs = pairs[:1]
        langs = langs[:1]

    output = {
        "phase1_localize": [],
        "phase2_tts_script": [],
        "phase3_assessment": [],
    }

    def save():
        Path(args.output).write_text(
            json.dumps(output, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Phase 1: Claude localize 准备输入
    # ------------------------------------------------------------------
    print("=" * 60, flush=True)
    print(f"PHASE 1: Claude localize {len(pairs)*len(langs)} times", flush=True)
    print("=" * 60, flush=True)
    localize_cache: dict[tuple[str, str], dict] = {}

    for asr_path, label in pairs:
        inputs = asr_to_inputs(Path(asr_path))
        print(f"[{label}] {len(inputs['script_segments'])} segs, "
              f"{len(inputs['source_full_text'].split())} words", flush=True)

        for lang in langs:
            print(f"  → {label}/{lang}/localize ...", end=" ", flush=True)
            r = run_localize(inputs["source_full_text"],
                             inputs["script_segments"], lang)
            output["phase1_localize"].append({
                "label": label, "target_lang": lang, **r,
            })
            if r["ok"]:
                u = r["usage"] or {}
                print(f"OK {r['elapsed_s']}s "
                      f"in={u.get('input_tokens')} out={u.get('output_tokens')}",
                      flush=True)
                localize_cache[(label, lang)] = {
                    "full_text": r["full_text"],
                    "sentences": r["sentences"],
                }
            else:
                print(f"FAIL {r['error']}", flush=True)
            save()

    # ------------------------------------------------------------------
    # Phase 2: tts_script × 2 modelos
    # ------------------------------------------------------------------
    print()
    print("=" * 60, flush=True)
    n_pairs = len(localize_cache) * len(TTS_MODELS) if not args.smoke \
        else min(1, len(localize_cache)) * 1
    print(f"PHASE 2: tts_script {n_pairs} times", flush=True)
    print("=" * 60, flush=True)

    models = TTS_MODELS[:1] if args.smoke else TTS_MODELS

    for (label, lang), localized in list(localize_cache.items())[:1 if args.smoke else None]:
        for prov_key, prov_name in models:
            key = f"{label}/{lang}/{prov_key}"
            print(f"  → {key} ...", end=" ", flush=True)
            r = run_tts_script(localized, prov_key)
            entry = {
                "label": label, "target_lang": lang,
                "provider_key": prov_key, "provider_name": prov_name,
                **r,
            }
            output["phase2_tts_script"].append(entry)

            # 自动统计
            assess = assess_one(localized["full_text"], r)
            output["phase3_assessment"].append({
                "label": label, "target_lang": lang,
                "provider_key": prov_key, **assess,
            })

            if r["ok"]:
                u = r["usage"] or {}
                print(f"OK {r['elapsed_s']}s "
                      f"in={u.get('input_tokens')} out={u.get('output_tokens')} "
                      f"schema={'OK' if assess['schema_ok'] else 'FAIL'} "
                      f"diff={assess['diff_ratio']}", flush=True)
            else:
                print(f"FAIL {r['error']}", flush=True)
            save()

    print()
    print(f"Done. Output: {args.output}", flush=True)


if __name__ == "__main__":
    main()
