# 英语/德语/法语视频翻译：TTS 音频时长与视频时长对齐（迭代收敛方案）

日期：2026-04-16
范围：英语（`PipelineRunner` 基类）、德语（`DeTranslateRunner`）、法语（`FrTranslateRunner`）三个模块同步改造

## 背景

当前 en/de/fr 主流程 9 步：

```
extract → asr → alignment → translate → tts → subtitle → compose → export → analysis
```

`_step_tts` 一次性生成整段音频，没有任何时长约束。产出的 TTS 音轨常与原视频时长不一致：

- 音频 > 视频：`compose` 阶段按 `timeline_manifest` 顺序消费视频帧，视频耗尽后最后一帧被 `tpad stop_mode=clone` 冻帧到音频结束 → 视觉上"末段静止一两秒"，剪辑师处理困难。
- 音频 < 视频：合成后末尾视频段没有配音，空洞感。

## 目标

最终音频时长严格落在 `[video_duration - 3, video_duration]` 区间。

为此引入一个"迭代收敛循环"：若首次生成不在区间内，调用 LLM 按目标字符数重写译文并重新生成 TTS，最多 3 轮，仍未收敛则任务失败。整个过程对前端可见：显示当前处于哪一轮、哪个子阶段、每一轮的完整中间文件都可查看下载。

en/de/fr 三个模块共享同一套时长控制实现（本次顺带把重复的 `_step_tts` 抽回基类）。

## 非目标

- 不动 v2 流水线（`runtime_v2.py` / `PipelineRunnerV2`，translate_lab 专用）
- 不改 subtitle / compose / export / analysis / capcut / TOS upload 等下游模块——通过"最终产物覆盖到原文件名"保证下游不变
- 不做 DB schema 迁移；`tts_duration_rounds` 等新字段仅在内存 task state 里存
- 不改 ElevenLabs 调用参数（stability/style/speed），完全靠文案字符数控制时长

## 关键设计决策

| 决策点 | 选择 | 说明 |
|---|---|---|
| 区间约束方向 | **双向硬约束** | 音频 `> video` → shrink；音频 `< video - 3` → expand |
| 3 轮仍未收敛 | **任务失败** | `status = error`，`EVT_PIPELINE_ERROR`，前端提示用户手工介入（换 voice / 改 prompt） |
| 收敛加速 | **自适应过矫正** | Round 3 的 target 基于 round 2 偏差的 0.5 倍反向矫正，并 clamp 在区间内 0.3s 安全距离 |
| 重写粒度 | **最上游 `localized_translation`** | 每轮 LLM 重写译文 → 重跑 `tts_script` → 重跑 ElevenLabs |
| 架构形态 | **融入现有 `tts` step** | STEP_ORDER 不动；迭代日志走新事件 `EVT_TTS_DURATION_ROUND` + 前端子组件 |
| 代码组织 | **`_step_tts` 抽回基类** | 消除 en/de/fr 三份高度重复的实现 |

## 主流程变化

```
改前：... → translate → tts（一次性生成整段） → subtitle → ...
改后：... → translate → tts（1..3 轮迭代收敛） → subtitle → ...
```

`tts` 步骤内部逻辑：

```
round = 1
while round ≤ 3:
    if round == 1:
        # 初始轮：复用 translate step 已有的 localized_translation
        localized_translation = task.localized_translation
    else:
        # rewrite：LLM 按 target_chars 改写
        target_duration, target_chars, direction = _compute_next_target(...)
        emit(phase="translate_rewrite", round=round, target_chars=target_chars, direction=direction)
        localized_translation = _rewrite_localized_translation(
            prev=prev_localized_translation,
            source_full_text=source_full_text,
            target_chars=target_chars,
            direction=direction,
        )
        save(f"localized_translation.round_{round}.json")

    emit(phase="tts_script_regen", round=round)
    tts_script = generate_tts_script(localized_translation, messages_builder=<lang-specific>)
    save(f"tts_script.round_{round}.json")

    emit(phase="audio_gen", round=round)
    result = generate_full_audio(tts_segments, voice, ..., variant=f"round_{round}")
    save(f"tts_full.round_{round}.mp3")

    emit(phase="measure", round=round, audio_duration=..., char_count=...)
    update_speech_rate_model(voice, language, chars=..., duration_seconds=...)

    if duration_lo ≤ audio_duration ≤ duration_hi:
        emit(phase="converged", round=round)
        break
    round += 1

if not converged:
    raise RuntimeError("TTS 音频时长 3 轮内未收敛到目标区间")

# 最终产物覆盖到不带 round 后缀的标准文件名（保持下游兼容）
copy(f"tts_full.round_{final}.mp3" → "tts_full.normal.mp3")
write("tts_script.normal.json", tts_script_final)
write("localized_translation.normal.json", localized_translation_final)
timeline_manifest = build_timeline_manifest(result.segments, video_duration)
save("timeline_manifest.normal.json")
```

## 详细设计

### 1. 收敛算法

**区间定义**：
- `video_duration = pipeline.extract.get_video_duration(video_path)`
- `duration_lo = max(0, video_duration - 3)`
- `duration_hi = video_duration`
- `center = video_duration - 1.5`

**语速系数 cps**（每 voice × language 一份）：
- 首选 `pipeline.speech_rate_model.get_rate(voice_id, language)`
- 无样本时退化为本轮实测 `len(tts_script.full_text) / audio_duration`
- 每轮 measure 后 `update_rate(voice_id, language, chars=..., duration_seconds=...)` 增量合并

**`_compute_next_target(round_index, last_audio_duration, cps, video_duration)`**（仅 round ≥ 2 调用）：

```python
duration_lo = max(0, video_duration - 3)
duration_hi = video_duration
center = video_duration - 1.5

if round_index == 2:
    if last_audio_duration > duration_hi:
        target_duration = video_duration - 2.0
        direction = "shrink"
    else:  # last_audio_duration < duration_lo
        target_duration = video_duration - 1.0
        direction = "expand"
elif round_index == 3:
    # 自适应过矫正：反向 0.5 倍偏差，clamp 在区间内侧 0.3s
    raw = center - 0.5 * (last_audio_duration - center)
    target_duration = max(duration_lo + 0.3, min(duration_hi - 0.3, raw))
    direction = "shrink" if last_audio_duration > center else "expand"

target_chars = max(10, round(target_duration * cps))
return target_duration, target_chars, direction
```

**`target_chars` 的 cps 口径**：基于 `tts_script.full_text` 的字符数（含空格标点）。与 `speech_rate_model` 现有口径一致。

### 2. LLM Rewrite 机制

**`_rewrite_localized_translation(prev, source_full_text, target_chars, direction, ...)`**：

- 输入：
  - `prev`: 上一轮的 `localized_translation` dict（`{full_text, sentences[...]}`）
  - `source_full_text`: translate step 的 `source_full_text_zh`（中/英原文）
  - `target_chars`: `_compute_next_target` 算出的目标字符数
  - `direction`: `"shrink"` 或 `"expand"`
- 调用：复用 `generate_localized_translation()` 机制（provider、schema 校验），但 messages 走新 builder `build_localized_rewrite_messages`
- 输出：新 `localized_translation`，格式与原翻译完全一致（`{full_text, sentences[{index, text, source_segment_indices}]}`），带 `_usage`

**新增 system prompt**（每语言一份，继承原本土化规则）：

- `pipeline/localization.py::LOCALIZED_REWRITE_SYSTEM_PROMPT`（英语）
- `pipeline/localization_de.py::LOCALIZED_REWRITE_SYSTEM_PROMPT`（德语）
- `pipeline/localization_fr.py::LOCALIZED_REWRITE_SYSTEM_PROMPT`（法语）

每份 prompt 结构：
1. 复刻原 `LOCALIZED_TRANSLATION_SYSTEM_PROMPT` 的本土化规则、风格、结构要求
2. 追加 rewrite 约束：
   ```
   You are REWRITING a previous translation to match a target character count.
   Target character count: approximately {target_chars} characters (±5%, measured on full_text).
   Direction: {direction}
     - "shrink": remove modifiers and repetitions while preserving all facts and the core benefit.
     - "expand": add natural phrasing (examples, relatable details, light elaboration); preserve all facts; do NOT invent new claims.
   Keep the same sentence count as the previous translation when possible.
   Preserve all source_segment_indices mappings from the previous translation's sentences where applicable.
   ```
3. 输出 schema 说明 = 原 schema

**新增 messages builder**（每语言一份）：

```python
# 新函数（各 localization 模块）
def build_localized_rewrite_messages(
    source_full_text: str,
    prev_localized_translation: dict,
    target_chars: int,
    direction: str,  # "shrink" | "expand"
    source_language: str = "zh",
) -> list[dict]:
    lang_label = {"zh": "Chinese", "en": "English"}.get(source_language, source_language)
    return [
        {"role": "system", "content": LOCALIZED_REWRITE_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Source {lang_label} full text (for reference):\n{source_full_text}\n\n"
            f"Previous translation (to rewrite):\n{json.dumps(prev_localized_translation, ensure_ascii=False, indent=2)}\n\n"
            f"Target character count: {target_chars}\n"
            f"Direction: {direction}"
        )},
    ]
```

**`pipeline.translate` 扩展**：

`generate_localized_translation()` 现有签名接受 `messages_builder` 参数吗？若不接受，新增 `generate_localized_rewrite()` 并行函数，或在 `generate_localized_translation` 加 `messages_builder` 参数。实现时查源码再定（优先扩参，避免增加函数）。

### 3. 后端架构

#### 3.1 `PipelineRunner` 基类抽取

当前 en 的 `_step_tts` 在基类，de/fr 各自 override。将 `_step_tts` 的**差异点**抽成类变量：

```python
class PipelineRunner:
    project_type: str = "translation"

    # ── TTS / 本土化差异点 ────────────────────────
    tts_language_code: str | None = None          # ElevenLabs language_code；None=让模型自检
    tts_model_id: str = "eleven_turbo_v2_5"       # ElevenLabs model_id
    tts_default_voice_language: str | None = None # voice_library.ensure_defaults 的 language；None=en 默认
    localization_module: str = "pipeline.localization"
    target_language_label: str = "en"             # 中文消息中展示的目标语言
    # ──────────────────────────────────────────
```

de/fr runner 只保留类变量 override，不再 override `_step_tts`：

```python
class DeTranslateRunner(PipelineRunner):
    project_type = "de_translate"
    tts_language_code = "de"
    tts_model_id = "eleven_multilingual_v2"
    tts_default_voice_language = "de"
    localization_module = "pipeline.localization_de"
    target_language_label = "de"

class FrTranslateRunner(PipelineRunner):
    project_type = "fr_translate"
    tts_language_code = "fr"
    tts_model_id = "eleven_multilingual_v2"
    tts_default_voice_language = "fr"
    localization_module = "pipeline.localization_fr"
    target_language_label = "fr"
```

de/fr runner 保留 `_step_asr`（language detect）、`_step_translate`（语言专属 prompt & 消息）、`_step_subtitle`（语言专属 WEAK_STARTERS / 法语标点后处理）的 override——本次**不碰这三步**。

#### 3.2 `_step_tts` 新实现（基类）

```python
def _step_tts(self, task_id: str, task_dir: str) -> None:
    task = task_state.get(task_id)
    loc_mod = importlib.import_module(self.localization_module)

    self._set_step(task_id, "tts", "running",
                   f"正在生成{_lang_name(self.target_language_label)}配音...")

    from appcore.api_keys import resolve_key
    from pipeline.extract import get_video_duration
    from pipeline.speech_rate_model import get_rate, update_rate
    from pipeline.tts import generate_full_audio, get_voice_by_id

    provider = _resolve_translate_provider(self.user_id)
    elevenlabs_api_key = resolve_key(self.user_id, "elevenlabs", "ELEVENLABS_API_KEY")

    voice = self._resolve_voice(task, loc_mod)
    video_duration = get_video_duration(task["video_path"])

    variant = "normal"
    variants = dict(task.get("variants", {}))
    variant_state = dict(variants.get(variant, {}))
    initial_localized_translation = variant_state.get("localized_translation", {})
    source_full_text = task.get("source_full_text_zh") or task.get("source_full_text", "")

    # 初始化迭代状态
    task_state.update(task_id, tts_duration_rounds=[], tts_duration_status="running")

    loop_result = self._run_tts_duration_loop(
        task_id=task_id,
        task_dir=task_dir,
        loc_mod=loc_mod,
        provider=provider,
        video_duration=video_duration,
        voice=voice,
        initial_localized_translation=initial_localized_translation,
        source_full_text=source_full_text,
        elevenlabs_api_key=elevenlabs_api_key,
        script_segments=task.get("script_segments", []),
        variant=variant,
    )
    # loop_result = {
    #   "localized_translation": dict,
    #   "tts_script": dict,
    #   "tts_audio_path": str (final round's tts_full.round_N.mp3),
    #   "tts_segments": list,
    #   "rounds": list[dict],  # 每轮的完整记录
    #   "final_round": int,
    # }

    # 复制最终产物到 normal 文件名（下游兼容）
    self._promote_final_artifacts(task_dir, loop_result, variant)
    timeline_manifest = build_timeline_manifest(
        loop_result["tts_segments"], video_duration=video_duration
    )

    # 更新 task_state（与既有行为一致）
    variant_state.update({
        "segments": loop_result["tts_segments"],
        "tts_script": loop_result["tts_script"],
        "tts_audio_path": os.path.join(task_dir, f"tts_full.normal.mp3"),
        "timeline_manifest": timeline_manifest,
        "voice_id": voice.get("id"),
        "localized_translation": loop_result["localized_translation"],
    })
    variants[variant] = variant_state
    task_state.set_preview_file(task_id, "tts_full_audio", variant_state["tts_audio_path"])

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
        tts_audio_path=variant_state["tts_audio_path"],
        voice_id=voice.get("id"),
        timeline_manifest=timeline_manifest,
        localized_translation=loop_result["localized_translation"],
        tts_duration_rounds=loop_result["rounds"],
        tts_duration_status="converged",
    )

    task_state.set_artifact(task_id, "tts",
        build_tts_artifact(loop_result["tts_script"], loop_result["tts_segments"],
                          duration_rounds=loop_result["rounds"]))
    self._emit(task_id, EVT_TTS_SCRIPT_READY, {"tts_script": loop_result["tts_script"]})
    self._set_step(task_id, "tts", "done",
                   f"{_lang_name(self.target_language_label)}配音生成完成（{loop_result['final_round']} 轮收敛）")

    # usage log（rewrite 次数累加）
    ...
```

**`_run_tts_duration_loop`** — 核心迭代循环：

```python
def _run_tts_duration_loop(self, task_id, task_dir, loc_mod, provider,
                            video_duration, voice, initial_localized_translation,
                            source_full_text, elevenlabs_api_key, script_segments,
                            variant) -> dict:
    from pipeline.speech_rate_model import get_rate, update_rate
    from pipeline.tts import generate_full_audio, _get_audio_duration
    from pipeline.translate import generate_tts_script, generate_localized_translation

    MAX_ROUNDS = 3
    duration_lo = max(0.0, video_duration - 3.0)
    duration_hi = video_duration

    rounds: list[dict] = []
    prev_localized = initial_localized_translation
    last_audio_duration = 0.0

    for round_index in range(1, MAX_ROUNDS + 1):
        round_record = {"round": round_index, "phases": {}, "artifact_paths": {}}

        # Phase 1: translate_rewrite (round 1 跳过)
        if round_index == 1:
            localized_translation = prev_localized
        else:
            cps = get_rate(voice["elevenlabs_voice_id"], self.tts_language_code or "en") \
                  or (last_char_count / last_audio_duration if last_audio_duration > 0 else 15.0)
            target_duration, target_chars, direction = self._compute_next_target(
                round_index, last_audio_duration, cps, video_duration)
            round_record["target_duration"] = target_duration
            round_record["target_chars"] = target_chars
            round_record["direction"] = direction

            self._emit_duration_round(task_id, round_index, "translate_rewrite",
                                      {**round_record, "message": f"正在重写译文（目标 {target_chars} 字符，{direction}）"})
            localized_translation = generate_localized_translation(
                source_full_text=source_full_text,
                script_segments=script_segments,
                variant=variant,
                provider=provider, user_id=self.user_id,
                messages_builder_kwargs={
                    "mode": "rewrite",
                    "prev_localized_translation": prev_localized,
                    "target_chars": target_chars,
                    "direction": direction,
                },
                localization_module=loc_mod,
            )
            _save_json(task_dir, f"localized_translation.round_{round_index}.json", localized_translation)
            round_record["artifact_paths"]["localized_translation"] = f"localized_translation.round_{round_index}.json"

        # Phase 2: tts_script_regen
        self._emit_duration_round(task_id, round_index, "tts_script_regen", round_record)
        tts_script = generate_tts_script(
            localized_translation,
            provider=provider, user_id=self.user_id,
            messages_builder=loc_mod.build_tts_script_messages,
            validator=_get_validator(loc_mod),
        )
        _save_json(task_dir, f"tts_script.round_{round_index}.json", tts_script)
        round_record["artifact_paths"]["tts_script"] = f"tts_script.round_{round_index}.json"

        # Phase 3: audio_gen
        self._emit_duration_round(task_id, round_index, "audio_gen", round_record)
        tts_segments = loc_mod.build_tts_segments(tts_script, script_segments)
        round_variant = f"round_{round_index}"  # → tts_full.round_{N}.mp3
        result = generate_full_audio(
            tts_segments, voice["elevenlabs_voice_id"], task_dir,
            variant=round_variant,
            elevenlabs_api_key=elevenlabs_api_key,
            model_id=self.tts_model_id,
            language_code=self.tts_language_code,
        )
        # generate_full_audio 命名规则：tts_full.{variant}.mp3 + tts_segments/{variant}/seg_*.mp3
        # 本循环固定 variant = "round_{N}" → tts_full.round_{N}.mp3 + tts_segments/round_{N}/seg_*.mp3
        round_record["artifact_paths"]["tts_full_audio"] = f"tts_full.round_{round_index}.mp3"

        # Phase 4: measure
        audio_duration = _get_audio_duration(result["full_audio_path"])
        char_count = len(tts_script.get("full_text", ""))
        update_rate(voice["elevenlabs_voice_id"],
                    self.tts_language_code or "en",
                    chars=char_count, duration_seconds=audio_duration)

        round_record["audio_duration"] = audio_duration
        round_record["char_count"] = char_count
        round_record["video_duration"] = video_duration
        round_record["duration_lo"] = duration_lo
        round_record["duration_hi"] = duration_hi

        self._emit_duration_round(task_id, round_index, "measure", round_record)

        # 持久化（每轮 measure 后）
        rounds.append(round_record)
        task_state.update(task_id, tts_duration_rounds=rounds)

        # 收敛判断
        if duration_lo <= audio_duration <= duration_hi:
            self._emit_duration_round(task_id, round_index, "converged", round_record)
            return {
                "localized_translation": localized_translation,
                "tts_script": tts_script,
                "tts_audio_path": result["full_audio_path"],
                "tts_segments": result["segments"],
                "rounds": rounds,
                "final_round": round_index,
            }

        # 未收敛，准备下一轮
        prev_localized = localized_translation
        last_audio_duration = audio_duration
        last_char_count = char_count

    # 3 轮都没收敛
    self._emit_duration_round(task_id, MAX_ROUNDS, "failed", round_record)
    task_state.update(task_id, tts_duration_status="failed")
    raise RuntimeError(
        f"TTS 音频时长 {MAX_ROUNDS} 轮内未收敛到 [{duration_lo:.1f}, {duration_hi:.1f}] 区间，"
        f"最后一次为 {last_audio_duration:.1f}s。请调整 voice 或翻译 prompt 后重试。"
    )
```

#### 3.3 `generate_full_audio` 的文件命名

当前 `pipeline.tts.generate_full_audio` 以 `tts_full.{variant}.mp3` 命名。直接传 `variant="round_1"` 会输出 `tts_full.round_1.mp3`，符合预期。

但 `tts_segments/{variant}/seg_0000.mp3` 也会按 variant 目录分开（每轮一个目录）——这正好满足"每轮的单段文件都独立留档"。

**最终产物"提升到标准文件名"的实现**（`_promote_final_artifacts`）：

```python
def _promote_final_artifacts(self, task_dir: str, loop_result: dict, variant: str) -> None:
    import shutil
    final_round = loop_result["final_round"]
    src = os.path.join(task_dir, f"tts_full.round_{final_round}.mp3")
    dst = os.path.join(task_dir, f"tts_full.{variant}.mp3")  # tts_full.normal.mp3
    shutil.copy2(src, dst)
    # tts_segments 目录不复制，下游不依赖 normal 目录
```

subtitle step 读 `variant_state["tts_audio_path"]`——指向 `tts_full.normal.mp3`（已覆盖），无需改 subtitle。

### 4. `pipeline.translate` 扩展

检查 `generate_localized_translation` 现有签名，以决定如何注入 rewrite messages：

- **路径 A**（推荐）：给 `generate_localized_translation` 增加可选参数 `messages_builder_kwargs: dict | None = None`。内部选择 builder 时若 `mode == "rewrite"`，调用 `build_localized_rewrite_messages(...)`；否则走现有 `build_localized_translation_messages(...)`。这样调用点统一。
- **路径 B**：新增 `generate_localized_rewrite()` 并行函数。代码略多，但职责分离更清。

实现时先读代码决定。**Spec 默认路径 B**（更清晰），若发现现有抽象已足够支持 A 则采用 A。

### 5. 前端事件与子组件

#### 5.1 新事件

- 常量 `EVT_TTS_DURATION_ROUND = "tts_duration_round"` 加到 `appcore/events.py`
- web socketio 适配器（`web/services/*_pipeline_runner.py` 或统一 `_make_socketio_handler`）把该事件转发到 room

payload 结构：

```json
{
  "round": 2,
  "phase": "translate_rewrite",
  "video_duration": 32.15,
  "duration_lo": 29.15,
  "duration_hi": 32.15,
  "target_duration": 30.15,
  "target_chars": 450,
  "direction": "shrink",
  "audio_duration": null,        // measure/converged/failed 后才填
  "char_count": null,
  "artifact_paths": {
      "localized_translation": "localized_translation.round_2.json",
      "tts_script": "tts_script.round_2.json",
      "tts_full_audio": "tts_full.round_2.mp3"
  },
  "message": "正在重写德语译文（目标 450 字符，shrink）"
}
```

phase 取值：
- `translate_rewrite` — 正在调 LLM 重写译文
- `tts_script_regen` — 正在调 LLM 切朗读块
- `audio_gen` — 正在调 ElevenLabs 生成音频
- `measure` — 测量音频时长完成，未判定收敛
- `converged` — 本轮落在区间内，循环结束
- `failed` — 达到 3 轮仍未收敛

#### 5.2 `task_state` 新字段

- `tts_duration_rounds: list[dict]` — 累积每轮完整记录（与 payload 结构一致）
- `tts_duration_status: "running" | "converged" | "failed"`

在 `task_state.create(...)` 初值中加 `tts_duration_rounds=[]`、`tts_duration_status=None`。老任务初始缺这两个字段，前端按空/None 处理。

#### 5.3 前端组件

**位置**：`_task_workbench.html` 的 `step-tts` 卡片 preview 区插入一个 `<div id="ttsDurationLog" class="duration-log"></div>`。

**渲染规则**（`_task_workbench_scripts.html`）：
- 仅当 `currentTask.tts_duration_rounds?.length > 0` 时渲染
- 一轮收敛（`length === 1`）时仍渲染但用压缩样式（一行即可）
- 多轮时按时间线展开，每轮显示：
  - 轮次编号 + 状态图标（✓/⟳/✗/待执行）
  - target（shrink/expand 时显示）
  - 音频时长 + 区间对比
  - 三个下载链接（译文 / 朗读文案 / 音频）
- 有 phase 正在 running 时在该轮下方显示一行"正在 XXX..."

**事件订阅**（加到 `_task_workbench_scripts.html` 现有 socket block）：

```js
socket.on("tts_duration_round", payload => {
  if (!currentTask) return;
  currentTask.tts_duration_rounds = currentTask.tts_duration_rounds || [];
  _upsertRound(currentTask.tts_duration_rounds, payload);
  currentTask.tts_duration_status =
      payload.phase === "converged" ? "converged"
    : payload.phase === "failed"    ? "failed"
    : "running";
  renderTtsDurationLog();
});
```

`_upsertRound` 按 `payload.round` 查找、合并 phase 记录。

**样式**：沿用现有 ocean-blue design tokens（`--accent`、`--warning`、`--success`、`--danger`），不引入新色。圆角 `--radius-md`，间距 `--space-3`。

### 6. 下载接口扩展

`web/routes/task.py` / `de_translate.py` / `fr_translate.py` 的 `/download/<key>` 路由需要放行：

- `tts_full.round_{1,2,3}.mp3`
- `localized_translation.round_{1,2,3}.json`
- `tts_script.round_{1,2,3}.json`

实现方式：
- 如果现有路由用白名单映射，追加这些 key 的 `→ filename` 映射
- 如果用 preview_files 字典，也新增对应 key（或通过 artifact 的 `artifact_paths` 直接暴露文件路径由前端拼 URL）

细节在实现阶段读代码确认。

### 7. 中间文件落盘清单

| 文件 | 时机 | 保留策略 |
|---|---|---|
| `task_dir/localized_translation.round_{N}.json` | 每轮 rewrite 后（round 1 无） | 任务生命期保留 |
| `task_dir/tts_script.round_{N}.json` | 每轮 tts_script_regen 后 | 任务生命期保留 |
| `task_dir/tts_full.round_{N}.mp3` | 每轮 audio_gen 后 | 任务生命期保留 |
| `task_dir/tts_segments/round_{N}/seg_*.mp3` | 每轮 audio_gen 后 | 任务生命期保留（磁盘占用） |
| `task_dir/tts_duration_rounds.json` | 每轮 measure 后累积写入 | 任务生命期保留 |
| `task_dir/tts_full.normal.mp3`（最终） | 收敛后 copy | 下游依赖 |
| `task_dir/tts_script.normal.json`（最终） | 收敛后 write | 下游依赖 |
| `task_dir/localized_translation.normal.json`（最终） | 收敛后 write | 下游依赖 |
| `task_dir/timeline_manifest.normal.json`（最终） | 收敛后 write | 下游依赖 |

磁盘占用：单任务最多 3 份音频 + 3 份 tts_segments 目录，大致翻倍。实际影响有限（单任务通常 < 100MB）。

### 8. 错误与边界

- **LLM rewrite 返回 JSON 不合规**：`generate_localized_translation` 现有校验会抛错 → `_run` 外层捕获 → `status=error`，不消耗 3 次预算
- **LLM 返回空 sentences 数组**：同上，格式校验失败即报错
- **ElevenLabs 调用失败**：抛异常 → `status=error`，不走完 3 轮
- **`target_chars < 10`**：clamp 到 10（公式里已有 `max(10, ...)`）；若语速模型 cps 异常大导致 target_chars 过小，至少仍有文本 rewrite
- **`video_duration ≤ 3`**：`duration_lo = 0`，只约束不超过 video_duration
- **`video_duration < 1`**：仍按流程跑，极端短任务可能在 round 3 仍失败 → fail loud
- **sentences 数量变化**：rewrite 后 `sentences.length` 可能与原不同——无副作用（tts_script 按新 sentences 重新生成）
- **交互式人工确认**（`interactive_review=True`）：translate step 等待人工 → 人工确认后 tts 自动跑循环，不再额外交互（每轮不弹确认框）

### 9. 数据兼容

- **老任务的 tts 产物**（无 `tts_duration_rounds` 字段）：前端默认 `[]`，迭代组件不渲染。老任务 `tts_full.normal.mp3` / `tts_script.normal.json` 照旧可用
- **老任务 `task_state`** 无 `tts_duration_rounds` / `tts_duration_status`：前端 `??` 运算符兜底
- **新任务单轮收敛**：仍写 `tts_full.round_1.mp3` + copy 到 `tts_full.normal.mp3`；`tts_duration_rounds` 只有 1 条记录，前端可选择压缩展示或隐藏迭代组件
- **`/download/soft` 等既有路由**：不动
- **task_state 初值**：`task_state.create(...)` 里加 `tts_duration_rounds=[]`、`tts_duration_status=None`，与 `steps` 初值风格一致（老任务从数据库/内存读取时若无此字段，访问返回默认）

## 测试策略

### 单元测试

- `tests/test_tts_duration_loop.py`（新）
  - round 1 就收敛（mock ElevenLabs 返回时长恰好在区间内）
  - round 2 shrink 收敛
  - round 2 expand 收敛
  - round 3 自适应过矫正后收敛
  - round 3 仍未收敛 → raise RuntimeError
  - `_compute_next_target` 各分支、clamp 边界（video < 3s、极端 last_duration）
  - cps fallback：模型无样本时用实测
  - `update_rate` 每轮被调一次
- `tests/test_localized_rewrite.py`（新）
  - 3 种语言 `build_localized_rewrite_messages` 的字段完整性与 target_chars / direction 注入
  - rewrite prompt 不漏关键规则（对比原 prompt 关键字）
- `tests/test_runner_tts_base.py`（新）
  - `PipelineRunner._step_tts` 被调用时正确读取类变量（`tts_language_code` 等）
  - 一次收敛场景不触发 rewrite
- `tests/test_runner_de.py` / `test_runner_fr.py`（增补）
  - de/fr runner 通过类变量 override 正确走入基类 `_step_tts` 循环
  - de fr 各自的 localization_module 被正确加载
- `tests/test_events.py`（增补）
  - `EVT_TTS_DURATION_ROUND` 常量存在且值不冲突

### 集成测试

- `tests/test_task_routes.py`（增补）
  - `GET /download/tts_full.round_1.mp3` 等新 key 的鉴权、存在检查、404 行为
- `tests/test_de_translate_routes.py` / `test_fr_translate_routes.py`（增补）
  - 同上

### 手工 QA

1. 建一个新的 **en** 项目（原视频 30s 左右），run 完整主流程：
   - 首次 tts 若恰好落在 [27, 30]：迭代日志只 1 行，tts 卡片文案"1 轮收敛"
   - 若首次 35s：前端能看到 round 2 的"正在重写英语译文" → "正在切朗读块" → "正在生成音频" → "audio 30.1s ✓ 收敛"，下载链接均可用
2. **de** 项目重复同样流程（含中/英文源）
3. **fr** 项目重复同样流程
4. **人为制造 3 轮不收敛**（改 target_chars 公式使其过激）：观察任务 `status=error`，前端有清晰错误提示和完整 3 轮中间文件
5. **打开老 en/de/fr 项目**：tts 卡片外观不变，无迭代日志
6. **translate_lab（v2）项目** 不受影响

## 风险与对策

- **LLM rewrite 的语义漂移**：要求 prompt 强调"保留事实、禁止编造"；测试覆盖"shrink 后关键卖点是否保留"——这是人工 QA 重点
- **ElevenLabs 成本**：3 轮最坏 3 倍 TTS 调用。可接受——配合"first-round 通常命中"的统计（speech_rate_model 长期收敛后 cps 越来越准，rewrite 触发率下降）
- **`generate_localized_translation` 扩参破坏其他调用者**：本次同步审视所有调用点（grep），确保签名兼容
- **并发同任务 tts**：同既有行为——tts 步不可重入（由 step status 保护）
- **`_step_tts` 抽回基类"顺带 refactor"**：是本次工作的必要重构（不抽则循环逻辑要复制 3 份）。风险通过 test_runner_de / test_runner_fr 覆盖
- **新事件 `tts_duration_round` 与现有 step_update 关系**：step_update 仍在，只是 message 会跟随 phase 更新，保证老代码或未订阅新事件的客户端也能看到粗粒度进度
- **interactive_review 与迭代循环交互**：tts 不接受人工确认（每轮不等人），只有 translate 仍保留人工确认——已在 8 节澄清

## 文件变更清单

### 新增

- 无（所有改动都在现有文件上）

### 修改

- [appcore/events.py](appcore/events.py) — 新增 `EVT_TTS_DURATION_ROUND` 常量
- [appcore/task_state.py](appcore/task_state.py) — `create()` 初值加 `tts_duration_rounds=[]`、`tts_duration_status=None`
- [appcore/runtime.py](appcore/runtime.py) — `PipelineRunner` 加类变量（`tts_language_code` 等）；`_step_tts` 改为调 `_run_tts_duration_loop`；新增 `_run_tts_duration_loop`、`_compute_next_target`、`_rewrite_localized_translation`、`_emit_duration_round`、`_promote_final_artifacts`、`_resolve_voice`（抽出 voice 解析）
- [appcore/runtime_de.py](appcore/runtime_de.py) — 删除 `_step_tts` override；加类变量覆盖
- [appcore/runtime_fr.py](appcore/runtime_fr.py) — 同上
- [appcore/runtime_v2.py](appcore/runtime_v2.py) — 若有 `_step_tts` override，确认不受影响；如 v2 直接继承需显式 override 回原行为（v2 不走本次循环）
- [pipeline/localization.py](pipeline/localization.py) — 新增 `LOCALIZED_REWRITE_SYSTEM_PROMPT` + `build_localized_rewrite_messages`
- [pipeline/localization_de.py](pipeline/localization_de.py) — 同上（德语 prompt）
- [pipeline/localization_fr.py](pipeline/localization_fr.py) — 同上（法语 prompt）
- [pipeline/translate.py](pipeline/translate.py) — 新增 `generate_localized_rewrite()`（或扩 `generate_localized_translation` 参数，实现时决定）
- [web/preview_artifacts.py](web/preview_artifacts.py) — `build_tts_artifact` 增加 `duration_rounds` 参数，返回结构加新 item `{type: "tts_duration_rounds", rounds: [...]}`
- [web/routes/task.py](web/routes/task.py) — `/download/<key>` 白名单加 `tts_full.round_{1,2,3}.mp3` / `localized_translation.round_{1,2,3}.json` / `tts_script.round_{1,2,3}.json`
- [web/routes/de_translate.py](web/routes/de_translate.py) — 同上
- [web/routes/fr_translate.py](web/routes/fr_translate.py) — 同上
- [web/services/pipeline_runner.py](web/services/pipeline_runner.py) / `de_pipeline_runner.py` / `fr_pipeline_runner.py` — 订阅 `EVT_TTS_DURATION_ROUND` → socketio emit
- [web/templates/_task_workbench.html](web/templates/_task_workbench.html) — `step-tts` 卡片加 `<div id="ttsDurationLog">` 容器
- [web/templates/_task_workbench_scripts.html](web/templates/_task_workbench_scripts.html) — 新增 `socket.on("tts_duration_round", ...)` + `renderTtsDurationLog()` + `_upsertRound()`
- [web/templates/_task_workbench_styles.html](web/templates/_task_workbench_styles.html) — 迭代日志样式（沿用 ocean-blue tokens）

### 测试

- [tests/test_tts_duration_loop.py](tests/test_tts_duration_loop.py) — 新
- [tests/test_localized_rewrite.py](tests/test_localized_rewrite.py) — 新
- [tests/test_runner_tts_base.py](tests/test_runner_tts_base.py) — 新
- [tests/test_runner_de.py](tests/test_runner_de.py) / [test_runner_fr.py](tests/test_runner_fr.py) — 增补
- [tests/test_task_routes.py](tests/test_task_routes.py) / [test_de_translate_routes.py](tests/test_de_translate_routes.py) / [test_fr_translate_routes.py](tests/test_fr_translate_routes.py) — 增补下载路由
- [tests/test_events.py](tests/test_events.py) — 增补常量检查

## 落地顺序（给后续实现计划参考）

1. `pipeline/translate.py` + 3 份 `localization*.py`：rewrite prompt / messages builder / `generate_localized_rewrite`（或扩参）+ 单测
2. `appcore/events.py`：`EVT_TTS_DURATION_ROUND` 常量
3. `appcore/runtime.py`：抽取类变量 + `_compute_next_target` + `_run_tts_duration_loop` + 新 `_step_tts`，单测（mock ElevenLabs/LLM）
4. `appcore/runtime_de.py` / `runtime_fr.py`：删 `_step_tts`，留类变量；跑集成测试验证 de/fr 走基类
5. `appcore/runtime_v2.py`：确认 v2 不受影响；必要时 override 回原 tts 行为
6. `appcore/task_state.py`：`create()` 加新初值
7. `web/services/*_pipeline_runner.py`：事件订阅 → socketio
8. `web/preview_artifacts.py`：artifact 结构扩展
9. `web/routes/*.py`：下载白名单
10. `web/templates/_task_workbench.html` + `_task_workbench_scripts.html` + `_task_workbench_styles.html`：前端组件
11. 手工 QA：en/de/fr 各跑一个真实任务，确认迭代日志与中间文件展示正确

## 开放问题

- `generate_localized_translation` 现有签名是否支持 `messages_builder_kwargs` 类扩展——实现阶段读代码决定扩参还是新函数
- `/download/<key>` 白名单的既有实现风格（显式映射 vs 通配）——实现阶段读代码确定新 key 的接入方式
- `runtime_v2.py` 的 tts 实现是否独立于基类——实现阶段读代码决定 v2 行为保持方式
