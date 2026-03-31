# Translation Subtitle Redesign Spec

## Goal

重构 AutoVideoSrt 的翻译、字幕、TTS 文本流水线，让“原始中文识别结果”“整段本土化翻译”“给 ElevenLabs 的朗读文案”“最终英文字幕”成为 4 个职责清晰、可独立展示的中间产物，并保证最终 `文案 = 语音 = 字幕`。

## Problem

当前实现存在 3 个核心问题：

1. `script_segments[].translated` 同时承担“翻译结果”“TTS 输入”“字幕文本”三种职责，任何一步的调整都会影响所有后续步骤。
2. 字幕断句目前主要靠本地规则换行，不能保证英文字幕的可读性和画面展示效果。
3. ElevenLabs 直接吃翻译结果，缺少专门面向朗读的重排层，数字、停顿、节奏、句块结构都不稳定。

结果是：

- 页面无法完整展示文本链路中的关键中间产物。
- 最终英文语音、字幕和网页里看到的翻译文案容易不一致。
- 后续想优化字幕或 TTS 时，没有明确的“文本真相源”。

## Scope

本次改造只覆盖文本与字幕相关流水线：

- 中文 ASR 结果拼接为整段中文中间产物
- 基于整段中文做整段本土化翻译
- 基于本土化英文生成 ElevenLabs 专用朗读文案和字幕分块
- 基于 ElevenLabs 音频做英文 ASR
- 基于英文 ASR 时间轴和朗读文案生成最终英文 SRT
- 把上述中间产物全部接入网页步骤预览

本次不做：

- 更换 ASR / TTS / 视频合成供应商
- 新增人工编辑器
- 重新设计分镜和视频时间线算法
- 用大模型参与最终英文 SRT 的时间轴校正

## Design Principles

1. 单一职责：同一个字段不能同时承担翻译、朗读、字幕三种职责。
2. 文本可追溯：每一层英文产物都能追踪回原始中文分段。
3. 真相源明确：`tts_script` 是英文语音和最终字幕文本的唯一真相源。
4. 时间与文本解耦：豆包英文 ASR 只负责时间轴参考，不负责最终字幕文案。
5. 页面可观察：每一阶段的关键文本、音频、字幕都必须能在网页端直接看到或听到。

## Pipeline

### 1. Chinese Source Assembly

输入保持现有 `utterances` 与 `script_segments`。

系统新增 `source_full_text_zh`，由确认后的 `script_segments[].text` 按顺序拼接生成。这个中间产物用于：

- 网页展示“整段中文源文案”
- 提供给整段本土化翻译步骤

拼接规则：

- 保留 `script_segments` 的顺序
- 相邻段之间用单个换行分隔
- 不对中文原文做增删改写

### 2. Localized Translation

输入：

- `source_full_text_zh`
- `script_segments[]`
- 每段的 `index`、`text`、`start_time`、`end_time`

调用 OpenRouter 的 `anthropic/claude-sonnet-4.5`，使用结构化 JSON 输出，生成 `localized_translation`。

输出结构：

- `full_text: str`
- `sentences: [{ index, text, source_segment_indices[] }]`

约束：

- 允许跨原始中文段自由合并或拆分英文句子，以保证英文自然度
- 每条英文句子必须带 `source_segment_indices`
- `full_text` 必须等于 `sentences[].text` 按顺序拼接
- 翻译允许本土化改写，但必须保持原始销售意图、信息点和整体节奏

### 3. ElevenLabs Script Arrangement

输入：

- `localized_translation.full_text`
- `localized_translation.sentences[]`

再次调用 OpenRouter 的 `anthropic/claude-sonnet-4.5`，生成 `tts_script`。

输出结构：

- `full_text: str`
- `blocks: [{ index, text, sentence_indices[], source_segment_indices[] }]`
- `subtitle_chunks: [{ index, text, block_indices[], sentence_indices[], source_segment_indices[] }]`

职责区分：

- `blocks`：给 ElevenLabs 的朗读块，优先服务语音自然度、呼吸点、数字口语化、语速节奏
- `subtitle_chunks`：给字幕显示的文本块，优先服务画面阅读性

约束：

- `tts_script.full_text` 是唯一英文文本真相源
- `blocks[].text` 拼接后必须等于 `tts_script.full_text`
- `subtitle_chunks[].text` 拼接后也必须等于 `tts_script.full_text`
- `subtitle_chunks` 只能重排断句和分块，不能引入任何不在 `tts_script.full_text` 中的新词
- `blocks` 可以做朗读友好的 text normalization，例如数字口语化、缩写展开、符号读法自然化，但其拼接结果必须稳定可直接送给 ElevenLabs

### 4. English Audio ASR

TTS 完成后，对整条英文音频调用豆包 ASR。

输入：

- 完整英文音频文件

输出：

- `english_asr_result.full_text`
- `english_asr_result.utterances[]`
- `english_asr_result.words[]` 或每个 utterance 下的 `words[]`

这个产物只用于：

- 网页端展示“英文语音识别校对结果”
- 给最终英文字幕提供时间轴参考

### 5. Subtitle Correction

最终英文字幕由 `tts_script.subtitle_chunks` 与 `english_asr_result` 共同生成。

规则：

- 字幕文本来源固定为 `tts_script.subtitle_chunks[].text`
- 字幕时间来源固定为 `english_asr_result`
- 不再调用大模型参与这一步

对齐策略：

1. 把豆包英文 ASR 展平成顺序词流，保留每个词的起止时间
2. 对 `subtitle_chunks` 做归一化匹配：
   - 忽略大小写
   - 忽略大部分标点
   - 兼容缩写和口语化差异
   - 兼容数字口语化差异
3. 每个字幕块的 `start_time` 取命中的第一个词时间
4. 每个字幕块的 `end_time` 取命中的最后一个词时间
5. 如果某块无法完整命中：
   - 优先用前后已命中块做边界插值
   - 再退回按整条音频总时长比例分配

输出结构：

- `corrected_subtitle.chunks: [{ index, text, start_time, end_time, source_asr_text }]`
- `corrected_subtitle.srt_content`

## Task State Changes

任务状态新增以下字段：

- `source_full_text_zh: str`
- `localized_translation: { full_text, sentences[] }`
- `tts_script: { full_text, blocks[], subtitle_chunks[] }`
- `english_asr_result: { full_text, utterances[] }`
- `corrected_subtitle: { chunks[], srt_content }`

保留现有字段，但职责调整如下：

- `script_segments`
  - 继续表示中文语义分段
  - 继续绑定原视频时间线和镜头信息
  - 不再作为英文字幕和最终 TTS 文案的唯一来源

- `segments`
  - 迁移期保留以兼容旧页面与下游流程
  - 最终内容改为和 `tts_script.blocks` 或时间线编译结果保持一致的过渡结构

- `timeline_manifest`
  - 最终仍需引用英文 TTS 结果和视频时间线
  - 其文案字段应从 `tts_script` 衍生，而不是直接读旧的 `translated`

## API And Event Changes

现有 HTTP 路由可以继续沿用，但任务详情返回值需要扩展上述新字段。

现有 WebSocket 事件可以继续沿用步骤广播机制，但需要补充更多 artifact 数据：

- `asr_result`
  - 除 utterances 外，带上 `source_full_text_zh`

- `translate_result`
  - 改为广播 `localized_translation`

- 新增 `tts_script_ready`
  - 广播 `tts_script`

- 新增 `english_asr_result`
  - 广播英文语音识别产物

- `subtitle_preview`
  - 改为广播 `corrected_subtitle.srt_content`

## Prompting Requirements

### Localized Translation Prompt

模型目标：

- 把整段中文转成适合美国短视频卖货语境的整段英文
- 输出稳定 JSON
- 允许本土化改写
- 必须保留每句与原中文分段的映射

关键约束：

- 输出 `full_text` 与 `sentences[]`
- 不允许额外解释
- 每句都必须有 `source_segment_indices`
- `sentences` 顺序必须与最终 `full_text` 一致

### TTS Script Prompt

模型目标：

- 生成适合 ElevenLabs 的稳定朗读文案结构
- 同时产出字幕分块

关键约束：

- 输出稳定 JSON
- `blocks` 更关注朗读自然度
- `subtitle_chunks` 更关注画面阅读性
- 不允许生成解释性文字
- `tts_script.full_text` 必须可直接送入 ElevenLabs
- `subtitle_chunks` 不能改词，只能重排断句和分块

## Web Preview Changes

网页步骤预览改造如下：

### ASR Step

展示：

- 中文 `utterances`
- `source_full_text_zh`

### Alignment Step

继续展示：

- 中文语义分段结果

### Translation Step

展示：

- `localized_translation.full_text`
- `localized_translation.sentences[]`
- 每句对应的 `source_segment_indices`

### TTS Preparation Step

展示：

- `tts_script.full_text`
- `tts_script.blocks[]`
- `tts_script.subtitle_chunks[]`

如果当前 UI 不想新增步骤卡片，可以把这一部分先挂在“语音生成”卡片内作为前置预览。

### TTS Step

展示：

- 完整英文音频播放器
- 每个朗读块对应文本

### English ASR Step

展示：

- 英文 ASR `utterances`
- `english_asr_result.full_text`
- 校正前 ASR 文案与最终字幕块的对照

### Subtitle Step

展示：

- `corrected_subtitle.chunks[]`
- `corrected_subtitle.srt_content`

## Error Handling

1. 任一大模型步骤返回非 JSON 或缺字段时：
   - 标记当前步骤失败
   - 保留原始响应到任务目录，便于排查

2. `localized_translation.full_text` 与 `sentences[]` 拼接不一致时：
   - 视为结构错误
   - 不进入下一步

3. `tts_script.full_text` 与 `blocks[]` 或 `subtitle_chunks[]` 拼接不一致时：
   - 视为结构错误
   - 不进入 ElevenLabs

4. 英文 ASR 结果过短、为空、或无法对齐时：
   - 仍允许按比例退化生成时间轴
   - 但任务详情要明确标出“字幕时间轴为退化模式”

## Testing

需要覆盖以下测试：

1. 中文分段拼接能生成稳定的 `source_full_text_zh`
2. 本土化翻译输出的结构校验：
   - `full_text`
   - `sentences[]`
   - `source_segment_indices`
3. TTS 文案输出的结构校验：
   - `full_text`
   - `blocks[]`
   - `subtitle_chunks[]`
4. `blocks[]` 与 `subtitle_chunks[]` 的拼接结果均等于 `tts_script.full_text`
5. 英文 ASR 对齐器在正常、漏词、标点差异、数字口语化差异下都能生成稳定 SRT
6. 网页任务详情接口能返回新增中间产物
7. 页面预览能展示：
   - 整段中文
   - 整段本土化英文
   - ElevenLabs 专用文案
   - 英文 ASR 结果
   - 最终英文 SRT

## Assumptions

- OpenRouter 继续通过现有 OpenAI SDK 接入
- `anthropic/claude-sonnet-4.5` 可通过 OpenRouter 使用 JSON schema 约束输出
- ElevenLabs 继续使用现有 `text_to_speech.convert` 调用
- 豆包 ASR 能对英文 TTS 音频返回可用的 utterance / word 时间戳
- 本次先保持页面主结构不大改，优先在现有步骤卡片中补充预览区
