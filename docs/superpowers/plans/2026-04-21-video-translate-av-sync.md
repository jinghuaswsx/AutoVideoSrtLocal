# 视频翻译音画同步(v2)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **本 plan 与 spec 配套**:详细设计在 `docs/superpowers/specs/2026-04-21-video-translate-v2-audiovisual-sync-design.md`。plan 只列操作步骤 + 关键代码骨架 + 验证命令,遇到"schema / prompt / 分支阈值"等细节请以 spec 为准。
>
> **本 plan 由 Codex 实施**:在 worktree `.worktrees/video-translate-av-sync`(分支 `feature/video-translate-av-sync`)内执行。每个 Phase 末尾必须 commit 并跑通指定验证命令才可进入下一 Phase。

**Goal:** 新增 `video_translate.av_localize` 两阶段管线(画面笔记 Stage1 + 纯文本翻译 Stage2)作为新 use case 与老 `video_translate.localize` 并存,翻译时带画面感 + 带货调性 + 逐句时长硬约束 + TTS 后时长闭环。

**Architecture:** Stage1 `shot_notes` 用 `gemini_aistudio` 一次多模态调用输出"全局摘要 + 逐句画面笔记";Stage2 `av_translate` 用 `openrouter` 纯文本调用,带 `target_chars_range` 硬约束;TTS 后 `duration_reconcile` 按 ±5% / 5-15% / ±15%+ 分支处理(speed 微调 / 局部重写 ≤2 轮 / warning 硬压兜底)。新老并存,DB additive。

**Tech Stack:** Python 3 / pytest / ElevenLabs SDK / Gemini AI Studio / OpenRouter / Flask + vanilla JS 前端 / MySQL JSON 字段

---

## Phase 0: 准备(无 commit)

- [ ] **Step 0.1:进入 worktree 并确认**

```bash
cd .worktrees/video-translate-av-sync
git status         # 期望: On branch feature/video-translate-av-sync, clean
git log -1         # 期望: 88c84a2 (或其后) docs(spec): 视频翻译音画同步(v2)设计方案
```

- [ ] **Step 0.2:阅读 spec 和本 plan**

```bash
cat docs/superpowers/specs/2026-04-21-video-translate-v2-audiovisual-sync-design.md
```

- [ ] **Step 0.3:确认 Python 环境**

```bash
python --version    # 期望: 3.10+
pytest --version
pytest tests/test_alignment.py -q   # 跑一个已有测试,确认环境可工作
```

---

## Phase 1: Use Case 注册 + Data Model 扩展

**Files:**
- Modify: `appcore/llm_use_cases.py`(USE_CASES 字典追加 2 条)
- Modify: `appcore/task_state.py`(扩展 task state dict 默认字段)
- Test: `tests/test_appcore_task_state.py`(追加测试)

- [ ] **Step 1.1:在 `appcore/llm_use_cases.py` USE_CASES 字典(line 104 `}` 之前)追加两条 use case**

```python
    # ── 视频翻译 v2(音画同步)──
    "video_translate.shot_notes": _uc(
        "video_translate.shot_notes", "video_translate", "画面笔记",
        "v2 Stage1:多模态 LLM 看视频,输出全局摘要 + 逐句画面笔记",
        "gemini_aistudio", "gemini-3.1-pro-preview", "gemini_video_analysis",
    ),
    "video_translate.av_localize": _uc(
        "video_translate.av_localize", "video_translate", "音画同步翻译",
        "v2 Stage2:纯文本 LLM 按画面笔记 + 带货 context + 时长约束做本地化口播",
        "openrouter", "anthropic/claude-sonnet-4.6", "openrouter",
    ),
    "video_translate.av_rewrite": _uc(
        "video_translate.av_rewrite", "video_translate", "音画同步单句重写",
        "v2 Stage2 的时长超限局部重写",
        "openrouter", "anthropic/claude-sonnet-4.6", "openrouter",
    ),
```

**说明**:Stage1 模型对齐 `video_score.run`(`gemini-3.1-pro-preview`);Stage2 对齐 `copywriting.generate`(`anthropic/claude-sonnet-4.6`)。

- [ ] **Step 1.2:运行现有 `appcore/llm_use_cases.py` 的相关测试**

```bash
pytest tests/ -k "use_case or llm_use_cases" -q
```

Expected: PASS(追加不破坏既有)。

- [ ] **Step 1.3:在 `appcore/task_state.py` 添加 v2 默认字段**

找到新建 task 初始化 dict 的位置(大约在 `def new_task_state` 或 `_default_state` 附近),在合适位置追加:

```python
AV_TRANSLATE_INPUTS_DEFAULT = {
    "target_language": None,
    "target_language_name": None,
    "target_market": None,
    "product_overrides": {
        "product_name": None,
        "brand": None,
        "selling_points": None,
        "price": None,
        "target_audience": None,
        "extra_info": None,
    },
}
```

并确保 `new_task_state` 返回的 dict 包含 `"av_translate_inputs": copy.deepcopy(AV_TRANSLATE_INPUTS_DEFAULT)`(以及 `"shot_notes": None`)。

- [ ] **Step 1.4:写测试 `tests/test_appcore_task_state.py` 追加一个测试**

```python
def test_new_task_state_contains_av_translate_defaults():
    from appcore.task_state import new_task_state  # 或实际函数名
    state = new_task_state(user_id=1)
    assert "av_translate_inputs" in state
    assert state["av_translate_inputs"]["target_language"] is None
    assert state["av_translate_inputs"]["product_overrides"]["product_name"] is None
    assert "shot_notes" in state
```

- [ ] **Step 1.5:运行测试**

```bash
pytest tests/test_appcore_task_state.py -q
```

Expected: PASS。

- [ ] **Step 1.6:Commit**

```bash
git add appcore/llm_use_cases.py appcore/task_state.py tests/test_appcore_task_state.py
git commit -m "feat(video-translate-v2): 注册三个新 use case 并扩展 task_state 字段"
```

---

## Phase 2: Stage1 画面笔记(`pipeline/shot_notes.py`)

**Files:**
- Create: `pipeline/shot_notes.py`
- Create: `tests/test_shot_notes.py`

- [ ] **Step 2.1:创建 `pipeline/shot_notes.py` 骨架**

核心 API:
```python
def generate_shot_notes(
    *,
    video_path: str | Path,
    script_segments: list[dict],   # [{index,start_time,end_time,text}, ...]
    target_language: str,
    target_market: str,
    user_id: int | None = None,
    project_id: str | None = None,
    max_retries: int = 2,
) -> dict:
    """返回 spec 中定义的 shot_notes JSON(含 global + sentences)。"""
```

内部走 `llm_client.invoke_generate("video_translate.shot_notes", prompt=USER_PROMPT, system=SYSTEM_PROMPT, media=video_path, response_schema=SHOT_NOTES_SCHEMA, ...)`。

**SYSTEM_PROMPT / USER_PROMPT / SHOT_NOTES_SCHEMA**:按 spec "Stage1 Shot Notes" 节定义。schema 覆盖 `global + sentences[]` 所有字段,`sentences` 长度必须等于 `len(script_segments)`。

- [ ] **Step 2.2:实现漏段补齐 + 重试**

返回前做后处理:
- 按 `asr_index` 建 dict 检索
- 输入 script_segments 遍历,若对应 asr_index 缺失,补 `{asr_index, start_time, end_time, scene:None, action:None, on_screen_text:[], product_visible:False, shot_type:None, emotion_hint:None}`
- 整体调用失败 `max_retries` 次后抛出(由 runtime 转 task.status=failed)

- [ ] **Step 2.3:写 `tests/test_shot_notes.py`**

覆盖:
```python
def test_shot_notes_happy_path(monkeypatch):
    # mock llm_client.invoke_generate 返回固定 JSON
    # 调 generate_shot_notes,验返回 dict.global 字段、sentences 长度、落盘结构

def test_shot_notes_fills_missing_sentences(monkeypatch):
    # mock 返回只有 2 条 sentences,但 script_segments 有 3 条
    # 验第 3 条被补齐为 None 字段

def test_shot_notes_retries_on_failure(monkeypatch):
    # mock 前两次抛异常,第三次返回正常
    # 验最终返回正常;call_count == 3

def test_shot_notes_fails_after_retries(monkeypatch):
    # mock 一直抛异常
    # 验抛出
```

- [ ] **Step 2.4:跑测试**

```bash
pytest tests/test_shot_notes.py -q -v
```

Expected: 4 PASS。

- [ ] **Step 2.5:Commit**

```bash
git add pipeline/shot_notes.py tests/test_shot_notes.py
git commit -m "feat(video-translate-v2): Stage1 shot_notes 多模态画面笔记"
```

---

## Phase 3: Stage2 `pipeline/av_translate.py`

**Files:**
- Create: `pipeline/av_translate.py`
- Create: `tests/test_av_translate.py`

- [ ] **Step 3.1:创建 `pipeline/av_translate.py` 骨架**

两个主 API:

```python
def generate_av_localized_translation(
    *,
    script_segments: list[dict],
    shot_notes: dict,
    av_inputs: dict,             # task.av_translate_inputs
    voice_id: str,                # 用于查 speech_rate_model
    user_id: int | None = None,
    project_id: str | None = None,
) -> dict:
    """返回 {"sentences": [{asr_index, text, est_chars, notes?, target_chars_range, target_duration}]}"""

def rewrite_one(
    *,
    asr_index: int,
    prev_text: str,
    overshoot_sec: float,
    new_target_chars_range: tuple[int, int],
    script_segments: list[dict],
    shot_notes: dict,
    av_inputs: dict,
    voice_id: str,
    user_id: int | None = None,
    project_id: str | None = None,
) -> str:
    """调 video_translate.av_rewrite use case,返回新译文。"""
```

- [ ] **Step 3.2:实现 `compute_target_chars_range(target_duration, voice_id, target_language) -> tuple[int, int]`**

```python
def compute_target_chars_range(target_duration, voice_id, target_language):
    from pipeline.speech_rate_model import get_rate
    cps = get_rate(voice_id, target_language)
    if cps is None or cps <= 0:
        cps = FALLBACK_CPS.get(target_language, 14.0)  # 预置 en=14, de=13, fr=14, ja=7, es=14
    lo = max(1, int(cps * target_duration * 0.92))
    hi = max(lo + 1, int(cps * target_duration * 1.08 + 0.5))
    return (lo, hi)
```

- [ ] **Step 3.3:实现 `_merge_global_context(shot_notes, av_inputs) -> dict`**

按 spec "Stage2 Prompt & Schema" 节:
- `product_name` / `brand` / `target_audience` / `extra_info`: overrides 优先,空则 shot_notes.global 对应字段
- `selling_points`: overrides 优先,空则 `shot_notes.global.observed_selling_points`
- `price`: overrides 优先,空则 `shot_notes.global.price_mentioned`
- `category` / `overall_theme` / `pacing_note`: 只从 shot_notes.global
- `structure_ranges`: 由 `shot_notes.global.{hook/demo/proof/cta}_range` 合成一个 list

- [ ] **Step 3.4:实现 `_role_in_structure(asr_index, structure_ranges) -> str`**

优先级 `hook > cta > demo > proof`(hook/cta 最重要;两者重叠罕见但有时会,按 hook>cta>demo>proof 裁决);不落任何 range 返 `"unknown"`。

- [ ] **Step 3.5:实现 `generate_av_localized_translation`**

1. 对每个 script_segment 算 `target_duration = end - start` 和 `target_chars_range`
2. 合并 `global_context`,计算每句 `role_in_structure`
3. 逐句 `shot_context`(从 shot_notes.sentences 按 asr_index 查)
4. 组装 messages(system + user),user 的 JSON 结构见 spec "Stage2 av_translate — Prompt & Schema"
5. 调 `llm_client.invoke_chat("video_translate.av_localize", messages=..., response_format=AV_TRANSLATE_SCHEMA, ...)`
6. 解析返回 sentences,按 asr_index 合并回原 script_segments,补上 `target_chars_range / target_duration`
7. 失败重试 1 次;仍失败抛

- [ ] **Step 3.6:实现 `rewrite_one`**

复用全部上下文,在 user message 里追加 spec 中的"上一版 + overshoot + new_target_chars_range"指令。调用 `llm_client.invoke_chat("video_translate.av_rewrite", ...)`。返回 `sentences[0].text`。

- [ ] **Step 3.7:写 `tests/test_av_translate.py`**

```python
def test_compute_target_chars_range_uses_speech_rate_model(monkeypatch): ...
def test_compute_target_chars_range_falls_back_when_cps_missing(monkeypatch): ...
def test_merge_global_context_overrides_priority(): ...
def test_merge_global_context_shotnotes_fallback(): ...
def test_role_in_structure_priority(): ...
def test_generate_av_localized_translation_happy(monkeypatch):
    # mock llm_client.invoke_chat 返回 {"sentences":[...]}
    # 验返回结构,每句带 target_chars_range / target_duration / role_in_structure
def test_generate_av_retries_on_failure(monkeypatch): ...
def test_rewrite_one_includes_overshoot_in_prompt(monkeypatch): ...
```

- [ ] **Step 3.8:跑测试**

```bash
pytest tests/test_av_translate.py -q -v
```

Expected: 全部 PASS。

- [ ] **Step 3.9:Commit**

```bash
git add pipeline/av_translate.py tests/test_av_translate.py
git commit -m "feat(video-translate-v2): Stage2 av_translate 逐句硬约束翻译 + rewrite_one"
```

---

## Phase 4: 时长闭环(`pipeline/duration_reconcile.py` + `tts.py` 加 speed)

**Files:**
- Modify: `pipeline/tts.py`(`generate_segment_audio` 加 `speed` 参数)
- Create: `pipeline/duration_reconcile.py`
- Create: `tests/test_duration_reconcile.py`

- [ ] **Step 4.1:在 `pipeline/tts.py:generate_segment_audio` 加 `speed: float | None = None` 参数**

```python
def generate_segment_audio(
    text: str,
    voice_id: str,
    output_path: str,
    elevenlabs_api_key: str | None = None,
    model_id: str = "eleven_turbo_v2_5",
    language_code: str | None = None,
    speed: float | None = None,   # 新增
) -> str:
    ...
    kwargs = dict(text=text, voice_id=voice_id, model_id=model_id,
                  output_format="mp3_44100_128")
    if language_code:
        kwargs["language_code"] = language_code
    if speed is not None and abs(speed - 1.0) > 0.001:
        # ElevenLabs voice_settings 传 speed
        from elevenlabs import VoiceSettings
        kwargs["voice_settings"] = VoiceSettings(speed=float(speed))
    audio = client.text_to_speech.convert(**kwargs)
    ...
```

**参考 ElevenLabs Python SDK 文档**:`VoiceSettings(stability=..., similarity_boost=..., speed=...)`。若现有 SDK 不支持 speed 参数,在 `voice_settings` dict 里直接传 `{"speed": 1.05}`。

- [ ] **Step 4.2:创建 `pipeline/duration_reconcile.py`**

核心 API:

```python
def classify_overshoot(target_duration: float, tts_duration: float) -> tuple[str, float]:
    """返回 (status, speed)。按 spec "时长闭环" 节的分支表。"""
    ratio = (tts_duration - target_duration) / target_duration
    if -0.05 <= ratio <= 0.05:
        return ("ok", 1.0)
    if 0.05 < ratio <= 0.15:
        speed = min(1.08, max(1.0, tts_duration / target_duration))
        return ("speed_adjusted", speed)
    if -0.15 <= ratio < -0.05:
        return ("ok_short", 1.0)
    if ratio > 0.15:
        return ("needs_rewrite", 1.0)   # 上层决定重写
    # ratio < -0.15
    return ("warning_short", 1.0)


def reconcile_duration(
    *,
    task,                          # task dict 或 state_json 引用
    av_output: dict,               # generate_av_localized_translation 返回
    tts_output: dict,              # generate_full_audio 返回
    voice_id: str,
    target_language: str,
    av_inputs: dict,
    shot_notes: dict,
    script_segments: list[dict],
    user_id: int | None = None,
    project_id: str | None = None,
    max_rewrite_rounds: int = 2,
) -> dict:
    """对每句做分类;needs_rewrite 走 rewrite_one + regenerate_segment,
    最多 max_rewrite_rounds 轮;仍失败给 warning_overshoot + speed=1.12 兜底硬压。
    返回最终 variants['av'].sentences 数组。"""
```

- [ ] **Step 4.3:实现重写循环**

伪代码:
```
for each sentence where status == needs_rewrite:
    for round in 1..max_rewrite_rounds:
        new_range = old × (target/tts)
        new_text = av_translate.rewrite_one(...)
        regenerate TTS segment
        重新 classify
        if ok / speed_adjusted / ok_short:
            break
    if 仍 needs_rewrite after max rounds:
        status = "warning_overshoot", speed = 1.12
        再 regenerate TTS 一次(带 speed=1.12)
```

- [ ] **Step 4.4:写 `tests/test_duration_reconcile.py`**

```python
import pytest
from pipeline.duration_reconcile import classify_overshoot

@pytest.mark.parametrize("target,tts,expected_status,expected_speed_range", [
    (5.0, 5.0,   "ok",             (1.0, 1.0)),
    (5.0, 5.2,   "ok",             (1.0, 1.0)),     # +4%
    (5.0, 5.4,   "speed_adjusted", (1.0, 1.08)),    # +8%
    (5.0, 5.7,   "speed_adjusted", (1.0, 1.08)),    # +14%
    (5.0, 5.9,   "needs_rewrite",  (1.0, 1.0)),     # +18%
    (5.0, 4.8,   "ok",             (1.0, 1.0)),     # -4%
    (5.0, 4.5,   "ok_short",       (1.0, 1.0)),     # -10%
    (5.0, 4.0,   "warning_short",  (1.0, 1.0)),     # -20%
])
def test_classify_overshoot(target, tts, expected_status, expected_speed_range):
    status, speed = classify_overshoot(target, tts)
    assert status == expected_status
    assert expected_speed_range[0] <= speed <= expected_speed_range[1]


def test_reconcile_duration_rewrite_success(monkeypatch):
    # mock rewrite_one 返回更短文案,mock regenerate_segment 返回更短 duration
    # 验重写一轮后 status=ok

def test_reconcile_duration_rewrite_gives_up(monkeypatch):
    # mock rewrite_one 每次返回同样长文案(无效重写)
    # 验 2 轮后 status=warning_overshoot, speed=1.12
```

- [ ] **Step 4.5:跑测试**

```bash
pytest tests/test_duration_reconcile.py -q -v
pytest tests/ -k "tts" -q   # 确认 tts.py 改动不破坏既有测试
```

Expected: 全部 PASS。

- [ ] **Step 4.6:Commit**

```bash
git add pipeline/tts.py pipeline/duration_reconcile.py tests/test_duration_reconcile.py
git commit -m "feat(video-translate-v2): 时长闭环 duration_reconcile + tts speed 参数"
```

---

## Phase 5: Runtime 集成 + 回滚开关

**Files:**
- Modify: `appcore/runtime.py`
- Modify: `config.py`(新增 `AV_LOCALIZE_FALLBACK` 布尔)
- Test: `tests/test_appcore_runtime.py`

- [x] **Step 5.1:在 `config.py` 加开关**

```python
AV_LOCALIZE_FALLBACK = _env("AV_LOCALIZE_FALLBACK", "0") == "1"
```

- [x] **Step 5.2:在 `appcore/runtime.py` 新增 `run_av_localize(task_id, variant="av")`**

参考现有 `run_localize` 的组织方式。内部顺序:

1. `if AV_LOCALIZE_FALLBACK: return run_localize(task_id, variant="normal")`
2. 读 task state(video_path, script_segments, av_translate_inputs, voice 选择)
3. 校验必填:`target_language` 和 `target_market` 非空,否则 task.status = `failed` 并返回
4. `shot_notes = generate_shot_notes(...)` → 落 `task.state_json.shot_notes`
5. `av_output = generate_av_localized_translation(...)` → 落 `task.state_json.variants["av"].sentences[*].text`
6. `tts_output = generate_full_audio(...)`(用 av_output 生成的 sentences 里的 text + 原 start/end 时间戳)
7. `final_sentences = reconcile_duration(...)` → 覆盖 `variants["av"].sentences`
8. `subtitle.build_srt_from_tts(...)` → `variants["av"].srt_path`
9. 每步落 task state 和 steps 日志

- [x] **Step 5.3:挂到任务 dispatcher**

runtime.py 里一般有 `PIPELINE_BY_TYPE` 或类似 dispatcher,新类型 `"av_translate"` 或沿用现有 `"translate"` 类型 + state 里一个 `"pipeline_version": "av"` 字段。参考同目录其他 run_* 的注册方式。

**确认**:Codex 读 runtime.py 首 300 行找到 dispatcher 注册点,按现有模式追加。

- [x] **Step 5.4:写集成测试 `tests/test_appcore_runtime.py` 追加**

```python
def test_run_av_localize_fallback_to_v1(monkeypatch):
    monkeypatch.setattr("config.AV_LOCALIZE_FALLBACK", True)
    # mock run_localize,验 run_av_localize 调它

def test_run_av_localize_fails_when_market_missing(monkeypatch):
    # av_translate_inputs.target_market = None
    # 验 task.status = "failed"

def test_run_av_localize_happy_flow(monkeypatch):
    # mock 所有阶段,验调用顺序 shot_notes → av_translate → tts → reconcile → subtitle
```

- [x] **Step 5.5:跑测试**

```bash
pytest tests/test_appcore_runtime.py -q -v
pytest tests/ -q   # 整体回归,期望不引入失败
```

Expected: 全部 PASS。

- [x] **Step 5.6:Commit**

```bash
git add appcore/runtime.py config.py tests/test_appcore_runtime.py
git commit -m "feat(video-translate-v2): runtime 集成 run_av_localize + AV_LOCALIZE_FALLBACK 开关"
```

---

## Phase 6: 任务创建表单

**Files:**
- Modify: `web/routes/`(视频翻译任务创建接口,具体文件 Codex 从 `web/routes/` 里找,应在 `projects.py` 或 `medias.py` 附近)
- Modify: 对应 `web/static/*.js` 和 `web/templates/*.html`

- [x] **Step 6.1:定位任务创建路由**

```bash
grep -rn "video_translate" web/routes/ | head -20
grep -rn "run_localize\|create.*project.*translate" web/routes/ | head -20
```

找到接收视频翻译任务创建的 POST 路由,读当前字段。

- [x] **Step 6.2:路由层接收新字段**

在 POST handler 里新增读取(使用 request.form.get / request.json.get,看现有代码风格):

```python
av_inputs = {
    "target_language": (request.form.get("target_language") or "").strip(),
    "target_language_name": _LANG_NAME_MAP.get(target_language, target_language),
    "target_market": (request.form.get("target_market") or "").strip(),
    "product_overrides": {
        "product_name": (request.form.get("override_product_name") or "").strip() or None,
        "brand": (request.form.get("override_brand") or "").strip() or None,
        "selling_points": _parse_list(request.form.get("override_selling_points")),
        "price": (request.form.get("override_price") or "").strip() or None,
        "target_audience": (request.form.get("override_target_audience") or "").strip() or None,
        "extra_info": (request.form.get("override_extra_info") or "").strip() or None,
    },
}
task_state["av_translate_inputs"] = av_inputs
```

必填校验:`target_language` 和 `target_market` 空 → 返回 400 错误。

- [x] **Step 6.3:前端表单加字段**

找到视频翻译任务创建的模板/JS,加:
- `<select name="target_language">`:填充现有支持语种列表(参考 `pipeline/speech_rate_model.py` 的 `BENCHMARK_TEXT` keys:en/de/fr/ja/es/pt)
- `<select name="target_market">`:`US/UK/AU/CA/SEA/JP/OTHER`
- 折叠区 `<details>` 或折叠卡片"带货资料微调(可选,留空自动识别)"内的 6 个 input

**UI 风格**:遵循项目 CLAUDE.md 的"Frontend Design System — Ocean Blue Admin"(深海蓝+大圆角+OKLCH token)。

- [x] **Step 6.4:手动冒烟**

```bash
# 启动本地服务
python run.py   # 或项目实际启动命令
# 浏览器打开任务创建页,提交一个测试任务,后端日志看到 av_translate_inputs
# 检查数据库 projects.state_json 字段包含新字段
```

- [x] **Step 6.5:Commit**

```bash
git add web/
git commit -m "feat(video-translate-v2): 任务创建表单加 target_language/market/产品 overrides"
```

---

## Phase 7: 任务详情页(画面笔记预览 + 时长警告列表)

**Files:**
- Modify: 任务详情页模板(`web/templates/`)和 JS(`web/static/`)
- 可能新增: 单句重写接口 `web/routes/<something>:POST /api/tasks/<id>/av/rewrite_sentence`

- [x] **Step 7.1:画面笔记预览卡片**

读 `task.state_json.shot_notes`,展示:
- 卡片头:`shot_notes.global.product_name / category / overall_theme`
- 结构分段:`hook_range / demo_range / proof_range / cta_range`
- 可展开"逐句画面笔记"表格:asr_index / scene / action / product_visible(图标) / shot_type

- [x] **Step 7.2:时长警告列表**

读 `task.state_json.variants["av"].sentences`,筛 `status in {"warning_overshoot", "warning_short"}`:
- 表格列:asr_index / target_duration / tts_duration / 偏差 % / 当前译文 / 操作
- 操作按钮:"手动重写"(弹窗显示原译文 textarea,运营修改提交 → 后端重跑 TTS)

- [x] **Step 7.3:单句重写后端接口**

```python
@bp.route("/api/tasks/<task_id>/av/rewrite_sentence", methods=["POST"])
def av_rewrite_sentence(task_id):
    data = request.get_json()
    asr_index = int(data["asr_index"])
    new_text = data["text"].strip()
    # 1. 更新 task.state_json.variants["av"].sentences[idx].text = new_text
    # 2. 调 tts.generate_segment_audio 重生成该 segment
    # 3. 重新计算 classify_overshoot,更新 status/speed
    # 4. 重新 stitch full audio + rebuild SRT
    return jsonify({"ok": True, "status": new_status, "tts_duration": new_dur})
```

- [x] **Step 7.4:手动冒烟**

- 创建测试任务 → 跑通 v2 → 详情页能看到画面笔记预览
- 预期有 warning 的句子(可以人工制造短文案或改 target_duration)
- 点"手动重写"修改,验证 SRT + 音频文件时间戳更新

- [x] **Step 7.5:Commit**

```bash
git add web/
git commit -m "feat(video-translate-v2): 详情页画面笔记预览 + 时长警告 + 单句重写"
```

---

## Phase 8: 集成验证 + 收尾

- [ ] **Step 8.1:选 3 段线上视频人工测试**

- 好档:已知转化高的样片(30-60s)
- 中档:普通带货视频
- 差档:Hook/画面脱节较明显的样片
- 各跑一次 v1(老 localize)和 v2(av_localize),人工打分:
  - 声画同步(当下画面 vs 译文讲的事)5 分制
  - 带货感(Hook / 痛点 / 卖点 / CTA 是否清晰)5 分制
  - 时长偏差(warning 数 / 总句数)

- [ ] **Step 8.2:写验收报告**

在 `docs/superpowers/specs/` 或任务详情备注里记录对比结果;如果 v2 声画同步主观评分 > v1 且 warning 比例 ≤ 10%,视为验收通过。

- [ ] **Step 8.3:跑完整测试回归**

```bash
pytest tests/ -q
```

Expected: v2 触及测试全绿(Phase 1-7 新增 + 改动波及的老测试)。
26 个 C 类 baseline 老失败不在本 PR 范围,见 `docs/superpowers/notes/2026-04-21-pytest-baseline-failures.md`

- [ ] **Step 8.4:合并前同步 master**

```bash
git fetch origin master
git rebase origin/master     # 解决可能的冲突
pytest tests/ -q             # 再跑一次
```

- [ ] **Step 8.5:Commit 收尾(如有验收报告)并告知可合并**

---

## Self-Review 检查

Codex 在执行过程中如果发现 spec 或 plan 中:
- **类型/字段名不一致**(如 plan 里叫 `target_chars_range` spec 里叫 `char_range`):**停止,告警,请 Claude 更新 spec**
- **发现 spec 遗漏**(如某个字段用的地方没定义):**停止,告警,请 Claude 更新 spec**
- **性能/可行性问题**(如 ElevenLabs SDK 不支持 `VoiceSettings.speed`):**先记 TODO,Phase 4 兜底用 ffmpeg atempo 代替**

## 退出标准

全部 8 个 Phase 完成后:
- worktree 分支 `feature/video-translate-av-sync` 上:spec commit + 8 个实现 commit
- `pytest tests/ -q` 全绿
- Phase 8 验收报告:v2 声画同步 > v1,warning 比例 ≤ 10%
- 通知 Claude 发起 PR 合并到 master
