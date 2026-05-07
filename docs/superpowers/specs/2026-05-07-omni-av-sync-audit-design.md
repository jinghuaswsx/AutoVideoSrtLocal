# Omni AV Sync Audit 安全修正设计

日期：2026-05-07

## 文档锚点

- `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md`：Omni 合并实验版的 `plugin_config` / preset / runtime dispatch 架构。
- `docs/superpowers/specs/2026-04-28-av-sync-v2-sentence-convergence-design.md`：句级音画同步的时长收敛、安全速度范围和可视化调试要求。
- `docs/superpowers/specs/2026-05-06-doubao-seed-2-lite-design.md`：Doubao Seed 2.0 Lite 的可选模型接入口径。

## 背景

Omni 已经合并为实验大本营，通过 `plugin_config` 动态选择 ASR 后处理、翻译算法、TTS 收敛策略和字幕策略。现有 `av_sentence + sentence_reconcile + sentence_units` 能做到句级翻译、句级 TTS 时长收敛和句级字幕生成，但它仍主要依赖文本、时长和已有 `shot_notes`，缺少一个专门判断“目标配音是否真正贴合画面动作和原声节奏”的多模态审计层。

本设计新增一个 Omni 专用能力点：先由 Doubao Seed 2.0 Lite 理解原视频、目标句级 TTS/字幕/时间轴并输出结构化问题，再由 Gemini 3 Flash 复核问题是否成立，最后只在安全边界内生成修正计划。修正计划可以被自动应用，也可以只作为报告展示。

## 目标

1. 在 Omni 合成前发现明显音画不同步、文案与画面动作不匹配、字幕/配音节奏风险。
2. 让 Doubao 负责多模态问题发现，让 Gemini 3 Flash 负责复核和压缩误判。
3. 只允许安全修正：单句压缩、单句扩写、单句 TTS 重生成、句级字幕重建。
4. 不改变原视频画面节奏，不自动剪画面，不移动原始 ASR 时间轴。
5. 将结果落到 task state / artifact，便于详情页和调试面板展示。

## 非目标

1. 不修改 `multi_translate` 生产链路。
2. 不做自由剪辑或全局重排，不新增跨句大改。
3. 不把 Doubao 的时间判断直接写进时间轴；时间轴仍以现有 ASR / TTS manifest 为准。
4. 不在第一版做合成后自动大修；合成后仍沿用 `video_ai_review` 做成品质检。
5. 不引入新的视频变速、画面裁切、镜头重排或音频强压缩。

## 能力点定义

在 `plugin_config` 中新增字段：

```json
{
  "av_sync_audit": "off | report_only | safe_auto"
}
```

默认值为 `off`，保证现有 preset 和历史任务不改变行为。

模式含义：

- `off`：完全不运行 AV 同步审计。
- `report_only`：运行 Doubao 诊断 + Gemini 复核，输出结构化报告，不应用修正。
- `safe_auto`：运行诊断和复核，并在安全范围内自动应用修正。

`safe_auto` 只在以下配置中允许：

```json
{
  "translate_algo": "av_sentence",
  "tts_strategy": "sentence_reconcile",
  "subtitle": "sentence_units"
}
```

其他组合如果传入 `safe_auto`，validator 自动降级为 `report_only`。这样保留报告能力，但避免对非句级链路做不可靠自动修正。

## 接入位置

Omni runtime 插入点：

```text
extract
→ asr
→ separate?
→ shot_decompose?
→ asr_clean | asr_normalize
→ voice_match
→ alignment?                 # av_sentence 不需要
→ translate
→ tts
→ av_sync_audit?             # 新增：合成前诊断/复核/安全修正
→ loudness_match?
→ subtitle
→ compose
→ analysis?
→ export
```

放在 `tts` 之后、`subtitle` 之前，原因：

- 已经有目标译文、每句 TTS 实测时长、句级状态和音频文件。
- 还没生成最终字幕和合成视频，修正单句 TTS / 句级字幕成本低。
- 避免合成后再大改造成反复渲染和节奏漂移。

## 输入数据

`pipeline/omni_av_sync_audit.py` 从 task state 读取：

- `video_path`：原视频。
- `source_language` / `target_lang`。
- `script_segments` 或 `normalized_script_segments`。
- `shot_notes`。
- `variants["av"]["sentences"]`：包含 `asr_index`、源文、目标文、`start_time`、`end_time`、`target_duration`、`tts_duration`、`duration_ratio`、`speed`、`status`、`tts_path`、`attempts`。
- `plugin_config`。

如果缺少 `variants["av"]["sentences"]`，审计直接跳过并标记 `skipped_missing_av_sentences`，不阻塞后续流程。

## Doubao 诊断输出

新增 use case：

- `omni_av_sync.diagnose`
- 默认 provider：`doubao`
- 默认模型：`doubao-seed-2-0-lite-260215`

输出结构：

```json
{
  "issues": [
    {
      "asr_index": 3,
      "severity": "low | medium | high",
      "problem_type": "visual_mismatch | speech_early | speech_late | duration_risk | subtitle_risk | tts_quality_risk",
      "evidence": "简短证据",
      "safe_action": "none | shorten_text | expand_text | regenerate_tts | manual_review",
      "suggested_text": "可选，目标语言单句",
      "confidence": 0.0
    }
  ],
  "summary": "简短中文总结"
}
```

Doubao 只能提出候选问题，不直接决定修改。

## Gemini 复核输出

新增 use case：

- `omni_av_sync.verify`
- 默认 provider：`openrouter`
- 默认模型：`google/gemini-3-flash-preview`

Gemini 输入为 Doubao issues、句级时间轴、目标文案、TTS duration 状态和安全约束。输出：

```json
{
  "accepted_issues": [
    {
      "asr_index": 3,
      "severity": "medium | high",
      "problem_type": "speech_late",
      "accepted": true,
      "reason": "复核理由",
      "safe_action": "shorten_text",
      "final_text": "可选，目标语言单句"
    }
  ],
  "rejected_count": 2,
  "summary": "简短中文总结"
}
```

只有 `accepted=true` 且 `severity in {"medium", "high"}` 的问题可以进入安全修正计划。

## 安全修正约束 v0.1

自动修正必须同时满足：

1. `plugin_config.av_sync_audit == "safe_auto"`。
2. 当前任务是句级链路：`av_sentence + sentence_reconcile + sentence_units`。
3. 问题由 Doubao 提出，并被 Gemini 接受。
4. 问题的 `asr_index` 能匹配现有句子。
5. 每次审计最多自动修正 20% 句子，且最多 5 句。
6. 单句最多执行 1 次安全修正，不进入循环。
7. 修正后必须重新生成该句 TTS，并测量真实时长。
8. 修正后 `duration_ratio` 必须落在 `0.95-1.05`，或比修正前更接近 1.0；否则回滚该句。
9. `speed` 仍限制在 `0.95-1.05`，不得扩大范围。
10. 不允许修改 `start_time` / `end_time` / `target_duration`。
11. 不允许修改原视频、画面速度、镜头顺序或 compose 参数。
12. 不允许新增原视频没有的价格、功效、材质、认证、承诺。

允许的动作：

- `shorten_text`：单句压缩，优先删除修饰、重复、口水词。
- `expand_text`：单句扩写，只补足原句已有含义，不新增事实。
- `regenerate_tts`：文本不变，仅重新生成该句 TTS。
- `manual_review`：只标记问题，不自动修。

禁止的动作：

- `shift_video`、`retime_video`、`speed_video`。
- 跨句合并、拆句、重排。
- 删除画面或重新剪辑。
- 将 TTS speed 拉出 `0.95-1.05`。
- 为了补时长新增未依据的卖点。

## 应用修正后的数据

在 `variants["av"]` 中新增：

```json
{
  "av_sync_audit": {
    "mode": "safe_auto",
    "diagnosis": {},
    "verification": {},
    "applied_fixes": [
      {
        "asr_index": 3,
        "action": "shorten_text",
        "before_text": "...",
        "after_text": "...",
        "before_tts_duration": 3.4,
        "after_tts_duration": 3.05,
        "before_duration_ratio": 1.13,
        "after_duration_ratio": 1.02,
        "status": "applied | rolled_back | report_only",
        "reason": "..."
      }
    ],
    "summary": {
      "diagnosed": 6,
      "accepted": 3,
      "applied": 2,
      "rolled_back": 1,
      "manual_review": 1
    }
  }
}
```

如果应用了任何修正，必须重建：

- `variants["av"]["sentences"]`
- `variants["av"]["localized_translation"]`
- `variants["av"]["tts_result"]`
- `variants["av"]["tts_audio_path"]`
- task 顶层 `segments`、`localized_translation`、`tts_audio_path`

字幕由后续 `subtitle` step 使用最新句子生成，不在审计 step 内直接写最终 SRT。

## 错误处理

- Doubao 调用失败：记录 `diagnose_failed`，流程继续到 subtitle。
- Gemini 调用失败：记录 `verify_failed`，不应用修正。
- JSON 解析失败：记录原始响应预览，流程继续。
- TTS 修正失败：该句回滚，其他句继续。
- 修正后时长变差：该句回滚并标记 `rolled_back_not_safer`。
- 总体审计失败不得让 Omni 任务失败，除非后续用户明确要求“审计失败即失败”。

## UI 与可视化

第一版 UI 只做最小展示：

- Omni 新建任务能力点增加 `av_sync_audit` radio。
- 任务详情页暂复用 artifact / 调试面板，不新增大 UI。
- `report_only` 和 `safe_auto` 都在 artifact 中展示诊断、复核、应用结果。

后续可在 `_task_workbench` 增加专门的“音画同步审计”卡片，但不作为 v0.1 必需项。

## 测试策略

单元测试：

- `omni_plugin_config` 支持 `av_sync_audit` 默认值、合法值、`safe_auto` 自动降级。
- `OmniTranslateRunner._get_pipeline_steps` 只在 `av_sync_audit != off` 时插入 `av_sync_audit`，位置在 `tts` 后、`subtitle` 前。
- `omni_av_sync_audit` 在 `report_only` 下只写报告不改句子。
- `safe_auto` 只应用 Gemini 接受的问题。
- 修正后时长变差会回滚。
- use case 注册包含 `omni_av_sync.diagnose` 与 `omni_av_sync.verify`。
- Doubao adapter 支持 `generate(media=...)` 路径，媒体走临时公网 URL，不内联 base64。

聚焦验证：

```bash
pytest tests/test_omni_plugin_config.py tests/test_runtime_omni_dispatch.py tests/test_llm_use_cases_registry.py tests/test_llm_client_invoke.py tests/test_llm_providers_openrouter.py -q
pytest tests/test_omni_av_sync_audit.py -q
```

## 验收标准

1. 默认 omni preset 行为不变。
2. 新建任务可选择 `off / report_only / safe_auto`。
3. `safe_auto` 在非句级链路会自动降级为 `report_only`。
4. 审计 step 只出现在 `tts` 后、`subtitle` 前。
5. 审计失败不阻塞任务继续合成。
6. 自动修正不会改原视频画面、不会扩大 TTS speed 范围。
7. 聚焦测试通过。
