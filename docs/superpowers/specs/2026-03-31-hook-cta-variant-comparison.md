# Hook CTA Variant Comparison Spec

## Goal

在现有中文单线处理链路不变的前提下，从英文侧开始分叉出两套可对比的营销版本：

- `normal`：自然本土化英文，对照组
- `hook_cta`：带 TikTok US 黄金 3 秒钩子和明确 CTA 的增强组

两套版本都要分别产出英文文案、ElevenLabs 音频、英文字幕、合成视频和 CapCut 工程包，并在网页端同一步骤内左右双列展示，方便直接对比效果。

## Problem

当前翻译链路只有一条英文输出，适合做“单版本优化”，但不适合做营销策略 A/B 对比。用户现在需要同时看两套英文策略的完整结果，而不是只看一版翻译后再人工脑补哪种更适合短视频带货。

缺失点主要有三类：

1. 没有“双版本英文分支”的任务状态结构
2. 没有针对 TikTok US 黄金 3 秒与 CTA 的稳定提示词策略
3. 网页端当前只能展示单套英文产物，无法在同一步骤中并排比较

## Scope

本次改造覆盖：

- 保留中文公共链路：`utterances`、`scene_cuts`、`script_segments`、`source_full_text_zh`
- 新增英文双版本链路：`normal` 与 `hook_cta`
- 为两套版本分别生成：
  - localized translation
  - ElevenLabs script
  - TTS audio
  - English ASR
  - corrected subtitle / SRT
  - soft/hard video
  - CapCut export
- 网页端在翻译、语音、字幕、视频、CapCut 步骤中双列展示

本次不做：

- 中文 ASR / 中文分段 / 分镜检测的双跑
- 新的人工编辑器
- 新的推荐排序或自动选优逻辑

## Design Principles

1. 中文只跑一次，英文从翻译开始分叉，避免重复成本
2. `normal` 与 `hook_cta` 共享输入，但从 `localized_translation` 开始完全独立产出
3. 对比必须是“完整链路对比”，而不是只比翻译文本
4. Prompt 约束必须可执行，不写模糊的“尽量更抓人”
5. 所有 variant 文件都显式带上 variant 名称，避免覆盖

## Variant Strategy

### Shared Chinese Upstream

以下字段继续只生成一次：

- `utterances`
- `scene_cuts`
- `script_segments`
- `source_full_text_zh`

这四类数据作为英文双版本的共同输入，不重复计算。

### Variant Keys

系统新增固定 variant 键：

- `normal`
- `hook_cta`

页面文案显示名称：

- `normal` -> `普通版`
- `hook_cta` -> `黄金3秒 + CTA版`

## Task State

任务状态新增 `variants` 根字段，结构如下：

```json
{
  "source_full_text_zh": "...",
  "script_segments": [...],
  "variants": {
    "normal": {
      "label": "普通版",
      "localized_translation": {},
      "tts_script": {},
      "tts_result": {},
      "english_asr_result": {},
      "corrected_subtitle": {},
      "timeline_manifest": {},
      "result": {},
      "exports": {},
      "artifacts": {},
      "preview_files": {}
    },
    "hook_cta": {
      "label": "黄金3秒 + CTA版",
      "localized_translation": {},
      "tts_script": {},
      "tts_result": {},
      "english_asr_result": {},
      "corrected_subtitle": {},
      "timeline_manifest": {},
      "result": {},
      "exports": {},
      "artifacts": {},
      "preview_files": {}
    }
  }
}
```

说明：

- 旧的单体字段可在迁移期保留，但页面和新流水线优先读取 `variants.*`
- `variants.*.artifacts` 专门用于网页端双列预览
- `variants.*.preview_files` 用于每个 variant 的音频/视频预览文件

## Prompting Strategy

### `normal` Variant

`normal` 版继续保持现有自然本土化策略：

- 自然、美国本土、适合 TikTok 电商口播
- 保留原有卖点与节奏
- 不强制加入黄金 3 秒钩子
- 不强制加入 CTA

这个版本作为纯翻译/本土化对照组。

### `hook_cta` Variant

`hook_cta` 版在保留自然本土化的前提下，新增硬性提示词约束。

#### Golden 3 Seconds Rule

模型必须将第一句视为前 3 秒核心钩子，按正常美式口播速度将“前 3 秒”近似理解为前 `7-10` 个英文词。第一句必须满足：

- 目标词数 `7-10`
- 承担 TikTok US 短视频开头钩子功能
- 优先使用以下钩子模式之一：
  - 强结果
  - 明确利益点
  - 好奇心
  - 反差/惊喜

#### CTA Rule

全文必须包含且仅包含一次主要 CTA。CTA 要求：

- 明确催促下单
- 听起来像美国 TikTok creator / shop seller 的自然说法
- 可以放在中段或结尾，由模型根据文案自然度决定
- 不能机械堆砌多个 CTA

允许的 CTA 风格示例：

- `grab one`
- `shop now`
- `pick one up today`
- `tap to order`

#### Hook CTA Prompt Contract

`hook_cta` 版 prompt 需要明确写出以下硬约束：

- Sentence 1 must function as the first-3-seconds hook
- Treat the first 3 spoken seconds as roughly the first 7-10 English words
- The full script must contain exactly one clear purchase CTA
- You may reorder sentence emphasis to improve hook performance, but must preserve the original selling points

## Pipeline

### 1. Localized Translation

对每个 variant 独立调用 `generate_localized_translation`：

- `normal`：使用标准 prompt
- `hook_cta`：使用 hook + CTA prompt

输出：

- `variants.<key>.localized_translation`

并分别保存到：

- `localized_translation.normal.json`
- `localized_translation.hook_cta.json`

### 2. ElevenLabs Script

对每个 variant 独立生成：

- `variants.<key>.tts_script`

并分别保存：

- `tts_script.normal.json`
- `tts_script.hook_cta.json`

`tts_script` 仍然是各自版本中 `文案 = 语音 = 字幕` 的唯一文本真相源。

### 3. TTS Audio

对每个 variant 独立调用 ElevenLabs：

- `variants.normal.tts_result`
- `variants.hook_cta.tts_result`

输出文件命名带 variant：

- `tts_full.normal.mp3`
- `tts_full.hook_cta.mp3`

### 4. English ASR And Subtitle

对每个 variant 独立执行：

- 英文 ASR
- subtitle alignment
- SRT 生成

输出文件：

- `english_asr_result.normal.json`
- `english_asr_result.hook_cta.json`
- `subtitle.normal.srt`
- `subtitle.hook_cta.srt`

### 5. Timeline And Video

对每个 variant 独立编译：

- `timeline_manifest.normal.json`
- `timeline_manifest.hook_cta.json`

并独立合成：

- `{task_id}_soft.normal.mp4`
- `{task_id}_soft.hook_cta.mp4`
- `{task_id}_hard.normal.mp4`
- `{task_id}_hard.hook_cta.mp4`

### 6. CapCut Export

每个 variant 都单独导出一套草稿：

- `capcut_normal/`
- `capcut_hook_cta/`
- `capcut_normal.zip`
- `capcut_hook_cta.zip`

CapCut manifest 也各自保留：

- `codex_export_manifest.normal.json`
- `codex_export_manifest.hook_cta.json`

## Web UI

中文公共步骤保持单列：

- 音频提取
- 语音识别
- 分段确认

从英文分叉开始改为双列对比。

### Translation Step

左右双列展示：

- 左：普通版
- 右：黄金3秒 + CTA版

每列展示：

- 整段英文文案
- 英文句子映射
- `source_segment_indices`

### TTS Step

每列展示：

- 完整音频播放器
- ElevenLabs 文案全文
- `blocks`
- `subtitle_chunks`

### Subtitle Step

每列展示：

- English ASR
- 校正后字幕块
- SRT 文本

### Compose Step

每列展示：

- 软字幕视频
- 硬字幕视频

### CapCut Export Step

每列展示：

- CapCut 工程包下载
- export manifest 文本

## API And Artifact Model

任务详情接口继续返回一个 task 对象，但要扩展出 `variants`。

网页端 preview artifact 需要支持两种数据模式：

1. 原单列模式
2. variant compare 模式

建议 artifact payload 增加：

```json
{
  "title": "翻译本土化",
  "layout": "variant_compare",
  "variants": {
    "normal": { "label": "普通版", "items": [...] },
    "hook_cta": { "label": "黄金3秒 + CTA版", "items": [...] }
  }
}
```

这样前端不用为每个步骤单独写死双列逻辑，只要支持一种通用 compare layout 即可。

## Output Naming

所有英文 variant 输出都必须显式带 variant 名称。

禁止：

- 两个版本共用同一文件名
- 用后一次运行覆盖前一次 variant 结果

## Error Handling

1. 任一 variant 失败，不应直接抹掉另一 variant 的结果
2. 页面要能显示“普通版成功 / hook_cta 版失败”这种部分成功状态
3. 如果 `hook_cta` 版没有生成出 CTA 或首句不满足规则，应视为结构失败并中断该 variant
4. `normal` 与 `hook_cta` 的失败状态和错误文案必须相互独立

## Testing

需要新增和修改的测试覆盖：

1. `hook_cta` prompt 明确包含黄金 3 秒和 CTA 硬约束
2. 任务状态能正确保存 `variants.normal` 与 `variants.hook_cta`
3. 双 variant 的产物文件名不会互相覆盖
4. TTS / subtitle / compose / export 都能按 variant 独立输出
5. 前端能渲染 `variant_compare` 布局
6. 某个 variant 失败时，另一个 variant 的结果仍然保留并可预览

## Success Criteria

满足以下条件才算本次改造完成：

1. 上传一个中文视频后，中文侧步骤仍然只跑一次
2. 页面从翻译步骤开始能同时看到普通版和黄金3秒+CTA版
3. 两版都能分别试听音频、预览视频、下载 CapCut
4. `hook_cta` 版首句明显更像 TikTok US 前 3 秒钩子
5. `hook_cta` 版全文中稳定出现一次自然 CTA
6. 两版所有输出文件都能稳定并存，不互相覆盖
