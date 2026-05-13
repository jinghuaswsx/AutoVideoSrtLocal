# Omni TTS 句级过程可视化设计

日期：2026-05-13

## 背景

Omni 任务详情页已能显示 `sentence_reconcile` 的最终句级收敛结果，但语音生成过程仍不够透明。用户在任务 `d8aba350-231a-45f4-909a-fb4ed77b6d75` 的 TTS 阶段需要看到：

1. 首轮音频生成进度。
2. 每一句进入文案重新翻译的过程。
3. 每一句重新生成音频和测量时长的过程。
4. 每轮尝试的前后译文、TTS 时长、偏差和失败原因。

本需求只针对 Omni 任务流程里的语音生成阶段，不改变普通多语言翻译、图片翻译、素材管理或订单分析模块。

## 目标

1. 在 Omni 任务详情页的「翻译本土化 / 语音生成」阶段展示更详细的 TTS 过程。
2. 首轮 TTS 生成时，复用现有 progress emitter，把 `done / total / active / queued` 透传到 `tts_duration_rounds`。
3. 句级收敛时，在 LLM 重写开始前发出 `rewrite_start` 事件，在 TTS 重生成开始前发出 `tts_regen_start` 事件。
4. 前端把 `sentence_reconcile` 的过程渲染成可扫读的「语音生成过程」面板，而不是压缩成一行文本。
5. 已完成或已失败的旧任务仍能根据已有 `attempts` 渲染更清晰的轮次详情。
6. 轮次详情必须使用分行或表格展示，禁止把所有 attempt 串成一条长文本；长文案必须自动换行，不能横向撑破卡片。

## 非目标

1. 不接 LLM streaming API。单次模型调用期间只能显示“正在重新翻译”，不能显示 token 级内容。
2. 不改变 TTS 收敛算法、阈值、速度范围或最终音频选择逻辑。
3. 不改变 OpenRouter / Google AIStudio 等 provider 配置。本需求只处理可视化。
4. 不新增独立数据库表。
5. 不重做任务详情页整体布局。

## 后端事件契约

所有新增可视化数据继续复用现有 `tts_duration_round` socket 事件和 `tts_duration_rounds` 状态字段。

### 首轮音频生成

`sentence_reconcile` 在调用 `tts_engine.synthesize_full(...)` 时，通过 `make_tts_progress_emitter(..., extra_state_update=...)` 额外发送：

```json
{
  "mode": "sentence_reconcile",
  "round": 0,
  "phase": "initial_audio_gen",
  "status": "initial_audio_gen",
  "audio_segments_done": 3,
  "audio_segments_total": 9,
  "audio_segments_active": 1,
  "audio_segments_queued": 5
}
```

说明：

- `round: 0` 表示首轮整批 TTS，不对应某一个句子。
- 前端句级列表必须过滤 `round: 0`，只把它展示在顶部进度条。
- 该事件可被持久化。刷新页面后能看到最后一次首轮进度快照。

### 句级重新翻译开始

在调用 `av_translate.rewrite_one(...)` 之前发送：

```json
{
  "mode": "sentence_reconcile",
  "phase": "rewrite_start",
  "round": 1,
  "sentence_position": 0,
  "asr_index": 0,
  "active_attempt": 2,
  "active_action": "shorten",
  "active_temperature": 0.8,
  "text": "当前译文",
  "source_text": "原文",
  "status": "needs_rewrite"
}
```

### 句级音频重生成开始

在重写文本返回后、调用 `_regenerate_segment(...)` 之前发送：

```json
{
  "mode": "sentence_reconcile",
  "phase": "tts_regen_start",
  "round": 1,
  "sentence_position": 0,
  "asr_index": 0,
  "active_attempt": 2,
  "active_tts_attempt": 1,
  "pending_tts_text": "即将送去 TTS 的新译文",
  "status": "needs_rewrite"
}
```

### 句级轮次完成

现有 `rewrite_attempt` 事件保留，并继续携带 `attempts`。前端需要把每个 attempt 展示为独立卡片，至少包含：

- 动作：压缩 / 扩写 / 语义修复。
- 文案：修改前、修改后。
- 音频：目标时长、TTS 时长、偏差百分比。
- 状态：收敛、偏长、偏短、重写失败、语义覆盖失败。
- 错误：LLM JSON 错误或 TTS 错误。

## 前端展示

`renderSentenceReconcileDurationLog(...)` 需要改造成三层：

1. 顶部 live progress：显示「语音生成过程」、首轮音频生成进度、当前句当前动作。
2. 句级摘要：统计总句数、已收敛、需人工关注、已重写次数、已重生成 TTS 次数。
3. 句级明细：每句展示原文、最终译文、目标时长、当前 TTS 时长、偏差，并展开每轮尝试。

布局要求：

- 句子卡片只放一句的摘要、原文、当前译文和状态，不把所有轮次塞进摘要行。
- 轮次详情用表格或网格卡片展示。每轮至少分成「语句重新翻译」「音频重生成」「结果」三组信息。
- 修改前 / 修改后文案各占独立块，允许多行换行。
- 错误原因和最佳候选标签独立显示，不能混在长句里。
- 移动端或窄屏时表格可以退化为纵向卡片，但仍必须按组分行。

前端必须识别以下 phase：

- `initial_audio_gen`：首轮音频生成。
- `initial_measure`：句子初始测量。
- `rewrite_start`：语句重新翻译中。
- `tts_regen_start`：音频重生成中。
- `rewrite_attempt`：单轮重写和 TTS 测量完成。
- `rewrite_error`：单轮重写失败。
- `speed_adjust`：速度微调。
- `sentence_done`：当前句完成。

## 验证策略

1. `tests/test_duration_reconcile.py` 覆盖 `rewrite_start`、`tts_regen_start`、`rewrite_attempt` 的事件顺序和字段。
2. `tests/test_translate_detail_shell_templates.py` 覆盖模板包含「语音生成过程」、新增 phase label、attempt 卡片和 CSS class。
3. 运行相关 runtime / Omni dispatch 测试，确保已有 `tts_duration_round` 兼容。
4. 发布后验证测试环境和生产环境服务 active，HTTP 返回 200 或登录跳转 302。
