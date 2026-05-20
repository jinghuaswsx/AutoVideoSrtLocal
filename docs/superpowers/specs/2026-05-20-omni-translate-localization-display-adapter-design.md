# 全能视频翻译本土化对照展示适配设计

- 日期：2026-05-20
- 模块：全能视频翻译 `omni_translate` 详情页翻译本土化展示
- 参考任务：`a389551e-c4a6-4047-b7d7-19ffed2dc550`

## 文档锚点

- `AGENTS.md`：文档驱动代码、隔离 worktree、详情路由验证顺序。
- `web/templates/CLAUDE.md`：翻译详情页必须继承 `_translate_detail_shell.html`，通用工作台脚本承担详情页交互。
- `docs/superpowers/specs/2026-05-13-omni-asr-primary-compact-timeline-design.md`：Omni `shot_char_limit` 翻译调试展示必须包含 `第一轮全文翻译对照` 和 `第一轮逐句翻译对照`，旧任务需要前端从 task 状态补齐。
- `docs/superpowers/specs/2026-05-18-english-redub-speed-aware-voice-match-design.md`：英语重配音复用 Omni 流水线和详情壳，`script_mode=original` 仍通过 `translate` artifact 展示原文组装/本土化文案对照。

## 背景

全能视频翻译详情页的“翻译本土化”用于排查第一轮翻译是否保留了原文含义、是否结合了画面上下文，以及后续 TTS 收敛是否从正确文本出发。

当前英语重配音模块会在 translate 阶段构造 `localized_translation`，再写入 `artifacts.translate`，详情页能展示全文对照和句子映射。全能视频翻译的 `shot_char_limit` 流程也会写入类似数据，但通用工作台的前端补齐逻辑偏向分镜表格：当旧任务的 stored artifact 缺少全文对照或逐句对照时，页面不能稳定按英语重配音的方式展示；即使数据存在，全文/逐句对照也可能排在较长的分镜过程表之后，调试入口不够直接。

## 目标行为

1. 全能视频翻译的 translate 预览必须优先展示 `第一轮全文翻译对照`。
2. 全能视频翻译的 translate 预览必须展示 `第一轮逐句翻译对照`，每行包含原文、目标语言本土化文本、时间信息和可用的画面上下文。
3. 页面应复用英语重配音的 task-state 兼容思路：优先使用 `artifacts.translate`，缺项时从 `task.translations`、`task.localized_translation`、`task.script_segments`、`task.utterances` 补齐。
4. 已完成的旧 Omni 任务不需要重跑流水线，也不需要数据库迁移。
5. 分镜翻译摘要、分镜过程表、目标语言句子映射继续保留，但不能把全文对照和逐句对照埋在主要调试内容之后。

## 非目标

- 不改 translate LLM prompt。
- 不改 `shot_char_limit`、`standard` 或英语重配音的后端翻译结果生成逻辑。
- 不回填数据库中的历史 `artifacts.translate`。
- 不改变多语种翻译、日语翻译和英语重配音的 API 路由。

## 展示适配

通用工作台脚本在渲染 `translate` step 时执行展示适配：

1. 读取 stored `artifacts.translate`。
2. 如 artifact 缺失，且 task 中存在 `localized_translation` 或逐句翻译数据，则构造一个 `翻译本土化` artifact。
3. 如 artifact 已存在但缺少 `side_by_side`，从 task 状态补齐 `第一轮全文翻译对照`。
4. 如 artifact 已存在但缺少 `translation_pairs`，或逐句内容可从 task 状态刷新，则补齐 `第一轮逐句翻译对照`。
5. 对 translate artifact 的 items 做稳定排序：
   - 分镜摘要类信息可保留在最前。
   - `第一轮全文翻译对照` 排在主要调试区域前部。
   - `第一轮逐句翻译对照` 紧跟全文对照。
   - 长分镜过程表和目标语言句子映射排在对照内容之后。

## 验证

- 单元测试覆盖旧 Omni artifact 只有目标句子映射时，前端脚本仍包含补齐全文对照和逐句对照的逻辑。
- 单元测试覆盖 translate artifact 排序逻辑，确保 `side_by_side` 和 `translation_pairs` 排在长分镜过程表之前。
- 运行相关 pytest：

```bash
python3 -m pytest tests/test_preview_artifacts.py tests/test_translate_detail_shell_templates.py tests/test_runtime_omni_dispatch.py tests/test_english_redub_runtime.py -q
```
