# LLM 卡片标题供应商与模型标注

日期：2026-05-13

## 文档锚点

- `AGENTS.md` "Stack" / "主题指引"：LLM 统一入口为 `appcore.llm_client`，新业务在 use case 体系中登记。
- `docs/superpowers/specs/2026-05-13-omni-shot-translate-model-tag-design.md`：模型标签展示应显示实际供应商与模型 ID，格式为 `<provider> · <model_id>`。
- `docs/superpowers/specs/2026-05-13-omni-llm-card-prompt-visualization-design.md`：LLM debug payload 通过 `llm_debug_refs` 关联到步骤卡片，payload 内记录 use_case、provider、model。
- `docs/superpowers/specs/2026-05-01-llm-client-consolidation-design.md`：实际 provider/model 应以 use case binding 和 LLM 入口解析结果为准，不在前端硬编码渠道。

## 背景

用户需要在所有大模型调用相关卡片标题上直接看到供应商和模型 ID，以便追踪当前任务实际调用了哪些模型、走了什么通道、属于哪条业务逻辑。

当前状态：

- 任务工作台的 `step_model_tags` 只追加在步骤消息后面，不在卡片标题行展示。
- `llm_debug_refs` 已保存 provider/model/use_case，但标题行只展示"提示词"按钮。
- translate lab 与文案创作页面有独立步骤卡片渲染，也只把模型标签放在消息或步骤内容里。
- 少量旧日语翻译路径把 `ja_translate.localize` 当作 provider/model 写入 debug ref 和 model tag，不能表达实际 OpenRouter/Gemini 通道。

## 范围

本次覆盖已有任务/步骤卡片的标题标注，不改 prompt、调用参数、流程顺序或服务发布动作。

需要调整：

1. `_task_workbench` 详情页标题行：
   - 视频翻译、多语种、全能、德语、法语、日语等共用步骤卡片。
   - 标题行展示去重后的 provider/model 标签。
   - 数据来源为 `step_model_tags[step]` 和 `llm_debug_refs[step]`。
   - 多次 LLM 调用显示多枚标签，悬停信息带 `use_case` / phase / label。
2. translate lab 详情页：
   - 每个步骤标题后显示 `step_model_tags[step]`。
3. 文案创作详情页：
   - 每个步骤标题后显示 `step_model_tags[step]`，运行时 socket 更新也同步标题。
4. 旧日语翻译路径：
   - `ja_translate.localize` 的运行中标签、debug ref 和 debug payload 使用当前 binding 解析出的 provider/model。

## 非目标

- 不为历史任务补写缺失的 debug 文件或修改已落盘 JSON。
- 不改 LLM prompt、temperature、max tokens、并发、重试策略。
- 不新增 provider 或模型枚举。
- 不改非卡片类后台报表、计费列表或纯命令行脚本。

## 设计

- UI 标签正文统一为 `provider · model_id`。
- `step_model_tags` 是即时状态来源；`llm_debug_refs` 是补齐来源，特别是历史任务或完成后才有 debug payload 的步骤。
- 同一步若有相同 provider/model，只显示一次；不同 use_case 或 phase 进入同一标签的 tooltip。
- 标签统一放在标题行，消息区只保留步骤状态文本，避免模型信息在卡片内重复和被长消息挤掉。
- 前端只渲染后端提供的 provider/model，不推断、不硬编码。

## 验收

- 工作台步骤标题行能从 `step_model_tags` 显示 `openrouter · google/gemini-3-flash-preview`。
- 工作台步骤标题行能从 `llm_debug_refs` 派生 provider/model，即使 `step_model_tags` 为空也能展示。
- 同一步多个 LLM 调用按 provider/model 去重，但 tooltip 保留 use_case/phase。
- translate lab 和文案创作步骤标题显示模型标签，运行时 socket 更新同步刷新。
- 日语翻译初始调用的 debug ref 不再把 `ja_translate.localize` 当 provider/model。
- 相关静态资产和 runtime 测试通过。
