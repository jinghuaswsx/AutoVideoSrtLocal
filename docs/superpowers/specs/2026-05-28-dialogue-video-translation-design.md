# 对话式视频翻译设计

日期：2026-05-28

状态：已由用户确认设计方向，等待 implementation plan

## 锚点

- `AGENTS.md`：本项目常规开发必须走隔离 worktree；改代码前必须有文档锚点；不得连接 Windows 本机 MySQL。
- `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md`：Omni 的 profile / plugin_config / TTS 策略体系是视频翻译能力聚合入口。
- `docs/superpowers/specs/2026-05-24-omni-v2-stable-default-design.md`：V2 稳定默认链路不得被新实验污染。
- `docs/superpowers/specs/2026-05-20-omni-asr-window-audio-alignment-design.md`：ASR 语音窗口是配音放置的时间轴依据。
- `docs/superpowers/specs/2026-05-20-conditional-asr-gap-audio-window-design.md`：没有 ASR 语音的窗口不生成配音，原视频时间轴保持不动。
- `docs/superpowers/plans/2026-05-05-multi-speaker-translation.md`：历史多人说话调研记录，指出当前流水线是 single-speaker assumption。

## 目标

新增一个独立的“对话式视频翻译”模块，面向一个视频里有两个人轮流说话的场景。首版只处理两位说话人 Speaker A / Speaker B，但主流程必须自动化：

1. 系统自动识别每句属于 Speaker A 还是 Speaker B。
2. 系统为 A/B 分别匹配候选音色。
3. 用户确认 A/B 两个最终音色后继续生成配音。
4. 每句 TTS 按 speaker 切换对应 voice_id。
5. 单句 TTS 生成、文案收敛、真实测时、改写/变速、音画对齐、背景混音、字幕和导出，复刻 Omni 单说话人流程。
6. 原视频时间轴不移动，配音尽量落回原 ASR 说话窗口。
7. 最终成片字幕不显示 Speaker 标识；后台详情页显示 A/B、置信度、音色和复核状态。

## 非目标

- 不把本能力塞进原 `/omni-translate` preset。
- 不改变 `/omni-translate-v2` 的稳定默认链路。
- 不修改 `multi_translate` 的稳定生产行为。
- 首版不支持 3 个及以上说话人。
- 首版不处理两个人同时说话的完整双轨重叠混音；检测到重叠时标记人工复核。
- 不做声音克隆，不训练用户私有 voice clone。
- 不让最终视频字幕强制显示 “Speaker A:” 这类标识。
- 不重写 Omni 现有的 TTS 收敛与对齐主干。

## 产品流程

新增独立入口和任务类型，例如：

- 列表页：`/dialogue-translate/`
- 详情页：`/dialogue-translate/<task_id>`
- 创建 API：`/api/dialogue-translate/start`
- 项目类型：`dialogue_translate`

创建任务只要求用户上传视频、选择源语种和目标语种。任务进入自动流程：

```text
extract
  -> asr
  -> speaker_detect
  -> voice_match_ab
  -> translate
  -> tts_ab
  -> subtitle
  -> compose
  -> export
```

`voice_match_ab` 是自动音色匹配门禁：系统按 Speaker A / B 的时间范围提取各自原声音频，复用 Omni 候选匹配和 `voice_selection.assess` 大模型排序，分别选择 `llm_rank=1` 的音色后进入后续步骤。Speaker 标签本身不是人工标注主流程；人工只用于少量纠错和失败兜底。

## 说话人识别

`speaker_detect` 输出统一的对话句段结构，不把 provider 原始字段泄漏到下游：

```json
{
  "dialogue_segments": [
    {
      "index": 0,
      "text": "source text",
      "start_time": 1.23,
      "end_time": 3.45,
      "speaker_id": "A",
      "speaker_confidence": 0.92,
      "speaker_source": "asr_provider",
      "overlap": false,
      "review_required": false,
      "review_reason": ""
    }
  ],
  "speaker_summary": {
    "A": {"segment_count": 12, "duration": 31.2},
    "B": {"segment_count": 9, "duration": 24.8}
  }
}
```

识别策略：

1. 优先读取 ASR provider 自带 speaker 字段。不同 provider 的字段名由 adapter 统一映射成 `speaker_id`、`confidence` 和 `speaker_source`。
2. 如果 ASR provider 没有可靠 speaker 标签，或标签覆盖率/置信度不达标，则调用独立 diarization 服务。
3. diarization 服务输出时间段后，按时间重叠比例 join 回 ASR utterance。每句只允许落到 A/B 其中一个 speaker；争议句标记复核。
4. 如果识别出超过 2 个 speaker，首版只保留主时长最长的两个作为 A/B，其余句段标记 `review_required=true`，原因是 `unsupported_extra_speaker`。
5. 如果检测到重叠说话，句段标记 `overlap=true` 和 `review_required=true`，首版不生成双轨重叠配音。

可靠性门槛建议：

- speaker 标签覆盖率低于 90%：视为不可靠。
- 任一 speaker 有效语音总时长低于 3 秒：该 speaker 的音色匹配标记低置信度。
- 单句与 diarization 时间段最大重叠比例低于 0.6：该句需要人工复核。

## A/B 音色匹配

### 2026-06-01 自动音色选择修正

`voice_match_ab` 不再把 A/B 候选音色作为必经人工门禁。该步骤必须为每个有效 speaker 保留原声抽样时间窗与样本音频，复用 Omni 全能视频翻译的音色候选匹配与 `voice_selection.assess` 大模型排名逻辑，并直接选择每个 speaker 的 `llm_rank=1` 音色。A/B 均选定后，`voice_match_ab` 标记为 `done`，清空 `current_review_step`，流水线继续进入实际下一步；只有缺少样本、缺少候选或自动选择失败时才停在错误态等待处理。

`voice_match_ab` 复用现有音色库、embedding 和语速辅助排序能力，但按 speaker 分组执行：

1. 对 A/B 各自聚合高置信度原声时间窗。
2. 为每个 speaker 切出 8-12 秒样本；如果单段不足，则拼接多个同 speaker 片段。
3. 对每个样本调用现有 `pipeline.voice_embedding.embed_audio_file()`。
4. 复用现有音色库候选匹配和 speed-aware rerank，给 A/B 各返回候选列表。
5. 候选列表、样本音频、query embedding、匹配置信度写入 `speaker_profiles`。

任务状态保存：

```json
{
  "speaker_profiles": {
    "A": {
      "sample_path": "speaker_A_sample.wav",
      "sample_windows": [[1.23, 5.67]],
      "candidates": [{"voice_id": "voice-a", "name": "Candidate A"}],
      "voice_ai_rankings": [{"voice_id": "voice-a", "llm_rank": 1}],
      "selected_voice": {"voice_id": "voice-a", "name": "Voice A", "llm_rank": 1},
      "match_warnings": []
    },
    "B": {
      "sample_path": "speaker_B_sample.wav",
      "sample_windows": [[8.9, 12.34]],
      "candidates": [{"voice_id": "voice-b", "name": "Candidate B"}],
      "voice_ai_rankings": [{"voice_id": "voice-b", "llm_rank": 1}],
      "selected_voice": {"voice_id": "voice-b", "name": "Voice B", "llm_rank": 1},
      "match_warnings": []
    }
  },
  "selected_voice_by_speaker": {
    "A": {"voice_id": "voice-a", "name": "Voice A"},
    "B": {"voice_id": "voice-b", "name": "Voice B"}
  }
}
```

## TTS 与音画对齐

对话式模块不得另写一套简化版配音和对齐算法。核心要求是复刻 Omni 单说话人流程：

- TTS 文案构造使用现有目标语种 localization adapter。
- 文案收敛、真实 TTS 测时、改写、变速、best-effort 兜底使用 Omni 现有策略。
- ASR speech window 是音频放置依据；没有 ASR 语音的窗口不生成配音。
- 原视频画面不剪、不拉、不挪。
- 背景保留、人声分离、响度匹配、字幕和 compose/export 沿用现有能力。

唯一新增差异是 per-segment voice override：

```json
{
  "tts_segments": [
    {
      "index": 0,
      "speaker_id": "A",
      "voice_id": "voice-a",
      "tts_text": "translated sentence",
      "start_time": 1.23,
      "end_time": 3.45
    },
    {
      "index": 1,
      "speaker_id": "B",
      "voice_id": "voice-b",
      "tts_text": "translated sentence",
      "start_time": 4.1,
      "end_time": 6.0
    }
  ]
}
```

TTS engine 层需要支持“同一个任务、不同句段使用不同 voice_id”。如果现有 `synthesize_full()` 只接受单个 `voice_id`，实现时应增加一个兼容路径，例如：

- 保持原 `synthesize_full(segments, voice_id, ...)` 不变，供 Omni/Multi 使用。
- 新增 `synthesize_segments_with_voices(segments, output_dir, ...)`，或允许 segments 携带 `voice_id` 时使用 per-segment voice。
- 输出仍补齐每段 `tts_path`、`tts_duration`，并最终生成一条完整 TTS 音轨。

单句超出窗口时沿用 Omni 逻辑：先改写或变速，仍无法放入窗口则标记复核，不静默覆盖下一位说话人的窗口。

## UI 设计

### 2026-05-28 Handoff 补充：必须同构 Omni 项目管理体验

用户确认本模块不是简化上传表单，而是“全能视频翻译”的双人说话人版本。因此 `/dialogue-translate` 的产品外壳必须复刻 `/omni-translate`：

- 列表页使用 Omni 的项目管理体验：卡片/列表视图、顶部新建项目按钮、语言筛选、创建人筛选（超管）、保留期提示、缩略图、项目状态、源语言到目标语言展示、复制项目、删除项目和复制进度遮罩。
- 创建项目流程复刻 Omni：上传视频、选择目标语言、选择源语言、填写项目名、超级管理员可选系统级 preset；提交时保存生效的 `plugin_config` 快照。已有任务不回查 preset。
- 生命周期端点复刻 Omni：详情、状态、失败恢复、强制重启、复制、删除、下载结果、artifact、round file、source-language、alignment、segments、resume、loudness-profile、visible-to-all、LLM debug。
- 路由必须保持 `@login_required + @admin_required`，并通过 `dialogue_translate` 权限门禁；所有 mutating 请求必须带 `X-CSRFToken`。
- 详情页继续复用 `_translate_detail_shell.html` 和 Omni workbench。唯一 UI 差异是 `voice_match` 阶段替换为 Speaker A / Speaker B 双音色匹配面板；不能暴露单音色确认作为主路径。
- 运行主干复用 Omni step 生命周期，`voice_match` 在 dialogue 中替换为 `speaker_detect` 和 `voice_match_ab`，后续从实际 step 顺序的下一步继续，不能硬编码只从 `alignment` 继续。

详情页复用现有视频翻译 workbench 结构，新增“说话人”面板：

- A/B 摘要：语音时长、句数、匹配状态、已选音色。
- A/B 样本试听：播放原声样本，便于判断候选是否贴近。
- A/B 候选音色：每个 speaker 一组候选，默认由大模型自动选择 rank 1。
- 句级时间线：后台显示 speaker、原文、译文、时间窗、置信度、复核标记。
- 少量纠错：允许用户修改某句 `speaker_id`。修改后清空受影响的 A/B 音色匹配和下游 TTS/合成状态，要求重跑。

最终视频字幕：

- 只显示译文。
- 不显示 “A:” / “B:” / “Speaker A:”。
- 后台详情页保留 speaker 信息用于审查。

## 状态与复核

任务状态新增或约定以下字段：

- `dialogue_segments`：句级 speaker 标注后的源文本时间线。
- `speaker_profiles`：A/B 样本、候选音色、匹配诊断、确认音色。
- `selected_voice_by_speaker`：A/B 最终 voice_id。
- `review_required_segments`：需要复核的句段索引和原因。
- `dialogue_warnings`：任务级风险，例如 provider 无 speaker、diarization 低置信度、超过 2 个 speaker、重叠说话。

复核原因枚举：

- `low_speaker_confidence`
- `speaker_overlap`
- `unsupported_extra_speaker`
- `insufficient_speaker_sample`
- `tts_overflow_window`
- `manual_speaker_changed`

## 错误处理

- 无有效 ASR 文本：沿用现有音乐/无语音直通或失败策略，不进入 A/B 音色匹配。
- 只识别出一个 speaker：可降级为单 speaker 对话任务，后台提示“仅检测到一位说话人”；用户仍可手动拆分少量句子后重跑匹配。
- A/B 任一无可用样本：停在 `voice_match_ab`，提示用户修正 speaker 时间线或改用单人 Omni。
- A/B 任一未确认音色：不得进入 TTS。
- diarization 服务失败：任务停在 `speaker_detect`，提示服务失败；不回退为静默单音色生成。
- 句段 TTS 放不回窗口：标记复核，保留中间音频和诊断。

## 实施分期

### Phase 1：自动 A/B 主链路骨架

- 新增 dialogue 入口、runner/profile、任务步骤。
- 实现 provider speaker 字段 adapter 和统一 `dialogue_segments`。
- 实现 A/B 音色匹配、自动选择、状态展示。
- TTS 支持 per-segment voice override，并复用 Omni 对齐主干。

### Phase 2：独立 diarization 服务

- 增加 diarization client 和配置。
- 当 ASR speaker 标签不可靠时自动调用 diarization。
- 实现 diarization 时间段到 ASR utterance 的 join 与置信度诊断。

### Phase 3：复核与体验增强

- 句级 speaker 少量纠错。
- 修正后局部重跑 speaker profile / TTS。
- 更清晰的重叠说话和窗口溢出诊断。

## 验收标准

1. 两人轮流说话视频可以自动产出 A/B speaker 时间线。
2. A/B 各自有原声样本、候选音色和最终确认音色。
3. 用户确认两个音色后，TTS 按句切换 voice_id。
4. 单句 TTS 生成和音画对齐复刻 Omni 单说话人流程。
5. 原视频时间轴不移动；没有 ASR 语音的窗口不生成翻译配音。
6. 最终字幕只显示译文，不显示 speaker 标识。
7. 后台详情页显示每句 speaker、置信度、音色和复核原因。
8. 重叠说话、低置信度、超过两位说话人、TTS 放不回窗口时必须标记复核。
9. 新模块不影响现有 `/omni-translate`、`/omni-translate-v2` 和 `multi_translate`。

## 测试建议

- 单元测试：speaker 字段 adapter 将不同 provider 原始响应统一成 A/B。
- 单元测试：diarization 时间段 join 到 ASR utterance 的重叠比例和低置信度标记。
- 单元测试：A/B 样本窗口选择跳过低置信度和重叠句。
- 单元测试：per-segment voice TTS 调用按句使用正确 voice_id。
- 单元测试：TTS 溢出窗口时标记复核，不覆盖下一句。
- 路由测试：未登录访问新页面 302；登录后 200；新 API 有 `login_required` 和 `admin_required`。
- 前端静态测试：详情页显示 A/B 面板、候选音色、自动完成态、后台 speaker 时间线。
- 回归测试：现有 Omni/Multi/V2 创建与详情页不出现 dialogue 字段依赖。

涉及数据库或线上数据验证时，遵守项目规则：不连接 Windows 本机 MySQL `127.0.0.1:3306`，数据库状态以测试服务器或线上服务器环境为准。
