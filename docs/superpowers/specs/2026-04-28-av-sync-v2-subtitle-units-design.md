# 音画同步 V2 字幕级混合编排设计

日期：2026-04-28

## 背景

音画同步 V2 已经完成句子级本土化、句级 TTS、95%-105% 时长收敛、收敛后重建整段音频和句级可视化。下一步要把“全能视频翻译/多语言视频翻译”里成熟的字幕与流程思想迁入音画同步模块，但不直接复用其字幕 ASR 回识别步骤。

## 目标

1. 在音画同步模块中新增字幕级上层编排：`subtitle_units`。
2. 保留句子级原子作为底层可控单位：每句仍有 `asr_index`、TTS 片段、时长、收敛记录。
3. 新增模式选择：`sentence` 与 `hybrid`。默认使用 `hybrid`。
4. `hybrid` 模式下，字幕显示由 `subtitle_units` 直接生成，不再对最终 TTS 音频做 ASR 回识别。
5. 手工单句重写后，必须同步刷新对应字幕 unit、整段音频和 SRT。
6. 页面可视化同时展示句级收敛和字幕 unit 编排结果。

## 核心判断

多语言视频翻译的关键资产不是“最后再 ASR 一次”，而是：

- 翻译结构化输出。
- TTS 文案与字幕 chunk 分离。
- 每轮中间结果可视化。
- 最终字幕由已知结构生成。

音画同步需要迁入这些能力，但要保持更强控制：字幕时间轴来自最终句音频拼接顺序和句时长，而不是来自二次 ASR。

## 数据结构

`av_translate_inputs` 新增：

```json
{
  "sync_granularity": "hybrid"
}
```

合法值：

- `sentence`：一条句子生成一条字幕。
- `hybrid`：多句可组合成一个字幕 unit，字幕更自然，底层仍保留句子原子。

`variants.av` 新增：

```json
{
  "subtitle_units": [
    {
      "unit_index": 0,
      "sentence_indices": [0, 1],
      "asr_indices": [0, 1],
      "start_time": 0.0,
      "end_time": 3.2,
      "target_duration": 3.2,
      "tts_duration": 3.1,
      "text": "Natural localized subtitle text",
      "source_text": "原句一 原句二",
      "unit_role": "hook",
      "status": "ok"
    }
  ]
}
```

## 编排规则

1. 首译仍输出句级结果，保证底层一一对应。
2. 生成或收敛每句 TTS 后，按最终句子列表构建 `subtitle_units`。
3. `sentence` 模式：每个 sentence 生成一个 unit。
4. `hybrid` 模式：按句子顺序合并相邻句，满足以下任一条件就切分：
   - 已有 unit 时长达到约 3.2 秒。
   - 已有 unit 字符数达到约 72 字符。
   - 当前句和下一句 role 明显切换，如 hook 到 demo、demo 到 cta。
   - 当前句后出现较明显时间间隔。
5. unit 的时间轴按最终 TTS 片段累计时长生成，不依赖字幕 ASR。
6. SRT 由 `subtitle_units` 生成，文本使用已有 `build_srt_from_chunks` 的断行格式化能力。

## 页面可视化

在“句级收敛”面板下方新增“字幕编排”区域：

- unit 编号
- 覆盖句号
- 原文摘要
- 字幕文本
- 开始/结束
- 时长
- 状态

这能让用户看清“每条字幕是由哪些句子组成、最终显示什么、对应多长音频”。

## 非目标

1. 不修改普通视频翻译、多语言视频翻译链路。
2. 不引入新的 LLM provider。
3. 不在本阶段做复杂的 LLM 字幕 unit 再改写；先用规则化 unit 编排跑通，后续再升级为 LLM 辅助字幕级本土化。
4. 不恢复最终音频 ASR 回识别。

## 验证

1. `normalize_av_translate_inputs` 能持久化 `sync_granularity`。
2. `build_subtitle_units_from_sentences` 能按 `sentence` 和 `hybrid` 生成稳定 unit。
3. AV runtime 在最终状态保存 `subtitle_units`。
4. AV SRT 来自 `subtitle_units`，不是句级 segments 的默认 SRT。
5. 手工单句重写后刷新 `subtitle_units` 和 SRT。
6. 页面包含字幕编排可视化入口。
