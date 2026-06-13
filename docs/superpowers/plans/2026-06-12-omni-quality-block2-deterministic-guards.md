# Block 2 — tts_script 防静默改写 + asr_clean 可靠性 实施计划

> **For agentic workers:** 按 Task 顺序 TDD 执行。Spec：[specs/2026-06-12-omni-quality-block2-deterministic-guards-design.md](../specs/2026-06-12-omni-quality-block2-deterministic-guards-design.md)；红线：[specs/2026-06-12-omni-quality-overview.md](../specs/2026-06-12-omni-quality-overview.md)。

**Goal:** 保证"送 TTS 的文本 == 翻译产物"恒成立（词级校验 + 重试 + 确定性回退）；asr_clean 不再因 max_tokens 截断静默放弃；asr_clean 兜底恢复异家族模型。

**Architecture:** `pipeline/localization.py`（校验 + 公共 helper）、`pipeline/translate.py`（重试/回退编排）、`pipeline/asr_clean.py`（max_tokens 估算）、`appcore/llm_use_cases.py`（绑定默认值）、`pipeline/localization_es.py` / `localization_it.py`（接入公共校验）。

**分支**: 从 `origin/audit/video-translate-quality` 切出 `fix/omni-quality-block2-guards`。

---

### Task 1: 词级一致性校验（localization.py）

**Files:**
- Modify: `pipeline/localization.py`
- Create: `tests/test_tts_script_wording_guard.py`

- [ ] **Step 1: 写失败测试**

```python
import pytest
from pipeline.localization import (
    TtsScriptWordingMismatchError,
    ensure_tts_script_wording,
    validate_tts_script,
)

SENTENCES = [
    {"index": 0, "text": "This melts slower than regular ice.", "source_segment_indices": [0]},
    {"index": 1, "text": "Everyone wants to take one home.", "source_segment_indices": [1]},
]


def _payload(block_texts):
    return {
        "full_text": " ".join(block_texts),
        "blocks": [
            {"index": i, "text": t, "sentence_indices": [i], "source_segment_indices": [i]}
            for i, t in enumerate(block_texts)
        ],
        "subtitle_chunks": [],
    }


def test_same_wording_passes():
    payload = _payload([s["text"] for s in SENTENCES])
    result = validate_tts_script(payload, sentences=SENTENCES)
    assert result["blocks"]


def test_changed_word_raises():
    payload = _payload(["This melts slower than normal ice.",  # regular→normal
                        "Everyone wants to take one home."])
    with pytest.raises(TtsScriptWordingMismatchError):
        validate_tts_script(payload, sentences=SENTENCES)


def test_dropped_sentence_raises():
    payload = _payload(["This melts slower than regular ice."])
    with pytest.raises(TtsScriptWordingMismatchError):
        validate_tts_script(payload, sentences=SENTENCES)


def test_punct_and_case_changes_are_ok():
    payload = _payload(["this melts slower, than regular ice",
                        "everyone wants to take one home!"])
    result = validate_tts_script(payload, sentences=SENTENCES)
    assert result["blocks"]


def test_ensure_helper_reports_context():
    with pytest.raises(TtsScriptWordingMismatchError) as ei:
        ensure_tts_script_wording(
            [{"text": "totally different words"}], SENTENCES,
        )
    assert "different" in str(ei.value)
```

跑 `pytest tests/test_tts_script_wording_guard.py -q` → FAIL（ImportError）。

- [ ] **Step 2: 实现**。`pipeline/localization.py` 新增（放在 `validate_tts_script` 之前）：

```python
class TtsScriptWordingMismatchError(ValueError):
    """tts_script blocks 的词序列与输入 sentences 不一致（LLM 静默改写）。"""


def ensure_tts_script_wording(blocks: list[dict], sentences: list[dict]) -> None:
    expected = _subtitle_word_signature(_concat_items(sentences, "text"))
    actual = _subtitle_word_signature(_concat_items(blocks, "text"))
    if expected == actual:
        return
    pos = next(
        (i for i, (a, b) in enumerate(zip(actual, expected)) if a != b),
        min(len(actual), len(expected)),
    )
    lo = max(0, pos - 5)
    raise TtsScriptWordingMismatchError(
        f"tts_script wording mismatch at word {pos}: "
        f"blocks[...{' '.join(actual[lo:pos + 10])}...] vs "
        f"sentences[...{' '.join(expected[lo:pos + 10])}...] "
        f"(blocks {len(actual)} words, sentences {len(expected)} words)"
    )
```

`validate_tts_script` 中，在 `blocks = _sanitize_text_items(...)` 与既有 blocks 校验完成之后、`subtitle_chunks` 重建之前加入：

```python
    if sentences:
        ensure_tts_script_wording(blocks, sentences)
```

- [ ] **Step 3: 跑测试** → PASS；**Step 4: commit** `git commit -am "feat(block2): word-signature guard in validate_tts_script"`

### Task 2: 重试 + 确定性回退（translate.py）

**Files:**
- Modify: `pipeline/translate.py`
- Create: `tests/test_tts_script_fallback.py`

- [ ] **Step 1: 写失败测试**（mock `_invoke_chat_for_use_case`）：

```python
from unittest.mock import patch
from pipeline.translate import _generate_tts_script_single

LOC = {
    "full_text": "Alpha beta gamma. Delta epsilon zeta.",
    "sentences": [
        {"index": 0, "text": "Alpha beta gamma.", "source_segment_indices": [0]},
        {"index": 1, "text": "Delta epsilon zeta.", "source_segment_indices": [1]},
    ],
}
GOOD = {
    "full_text": "Alpha beta gamma. Delta epsilon zeta.",
    "blocks": [
        {"index": 0, "text": "Alpha beta gamma.", "sentence_indices": [0], "source_segment_indices": [0]},
        {"index": 1, "text": "Delta epsilon zeta.", "sentence_indices": [1], "source_segment_indices": [1]},
    ],
    "subtitle_chunks": [],
}
BAD = {**GOOD, "blocks": [dict(GOOD["blocks"][0], text="Alpha CHANGED gamma."), GOOD["blocks"][1]],
       "full_text": "Alpha CHANGED gamma. Delta epsilon zeta."}


def test_retry_recovers_wording():
    with patch("pipeline.translate._invoke_chat_for_use_case",
               side_effect=[(BAD, None), (GOOD, None)]) as call:
        result = _generate_tts_script_single(LOC, use_case="video_translate.tts_script")
    assert call.call_count == 2
    retry_messages = call.call_args_list[1].args[1]
    assert "EXACT wording" in retry_messages[-1]["content"]
    assert not result.get("_wording_fallback")


def test_double_failure_falls_back_deterministic():
    with patch("pipeline.translate._invoke_chat_for_use_case",
               side_effect=[(BAD, None), (BAD, None)]):
        result = _generate_tts_script_single(LOC, use_case="video_translate.tts_script")
    assert result["_wording_fallback"] is True
    assert [b["text"] for b in result["blocks"]] == [s["text"] for s in LOC["sentences"]]
    assert result["subtitle_chunks"]  # 重建成功
```

- [ ] **Step 2: 实现**。改造 `_generate_tts_script_single`：把"调用 + validate"段提为内部函数 `_attempt(messages)`；首次 mismatch → 构造 `retry_messages = messages + [{"role": "user", "content": "Your previous attempt changed the wording. Reproduce the input sentences with EXACT wording — same words in the same order. Only regroup them into blocks and subtitle_chunks."}]` 再 `_attempt`；二次 mismatch → 回退：

```python
def _deterministic_tts_script_from_sentences(sentences: list[dict], validate_fn, max_words_kw) -> dict:
    blocks = [
        {
            "index": i,
            "text": s.get("text", ""),
            "sentence_indices": [i],
            "source_segment_indices": list(s.get("source_segment_indices") or [i]),
        }
        for i, s in enumerate(sentences)
    ]
    payload = {
        "full_text": " ".join(b["text"] for b in blocks if b["text"]),
        "blocks": blocks,
        "subtitle_chunks": [],
    }
    result = validate_fn(payload, sentences=sentences)
    result["_wording_fallback"] = True
    return result
```

注意：仅捕获 `TtsScriptWordingMismatchError`；其他 `ValueError`（schema 缺 blocks 等）保持现状向上抛。`validator` 为自定义（es/it）时同样适用——回退也走同一 validator。
- [ ] **Step 3: batched 合并兜底**。`_generate_tts_script_batched` 最终 `validate_fn(merged, sentences=sentences)` 包 try/except `TtsScriptWordingMismatchError` → 整体 `_deterministic_tts_script_from_sentences(sentences, ...)`。
- [ ] **Step 4: runner 标记**。`appcore/runtime/_pipeline_runner.py` 中 tts_script 生成后（`round_record["tts_script_source"]` 赋值处附近）加：`if tts_script.get("_wording_fallback"): round_record["tts_script_source"] = "wording_fallback"`（≤3 行，不碰其他逻辑）。
- [ ] **Step 5: 跑测试** → PASS；commit `git commit -am "feat(block2): tts_script wording retry + deterministic fallback"`

### Task 3: es/it 模块接入公共校验

**Files:**
- Modify: `pipeline/localization_es.py`、`pipeline/localization_it.py`（若各自导出 `validate_tts_script`）
- Test: 在 `tests/test_tts_script_wording_guard.py` 追加

- [ ] **Step 1**: `grep -n "def validate_tts_script" pipeline/localization_es.py pipeline/localization_it.py`。若存在自有实现：在其校验流程中（blocks 解析后）调用 `from pipeline.localization import ensure_tts_script_wording`，传入 blocks 与 sentences（如签名无 sentences 参数则补 `sentences=None` 可选参数，None 时跳过——与主实现一致）。若不存在自有实现（直接复用主函数），本 Task 仅在测试中标注确认结论。
- [ ] **Step 2**: 追加测试（es 路径同样触发 mismatch），跑通过，commit `git commit -am "feat(block2): wire wording guard into es/it validators"`。

### Task 4: asr_clean max_tokens 动态 + 兜底绑定

**Files:**
- Modify: `pipeline/asr_clean.py`、`appcore/llm_use_cases.py`
- Create: `tests/test_asr_clean_max_tokens.py`

- [ ] **Step 1: 写失败测试**

```python
from pipeline.asr_clean import _estimate_max_tokens


def test_small_input_floor():
    assert _estimate_max_tokens([{"text": "hi"}]) == 4000


def test_large_input_scales_and_caps():
    utts = [{"text": "word " * 200} for _ in range(120)]
    assert _estimate_max_tokens(utts) == 16000


def test_medium_input_between_bounds():
    utts = [{"text": "a" * 100} for _ in range(40)]
    v = _estimate_max_tokens(utts)
    assert 4000 < v < 16000
```

- [ ] **Step 2: 实现**。`pipeline/asr_clean.py`：

```python
def _estimate_max_tokens(utterances: list[dict]) -> int:
    text_chars = sum(len(str(u.get("text") or "")) for u in utterances)
    est = 600 + text_chars * 2 + len(utterances) * 30
    return min(16000, max(4000, est))
```

`_call` 签名加 `max_tokens: int = 4000`，内部两处 `max_tokens=4000`（debug payload 与 invoke_chat）改用入参；`purify_utterances` 调 `_call` 时传 `max_tokens=_estimate_max_tokens(utterances)`。
- [ ] **Step 3: 绑定修正**。`appcore/llm_use_cases.py` 的 `asr_clean.purify_fallback`：default_provider/model 改为 `"openrouter"`, `"anthropic/claude-sonnet-4.6"`，描述改为「Claude Sonnet 兜底：主路校验失败时换异家族模型重跑同样 prompt」。同步检查 `tests/test_llm_use_cases_registry.py` 是否有断言旧模型的用例，按新值更新。
- [ ] **Step 4: 跑测试** → PASS；commit `git commit -am "feat(block2): dynamic asr_clean max_tokens; restore cross-family fallback binding"`

### Task 5: 收尾验证

- [ ] `python3 scripts/pytest_related.py --base origin/master --run` 全 PASS。
- [ ] 自查 diff：`_pipeline_runner.py` 改动仅限 `tts_script_source` 标记 ≤3 行；无 multi / 时长窗口改动。
- [ ] push `fix/omni-quality-block2-guards`，停下等验收。汇报注明：现网 DB 若已有 `asr_clean.purify_fallback` 绑定行，需管理员在 `/settings?tab=bindings` 同步改为 `openrouter / anthropic/claude-sonnet-4.6`。
