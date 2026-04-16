# 英语/德语/法语 TTS 音频时长迭代收敛 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 en/de/fr 三个视频翻译模块的 TTS 音频时长严格落在 `[video-3, video]` 区间；超出时按比例重写译文最多 3 轮；前端实时展示迭代过程与每轮中间文件。

**Architecture:**
- 纯函数 `_compute_next_target` 算每轮 target_duration / target_chars（反馈控制 + 自适应过矫正）
- 3 份语言 `build_localized_rewrite_messages` + `LOCALIZED_REWRITE_SYSTEM_PROMPT`（英/德/法各一）
- `pipeline.translate.generate_localized_rewrite` 新函数调 LLM 重写译文
- `PipelineRunner._step_tts` 抽回基类，de/fr 仅通过类变量 override（消除 3 份重复）
- 循环内每 phase emit 新事件 `EVT_TTS_DURATION_ROUND`，前端 tts 卡片内嵌"迭代日志"子组件
- 收敛后最终产物 copy 到 `tts_full.normal.mp3` 等标准名，下游（subtitle/compose/export）零改动

**Tech Stack:**
- Python 3.10+，pytest，OpenAI SDK（通过 openrouter/doubao），ElevenLabs SDK
- Flask + Flask-SocketIO
- Vanilla JS（无 React/Vue），Jinja2 模板
- MySQL（通过 `appcore.db`）

**参考 Spec:** [docs/superpowers/specs/2026-04-16-en-de-fr-tts-duration-control-design.md](docs/superpowers/specs/2026-04-16-en-de-fr-tts-duration-control-design.md)

---

## 文件结构

### 新增文件

| 文件 | 责任 |
|---|---|
| `tests/test_tts_duration_loop.py` | 测试 `_compute_next_target` 纯函数 + 迭代循环的收敛 / 未收敛 / cps fallback |
| `tests/test_localized_rewrite_prompts.py` | 测试 3 种语言 rewrite prompt/builder 的 target_chars/direction 注入 |

### 修改文件

| 文件 | 改动摘要 |
|---|---|
| `pipeline/localization.py` | 新增 `LOCALIZED_REWRITE_SYSTEM_PROMPT` + `build_localized_rewrite_messages`（英语） |
| `pipeline/localization_de.py` | 同上（德语） |
| `pipeline/localization_fr.py` | 同上（法语） |
| `pipeline/translate.py` | 新增 `generate_localized_rewrite()` 函数 |
| `appcore/events.py` | 新增 `EVT_TTS_DURATION_ROUND` 常量 |
| `appcore/task_state.py` | `create()` 加 `tts_duration_rounds`、`tts_duration_status` 初值 |
| `appcore/runtime.py` | `PipelineRunner` 加类变量 + `_compute_next_target` + `_run_tts_duration_loop` + 新 `_step_tts` |
| `appcore/runtime_de.py` | 删除 `_step_tts` override，加类变量 |
| `appcore/runtime_fr.py` | 同上 |
| `web/preview_artifacts.py` | `build_tts_artifact` 支持 `duration_rounds` 参数 |
| `web/routes/task.py` | 新增 `/round-file/<round>/<kind>` 路由（英语） |
| `web/routes/de_translate.py` | 同上 |
| `web/routes/fr_translate.py` | 同上 |
| `web/templates/_task_workbench.html` | step-tts 卡片加 `<div id="ttsDurationLog">` 容器 |
| `web/templates/_task_workbench_styles.html` | 迭代日志样式 |
| `web/templates/_task_workbench_scripts.html` | `socket.on("tts_duration_round", ...)` + `renderTtsDurationLog()` |

### 不改动（确认过）

- `pipeline/tts.py`（ElevenLabs 调用保持原样；仅 variant 参数变）
- `pipeline/timeline.py`（timeline_manifest 逻辑不变）
- `pipeline/subtitle.py` / `pipeline/subtitle_alignment.py` / `pipeline/compose.py`（收敛后 copy 到 normal 文件名，下游零感知）
- `appcore/runtime_v2.py`（`PipelineRunnerV2` 已 override `_build_steps` 与 `_run`，用独立 `_step_tts_verify`，不走新循环）
- `web/services/*_pipeline_runner.py`（现有 `_make_socketio_handler` 直接转发所有 event type，新事件自动可达）

---

## Task 1: `_compute_next_target` 纯函数

**Files:**
- Modify: `appcore/runtime.py`（新增模块级函数，暂不接入 `_step_tts`）
- Test: `tests/test_tts_duration_loop.py`（新文件）

- [ ] **Step 1: Write failing tests**

Create `tests/test_tts_duration_loop.py`:

```python
"""Tests for TTS duration convergence helpers."""
import pytest

from appcore.runtime import _compute_next_target


class TestComputeNextTarget:
    def test_round2_shrink_when_audio_over_video(self):
        # video=30, audio=35 (over by 5)
        td, tc, direction = _compute_next_target(
            round_index=2, last_audio_duration=35.0, cps=15.0, video_duration=30.0,
        )
        assert direction == "shrink"
        assert td == pytest.approx(28.0)  # video - 2.0
        assert tc == round(28.0 * 15.0)  # 420

    def test_round2_expand_when_audio_below_lower_bound(self):
        # video=30, lo=27, audio=25 (under lo by 2)
        td, tc, direction = _compute_next_target(
            round_index=2, last_audio_duration=25.0, cps=15.0, video_duration=30.0,
        )
        assert direction == "expand"
        assert td == pytest.approx(29.0)  # video - 1.0
        assert tc == round(29.0 * 15.0)  # 435

    def test_round3_adaptive_overcorrection_when_still_long(self):
        # video=30, center=28.5, audio=33 (still long by ~4.5 from center)
        # target = center - 0.5 * (33 - 28.5) = 28.5 - 2.25 = 26.25
        # clamp: max(lo+0.3, min(hi-0.3, 26.25)) = max(27.3, min(29.7, 26.25)) = 27.3
        td, tc, direction = _compute_next_target(
            round_index=3, last_audio_duration=33.0, cps=15.0, video_duration=30.0,
        )
        assert direction == "shrink"
        assert td == pytest.approx(27.3)  # clamped to duration_lo + 0.3

    def test_round3_adaptive_overcorrection_when_still_short(self):
        # video=30, center=28.5, audio=25 (still short)
        # target = 28.5 - 0.5 * (25 - 28.5) = 28.5 + 1.75 = 30.25
        # clamp to hi - 0.3 = 29.7
        td, tc, direction = _compute_next_target(
            round_index=3, last_audio_duration=25.0, cps=15.0, video_duration=30.0,
        )
        assert direction == "expand"
        assert td == pytest.approx(29.7)  # clamped to duration_hi - 0.3

    def test_target_chars_floor_at_10(self):
        # Tiny video + small cps → target_chars would be ~0
        td, tc, direction = _compute_next_target(
            round_index=2, last_audio_duration=5.0, cps=0.1, video_duration=1.0,
        )
        assert tc >= 10

    def test_short_video_below_3s_lo_is_zero(self):
        # video=2 → duration_lo = 0
        td, tc, direction = _compute_next_target(
            round_index=2, last_audio_duration=5.0, cps=15.0, video_duration=2.0,
        )
        # round 2 shrink → target = video - 2.0 = 0.0; target_chars clamped to >=10
        assert direction == "shrink"
        assert tc >= 10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tts_duration_loop.py -v`
Expected: FAIL with `ImportError: cannot import name '_compute_next_target'`

- [ ] **Step 3: Implement `_compute_next_target`**

Add to end of `appcore/runtime.py` (module level, before `_upload_artifacts_to_tos` or after other helpers):

```python
def _compute_next_target(
    round_index: int,
    last_audio_duration: float,
    cps: float,
    video_duration: float,
) -> tuple[float, int, str]:
    """Compute (target_duration, target_chars, direction) for round 2 or 3.

    Round 2 uses fixed offsets; round 3 uses adaptive over-correction
    (reverse half of the error, clamped to the interior of the target range
    with a 0.3s safety margin).

    Args:
        round_index: 2 or 3.
        last_audio_duration: audio length from the previous round (seconds).
        cps: characters-per-second rate for this voice×language.
        video_duration: original video duration (seconds).

    Returns:
        (target_duration_seconds, target_char_count, direction)
        direction ∈ {"shrink", "expand"}
    """
    duration_lo = max(0.0, video_duration - 3.0)
    duration_hi = video_duration
    center = video_duration - 1.5

    if round_index == 2:
        if last_audio_duration > duration_hi:
            target_duration = max(0.0, video_duration - 2.0)
            direction = "shrink"
        else:
            # below lo
            target_duration = max(0.0, video_duration - 1.0)
            direction = "expand"
    else:  # round_index == 3 (and any fallback)
        raw = center - 0.5 * (last_audio_duration - center)
        target_duration = max(duration_lo + 0.3, min(duration_hi - 0.3, raw))
        direction = "shrink" if last_audio_duration > center else "expand"

    target_chars = max(10, round(target_duration * cps))
    return target_duration, target_chars, direction
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tts_duration_loop.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add appcore/runtime.py tests/test_tts_duration_loop.py
git commit -m "feat(runtime): _compute_next_target 反馈控制 + round 3 过矫正

纯函数决定每轮 rewrite 目标时长与字符数。round 2 按方向
固定偏移 1-2s；round 3 按上轮偏差反向 50% 过矫正并 clamp
在区间内侧 0.3s 安全距离。"
```

---

## Task 2: 英语 Rewrite Prompt + Messages Builder

**Files:**
- Modify: `pipeline/localization.py`
- Test: `tests/test_localized_rewrite_prompts.py`（新文件）

- [ ] **Step 1: Write failing test**

Create `tests/test_localized_rewrite_prompts.py`:

```python
"""Tests for language-specific rewrite prompts / messages builders."""
import pytest


class TestEnglishRewritePrompt:
    def test_prompt_contains_rewrite_instructions(self):
        from pipeline.localization import LOCALIZED_REWRITE_SYSTEM_PROMPT
        assert "REWRITING" in LOCALIZED_REWRITE_SYSTEM_PROMPT.upper()
        assert "target character count" in LOCALIZED_REWRITE_SYSTEM_PROMPT.lower()
        assert "shrink" in LOCALIZED_REWRITE_SYSTEM_PROMPT.lower()
        assert "expand" in LOCALIZED_REWRITE_SYSTEM_PROMPT.lower()

    def test_prompt_inherits_original_style_rules(self):
        """Rewrite prompt must preserve hook / CTA / structure rules from original."""
        from pipeline.localization import (
            LOCALIZED_REWRITE_SYSTEM_PROMPT,
            LOCALIZED_TRANSLATION_SYSTEM_PROMPT,
        )
        # Key rules from original should be restated:
        assert "source_segment_indices" in LOCALIZED_REWRITE_SYSTEM_PROMPT
        assert "JSON" in LOCALIZED_REWRITE_SYSTEM_PROMPT

    def test_builder_injects_target_chars_and_direction(self):
        from pipeline.localization import build_localized_rewrite_messages
        msgs = build_localized_rewrite_messages(
            source_full_text="Hello world. This is source.",
            prev_localized_translation={
                "full_text": "Bonjour monde.",
                "sentences": [{"index": 0, "text": "Bonjour monde.", "source_segment_indices": [0]}],
            },
            target_chars=200,
            direction="shrink",
            source_language="zh",
        )
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        user_content = msgs[1]["content"]
        assert "200" in user_content
        assert "shrink" in user_content.lower()
        assert "Bonjour monde." in user_content

    def test_builder_respects_source_language_label(self):
        from pipeline.localization import build_localized_rewrite_messages
        msgs_zh = build_localized_rewrite_messages(
            source_full_text="中文原文",
            prev_localized_translation={"full_text": "x", "sentences": [{"index": 0, "text": "x", "source_segment_indices": [0]}]},
            target_chars=100, direction="shrink", source_language="zh",
        )
        msgs_en = build_localized_rewrite_messages(
            source_full_text="English source",
            prev_localized_translation={"full_text": "x", "sentences": [{"index": 0, "text": "x", "source_segment_indices": [0]}]},
            target_chars=100, direction="shrink", source_language="en",
        )
        assert "Chinese" in msgs_zh[1]["content"]
        assert "English" in msgs_en[1]["content"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_localized_rewrite_prompts.py::TestEnglishRewritePrompt -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement English rewrite prompt + builder**

Append to `pipeline/localization.py` (after `build_tts_script_messages`):

```python
LOCALIZED_REWRITE_SYSTEM_PROMPT = """You are a US short-video commerce copywriter REWRITING an existing English translation to match a target character count.
Return valid JSON only. The response must be a JSON object with this exact structure:
{"full_text": "all sentences joined by spaces", "sentences": [{"index": 0, "text": "...", "source_segment_indices": [0, 1]}, ...]}

REWRITE CONSTRAINTS (critical):
- Target character count: approximately {target_chars} characters (±5%, measured on full_text).
- Direction: {direction}
  * "shrink": remove modifiers, examples, and repetitions while preserving every factual claim and the core selling point.
  * "expand": add natural elaborations, relatable details, or examples. Preserve all facts; never invent new claims.
- Keep the same number of sentences as the previous translation when possible.
- Preserve every source_segment_indices mapping from the previous translation's sentences; do not reorder.

STYLE (identical to original translation prompt):
- Natural, native, sales-capable American English.
- Keep each sentence concise and punchy for subtitles. Prefer 6-10 words.
- Do not use em dashes or en dashes. Plain ASCII punctuation only, preferring commas, periods, and question marks.
- Preserve meaning — never drop key facts or invent new ones."""


def build_localized_rewrite_messages(
    source_full_text: str,
    prev_localized_translation: dict,
    target_chars: int,
    direction: str,
    source_language: str = "zh",
) -> list[dict]:
    lang_label = {"zh": "Chinese", "en": "English"}.get(source_language, source_language)
    prompt = LOCALIZED_REWRITE_SYSTEM_PROMPT.replace(
        "{target_chars}", str(target_chars)
    ).replace("{direction}", direction)
    return [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": (
                f"Source {lang_label} full text (for reference, preserve meaning):\n"
                f"{source_full_text}\n\n"
                f"Previous translation (rewrite this to {direction} to ~{target_chars} chars):\n"
                f"{json.dumps(prev_localized_translation, ensure_ascii=False, indent=2)}"
            ),
        },
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_localized_rewrite_prompts.py::TestEnglishRewritePrompt -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add pipeline/localization.py tests/test_localized_rewrite_prompts.py
git commit -m "feat(localization): 英语 rewrite prompt + messages builder

新增 LOCALIZED_REWRITE_SYSTEM_PROMPT（继承原翻译风格 + rewrite 约束）
与 build_localized_rewrite_messages。支持 target_chars / direction /
source_language 注入，preserve 原 source_segment_indices 映射。"
```

---

## Task 3: 德语 Rewrite Prompt + Messages Builder

**Files:**
- Modify: `pipeline/localization_de.py`
- Test: `tests/test_localized_rewrite_prompts.py`（append）

- [ ] **Step 1: Write failing test**

Append to `tests/test_localized_rewrite_prompts.py`:

```python
class TestGermanRewritePrompt:
    def test_prompt_inherits_german_localization_rules(self):
        from pipeline.localization_de import LOCALIZED_REWRITE_SYSTEM_PROMPT
        # German-specific rules must persist
        assert "German" in LOCALIZED_REWRITE_SYSTEM_PROMPT or "Deutsch" in LOCALIZED_REWRITE_SYSTEM_PROMPT
        assert "DACH" in LOCALIZED_REWRITE_SYSTEM_PROMPT or "Germans" in LOCALIZED_REWRITE_SYSTEM_PROMPT
        # rewrite constraints
        assert "target" in LOCALIZED_REWRITE_SYSTEM_PROMPT.lower()
        assert "shrink" in LOCALIZED_REWRITE_SYSTEM_PROMPT.lower()
        assert "expand" in LOCALIZED_REWRITE_SYSTEM_PROMPT.lower()

    def test_builder_for_german(self):
        from pipeline.localization_de import build_localized_rewrite_messages
        msgs = build_localized_rewrite_messages(
            source_full_text="Source text",
            prev_localized_translation={
                "full_text": "Hallo Welt.",
                "sentences": [{"index": 0, "text": "Hallo Welt.", "source_segment_indices": [0]}],
            },
            target_chars=300, direction="expand", source_language="en",
        )
        assert "300" in msgs[1]["content"]
        assert "expand" in msgs[1]["content"].lower()
        assert "Hallo Welt" in msgs[1]["content"]
        assert "English" in msgs[1]["content"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_localized_rewrite_prompts.py::TestGermanRewritePrompt -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement German rewrite prompt + builder**

Append to `pipeline/localization_de.py` (after `build_tts_script_messages`):

```python
LOCALIZED_REWRITE_SYSTEM_PROMPT = """You are a native German content creator REWRITING an existing German translation to match a target character count.
Return valid JSON only. The response must be a JSON object with this exact structure:
{"full_text": "all sentences joined by spaces", "sentences": [{"index": 0, "text": "...", "source_segment_indices": [0, 1]}, ...]}

REWRITE CONSTRAINTS (critical):
- Target character count: approximately {target_chars} characters (±5%, measured on full_text).
- Direction: {direction}
  * "shrink": remove modifiers and repetitions while preserving every factual claim and the core benefit (Kernvorteil).
  * "expand": add natural elaborations (examples, relatable details). Preserve all facts; never invent new claims.
- Keep the same number of sentences as the previous translation when possible.
- Preserve every source_segment_indices mapping from the previous translation's sentences; do not reorder.

STYLE (identical to original German localization):
- Write authentically and sachlich (no exaggerated claims, no artificial urgency).
- Use the product terms Germans actually use (Caps, Organizer, Display, etc. — not literal translations).
- Conversational German at B1 level. Prefer 6-12 words per sentence.
- Capitalize all nouns. Use German number conventions (2,5 not 2.5).
- Do not use em dashes or en dashes. Plain ASCII punctuation only.
- Do NOT add any CTA at the end — the video will have a separate CTA clip appended later."""


def build_localized_rewrite_messages(
    source_full_text: str,
    prev_localized_translation: dict,
    target_chars: int,
    direction: str,
    source_language: str = "zh",
) -> list[dict]:
    lang_label = {"zh": "Chinese", "en": "English"}.get(source_language, source_language)
    prompt = LOCALIZED_REWRITE_SYSTEM_PROMPT.replace(
        "{target_chars}", str(target_chars)
    ).replace("{direction}", direction)
    return [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": (
                f"Source {lang_label} full text (for reference, preserve meaning):\n"
                f"{source_full_text}\n\n"
                f"Previous German translation (rewrite this to {direction} to ~{target_chars} chars):\n"
                f"{json.dumps(prev_localized_translation, ensure_ascii=False, indent=2)}"
            ),
        },
    ]
```

Also update `__all__` at top of `pipeline/localization_de.py` to include `"LOCALIZED_REWRITE_SYSTEM_PROMPT"` and `"build_localized_rewrite_messages"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_localized_rewrite_prompts.py::TestGermanRewritePrompt -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add pipeline/localization_de.py tests/test_localized_rewrite_prompts.py
git commit -m "feat(localization_de): 德语 rewrite prompt + messages builder

继承 DACH 本土化规则（sachlich / 英语外来词 / 名词大写 / 德语数字格式）
加上 rewrite 约束（target_chars / direction / 保留 source_segment_indices）。"
```

---

## Task 4: 法语 Rewrite Prompt + Messages Builder

**Files:**
- Modify: `pipeline/localization_fr.py`
- Test: `tests/test_localized_rewrite_prompts.py`（append）

- [ ] **Step 1: Write failing test**

Append to `tests/test_localized_rewrite_prompts.py`:

```python
class TestFrenchRewritePrompt:
    def test_prompt_inherits_french_elision_rules(self):
        from pipeline.localization_fr import LOCALIZED_REWRITE_SYSTEM_PROMPT
        # French-specific rules must persist
        text = LOCALIZED_REWRITE_SYSTEM_PROMPT.lower()
        assert "french" in text or "français" in text
        assert "élision" in LOCALIZED_REWRITE_SYSTEM_PROMPT or "elision" in text
        # rewrite constraints
        assert "target" in text
        assert "shrink" in text
        assert "expand" in text

    def test_builder_for_french(self):
        from pipeline.localization_fr import build_localized_rewrite_messages
        msgs = build_localized_rewrite_messages(
            source_full_text="Source",
            prev_localized_translation={
                "full_text": "C'est super.",
                "sentences": [{"index": 0, "text": "C'est super.", "source_segment_indices": [0]}],
            },
            target_chars=250, direction="shrink", source_language="zh",
        )
        assert "250" in msgs[1]["content"]
        assert "shrink" in msgs[1]["content"].lower()
        assert "Chinese" in msgs[1]["content"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_localized_rewrite_prompts.py::TestFrenchRewritePrompt -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement French rewrite prompt + builder**

Append to `pipeline/localization_fr.py` (after `build_tts_script_messages`):

```python
LOCALIZED_REWRITE_SYSTEM_PROMPT = """You are a French content creator based in France REWRITING an existing French translation to match a target character count.
Return valid JSON only. The response must be a JSON object with this exact structure:
{"full_text": "all sentences joined by spaces", "sentences": [{"index": 0, "text": "...", "source_segment_indices": [0, 1]}, ...]}

REWRITE CONSTRAINTS (critical):
- Target character count: approximately {target_chars} characters (±5%, measured on full_text).
- Direction: {direction}
  * "shrink": remove modifiers, examples, and repetitions while preserving every factual claim and the core benefit.
  * "expand": add natural elaborations (examples, relatable details, light context). Preserve all facts; never invent new claims.
- Keep the same number of sentences as the previous translation when possible.
- Preserve every source_segment_indices mapping from the previous translation's sentences; do not reorder.

STYLE (identical to original French localization):
- Tone: décontracté et informatif (relaxed, informative). Like a friend casually recommending something.
- No exaggerated claims, no hype. French audiences distrust aggressive selling.
- Conversational French at B1-B2 level. Default to "vous" unless explicitly told otherwise.
- Prefer 6-10 words per sentence. Avoid long subordinate clause chains.
- Always apply mandatory French élision: l'organizer, d'abord, j'adore, qu'il, c'est, n'est. NEVER write "le organizer" or "de abord".
- Proper contractions: au / aux / du / des.
- Preserve accents on uppercase letters. French punctuation: non-breaking space before ? ! : ; in the output.
- Do not use em dashes or en dashes. Plain ASCII punctuation only (except the required French non-breaking spaces).
- Do NOT add any CTA at the end — the video will have a separate CTA clip appended later."""


def build_localized_rewrite_messages(
    source_full_text: str,
    prev_localized_translation: dict,
    target_chars: int,
    direction: str,
    source_language: str = "zh",
) -> list[dict]:
    lang_label = {"zh": "Chinese", "en": "English"}.get(source_language, source_language)
    prompt = LOCALIZED_REWRITE_SYSTEM_PROMPT.replace(
        "{target_chars}", str(target_chars)
    ).replace("{direction}", direction)
    return [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": (
                f"Source {lang_label} full text (for reference, preserve meaning):\n"
                f"{source_full_text}\n\n"
                f"Previous French translation (rewrite this to {direction} to ~{target_chars} chars):\n"
                f"{json.dumps(prev_localized_translation, ensure_ascii=False, indent=2)}"
            ),
        },
    ]
```

Also update `__all__` at top of `pipeline/localization_fr.py` to include `"LOCALIZED_REWRITE_SYSTEM_PROMPT"` and `"build_localized_rewrite_messages"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_localized_rewrite_prompts.py::TestFrenchRewritePrompt -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add pipeline/localization_fr.py tests/test_localized_rewrite_prompts.py
git commit -m "feat(localization_fr): 法语 rewrite prompt + messages builder

继承法语本土化规则（décontracté / élision 强制 / 法语标点间距 / vous 默认）
加上 rewrite 约束。"
```

---

## Task 5: `pipeline.translate.generate_localized_rewrite`

**Files:**
- Modify: `pipeline/translate.py`
- Test: `tests/test_localized_rewrite_prompts.py`（append）

- [ ] **Step 1: Write failing test**

Append to `tests/test_localized_rewrite_prompts.py`:

```python
class TestGenerateLocalizedRewrite:
    def test_rewrite_calls_llm_with_custom_messages_builder(self, monkeypatch):
        """generate_localized_rewrite 必须走语言专属 messages_builder 路径。"""
        from pipeline import translate

        # Mock resolve_provider_config
        captured = {}
        class FakeResponse:
            class choices: pass
        class FakeChoice:
            class message: pass

        def fake_resolve(provider, user_id=None, api_key_override=None):
            class FakeClient:
                class chat:
                    class completions:
                        @staticmethod
                        def create(**kwargs):
                            captured["messages"] = kwargs["messages"]
                            captured["model"] = kwargs["model"]
                            r = type("R", (), {})()
                            c = type("C", (), {})()
                            m = type("M", (), {})()
                            m.content = '{"full_text": "Short.", "sentences": [{"index": 0, "text": "Short.", "source_segment_indices": [0]}]}'
                            c.message = m
                            r.choices = [c]
                            r.usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 5})()
                            return r
            return FakeClient(), "fake-model"

        monkeypatch.setattr(translate, "resolve_provider_config", fake_resolve)

        from pipeline.localization_de import build_localized_rewrite_messages
        result = translate.generate_localized_rewrite(
            source_full_text="Source",
            prev_localized_translation={
                "full_text": "Hallo.",
                "sentences": [{"index": 0, "text": "Hallo.", "source_segment_indices": [0]}],
            },
            target_chars=50,
            direction="shrink",
            source_language="en",
            messages_builder=build_localized_rewrite_messages,
            provider="openrouter",
        )
        assert result["full_text"] == "Short."
        assert len(result["sentences"]) == 1
        # Confirm messages_builder was called with rewrite-specific args
        assert "50" in captured["messages"][1]["content"]
        assert "shrink" in captured["messages"][1]["content"].lower()
        assert "Hallo." in captured["messages"][1]["content"]
        # usage was attached
        assert result["_usage"]["input_tokens"] == 10
        assert result["_usage"]["output_tokens"] == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_localized_rewrite_prompts.py::TestGenerateLocalizedRewrite -v`
Expected: FAIL with `AttributeError: module 'pipeline.translate' has no attribute 'generate_localized_rewrite'`

- [ ] **Step 3: Implement `generate_localized_rewrite`**

Append to `pipeline/translate.py` (after `generate_tts_script`):

```python
def generate_localized_rewrite(
    source_full_text: str,
    prev_localized_translation: dict,
    target_chars: int,
    direction: str,
    source_language: str,
    messages_builder,
    *,
    provider: str = "openrouter",
    user_id: int | None = None,
    openrouter_api_key: str | None = None,
) -> dict:
    """Rewrite an existing localized_translation to a target character count.

    Args:
        source_full_text: original source text (Chinese or English).
        prev_localized_translation: previous round's translation dict
            ({full_text, sentences[...]}); supplied as reference for the LLM.
        target_chars: approximate character count target for the new full_text.
        direction: "shrink" or "expand".
        source_language: "zh" or "en" (used for lang_label in the prompt).
        messages_builder: language-specific callable, e.g.
            pipeline.localization_de.build_localized_rewrite_messages.
        provider: "openrouter" | "doubao".
        user_id: user id for key/extras resolution.
        openrouter_api_key: override api key.

    Returns:
        Same schema as generate_localized_translation:
        {"full_text": str, "sentences": [...], "_usage": {...}}
    """
    client, model = resolve_provider_config(provider, user_id, api_key_override=openrouter_api_key)
    extra_body: dict = {}
    if provider != "doubao":
        extra_body["response_format"] = LOCALIZED_TRANSLATION_RESPONSE_FORMAT
    if provider == "openrouter":
        extra_body["plugins"] = [{"id": "response-healing"}]

    messages = messages_builder(
        source_full_text=source_full_text,
        prev_localized_translation=prev_localized_translation,
        target_chars=target_chars,
        direction=direction,
        source_language=source_language,
    )

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.2,
        max_tokens=4096,
        **({"extra_body": extra_body} if extra_body else {}),
    )
    raw_content = response.choices[0].message.content
    log.info("localized_rewrite raw response (provider=%s, direction=%s, target_chars=%d): %s",
             provider, direction, target_chars, raw_content[:2000])
    payload = parse_json_content(raw_content)
    result = validate_localized_translation(payload)
    usage = getattr(response, "usage", None)
    if usage:
        result["_usage"] = {
            "input_tokens": getattr(usage, "prompt_tokens", None),
            "output_tokens": getattr(usage, "completion_tokens", None),
        }
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_localized_rewrite_prompts.py::TestGenerateLocalizedRewrite -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add pipeline/translate.py tests/test_localized_rewrite_prompts.py
git commit -m "feat(translate): generate_localized_rewrite 调 LLM 重写译文

接收 target_chars / direction / source_language + 语言专属
messages_builder，复用 LOCALIZED_TRANSLATION_RESPONSE_FORMAT 与
validator，返回与 generate_localized_translation 同 schema。"
```

---

## Task 6: 事件常量 `EVT_TTS_DURATION_ROUND`

**Files:**
- Modify: `appcore/events.py`
- Test: `tests/test_appcore_events.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_appcore_events.py`:

```python
def test_tts_duration_round_event_constant_exists():
    from appcore.events import EVT_TTS_DURATION_ROUND
    assert EVT_TTS_DURATION_ROUND == "tts_duration_round"

def test_tts_duration_round_does_not_collide_with_other_events():
    from appcore import events
    # Gather all EVT_* constants
    constants = {
        name: getattr(events, name)
        for name in dir(events)
        if name.startswith("EVT_")
    }
    values = list(constants.values())
    assert len(values) == len(set(values)), "EVT_* constants must be unique"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_appcore_events.py::test_tts_duration_round_event_constant_exists -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Add the constant**

In `appcore/events.py`, add after `EVT_SR_ERROR` (line ~20):

```python
EVT_TTS_DURATION_ROUND = "tts_duration_round"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_appcore_events.py -v`
Expected: PASS (all tests including the new two).

- [ ] **Step 5: Commit**

```bash
git add appcore/events.py tests/test_appcore_events.py
git commit -m "feat(events): EVT_TTS_DURATION_ROUND 事件常量"
```

---

## Task 7: `task_state.create()` 初值扩展

**Files:**
- Modify: `appcore/task_state.py:95-159`
- Test: `tests/test_appcore_task_state.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_appcore_task_state.py`:

```python
def test_create_task_initializes_tts_duration_fields(tmp_path):
    from appcore import task_state
    task_id = "test-duration-init"
    task = task_state.create(
        task_id, str(tmp_path / "video.mp4"), str(tmp_path / "out"),
        original_filename="video.mp4", user_id=None,
    )
    assert task["tts_duration_rounds"] == []
    assert task["tts_duration_status"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_appcore_task_state.py::test_create_task_initializes_tts_duration_fields -v`
Expected: FAIL with KeyError.

- [ ] **Step 3: Add fields to `create()`**

In `appcore/task_state.py`, locate the `create()` function's `task` dict literal. After `"variants": {...}` (around line 149-151), add:

```python
        "tts_duration_rounds": [],
        "tts_duration_status": None,
```

Final block should look like:
```python
    task = {
        "id": task_id,
        ...
        "variants": {
            "normal": _empty_variant_state("普通版"),
        },
        "tts_duration_rounds": [],
        "tts_duration_status": None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_appcore_task_state.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add appcore/task_state.py tests/test_appcore_task_state.py
git commit -m "feat(task_state): create 初值加 tts_duration_rounds / tts_duration_status"
```

---

## Task 8: 基类类变量抽取（保持行为）

**Files:**
- Modify: `appcore/runtime.py`（`PipelineRunner` class 头部加类变量，`_step_tts` 暂不变）

先引入类变量但不改 `_step_tts`，确保现有行为完全不变。de/fr 仍保留各自的 `_step_tts` override（下游 Task 会删掉）。

- [ ] **Step 1: Write failing test**

Append to `tests/test_appcore_runtime.py`:

```python
def test_pipeline_runner_has_tts_class_attributes():
    from appcore.runtime import PipelineRunner
    # Default (English) values
    assert PipelineRunner.tts_language_code is None
    assert PipelineRunner.tts_model_id == "eleven_turbo_v2_5"
    assert PipelineRunner.tts_default_voice_language is None
    assert PipelineRunner.localization_module == "pipeline.localization"
    assert PipelineRunner.target_language_label == "en"


def test_de_runner_overrides_tts_class_attributes():
    from appcore.runtime_de import DeTranslateRunner
    assert DeTranslateRunner.tts_language_code == "de"
    assert DeTranslateRunner.tts_model_id == "eleven_multilingual_v2"
    assert DeTranslateRunner.tts_default_voice_language == "de"
    assert DeTranslateRunner.localization_module == "pipeline.localization_de"
    assert DeTranslateRunner.target_language_label == "de"


def test_fr_runner_overrides_tts_class_attributes():
    from appcore.runtime_fr import FrTranslateRunner
    assert FrTranslateRunner.tts_language_code == "fr"
    assert FrTranslateRunner.tts_model_id == "eleven_multilingual_v2"
    assert FrTranslateRunner.tts_default_voice_language == "fr"
    assert FrTranslateRunner.localization_module == "pipeline.localization_fr"
    assert FrTranslateRunner.target_language_label == "fr"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_appcore_runtime.py -v -k "tts_class_attributes or overrides_tts"`
Expected: FAIL with AttributeError.

- [ ] **Step 3: Add class attributes**

In `appcore/runtime.py`, locate `class PipelineRunner:` (around line 130). After `project_type: str = "translation"`, add:

```python
class PipelineRunner:
    project_type: str = "translation"

    # ── TTS / localization 差异点（子类 override） ──
    tts_language_code: str | None = None           # ElevenLabs language_code; None=auto
    tts_model_id: str = "eleven_turbo_v2_5"        # ElevenLabs model_id
    tts_default_voice_language: str | None = None  # voice_library.ensure_defaults language; None=en
    localization_module: str = "pipeline.localization"
    target_language_label: str = "en"              # 中文消息展示标签，例如 "de" / "fr"

    # existing class-level attrs (include_soft_video etc.) stay after these
    include_soft_video: bool = False
    include_analysis_in_main_flow: bool = False
```

(Keep `include_soft_video` / `include_analysis_in_main_flow` as they currently exist — just re-list for clarity.)

In `appcore/runtime_de.py`, add class attributes right after `project_type`:

```python
class DeTranslateRunner(PipelineRunner):
    """German-specific pipeline runner."""

    project_type: str = "de_translate"
    tts_language_code = "de"
    tts_model_id = "eleven_multilingual_v2"
    tts_default_voice_language = "de"
    localization_module = "pipeline.localization_de"
    target_language_label = "de"
```

In `appcore/runtime_fr.py`, analogous:

```python
class FrTranslateRunner(PipelineRunner):
    """French-specific pipeline runner."""

    project_type: str = "fr_translate"
    tts_language_code = "fr"
    tts_model_id = "eleven_multilingual_v2"
    tts_default_voice_language = "fr"
    localization_module = "pipeline.localization_fr"
    target_language_label = "fr"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_appcore_runtime.py -v -k "tts_class_attributes or overrides_tts"`
Expected: PASS (3 tests)

Run full existing runtime tests to confirm nothing else broke:
Run: `pytest tests/test_appcore_runtime.py tests/test_runtime_v2.py tests/test_pipeline_runner.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add appcore/runtime.py appcore/runtime_de.py appcore/runtime_fr.py tests/test_appcore_runtime.py
git commit -m "refactor(runtime): 抽 TTS/localization 差异为类变量

为下一步抽取 _step_tts 到基类做准备。子类通过类变量 override
tts_language_code / tts_model_id / tts_default_voice_language /
localization_module / target_language_label。本次不改行为。"
```

---

## Task 9: `_run_tts_duration_loop` 基础框架（round 1 only）

**Files:**
- Modify: `appcore/runtime.py`（新增 `_run_tts_duration_loop` 方法，暂时只处理 round 1）
- Test: `tests/test_tts_duration_loop.py`（append）

这一步先搭起循环的骨架，只处理 round 1（收敛或继续）。不调用 rewrite。下一 task 再加 round 2/3 完整路径。

- [ ] **Step 1: Write failing test**

Append to `tests/test_tts_duration_loop.py`:

```python
import os
import json
from unittest.mock import MagicMock, patch

class TestDurationLoopRound1Only:
    def _make_runner(self):
        from appcore.events import EventBus
        from appcore.runtime import PipelineRunner
        bus = EventBus()
        runner = PipelineRunner(bus=bus, user_id=1)
        return runner

    def test_round1_converges_returns_final_immediately(self, tmp_path, monkeypatch):
        """round 1 音频时长在区间内时，循环返回 final_round=1。"""
        runner = self._make_runner()

        # Mock generate_full_audio → returns dict with full_audio_path
        fake_audio_path = str(tmp_path / "tts_full.round_1.mp3")
        # Write a dummy file so path exists
        with open(fake_audio_path, "wb") as f:
            f.write(b"fake")

        def fake_gen_full_audio(tts_segments, voice_id, task_dir, variant=None, **kw):
            out = os.path.join(task_dir, f"tts_full.{variant}.mp3")
            # Simulate variant="round_1" → tts_full.round_1.mp3
            with open(out, "wb") as f:
                f.write(b"fake")
            return {"full_audio_path": out, "segments": [{"index": 0, "tts_path": out, "tts_duration": 28.5}]}

        def fake_get_audio_duration(path):
            return 28.5  # Within [27, 30] for video=30

        # tts_script contains full_text
        def fake_gen_tts_script(loc, **kwargs):
            return {"full_text": "Short text.", "blocks": [{"index": 0, "text": "Short.",
                                                              "sentence_indices": [0],
                                                              "source_segment_indices": [0]}],
                    "subtitle_chunks": []}

        monkeypatch.setattr("pipeline.tts.generate_full_audio", fake_gen_full_audio)
        monkeypatch.setattr("pipeline.tts._get_audio_duration", fake_get_audio_duration)
        monkeypatch.setattr("pipeline.translate.generate_tts_script", fake_gen_tts_script)
        monkeypatch.setattr("pipeline.speech_rate_model.get_rate", lambda v, l: 15.0)
        monkeypatch.setattr("pipeline.speech_rate_model.update_rate", lambda *a, **kw: None)

        # task_state needs task to exist
        from appcore import task_state
        task_state.create("tdl-r1-conv", "v.mp4", str(tmp_path),
                          original_filename="v.mp4", user_id=1)

        import importlib
        loc_mod = importlib.import_module("pipeline.localization")
        # patch build_tts_segments to simple identity
        monkeypatch.setattr(loc_mod, "build_tts_segments",
                            lambda script, segs: [{"index": 0, "tts_text": "Short.", "tts_duration": 0.0}])

        initial_localized = {
            "full_text": "Short text.",
            "sentences": [{"index": 0, "text": "Short text.", "source_segment_indices": [0]}],
        }
        voice = {"id": 1, "elevenlabs_voice_id": "test-voice"}

        result = runner._run_tts_duration_loop(
            task_id="tdl-r1-conv",
            task_dir=str(tmp_path),
            loc_mod=loc_mod,
            provider="openrouter",
            video_duration=30.0,
            voice=voice,
            initial_localized_translation=initial_localized,
            source_full_text="Source zh.",
            source_language="zh",
            elevenlabs_api_key="fake-key",
            script_segments=[{"index": 0, "text": "x", "start_time": 0, "end_time": 3}],
            variant="normal",
        )

        assert result["final_round"] == 1
        assert result["tts_audio_path"].endswith("tts_full.round_1.mp3")
        assert len(result["rounds"]) == 1
        assert result["rounds"][0]["round"] == 1
        # Measured audio_duration stored
        assert result["rounds"][0]["audio_duration"] == 28.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tts_duration_loop.py::TestDurationLoopRound1Only::test_round1_converges_returns_final_immediately -v`
Expected: FAIL with `AttributeError: 'PipelineRunner' object has no attribute '_run_tts_duration_loop'`

- [ ] **Step 3: Implement `_run_tts_duration_loop` (round 1 + stub for 2/3)**

Add to `appcore/runtime.py` inside `class PipelineRunner:` (after existing methods, near end of class):

```python
    def _emit_duration_round(self, task_id: str, round_index: int,
                             phase: str, record: dict) -> None:
        """Emit EVT_TTS_DURATION_ROUND with merged payload."""
        from appcore.events import EVT_TTS_DURATION_ROUND
        payload = dict(record)
        payload["round"] = round_index
        payload["phase"] = phase
        self._emit(task_id, EVT_TTS_DURATION_ROUND, payload)

    def _run_tts_duration_loop(
        self, *, task_id: str, task_dir: str, loc_mod,
        provider: str, video_duration: float, voice: dict,
        initial_localized_translation: dict, source_full_text: str,
        source_language: str, elevenlabs_api_key: str,
        script_segments: list, variant: str,
    ) -> dict:
        """Iterate translate_rewrite → tts_script_regen → audio_gen → measure
        up to 3 rounds until audio duration lands in [video-3, video].

        Returns dict with: localized_translation, tts_script, tts_audio_path,
        tts_segments, rounds, final_round.
        """
        import importlib
        from pipeline.speech_rate_model import get_rate, update_rate
        from pipeline.tts import generate_full_audio, _get_audio_duration
        from pipeline.translate import generate_tts_script, generate_localized_rewrite

        MAX_ROUNDS = 3
        duration_lo = max(0.0, video_duration - 3.0)
        duration_hi = video_duration

        rounds: list[dict] = []
        prev_localized = initial_localized_translation
        last_audio_duration = 0.0
        last_char_count = 0

        # validator for TTS script (language may need non-default max_words)
        from functools import partial
        validator = partial(
            getattr(loc_mod, "validate_tts_script", None)
            or importlib.import_module("pipeline.localization").validate_tts_script,
            max_words=14 if self.target_language_label in ("de", "fr") else 10,
        )

        for round_index in range(1, MAX_ROUNDS + 1):
            round_record: dict = {
                "round": round_index,
                "video_duration": video_duration,
                "duration_lo": duration_lo,
                "duration_hi": duration_hi,
                "artifact_paths": {},
            }

            # Phase 1: translate_rewrite (skipped on round 1)
            if round_index == 1:
                localized_translation = prev_localized
                round_record["message"] = "初始译文（来自 translate 步骤）"
            else:
                voice_el_id = voice["elevenlabs_voice_id"]
                lang_code = self.tts_language_code or "en"
                cps = get_rate(voice_el_id, lang_code)
                if not cps or cps <= 0:
                    cps = (last_char_count / last_audio_duration) if last_audio_duration > 0 else 15.0
                target_duration, target_chars, direction = _compute_next_target(
                    round_index, last_audio_duration, cps, video_duration,
                )
                round_record["target_duration"] = target_duration
                round_record["target_chars"] = target_chars
                round_record["direction"] = direction
                round_record["message"] = (
                    f"第 {round_index} 轮：重写{_lang_display(self.target_language_label)}译文"
                    f"（目标 {target_chars} 字符，{direction}）"
                )
                self._emit_duration_round(task_id, round_index, "translate_rewrite", round_record)

                localized_translation = generate_localized_rewrite(
                    source_full_text=source_full_text,
                    prev_localized_translation=prev_localized,
                    target_chars=target_chars,
                    direction=direction,
                    source_language=source_language,
                    messages_builder=loc_mod.build_localized_rewrite_messages,
                    provider=provider,
                    user_id=self.user_id,
                )
                _save_json(task_dir, f"localized_translation.round_{round_index}.json", localized_translation)
                round_record["artifact_paths"]["localized_translation"] = f"localized_translation.round_{round_index}.json"
                round_record["char_count_prev"] = last_char_count

            # Phase 2: tts_script_regen
            self._emit_duration_round(task_id, round_index, "tts_script_regen", round_record)
            tts_script = generate_tts_script(
                localized_translation,
                provider=provider, user_id=self.user_id,
                messages_builder=loc_mod.build_tts_script_messages,
                validator=validator,
            )
            _save_json(task_dir, f"tts_script.round_{round_index}.json", tts_script)
            round_record["artifact_paths"]["tts_script"] = f"tts_script.round_{round_index}.json"

            # Phase 3: audio_gen
            self._emit_duration_round(task_id, round_index, "audio_gen", round_record)
            tts_segments = loc_mod.build_tts_segments(tts_script, script_segments)
            result = generate_full_audio(
                tts_segments, voice["elevenlabs_voice_id"], task_dir,
                variant=f"round_{round_index}",
                elevenlabs_api_key=elevenlabs_api_key,
                model_id=self.tts_model_id,
                language_code=self.tts_language_code,
            )
            round_record["artifact_paths"]["tts_full_audio"] = f"tts_full.round_{round_index}.mp3"

            # Phase 4: measure
            audio_duration = _get_audio_duration(result["full_audio_path"])
            char_count = len(tts_script.get("full_text", ""))
            update_rate(voice["elevenlabs_voice_id"],
                        self.tts_language_code or "en",
                        chars=char_count, duration_seconds=audio_duration)
            round_record["audio_duration"] = audio_duration
            round_record["char_count"] = char_count

            # persist rounds incrementally so UI survives page refresh
            import appcore.task_state as task_state
            rounds.append(round_record)
            task_state.update(task_id, tts_duration_rounds=rounds)

            self._emit_duration_round(task_id, round_index, "measure", round_record)

            if duration_lo <= audio_duration <= duration_hi:
                self._emit_duration_round(task_id, round_index, "converged", round_record)
                task_state.update(task_id, tts_duration_status="converged")
                return {
                    "localized_translation": localized_translation,
                    "tts_script": tts_script,
                    "tts_audio_path": result["full_audio_path"],
                    "tts_segments": result["segments"],
                    "rounds": rounds,
                    "final_round": round_index,
                }

            prev_localized = localized_translation
            last_audio_duration = audio_duration
            last_char_count = char_count

        # 3 rounds exhausted, not converged
        self._emit_duration_round(task_id, MAX_ROUNDS, "failed", round_record)
        import appcore.task_state as task_state
        task_state.update(task_id, tts_duration_status="failed")
        raise RuntimeError(
            f"TTS 音频时长 {MAX_ROUNDS} 轮内未收敛到 [{duration_lo:.1f}, {duration_hi:.1f}] 区间，"
            f"最后一次为 {last_audio_duration:.1f}s。请调整 voice 或翻译 prompt 后重试。"
        )
```

Also add small helper at module level (near other helpers):

```python
def _lang_display(label: str) -> str:
    return {"en": "英语", "de": "德语", "fr": "法语"}.get(label, label)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tts_duration_loop.py::TestDurationLoopRound1Only -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add appcore/runtime.py tests/test_tts_duration_loop.py
git commit -m "feat(runtime): _run_tts_duration_loop 骨架 + round 1 收敛路径

新增迭代循环方法，处理 round 1..3：每轮 4 phase（translate_rewrite /
tts_script_regen / audio_gen / measure）各自 emit EVT_TTS_DURATION_ROUND。
本 task 仅验证 round 1 路径；round 2/3 rewrite 路径由下一 task 覆盖。"
```

---

## Task 10: 循环 round 2/3 完整路径测试

**Files:**
- Test: `tests/test_tts_duration_loop.py`（append）

- [ ] **Step 1: Write failing tests**

Append to `tests/test_tts_duration_loop.py`:

```python
class TestDurationLoopMultiRound:
    def _setup(self, monkeypatch, tmp_path, audio_durations):
        """audio_durations: list of durations returned in sequence per round."""
        from appcore import task_state
        task_state.create("tdl-multi", "v.mp4", str(tmp_path),
                          original_filename="v.mp4", user_id=1)
        call_counter = {"i": 0}

        def fake_gen_full_audio(tts_segments, voice_id, task_dir, variant=None, **kw):
            out = os.path.join(task_dir, f"tts_full.{variant}.mp3")
            with open(out, "wb") as f:
                f.write(b"fake")
            return {"full_audio_path": out,
                    "segments": [{"index": 0, "tts_path": out, "tts_duration": 1.0}]}

        def fake_get_audio_duration(path):
            idx = call_counter["i"]
            call_counter["i"] += 1
            return audio_durations[min(idx, len(audio_durations) - 1)]

        def fake_gen_tts_script(loc, **kwargs):
            return {"full_text": loc.get("full_text", ""), "blocks": [], "subtitle_chunks": []}

        def fake_gen_rewrite(**kwargs):
            # Pretend rewrite shortens by 30%
            prev = kwargs["prev_localized_translation"]
            new_text = prev["full_text"][: int(len(prev["full_text"]) * 0.7)]
            return {
                "full_text": new_text,
                "sentences": [{"index": 0, "text": new_text, "source_segment_indices": [0]}],
            }

        monkeypatch.setattr("pipeline.tts.generate_full_audio", fake_gen_full_audio)
        monkeypatch.setattr("pipeline.tts._get_audio_duration", fake_get_audio_duration)
        monkeypatch.setattr("pipeline.translate.generate_tts_script", fake_gen_tts_script)
        monkeypatch.setattr("pipeline.translate.generate_localized_rewrite", fake_gen_rewrite)
        monkeypatch.setattr("pipeline.speech_rate_model.get_rate", lambda v, l: 15.0)
        monkeypatch.setattr("pipeline.speech_rate_model.update_rate", lambda *a, **kw: None)

        import importlib
        loc_mod = importlib.import_module("pipeline.localization")
        monkeypatch.setattr(loc_mod, "build_tts_segments", lambda s, sg: [])
        # stub the rewrite builder
        monkeypatch.setattr(loc_mod, "build_localized_rewrite_messages",
                            lambda **kw: [{"role": "system", "content": ""},
                                          {"role": "user", "content": ""}], raising=False)

        from appcore.events import EventBus
        from appcore.runtime import PipelineRunner
        runner = PipelineRunner(bus=EventBus(), user_id=1)

        initial = {"full_text": "A" * 400, "sentences": [{"index": 0, "text": "A" * 400, "source_segment_indices": [0]}]}
        return runner, loc_mod, initial

    def test_round2_shrink_converges(self, tmp_path, monkeypatch):
        # round 1: 35s (over), round 2: 28.5s (in range for video=30)
        runner, loc_mod, initial = self._setup(monkeypatch, tmp_path, [35.0, 28.5])
        result = runner._run_tts_duration_loop(
            task_id="tdl-multi", task_dir=str(tmp_path), loc_mod=loc_mod,
            provider="openrouter", video_duration=30.0,
            voice={"id": 1, "elevenlabs_voice_id": "v"},
            initial_localized_translation=initial,
            source_full_text="Source", source_language="zh",
            elevenlabs_api_key="k", script_segments=[{"index": 0, "text": "x", "start_time": 0, "end_time": 3}],
            variant="normal",
        )
        assert result["final_round"] == 2
        assert len(result["rounds"]) == 2
        assert result["rounds"][1].get("direction") == "shrink"
        # round 1 record has no direction (no rewrite)
        assert "direction" not in result["rounds"][0]

    def test_three_rounds_exhausted_raises(self, tmp_path, monkeypatch):
        # All three rounds return audio longer than video
        runner, loc_mod, initial = self._setup(monkeypatch, tmp_path, [40.0, 38.0, 36.0])
        with pytest.raises(RuntimeError, match="3 轮内未收敛"):
            runner._run_tts_duration_loop(
                task_id="tdl-multi", task_dir=str(tmp_path), loc_mod=loc_mod,
                provider="openrouter", video_duration=30.0,
                voice={"id": 1, "elevenlabs_voice_id": "v"},
                initial_localized_translation=initial,
                source_full_text="Source", source_language="zh",
                elevenlabs_api_key="k",
                script_segments=[{"index": 0, "text": "x", "start_time": 0, "end_time": 3}],
                variant="normal",
            )
        from appcore import task_state
        task = task_state.get("tdl-multi")
        assert task["tts_duration_status"] == "failed"

    def test_intermediate_files_written(self, tmp_path, monkeypatch):
        runner, loc_mod, initial = self._setup(monkeypatch, tmp_path, [35.0, 28.5])
        runner._run_tts_duration_loop(
            task_id="tdl-multi", task_dir=str(tmp_path), loc_mod=loc_mod,
            provider="openrouter", video_duration=30.0,
            voice={"id": 1, "elevenlabs_voice_id": "v"},
            initial_localized_translation=initial,
            source_full_text="Source", source_language="zh",
            elevenlabs_api_key="k",
            script_segments=[{"index": 0, "text": "x", "start_time": 0, "end_time": 3}],
            variant="normal",
        )
        # round 1 only produces tts_script + audio (no localized, since initial reused)
        assert (tmp_path / "tts_script.round_1.json").exists()
        assert (tmp_path / "tts_full.round_1.mp3").exists()
        # round 2 produces all three
        assert (tmp_path / "localized_translation.round_2.json").exists()
        assert (tmp_path / "tts_script.round_2.json").exists()
        assert (tmp_path / "tts_full.round_2.mp3").exists()
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_tts_duration_loop.py::TestDurationLoopMultiRound -v`
Expected: PASS (3 tests). If the Task 9 impl is correct, these pass directly.

If any test fails, debug and adjust the loop — common issues:
- `last_audio_duration` not updated after round 1 → round 2 cps fallback uses 0
- `round_record` fields missing in failed branch → fix `round_record` default on failed path

- [ ] **Step 3: Commit (tests only)**

```bash
git add tests/test_tts_duration_loop.py
git commit -m "test(tts-duration): multi-round rewrite 路径与失败分支

覆盖 round 2 shrink 收敛、3 轮耗尽 raise、中间文件落盘三种场景。"
```

---

## Task 11: `_promote_final_artifacts` + `_resolve_voice` 辅助

**Files:**
- Modify: `appcore/runtime.py`
- Test: `tests/test_tts_duration_loop.py`（append）

- [ ] **Step 1: Write failing test**

Append to `tests/test_tts_duration_loop.py`:

```python
class TestPromoteFinalArtifacts:
    def test_promotes_round_n_to_normal(self, tmp_path):
        from appcore.runtime import PipelineRunner
        from appcore.events import EventBus
        runner = PipelineRunner(bus=EventBus(), user_id=1)

        # Create round_2 source file
        src = tmp_path / "tts_full.round_2.mp3"
        src.write_bytes(b"audio data")

        runner._promote_final_artifacts(
            task_dir=str(tmp_path),
            final_round=2,
            variant="normal",
        )

        dst = tmp_path / "tts_full.normal.mp3"
        assert dst.exists()
        assert dst.read_bytes() == b"audio data"
```

- [ ] **Step 2: Run test**

Run: `pytest tests/test_tts_duration_loop.py::TestPromoteFinalArtifacts -v`
Expected: FAIL with AttributeError.

- [ ] **Step 3: Implement**

Add to `PipelineRunner` class in `appcore/runtime.py`:

```python
    def _promote_final_artifacts(self, task_dir: str, final_round: int, variant: str) -> None:
        """Copy tts_full.round_{N}.mp3 to tts_full.{variant}.mp3 for downstream compatibility."""
        import shutil
        src = os.path.join(task_dir, f"tts_full.round_{final_round}.mp3")
        dst = os.path.join(task_dir, f"tts_full.{variant}.mp3")
        if os.path.exists(src):
            shutil.copy2(src, dst)

    def _resolve_voice(self, task: dict, loc_mod) -> dict:
        """Resolve voice for TTS: explicit task.voice_id → recommended → default.

        Falls back to loc_mod.DEFAULT_{MALE,FEMALE}_VOICE_ID if library has none.
        """
        from pipeline.tts import get_voice_by_id

        voice = None
        if task.get("voice_id"):
            voice = get_voice_by_id(task["voice_id"], self.user_id)
        if not voice and task.get("recommended_voice_id"):
            voice = get_voice_by_id(task["recommended_voice_id"], self.user_id)
        if not voice:
            from pipeline.voice_library import get_voice_library
            gender = task.get("voice_gender", "male")
            lib = get_voice_library()
            if self.tts_default_voice_language:
                lib.ensure_defaults(self.user_id, language=self.tts_default_voice_language)
                voice = lib.get_default_voice(self.user_id, gender=gender,
                                              language=self.tts_default_voice_language)
            else:
                voice = lib.get_default_voice(self.user_id, gender=gender)
        if not voice:
            default_male = getattr(loc_mod, "DEFAULT_MALE_VOICE_ID", None)
            default_female = getattr(loc_mod, "DEFAULT_FEMALE_VOICE_ID", None)
            gender = task.get("voice_gender", "male")
            voice = {
                "id": None,
                "elevenlabs_voice_id": default_male if gender == "male" else default_female,
                "name": "Default",
            }
        return voice
```

- [ ] **Step 4: Run test**

Run: `pytest tests/test_tts_duration_loop.py::TestPromoteFinalArtifacts -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add appcore/runtime.py tests/test_tts_duration_loop.py
git commit -m "feat(runtime): _promote_final_artifacts + _resolve_voice 辅助

final 音频 copy 到 tts_full.normal.mp3 保持下游兼容。
_resolve_voice 把三个模块共用的 voice 解析逻辑集中到基类。"
```

---

## Task 12: 新 `_step_tts` 接入循环

**Files:**
- Modify: `appcore/runtime.py`（替换 `PipelineRunner._step_tts` 现有实现）
- Test: `tests/test_pipeline_runner.py` / `tests/test_appcore_runtime.py`

- [ ] **Step 1: Write integration test**

Append to `tests/test_tts_duration_loop.py`:

```python
class TestStepTtsIntegration:
    def test_step_tts_persists_final_artifacts_to_variant_state(self, tmp_path, monkeypatch):
        """_step_tts 把 loop 最终产物写回 task.variants[normal] 并覆盖 normal 文件名。"""
        from appcore import task_state
        from appcore.events import EventBus
        from appcore.runtime import PipelineRunner

        task_id = "step-tts-int"
        task = task_state.create(task_id, str(tmp_path / "v.mp4"), str(tmp_path),
                                  original_filename="v.mp4", user_id=1)
        # Prime with script_segments + localized_translation (what translate step would have set)
        task_state.update(
            task_id,
            script_segments=[{"index": 0, "text": "x", "start_time": 0.0, "end_time": 3.0}],
            source_full_text_zh="中文原文",
            source_language="zh",
            localized_translation={
                "full_text": "EN text.",
                "sentences": [{"index": 0, "text": "EN text.", "source_segment_indices": [0]}],
            },
            variants={"normal": {"label": "普通版", "localized_translation": {
                "full_text": "EN text.",
                "sentences": [{"index": 0, "text": "EN text.", "source_segment_indices": [0]}],
            }}},
            voice_id=None,
            recommended_voice_id=None,
            voice_gender="male",
        )

        def fake_gen_full_audio(tts_segments, voice_id, task_dir, variant=None, **kw):
            out = os.path.join(task_dir, f"tts_full.{variant}.mp3")
            with open(out, "wb") as f:
                f.write(b"audio")
            return {"full_audio_path": out,
                    "segments": [{"index": 0, "tts_path": out, "tts_duration": 28.0}]}

        monkeypatch.setattr("pipeline.tts.generate_full_audio", fake_gen_full_audio)
        monkeypatch.setattr("pipeline.tts._get_audio_duration", lambda p: 28.0)
        monkeypatch.setattr("pipeline.translate.generate_tts_script",
                            lambda loc, **kw: {"full_text": "EN.", "blocks": [], "subtitle_chunks": []})
        monkeypatch.setattr("pipeline.speech_rate_model.get_rate", lambda v, l: 15.0)
        monkeypatch.setattr("pipeline.speech_rate_model.update_rate", lambda *a, **kw: None)
        monkeypatch.setattr("pipeline.extract.get_video_duration", lambda p: 30.0)
        monkeypatch.setattr("appcore.api_keys.resolve_key", lambda u, s, e: "fake-key")

        from pipeline import localization as loc_mod
        monkeypatch.setattr(loc_mod, "build_tts_segments", lambda s, sg: [])

        # voice resolution: make get_voice_by_id return a fake voice
        monkeypatch.setattr("pipeline.tts.get_voice_by_id",
                            lambda vid, uid: {"id": 99, "elevenlabs_voice_id": "vx", "name": "V"})
        # Skip library fallback by forcing voice_id
        task_state.update(task_id, voice_id=99)

        monkeypatch.setattr("appcore.usage_log.record", lambda *a, **kw: None)

        runner = PipelineRunner(bus=EventBus(), user_id=1)
        runner._step_tts(task_id, str(tmp_path))

        task = task_state.get(task_id)
        assert task["steps"]["tts"] == "done"
        assert (tmp_path / "tts_full.normal.mp3").exists()
        # round_1 file must also exist (intermediate)
        assert (tmp_path / "tts_full.round_1.mp3").exists()
        # variant state updated
        v_state = task["variants"]["normal"]
        assert v_state["tts_audio_path"].endswith("tts_full.normal.mp3")
        assert task["tts_duration_status"] == "converged"
        assert len(task["tts_duration_rounds"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tts_duration_loop.py::TestStepTtsIntegration -v`
Expected: FAIL (current `_step_tts` doesn't use loop, doesn't set `tts_duration_rounds`).

- [ ] **Step 3: Replace `_step_tts` in `PipelineRunner`**

In `appcore/runtime.py`, replace the existing `_step_tts` method body with:

```python
    def _step_tts(self, task_id: str, task_dir: str) -> None:
        import importlib
        import appcore.task_state as task_state

        task = task_state.get(task_id)
        loc_mod = importlib.import_module(self.localization_module)

        lang_display = _lang_display(self.target_language_label)
        self._set_step(task_id, "tts", "running", f"正在生成{lang_display}配音...")

        from appcore.api_keys import resolve_key
        from pipeline.extract import get_video_duration

        provider = _resolve_translate_provider(self.user_id)
        elevenlabs_api_key = resolve_key(self.user_id, "elevenlabs", "ELEVENLABS_API_KEY")
        voice = self._resolve_voice(task, loc_mod)

        variant = "normal"
        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get(variant, {}))
        initial_localized = variant_state.get("localized_translation", {}) \
                            or task.get("localized_translation", {})
        source_full_text = task.get("source_full_text_zh") or task.get("source_full_text", "")
        source_language = task.get("source_language", "zh")
        video_duration = get_video_duration(task["video_path"])

        # reset duration tracking for a fresh run (e.g. resume)
        task_state.update(task_id, tts_duration_rounds=[], tts_duration_status="running")

        loop_result = self._run_tts_duration_loop(
            task_id=task_id,
            task_dir=task_dir,
            loc_mod=loc_mod,
            provider=provider,
            video_duration=video_duration,
            voice=voice,
            initial_localized_translation=initial_localized,
            source_full_text=source_full_text,
            source_language=source_language,
            elevenlabs_api_key=elevenlabs_api_key,
            script_segments=task.get("script_segments", []),
            variant=variant,
        )

        # Promote final to standard file names
        self._promote_final_artifacts(task_dir, loop_result["final_round"], variant)
        final_audio_path = os.path.join(task_dir, f"tts_full.{variant}.mp3")

        from pipeline.timeline import build_timeline_manifest
        timeline_manifest = build_timeline_manifest(
            loop_result["tts_segments"], video_duration=video_duration,
        )

        variant_state.update({
            "segments": loop_result["tts_segments"],
            "tts_script": loop_result["tts_script"],
            "tts_audio_path": final_audio_path,
            "timeline_manifest": timeline_manifest,
            "voice_id": voice.get("id"),
            "localized_translation": loop_result["localized_translation"],
        })
        variants[variant] = variant_state

        task_state.set_preview_file(task_id, "tts_full_audio", final_audio_path)
        _save_json(task_dir, "tts_script.normal.json", loop_result["tts_script"])
        _save_json(task_dir, "tts_result.normal.json", loop_result["tts_segments"])
        _save_json(task_dir, "timeline_manifest.normal.json", timeline_manifest)
        _save_json(task_dir, "localized_translation.normal.json", loop_result["localized_translation"])
        _save_json(task_dir, "tts_duration_rounds.json", loop_result["rounds"])

        task_state.update(
            task_id,
            variants=variants,
            segments=loop_result["tts_segments"],
            tts_script=loop_result["tts_script"],
            tts_audio_path=final_audio_path,
            voice_id=voice.get("id"),
            timeline_manifest=timeline_manifest,
            localized_translation=loop_result["localized_translation"],
        )

        from web.preview_artifacts import build_tts_artifact
        task_state.set_artifact(task_id, "tts",
            build_tts_artifact(loop_result["tts_script"], loop_result["tts_segments"],
                               duration_rounds=loop_result["rounds"]))

        from appcore.events import EVT_TTS_SCRIPT_READY
        self._emit(task_id, EVT_TTS_SCRIPT_READY, {"tts_script": loop_result["tts_script"]})
        self._set_step(
            task_id, "tts", "done",
            f"{lang_display}配音生成完成（{loop_result['final_round']} 轮收敛）",
        )

        # Usage log for LLM + ElevenLabs (rewrite rounds 2/3 also recorded)
        from appcore.usage_log import record as _log_usage
        from pipeline.translate import get_model_display_name
        for round_record in loop_result["rounds"]:
            round_idx = round_record["round"]
            if round_idx >= 2:
                # rewrite LLM call
                _log_usage(self.user_id, task_id, provider,
                           model_name=get_model_display_name(provider, self.user_id),
                           success=True)
            # tts_script LLM call every round
            _log_usage(self.user_id, task_id, provider,
                       model_name=get_model_display_name(provider, self.user_id),
                       success=True)
        # ElevenLabs call every round
        for _ in loop_result["rounds"]:
            _log_usage(self.user_id, task_id, "elevenlabs", success=True)
```

- [ ] **Step 4: Run test**

Run: `pytest tests/test_tts_duration_loop.py::TestStepTtsIntegration -v`
Expected: PASS.

Run full test suite for runtime-related:
Run: `pytest tests/test_appcore_runtime.py tests/test_pipeline_runner.py tests/test_runtime_v2.py tests/test_tts_duration_loop.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add appcore/runtime.py tests/test_tts_duration_loop.py
git commit -m "feat(runtime): _step_tts 接入 _run_tts_duration_loop

新的 TTS 步骤：初始轮复用 translate 产物，最多 3 轮 rewrite+retts 收敛。
收敛后 copy 到 tts_full.normal.mp3，下游 subtitle/compose/export 零感知。
failure 路径抛异常被 _run 捕获 → status=error。"
```

---

## Task 13: 删除 de/fr 子类的 `_step_tts` override

**Files:**
- Modify: `appcore/runtime_de.py`（删除 `_step_tts` 方法）
- Modify: `appcore/runtime_fr.py`（删除 `_step_tts` 方法）

- [ ] **Step 1: Add integration-style tests covering de/fr**

Append to `tests/test_tts_duration_loop.py`:

```python
class TestLanguageSpecificRunners:
    def test_de_runner_uses_german_localization_module(self, monkeypatch):
        """DeTranslateRunner._step_tts goes through base class with de localization."""
        from appcore.runtime_de import DeTranslateRunner
        from appcore.events import EventBus
        captured_modules = []

        import importlib
        real_import_module = importlib.import_module

        def tracking_import(name, *a, **kw):
            if "localization" in name:
                captured_modules.append(name)
            return real_import_module(name, *a, **kw)

        monkeypatch.setattr(importlib, "import_module", tracking_import)

        runner = DeTranslateRunner(bus=EventBus(), user_id=1)
        # Just trigger the module resolution via loc_mod lookup
        import importlib as _il
        loc_mod = _il.import_module(runner.localization_module)
        assert loc_mod.__name__ == "pipeline.localization_de"
        assert hasattr(loc_mod, "build_localized_rewrite_messages")
        assert hasattr(loc_mod, "build_tts_script_messages")

    def test_fr_runner_uses_french_localization_module(self):
        from appcore.runtime_fr import FrTranslateRunner
        from appcore.events import EventBus
        runner = FrTranslateRunner(bus=EventBus(), user_id=1)
        import importlib as _il
        loc_mod = _il.import_module(runner.localization_module)
        assert loc_mod.__name__ == "pipeline.localization_fr"
        assert hasattr(loc_mod, "build_localized_rewrite_messages")

    def test_de_runner_does_not_override_step_tts(self):
        """DeTranslateRunner must inherit _step_tts from base (no local override)."""
        from appcore.runtime_de import DeTranslateRunner
        from appcore.runtime import PipelineRunner
        # The bound method should resolve to the same function as base class
        assert DeTranslateRunner._step_tts is PipelineRunner._step_tts

    def test_fr_runner_does_not_override_step_tts(self):
        from appcore.runtime_fr import FrTranslateRunner
        from appcore.runtime import PipelineRunner
        assert FrTranslateRunner._step_tts is PipelineRunner._step_tts
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tts_duration_loop.py::TestLanguageSpecificRunners -v`
Expected: FAIL on `test_de_runner_does_not_override_step_tts` (de still has its own `_step_tts`).

- [ ] **Step 3: Delete `_step_tts` from `runtime_de.py` and `runtime_fr.py`**

In `appcore/runtime_de.py`, delete the entire `def _step_tts(self, task_id: str, task_dir: str) -> None:` method (lines ~125–221). Keep everything else (the `_step_asr`, `_step_translate`, `_step_subtitle` overrides; the class attributes).

Same for `appcore/runtime_fr.py`: delete `_step_tts` (lines ~125–221).

Verify the remaining file imports are still used — unused imports at the top of each file might need trimming (e.g. `build_tts_segments`, `TTS_LANGUAGE_CODE` etc. imported at module top). Do this ONLY if static analysis / linter catches them; otherwise leave imports as-is to minimize diff.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_tts_duration_loop.py tests/test_appcore_runtime.py tests/test_pipeline_runner.py tests/test_runtime_v2.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add appcore/runtime_de.py appcore/runtime_fr.py tests/test_tts_duration_loop.py
git commit -m "refactor(runtime): 删除 de/fr 的 _step_tts override

de/fr 通过类变量（tts_language_code / tts_model_id / localization_module
等）继承基类 _step_tts，消除 3 份重复实现。行为等价，同时自动获得
迭代收敛循环。"
```

---

## Task 14: `build_tts_artifact` 扩展 duration_rounds

**Files:**
- Modify: `web/preview_artifacts.py`
- Test: `tests/test_preview_artifacts.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_preview_artifacts.py`:

```python
def test_build_tts_artifact_with_duration_rounds():
    from web.preview_artifacts import build_tts_artifact
    rounds = [
        {"round": 1, "audio_duration": 35.0, "char_count": 400, "video_duration": 30.0,
         "duration_lo": 27.0, "duration_hi": 30.0,
         "artifact_paths": {"tts_script": "tts_script.round_1.json",
                            "tts_full_audio": "tts_full.round_1.mp3"}},
        {"round": 2, "direction": "shrink", "target_duration": 28.0, "target_chars": 420,
         "audio_duration": 28.5, "char_count": 310, "video_duration": 30.0,
         "duration_lo": 27.0, "duration_hi": 30.0,
         "artifact_paths": {"localized_translation": "localized_translation.round_2.json",
                            "tts_script": "tts_script.round_2.json",
                            "tts_full_audio": "tts_full.round_2.mp3"}},
    ]
    artifact = build_tts_artifact(
        {"full_text": "hi", "blocks": [], "subtitle_chunks": []},
        [{"index": 0, "tts_path": "/x/y.mp3", "tts_duration": 1.0}],
        duration_rounds=rounds,
    )
    items = artifact["items"]
    duration_items = [it for it in items if it.get("type") == "tts_duration_rounds"]
    assert len(duration_items) == 1
    assert duration_items[0]["rounds"] == rounds


def test_build_tts_artifact_without_duration_rounds_is_backward_compatible():
    from web.preview_artifacts import build_tts_artifact
    artifact = build_tts_artifact(
        {"full_text": "hi", "blocks": [], "subtitle_chunks": []},
        [{"index": 0, "tts_path": "/x/y.mp3", "tts_duration": 1.0}],
    )
    items = artifact["items"]
    duration_items = [it for it in items if it.get("type") == "tts_duration_rounds"]
    assert len(duration_items) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_preview_artifacts.py -v -k "duration_rounds"`
Expected: FAIL (the first test will fail because current function ignores `duration_rounds`).

- [ ] **Step 3: Modify `build_tts_artifact`**

In `web/preview_artifacts.py`, update the signature and body:

```python
def build_tts_artifact(tts_script_or_segments, segments: list[dict] | None = None,
                       duration_rounds: list[dict] | None = None) -> dict:
    if segments is None and isinstance(tts_script_or_segments, list):
        artifact = {
            "title": "语音生成",
            "items": [
                media_item("audio", "整段配音", "tts_full_audio"),
                {
                    "type": "segments",
                    "label": "配音段落",
                    "segments": tts_script_or_segments,
                    "break_after": [],
                },
            ],
        }
        if duration_rounds:
            artifact["items"].append({
                "type": "tts_duration_rounds",
                "label": "时长控制迭代",
                "rounds": duration_rounds,
            })
        return artifact

    tts_script = tts_script_or_segments or {}
    items = [
        media_item("audio", "整段配音", "tts_full_audio"),
        text_item("ElevenLabs 文案", tts_script.get("full_text", "")),
        {
            "type": "tts_blocks",
            "label": "朗读块",
            "blocks": tts_script.get("blocks", []),
        },
        {
            "type": "subtitle_chunks",
            "label": "字幕块",
            "chunks": tts_script.get("subtitle_chunks", []),
        },
    ]
    if segments:
        items.append(
            {
                "type": "segments",
                "label": "配音段落映射",
                "segments": segments,
                "break_after": [],
            }
        )
    if duration_rounds:
        items.append({
            "type": "tts_duration_rounds",
            "label": "时长控制迭代",
            "rounds": duration_rounds,
        })
    return {"title": "语音生成", "items": items}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_preview_artifacts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/preview_artifacts.py tests/test_preview_artifacts.py
git commit -m "feat(preview_artifacts): build_tts_artifact 支持 duration_rounds

往 artifact items 里追加一条 type=tts_duration_rounds 供前端渲染
迭代日志。不传该参数时完全向后兼容，老任务 artifact 不受影响。"
```

---

## Task 15: 下载路由新增 round-file（德语）

**Files:**
- Modify: `web/routes/de_translate.py`
- Test: 新增路由级测试（手工或通过 curl 验证）；单元测试可选

- [ ] **Step 1: Add route**

Append to `web/routes/de_translate.py` (before the last `@bp.route` or after `get_artifact`):

```python
_ALLOWED_ROUND_KINDS = {
    "localized_translation": ("localized_translation.round_{r}.json", "application/json"),
    "tts_script":            ("tts_script.round_{r}.json",            "application/json"),
    "tts_full_audio":        ("tts_full.round_{r}.mp3",               "audio/mpeg"),
}


@bp.route("/api/de-translate/<task_id>/round-file/<int:round_index>/<kind>")
@login_required
def get_round_file(task_id: str, round_index: int, kind: str):
    """Serve per-round intermediate artifacts (localized_translation / tts_script / tts_full_audio)."""
    if round_index not in (1, 2, 3):
        abort(404)
    if kind not in _ALLOWED_ROUND_KINDS:
        abort(404)

    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    filename_pattern, mime = _ALLOWED_ROUND_KINDS[kind]
    filename = filename_pattern.format(r=round_index)
    path = os.path.join(task.get("task_dir", ""), filename)
    if not os.path.exists(path):
        return jsonify({"error": "File not ready"}), 404

    return send_file(os.path.abspath(path), mimetype=mime,
                     as_attachment=False, download_name=filename)
```

- [ ] **Step 2: Manual smoke test**

Start the app (user action) and call:
```
GET /api/de-translate/<existing-task-id>/round-file/1/tts_full_audio
```
If the task doesn't yet have round files, expect 404 with `File not ready`.
Once a task finishes running the new code, request should return the mp3.

- [ ] **Step 3: Add route test (optional, if project has test client fixture)**

Check `tests/conftest.py` for a `test_client` fixture. If present, add:

```python
# tests/test_de_translate_routes.py  (create or append)
def test_round_file_route_404_for_invalid_round(test_client, login_user_factory):
    user = login_user_factory()
    with test_client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
    resp = test_client.get("/api/de-translate/nonexistent/round-file/9/tts_full_audio")
    assert resp.status_code == 404

def test_round_file_route_404_for_invalid_kind(test_client, login_user_factory):
    user = login_user_factory()
    with test_client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
    resp = test_client.get("/api/de-translate/nonexistent/round-file/1/bogus")
    assert resp.status_code == 404
```

If `tests/conftest.py` does NOT expose a test client + login fixture, skip this step — route will be validated by manual QA at the end.

- [ ] **Step 4: Commit**

```bash
git add web/routes/de_translate.py
# if test was added:
# git add tests/test_de_translate_routes.py
git commit -m "feat(de-translate): /round-file 路由供前端下载每轮中间文件

白名单 kind ∈ {localized_translation, tts_script, tts_full_audio},
round ∈ {1,2,3}。路径直接拼 task_dir 下固定文件名,鉴权复用归属校验。"
```

---

## Task 16: 下载路由新增 round-file（法语）

**Files:**
- Modify: `web/routes/fr_translate.py`

- [ ] **Step 1: Add route**

Apply the identical change to `web/routes/fr_translate.py` with `/api/fr-translate/...` prefix:

```python
_ALLOWED_ROUND_KINDS = {
    "localized_translation": ("localized_translation.round_{r}.json", "application/json"),
    "tts_script":            ("tts_script.round_{r}.json",            "application/json"),
    "tts_full_audio":        ("tts_full.round_{r}.mp3",               "audio/mpeg"),
}


@bp.route("/api/fr-translate/<task_id>/round-file/<int:round_index>/<kind>")
@login_required
def get_round_file(task_id: str, round_index: int, kind: str):
    if round_index not in (1, 2, 3):
        abort(404)
    if kind not in _ALLOWED_ROUND_KINDS:
        abort(404)

    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    filename_pattern, mime = _ALLOWED_ROUND_KINDS[kind]
    filename = filename_pattern.format(r=round_index)
    path = os.path.join(task.get("task_dir", ""), filename)
    if not os.path.exists(path):
        return jsonify({"error": "File not ready"}), 404

    return send_file(os.path.abspath(path), mimetype=mime,
                     as_attachment=False, download_name=filename)
```

- [ ] **Step 2: Manual smoke test** (same approach as Task 15)

- [ ] **Step 3: Commit**

```bash
git add web/routes/fr_translate.py
git commit -m "feat(fr-translate): /round-file 路由供前端下载每轮中间文件"
```

---

## Task 17: 下载路由新增 round-file（英语）

**Files:**
- Modify: `web/routes/task.py`

- [ ] **Step 1: Find the right spot and add route**

Read `web/routes/task.py` around the existing `/download/<file_type>` route and the `/artifact/<name>` route. The prefix in this file is `/<task_id>/...` (registered under `/api/task`). Add the new route near the other artifact routes:

```python
_ALLOWED_ROUND_KINDS = {
    "localized_translation": ("localized_translation.round_{r}.json", "application/json"),
    "tts_script":            ("tts_script.round_{r}.json",            "application/json"),
    "tts_full_audio":        ("tts_full.round_{r}.mp3",               "audio/mpeg"),
}


@bp.route("/<task_id>/round-file/<int:round_index>/<kind>", methods=["GET"])
@login_required
def get_round_file(task_id: str, round_index: int, kind: str):
    """Serve per-round intermediate artifacts for the default (English) translation pipeline."""
    if round_index not in (1, 2, 3):
        abort(404)
    if kind not in _ALLOWED_ROUND_KINDS:
        abort(404)

    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify({"error": "Task not found"}), 404

    filename_pattern, mime = _ALLOWED_ROUND_KINDS[kind]
    filename = filename_pattern.format(r=round_index)
    path = os.path.join(task.get("task_dir", ""), filename)
    if not os.path.exists(path):
        return jsonify({"error": "File not ready"}), 404

    return send_file(os.path.abspath(path), mimetype=mime,
                     as_attachment=False, download_name=filename)
```

Confirm imports at top of file include `send_file`, `abort`, `jsonify`, `os`, `store`. If not, add them.

- [ ] **Step 2: Manual smoke test** (same approach as Task 15)

- [ ] **Step 3: Commit**

```bash
git add web/routes/task.py
git commit -m "feat(task): /round-file 路由供前端下载每轮中间文件（英语模块）"
```

---

## Task 18: 前端容器插入 `_task_workbench.html`

**Files:**
- Modify: `web/templates/_task_workbench.html`

- [ ] **Step 1: Locate step-tts card**

Read `web/templates/_task_workbench.html` and find the `<div id="step-tts" ...>` section (or equivalent). The card typically has a title + a preview area populated dynamically.

- [ ] **Step 2: Add the container**

Inside the step-tts card, after the existing preview/message area and before card close, add:

```html
<div id="ttsDurationLog" class="duration-log" hidden></div>
```

Exact placement depends on existing structure. If the card looks like:
```html
<div class="step-card" data-step="tts">
  <div class="step-header">...</div>
  <div class="step-body">
    <div class="step-message"></div>
    <div class="step-preview"></div>
  </div>
</div>
```
insert `<div id="ttsDurationLog" class="duration-log" hidden></div>` just before `</div>` closing `.step-body`.

- [ ] **Step 3: Verify by rendering a page**

Manually check: reload any en/de/fr task detail page and inspect DOM — the `<div id="ttsDurationLog">` must be present and initially hidden.

- [ ] **Step 4: Commit**

```bash
git add web/templates/_task_workbench.html
git commit -m "feat(workbench): step-tts 卡片加 ttsDurationLog 容器"
```

---

## Task 19: 前端样式

**Files:**
- Modify: `web/templates/_task_workbench_styles.html`

- [ ] **Step 1: Add styles**

Append to `web/templates/_task_workbench_styles.html` inside the existing `<style>` block:

```css
.duration-log {
  margin-top: var(--space-4);
  padding: var(--space-4);
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
}

.duration-log-header {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
  color: var(--fg);
  font-weight: 600;
}

.duration-log-header .meta {
  color: var(--fg-muted);
  font-weight: 400;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
}

.duration-round {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  border-radius: var(--radius);
  background: var(--bg);
  border: 1px solid var(--border);
  margin-bottom: var(--space-2);
}

.duration-round:last-child { margin-bottom: 0; }

.duration-round-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-weight: 600;
  color: var(--fg);
}

.duration-round-title .status-icon {
  display: inline-flex;
  width: 20px;
  height: 20px;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-full);
  font-size: var(--text-xs);
}

.duration-round-title .status-icon.success { background: var(--success-bg); color: var(--success-fg); }
.duration-round-title .status-icon.running { background: var(--info-bg); color: var(--info); }
.duration-round-title .status-icon.pending { background: var(--bg-muted); color: var(--fg-subtle); }
.duration-round-title .status-icon.error { background: var(--danger-bg); color: var(--danger-fg); }
.duration-round-title .status-icon.warning { background: var(--warning-bg); color: var(--warning-fg); }

.duration-round-meta {
  color: var(--fg-muted);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
}

.duration-round-phase {
  color: var(--accent);
  font-size: var(--text-xs);
}

.duration-round-files {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-1);
}

.duration-round-files a {
  color: var(--accent);
  text-decoration: none;
  font-size: var(--text-xs);
  padding: 2px 8px;
  background: var(--accent-subtle);
  border-radius: var(--radius-sm);
}

.duration-round-files a:hover { background: var(--bg-muted); }

.duration-log-final {
  margin-top: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--border);
  color: var(--success-fg);
  font-weight: 600;
}

.duration-log-final.failed { color: var(--danger-fg); }
```

- [ ] **Step 2: Verify visually**

Open any task detail page — styles are loaded but container is hidden, so no visible impact yet. Confirm no CSS errors in DevTools.

- [ ] **Step 3: Commit**

```bash
git add web/templates/_task_workbench_styles.html
git commit -m "feat(workbench): duration-log 样式（ocean-blue tokens）"
```

---

## Task 20: 前端事件监听 + 渲染

**Files:**
- Modify: `web/templates/_task_workbench_scripts.html`

- [ ] **Step 1: Add event handler + renderer**

Find the block where `socket.on("step_update", ...)` and other handlers are registered (around line 450 per earlier exploration). Add the new handler next to them:

```js
  socket.on("tts_duration_round", payload => {
    if (!currentTask || !taskId) return;
    currentTask.tts_duration_rounds = currentTask.tts_duration_rounds || [];
    _upsertDurationRound(currentTask.tts_duration_rounds, payload);
    if (payload.phase === "converged") {
      currentTask.tts_duration_status = "converged";
    } else if (payload.phase === "failed") {
      currentTask.tts_duration_status = "failed";
    } else {
      currentTask.tts_duration_status = "running";
    }
    renderTtsDurationLog();
    scheduleRefreshTaskState();
  });
```

Then add helper functions (place near other render helpers):

```js
  function _upsertDurationRound(rounds, payload) {
    const idx = rounds.findIndex(r => r.round === payload.round);
    if (idx >= 0) {
      rounds[idx] = { ...rounds[idx], ...payload, __current_phase: payload.phase };
    } else {
      rounds.push({ ...payload, __current_phase: payload.phase });
    }
  }

  function _durationRoundFileUrl(roundIndex, kind) {
    const base = (typeof API_BASE !== 'undefined' && API_BASE) || '/api/task';
    return `${base}/${taskId}/round-file/${roundIndex}/${kind}`;
  }

  function _phaseLabel(phase) {
    return ({
      translate_rewrite: '正在重写译文',
      tts_script_regen:  '正在切分朗读块',
      audio_gen:         '正在生成 TTS 音频',
      measure:           '测量音频时长',
      converged:         '已收敛',
      failed:            '迭代失败',
    })[phase] || phase || '';
  }

  function _roundStatusIcon(round, isFinalConverged, isFailed) {
    if (round.audio_duration != null) {
      const lo = round.duration_lo, hi = round.duration_hi;
      if (lo != null && hi != null && round.audio_duration >= lo && round.audio_duration <= hi) {
        return ['success', '✓'];
      }
      if (isFailed) return ['error', '✗'];
      return ['warning', '!'];
    }
    return ['running', '⟳'];
  }

  function renderTtsDurationLog() {
    const container = document.getElementById('ttsDurationLog');
    if (!container) return;
    const rounds = (currentTask && currentTask.tts_duration_rounds) || [];
    if (!rounds.length) {
      container.hidden = true;
      container.innerHTML = '';
      return;
    }
    const status = currentTask.tts_duration_status || 'running';
    const video_d = rounds[0].video_duration || null;
    const lo = rounds[0].duration_lo, hi = rounds[0].duration_hi;

    const metaStr = (video_d != null)
      ? `视频 ${video_d.toFixed(1)}s · 目标区间 [${lo.toFixed(1)}, ${hi.toFixed(1)}]`
      : '';

    const parts = [];
    parts.push(`<div class="duration-log-header">时长控制迭代 <span class="meta">${metaStr}</span></div>`);

    rounds.forEach((r, idx) => {
      const isLast = idx === rounds.length - 1;
      const [iconClass, iconChar] = _roundStatusIcon(r, status === 'converged' && isLast, status === 'failed' && isLast);
      const phaseLabel = isLast && status === 'running' ? _phaseLabel(r.__current_phase) : '';
      const targetStr = (r.target_duration != null)
        ? `目标 ${r.target_duration.toFixed(1)}s / ${r.target_chars} 字符（${r.direction}）`
        : '初始译文';
      const measuredStr = (r.audio_duration != null)
        ? `实测 ${r.audio_duration.toFixed(1)}s / ${r.char_count} 字符`
        : '';

      const files = [];
      const ap = r.artifact_paths || {};
      if (ap.localized_translation) {
        files.push(`<a href="${_durationRoundFileUrl(r.round, 'localized_translation')}" target="_blank">译文 JSON</a>`);
      }
      if (ap.tts_script) {
        files.push(`<a href="${_durationRoundFileUrl(r.round, 'tts_script')}" target="_blank">朗读文案 JSON</a>`);
      }
      if (ap.tts_full_audio) {
        files.push(`<a href="${_durationRoundFileUrl(r.round, 'tts_full_audio')}" target="_blank">音频 MP3</a>`);
      }

      parts.push(`
        <div class="duration-round">
          <div class="duration-round-title">
            <span class="status-icon ${iconClass}">${iconChar}</span>
            <span>轮次 ${r.round}</span>
          </div>
          <div class="duration-round-meta">${targetStr}${measuredStr ? ' · ' + measuredStr : ''}</div>
          ${phaseLabel ? `<div class="duration-round-phase">${phaseLabel}…</div>` : ''}
          ${files.length ? `<div class="duration-round-files">${files.join('')}</div>` : ''}
        </div>
      `);
    });

    if (status === 'converged') {
      const last = rounds[rounds.length - 1];
      parts.push(`<div class="duration-log-final">✓ 第 ${last.round} 轮收敛 · 最终音频 ${last.audio_duration.toFixed(1)}s</div>`);
    } else if (status === 'failed') {
      parts.push(`<div class="duration-log-final failed">✗ 3 轮未收敛，任务失败</div>`);
    }

    container.innerHTML = parts.join('');
    container.hidden = false;
  }
```

Then ensure `renderTtsDurationLog()` is invoked during `renderTaskState()` so page reloads repopulate it. Locate `function renderTaskState()` and add a call near the end (before the last `}`):

```js
    renderTtsDurationLog();
```

Also ensure the `API_BASE` variable used in `_durationRoundFileUrl` is defined (the detail templates set `api_base` — confirm existing code names it `API_BASE` or `api_base`). If it's a different name, adjust `_durationRoundFileUrl`'s lookup:

```js
  function _durationRoundFileUrl(roundIndex, kind) {
    const base = (typeof api_base !== 'undefined' && api_base) || '/api/task';
    return `${base}/${taskId}/round-file/${roundIndex}/${kind}`;
  }
```

Check templates for the exact variable name; a grep of `_task_workbench_scripts.html` for `api_base` or `API_BASE` will settle this.

- [ ] **Step 2: Open a task page and verify visually**

No new task yet; old tasks have no `tts_duration_rounds` so the container stays hidden — confirm no console errors.

- [ ] **Step 3: Commit**

```bash
git add web/templates/_task_workbench_scripts.html
git commit -m "feat(workbench): tts_duration_round 事件监听 + 迭代日志渲染

订阅新事件更新 currentTask.tts_duration_rounds,renderTaskState
末尾调用 renderTtsDurationLog,按轮次渲染 status icon / target /
实测 / 中间文件下载链接。初始译文（round 1）没有 rewrite 参数。"
```

---

## Task 21: 手工 QA

**Files:** （无代码改动，纯验证）

- [ ] **Step 1: 启动 dev server**

Run the app locally (or point to a dev deploy). Ensure browser DevTools is open.

- [ ] **Step 2: 英语任务**

1. 上传一段 30s 英文/中文源视频，启动默认翻译流水线
2. 观察 tts 步骤：首次生成若恰好在 `[27, 30]`，迭代日志只有一行 "轮次 1 ✓ 初始译文 · 实测 XX.Xs"
3. 如果首次超过 30s：
   - 日志追加 "轮次 2"，看 phase 依次切换到 "正在重写译文" → "正在切分朗读块" → "正在生成 TTS 音频"
   - 完成后出现 "实测 Y.Ys" 和三个中间文件链接（译文 / 朗读文案 / 音频）
   - 点击每个链接能下载/预览成功
4. 最终看到 "✓ 第 N 轮收敛" 并进入 subtitle→compose→export

- [ ] **Step 3: 德语任务**

重复同样流程于 `/de-translate` 路径（用一段带中文或英文口播的源视频）。验证：
- 译文是德语
- 迭代日志文案显示 "重写德语译文"
- 每轮中间文件下载走 `/api/de-translate/<task_id>/round-file/...`

- [ ] **Step 4: 法语任务**

重复于 `/fr-translate`。验证：
- 译文是法语（含正确 élision）
- 迭代日志文案显示 "重写法语译文"

- [ ] **Step 5: 3 轮不收敛场景**

在一个 en/de/fr 任务里人为触发失败：
- 临时改 `_compute_next_target` 的系数为过于保守（如把 `video - 2.0` 改成 `video + 5.0` —— 不要 commit 这个改动）
- 跑任务，观察 3 轮都生成音频、迭代日志展示 3 条记录、每条都有中间文件链接
- tts 步骤状态变 `error`，pipeline_error 事件触发
- 前端显示清晰错误信息 "TTS 音频时长 3 轮内未收敛..."
- Revert 临时改动

- [ ] **Step 6: 老任务兼容**

打开一个本次改动前的老 en/de/fr 任务：
- tts 卡片外观与之前一致
- 没有 `ttsDurationLog` 显示（`hidden`）
- 所有既有功能（下载 hard/srt/capcut）正常

- [ ] **Step 7: translate_lab v2 不受影响**

打开一个 translate_lab 项目：
- tts_verify 步骤照旧工作
- 不触发迭代循环逻辑

- [ ] **Step 8: 回归测试**

Run: `pytest tests/ -v`
Expected: all previously passing tests still pass.

- [ ] **Step 9: Commit QA 结果**

如果发现 bug，修复后提交。如果全部通过，创建一个空 commit 记录 QA：

```bash
git commit --allow-empty -m "qa: en/de/fr TTS 迭代收敛手工验证通过

覆盖 1 轮收敛 / 多轮 rewrite 收敛 / 3 轮失败 / 老任务兼容 / v2 无影响。"
```

---

## 完成标志

所有 21 个 task 完成后：

- ✓ `pytest tests/ -v` 全绿
- ✓ 新建 en/de/fr 任务能看到迭代日志（至少 1 行）
- ✓ 每轮中间文件可下载
- ✓ 3 轮未收敛正确报错
- ✓ 老任务、v2 任务零回归
- ✓ `git log` 历史清晰（每个 task 一个 commit）

