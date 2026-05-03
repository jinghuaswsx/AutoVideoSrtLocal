import types

import pytest

from pipeline.localization import (
    LOCALIZED_TRANSLATION_SYSTEM_PROMPT,
    TTS_SCRIPT_SYSTEM_PROMPT,
    _derive_tts_script_indices,
    _split_segments_into_batches,
    _subtitle_word_signature,
    build_localized_translation_messages,
    build_source_full_text_zh,
    validate_localized_translation,
    validate_tts_script,
)
from pipeline.translate import generate_localized_translation, generate_tts_script


def _fake_openai_client(create_fn):
    return types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=create_fn)
        )
    )


def test_build_source_full_text_zh_joins_confirmed_segments_with_newlines():
    segments = [
        {"index": 0, "text": "part one"},
        {"index": 1, "text": "part two"},
    ]

    assert build_source_full_text_zh(segments) == "part one\npart two"


def test_validate_localized_translation_requires_full_text_and_source_segment_indices():
    payload = {
        "full_text": "Hook line. Closing line.",
        "sentences": [
            {"index": 0, "text": "Hook line.", "source_segment_indices": [0]},
            {"index": 1, "text": "Closing line.", "source_segment_indices": [1]},
        ],
    }

    validated = validate_localized_translation(payload)

    assert validated["full_text"] == "Hook line. Closing line."
    assert validated["sentences"][1]["source_segment_indices"] == [1]


def test_validate_tts_script_rebuilds_invalid_subtitle_chunks_from_blocks():
    payload = {
        "full_text": "say it smooth. keep it fun.",
        "blocks": [
            {"index": 0, "text": "say it smooth.", "sentence_indices": [0], "source_segment_indices": [0]},
            {"index": 1, "text": "keep it fun.", "sentence_indices": [1], "source_segment_indices": [1]},
        ],
        "subtitle_chunks": [
            {"index": 0, "text": "Say it smooth.", "block_indices": [0], "sentence_indices": [0], "source_segment_indices": [0]},
            {"index": 1, "text": "Keep this different.", "block_indices": [1], "sentence_indices": [1], "source_segment_indices": [1]},
        ],
    }

    validated = validate_tts_script(payload)

    assert [chunk["text"] for chunk in validated["subtitle_chunks"]] == [
        "Say it smooth",
        "Keep it fun",
    ]


def test_validate_localized_translation_normalizes_em_dash_and_curly_quotes():
    payload = {
        "full_text": "A hands-on toy like this—your kids will love it.",
        "sentences": [
            {
                "index": 0,
                "text": "A hands-on toy like this—your kids will love it.",
                "source_segment_indices": [0],
            }
        ],
    }

    validated = validate_localized_translation(payload)

    assert "—" not in validated["full_text"]
    assert validated["full_text"] == "A hands-on toy like this, your kids will love it."


def test_validate_tts_script_normalizes_dash_split_boundaries():
    payload = {
        "full_text": "Frame pieces—follow the instructions.",
        "blocks": [
            {
                "index": 0,
                "text": "Frame pieces—follow the instructions.",
                "sentence_indices": [0],
                "source_segment_indices": [0],
            }
        ],
        "subtitle_chunks": [
            {
                "index": 0,
                "text": "Frame pieces—",
                "block_indices": [0],
                "sentence_indices": [0],
                "source_segment_indices": [0],
            },
            {
                "index": 1,
                "text": "follow the instructions.",
                "block_indices": [0],
                "sentence_indices": [0],
                "source_segment_indices": [0],
            },
        ],
    }

    validated = validate_tts_script(payload)

    assert validated["full_text"] == "Frame pieces, follow the instructions."
    assert validated["subtitle_chunks"][0]["text"] == "Frame pieces, follow the instructions"


def test_validate_tts_script_splits_subtitle_chunk_longer_than_ten_words():
    payload = {
        "full_text": "One two three four five six seven eight nine ten eleven.",
        "blocks": [
            {
                "index": 0,
                "text": "One two three four five six seven eight nine ten eleven.",
                "sentence_indices": [0],
                "source_segment_indices": [0],
            }
        ],
        "subtitle_chunks": [
            {
                "index": 0,
                "text": "One two three four five six seven eight nine ten eleven.",
                "block_indices": [0],
                "sentence_indices": [0],
                "source_segment_indices": [0],
            }
        ],
    }

    validated = validate_tts_script(payload)

    assert all(len(chunk["text"].split()) <= 10 for chunk in validated["subtitle_chunks"])
    assert _subtitle_word_signature(" ".join(chunk["text"] for chunk in validated["subtitle_chunks"])) == _subtitle_word_signature(validated["full_text"])


def test_validate_tts_script_rebuilds_subtitle_chunks_when_model_drops_words():
    payload = {
        "full_text": "Keep every word exactly the same for subtitles.",
        "blocks": [
            {
                "index": 0,
                "text": "Keep every word exactly the same for subtitles.",
                "sentence_indices": [0],
                "source_segment_indices": [0],
            }
        ],
        "subtitle_chunks": [
            {
                "index": 0,
                "text": "Keep every word the same for subtitles.",
                "block_indices": [0],
                "sentence_indices": [0],
                "source_segment_indices": [0],
            }
        ],
    }

    validated = validate_tts_script(payload)

    assert "exactly" in validated["subtitle_chunks"][0]["text"]
    assert " ".join(chunk["text"] for chunk in validated["subtitle_chunks"]).replace(".", "") == validated["full_text"].replace(".", "")


def test_validate_tts_script_avoids_one_and_two_word_fragments_when_five_to_ten_is_possible():
    payload = {
        "full_text": "circuit boards, motors, propellers, and a frame",
        "blocks": [
            {
                "index": 0,
                "text": "circuit boards, motors, propellers, and a frame",
                "sentence_indices": [0],
                "source_segment_indices": [0],
            }
        ],
        "subtitle_chunks": [],
    }

    validated = validate_tts_script(payload)

    assert len(validated["subtitle_chunks"]) == 1
    assert validated["subtitle_chunks"][0]["text"] == "Circuit boards, motors, propellers, and a frame"
    assert 5 <= len(validated["subtitle_chunks"][0]["text"].split()) <= 10


def test_validate_tts_script_balances_long_sentence_to_avoid_tiny_tail_chunks():
    payload = {
        "full_text": "Give them this kit with circuit boards, motors, propellers, and frame pieces.",
        "blocks": [
            {
                "index": 0,
                "text": "Give them this kit with circuit boards, motors, propellers, and frame pieces.",
                "sentence_indices": [0],
                "source_segment_indices": [0],
            }
        ],
        "subtitle_chunks": [],
    }

    validated = validate_tts_script(payload)
    counts = [len(chunk["text"].split()) for chunk in validated["subtitle_chunks"]]

    assert len(validated["subtitle_chunks"]) == 2
    assert all(5 <= count <= 10 for count in counts)


def test_validate_tts_script_rejects_block_missing_source_segment_indices_when_no_sentences():
    """没有传 sentences 时，validate 仍然按硬校验报 ValueError——保留作为最后防线，
    避免坏数据无声进入 build_tts_segments。"""
    payload = {
        "full_text": "say it smooth.",
        "blocks": [
            {"index": 0, "text": "say it smooth.", "sentence_indices": [0]},
        ],
        "subtitle_chunks": [],
    }

    with pytest.raises(ValueError, match="source_segment_indices"):
        validate_tts_script(payload)


def test_split_segments_short_video_returns_single_batch():
    """≤ batch_size 的视频走单批路径，分段策略不应启动。"""
    segments = [{"index": i, "text": f"seg {i}"} for i in range(10)]
    batches = _split_segments_into_batches(segments, target_size=12)
    assert len(batches) == 1
    assert batches[0] == segments


def test_split_segments_long_video_splits_into_balanced_batches():
    """长视频按 target_size 等分，避免最后一批过小。"""
    segments = [{"index": i, "text": f"seg {i}"} for i in range(35)]
    batches = _split_segments_into_batches(segments, target_size=12)
    # 35 / 12 = 3 批
    assert len(batches) == 3
    sizes = [len(b) for b in batches]
    assert sum(sizes) == 35
    # 等分后每批接近 12，不应有 < target_size/2 的孤批
    assert all(s >= 6 for s in sizes)
    assert max(sizes) - min(sizes) <= 1


def test_split_segments_avoids_orphan_tail_batch():
    """50/12 = 4.16，向上取 5 批 → 每批 10。
    防止 12+12+12+12+2 这种最后一批极小的情况。"""
    segments = [{"index": i, "text": f"seg {i}"} for i in range(50)]
    batches = _split_segments_into_batches(segments, target_size=12)
    sizes = [len(b) for b in batches]
    assert sum(sizes) == 50
    assert min(sizes) >= 6  # 没有孤批


def test_generate_localized_translation_short_video_skips_batching(monkeypatch):
    """≤ threshold 的短视频继续走单批路径，long-prompt 风险与现状一致。"""
    from pipeline import translate as translate_mod
    segments = [{"index": i, "text": f"seg-{i}"} for i in range(15)]

    call_count = {"n": 0}

    def fake_single(source_full_text_zh, batch_segments, **kwargs):
        call_count["n"] += 1
        assert len(batch_segments) == 15
        return {
            "full_text": "out",
            "sentences": [
                {"index": 0, "text": "out", "source_segment_indices": [0]},
            ],
        }

    monkeypatch.setattr(translate_mod, "_generate_localized_translation_single", fake_single)
    translate_mod.generate_localized_translation(
        "source", segments, use_case="video_translate.localize",
    )
    assert call_count["n"] == 1


def test_generate_localized_translation_long_video_splits_into_batches(monkeypatch):
    """长视频自动分批；LLM 返回该批的全局 source_segment_indices 时直接合并。"""
    from pipeline import translate as translate_mod
    segments = [{"index": i, "text": f"seg-{i}"} for i in range(30)]
    call_count = {"n": 0}

    def fake_single(source_full_text_zh, batch_segments, **kwargs):
        call_count["n"] += 1
        return {
            "full_text": " ".join(f"sent-{s['index']}" for s in batch_segments),
            "sentences": [
                {"index": i, "text": f"sent-{s['index']}",
                 "source_segment_indices": [s["index"]]}
                for i, s in enumerate(batch_segments)
            ],
        }

    monkeypatch.setattr(translate_mod, "_generate_localized_translation_single", fake_single)
    result = translate_mod.generate_localized_translation(
        "source", segments, use_case="video_translate.localize",
    )

    assert call_count["n"] == 3  # 30/12 → 3 批
    assert len(result["sentences"]) == 30
    assert result["sentences"][0]["index"] == 0
    assert result["sentences"][29]["index"] == 29
    assert result["sentences"][0]["source_segment_indices"] == [0]
    assert result["sentences"][15]["source_segment_indices"] == [15]
    assert result["sentences"][29]["source_segment_indices"] == [29]


def test_generate_localized_translation_batched_normalizes_relative_indices(monkeypatch):
    """LLM 经常用 0-based 相对索引；wrapper 自动平移到该批的全局段索引。"""
    from pipeline import translate as translate_mod
    segments = [{"index": i, "text": f"seg-{i}"} for i in range(24)]

    def fake_single(source_full_text_zh, batch_segments, **kwargs):
        return {
            "full_text": "...",
            "sentences": [
                {"index": i, "text": f"s{i}",
                 "source_segment_indices": [i]}  # 0-based 相对索引
                for i in range(len(batch_segments))
            ],
        }

    monkeypatch.setattr(translate_mod, "_generate_localized_translation_single", fake_single)
    result = translate_mod.generate_localized_translation(
        "source", segments, use_case="video_translate.localize",
    )

    assert len(result["sentences"]) == 24
    # 第 2 批的第 1 句应当映射到全局 segment index 12
    assert result["sentences"][12]["source_segment_indices"] == [12]
    assert result["sentences"][23]["source_segment_indices"] == [23]


def test_split_segments_preserves_order_and_indices():
    """分批后每段的原始 index/text 不应被改动。"""
    segments = [{"index": i, "text": f"text-{i}"} for i in range(25)]
    batches = _split_segments_into_batches(segments, target_size=12)
    flat = [seg for batch in batches for seg in batch]
    assert flat == segments


def test_derive_tts_script_indices_fills_missing_source_segment_indices_from_sentence_indices():
    """LLM 漏 source_segment_indices 但保留 sentence_indices 时，从 sentences 反查补齐。"""
    sentences = [
        {"index": 0, "text": "If your pants are too long.", "source_segment_indices": [0]},
        {"index": 1, "text": "Stop paying tailors.", "source_segment_indices": [1, 2]},
    ]
    payload = {
        "full_text": "If your pants are too long. Stop paying tailors.",
        "blocks": [
            {"index": 0, "text": "If your pants are too long.", "sentence_indices": [0]},
            {"index": 1, "text": "Stop paying tailors.", "sentence_indices": [1]},
        ],
        "subtitle_chunks": [],
    }

    derived = _derive_tts_script_indices(payload, sentences)

    assert derived["blocks"][0]["source_segment_indices"] == [0]
    assert derived["blocks"][1]["source_segment_indices"] == [1, 2]


def test_derive_tts_script_indices_aligns_blocks_to_sentences_when_llm_omits_all_indices():
    """LLM 同时漏 sentence_indices 和 source_segment_indices 时，
    用 token-by-token 对齐把 block 文本归到对应 sentence。"""
    sentences = [
        {"index": 0, "text": "If your pants are too long.", "source_segment_indices": [0]},
        {"index": 1, "text": "Stop paying tailors.", "source_segment_indices": [1]},
    ]
    payload = {
        "full_text": "If your pants are too long. Stop paying tailors.",
        "blocks": [
            {"index": 0, "text": "If your pants are too long."},
            {"index": 1, "text": "Stop paying tailors."},
        ],
        "subtitle_chunks": [],
    }

    derived = _derive_tts_script_indices(payload, sentences)

    assert derived["blocks"][0]["sentence_indices"] == [0]
    assert derived["blocks"][0]["source_segment_indices"] == [0]
    assert derived["blocks"][1]["sentence_indices"] == [1]
    assert derived["blocks"][1]["source_segment_indices"] == [1]


def test_derive_tts_script_indices_handles_block_spanning_multiple_sentences():
    """block 一次合并多个 sentence 的内容时，sentence_indices 和 source_segment_indices 取 union。"""
    sentences = [
        {"index": 0, "text": "Hook line.", "source_segment_indices": [0]},
        {"index": 1, "text": "Closing line.", "source_segment_indices": [1, 2]},
    ]
    payload = {
        "full_text": "Hook line. Closing line.",
        "blocks": [
            {"index": 0, "text": "Hook line. Closing line."},
        ],
        "subtitle_chunks": [],
    }

    derived = _derive_tts_script_indices(payload, sentences)

    assert derived["blocks"][0]["sentence_indices"] == [0, 1]
    assert derived["blocks"][0]["source_segment_indices"] == [0, 1, 2]


def test_derive_tts_script_indices_infers_subtitle_chunks_from_blocks():
    """subtitle_chunks 同样基于 blocks 反推所有派生索引，LLM 完全可以不输出 chunk-level indices。"""
    sentences = [
        {"index": 0, "text": "If your pants are too long are too long.",
         "source_segment_indices": [0]},
    ]
    payload = {
        "full_text": "If your pants are too long are too long.",
        "blocks": [
            {"index": 0, "text": "If your pants",
             "sentence_indices": [0], "source_segment_indices": [0]},
            {"index": 1, "text": "are too long are too long.",
             "sentence_indices": [0], "source_segment_indices": [0]},
        ],
        "subtitle_chunks": [
            {"index": 0, "text": "If your pants"},
            {"index": 1, "text": "are too long"},
            {"index": 2, "text": "are too long"},
        ],
    }

    derived = _derive_tts_script_indices(payload, sentences)

    assert derived["subtitle_chunks"][0]["block_indices"] == [0]
    assert derived["subtitle_chunks"][0]["sentence_indices"] == [0]
    assert derived["subtitle_chunks"][0]["source_segment_indices"] == [0]
    assert derived["subtitle_chunks"][1]["block_indices"] == [1]
    assert derived["subtitle_chunks"][2]["block_indices"] == [1]


def test_derive_tts_script_indices_overrides_llm_indices_when_text_aligns():
    """当 LLM 自己也返回了 indices 但与文本对齐结果不一致，应当以代码推断为准
    (LLM 偶尔会写错索引——比如 block 实际属于 sentence 1 却写成 0)。"""
    sentences = [
        {"index": 0, "text": "Sentence A.", "source_segment_indices": [0]},
        {"index": 1, "text": "Sentence B.", "source_segment_indices": [1]},
    ]
    payload = {
        "full_text": "Sentence A. Sentence B.",
        "blocks": [
            {"index": 0, "text": "Sentence A.",
             "sentence_indices": [1], "source_segment_indices": [1]},  # LLM 写错
            {"index": 1, "text": "Sentence B.",
             "sentence_indices": [0], "source_segment_indices": [0]},  # LLM 写错
        ],
        "subtitle_chunks": [],
    }

    derived = _derive_tts_script_indices(payload, sentences)

    assert derived["blocks"][0]["sentence_indices"] == [0]
    assert derived["blocks"][0]["source_segment_indices"] == [0]
    assert derived["blocks"][1]["sentence_indices"] == [1]
    assert derived["blocks"][1]["source_segment_indices"] == [1]


def test_validate_tts_script_with_sentences_recovers_from_missing_indices():
    """端到端：validate_tts_script 接收 sentences 时，先 derive 再校验。
    LLM 漏 source_segment_indices 不再炸；这是修长文案 LLM 漏字段问题的根因解。"""
    sentences = [
        {"index": 0, "text": "If your pants are too long.", "source_segment_indices": [0]},
        {"index": 1, "text": "Stop paying tailors.", "source_segment_indices": [1]},
    ]
    payload = {
        "full_text": "If your pants are too long. Stop paying tailors.",
        "blocks": [
            {"index": 0, "text": "If your pants are too long."},
            {"index": 1, "text": "Stop paying tailors."},
        ],
        "subtitle_chunks": [],
    }

    validated = validate_tts_script(payload, sentences=sentences)

    assert validated["blocks"][0]["source_segment_indices"] == [0]
    assert validated["blocks"][1]["source_segment_indices"] == [1]


def test_prompts_require_shorter_sentences_and_no_em_dash():
    assert "Do not use em dashes" in LOCALIZED_TRANSLATION_SYSTEM_PROMPT
    assert "5-10 words" in TTS_SCRIPT_SYSTEM_PROMPT


def test_hook_cta_prompt_mentions_first_three_seconds_and_single_cta():
    messages = build_localized_translation_messages(
        source_full_text_zh="test chinese",
        script_segments=[{"index": 0, "text": "test chinese"}],
        variant="hook_cta",
    )

    system_prompt = messages[0]["content"]
    assert "first 3 spoken seconds" in system_prompt
    assert "exactly one clear purchase CTA" in system_prompt


# D-4 之后 generate_localized_translation / generate_tts_script 走 invoke_chat
# （use_case 必传）。下面 3 个测试原本通过 patch resolve_provider_config + fake
# OpenAI client.chat.completions.create 测老 _call_openai_compat 路径，但该路径
# 已被删除。等价行为由 tests/test_translate_use_case_kwarg.py 中的
# test_generate_localized_translation_use_case_invokes_chat / test_generate_tts_script_use_case_invokes_chat
# 覆盖（直接 patch invoke_chat 验证调用入参）。
def test_generate_localized_translation_use_case_path_covered_elsewhere():
    """占位说明：parses_structured_output / passes_variant_specific_prompt /
    test_generate_tts_script_returns_validated_blocks 三个老 _call_openai_compat
    路径测试已废弃（D-4 删除老入口），等价覆盖在
    tests/test_translate_use_case_kwarg.py。"""
    assert True
