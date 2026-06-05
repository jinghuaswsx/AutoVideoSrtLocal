# 对话式视频翻译流程与全能翻译对齐

最后更新：2026-06-05

## 背景

对话式视频翻译在运行时会额外插入说话人检测、说话人确认、A/B 音色匹配等步骤；这些步骤是对话式专属差异。除此之外，字幕配置、通用流程卡片展示、末尾翻译质量评估等能力应尽量复用全能视频翻译的现有模块，避免同类任务在 UI 和能力入口上产生分叉。

## 目标

- 对话式详情页的流程卡片顺序必须与 `DialogueTranslateRunner.pipeline_step_names_for_config()` 输出一致。
- 对话式页面不得复用全能/多语页面里只为视觉布局服务的 ASR 提前排序规则。
- 全能视频翻译已有的翻译质量评估卡片、API 路由和前端脚本应支持 `dialogue_translate`。
- 说话人检测、说话人确认、A/B 音色匹配和后续 A/B TTS 仍保持对话式专属实现。

## 非目标

- 不改动 A/B ASR 或 A/B TTS 的运行逻辑。
- 不改变全能视频翻译、批量翻译、音画同步已有流程顺序。
- 不新增本地数据库依赖或本机 MySQL 验证。

## 实现约束

- 流程卡片数据源继续由后端传入的 `pipeline_main_steps` / `pipeline_step_order` 决定。
- 共享质量评估使用既有 `translation_quality` blueprint 和 `quality_assessment_card.js`，仅补齐 `dialogue_translate -> /api/dialogue-translate` 映射。
- 对话式确认 A/B 音色后的提示文案应从页面实际 pipeline 顺序推导下一步名称，而不是硬编码 `alignment`。
