# Omni 动态恢复与提示词前端展现修复

日期：2026-05-07

## 文档锚点

- `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md` §6：Omni runner 的步骤列表必须基于 `task.plugin_config` 动态生成。
- `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md` §7.1：四套基线 preset 必须从 `extract` 跑到 `export`。
- `docs/superpowers/specs/2026-04-26-omni-explicit-source-language-design.md` §E/F：用户重选源语言后复用 ASR 文本，从 ASR 后处理步骤继续。
- `docs/project-audit-2026-05-01.md` 2026-05-02 multi `asr_normalize` 恢复兼容记录：恢复入口必须补齐标准化步骤需要的语言状态和下游产物清理。
- `AGENTS.md` “翻译详情页 Jinja 模板继承防呆”：详情页追加内容必须留在 `_translate_detail_shell.html` 的 block 内。

## 背景

Omni 合并后不再只有固定的 `asr_clean` 流程。不同 preset 会动态插入或替换以下步骤：

- `separate`
- `shot_decompose`
- `asr_clean` 或 `asr_normalize`
- `av_sync_audit`
- `loudness_match`

当前 Web 恢复入口、详情页步骤顺序和通用 runner 的自动重试仍有固定步骤假设，导致部分 preset 在手动恢复、重选源语言、前端轮询或异常重试时无法按真实步骤继续。

## 修复范围

1. `web/routes/omni_translate.py` 的 `resume` 和 `source-language` 入口必须按当前任务真实 `plugin_config` 计算 step list。
2. 禁止把 `asr_normalize` 无条件别名成 `asr_clean`。只有真实 step list 中存在的步骤才允许作为恢复起点。
3. `source-language` 改选后从当前配置的 ASR 后处理步骤继续：`asr_clean` 配置从 `asr_clean` 继续，`asr_normalize` 配置从 `asr_normalize` 继续。
4. 重置步骤和清理 artifacts 必须使用真实 step list，覆盖 `separate`、`shot_decompose`、`av_sync_audit`、`loudness_match` 等动态步骤。
5. `_task_workbench` 的 omni 前端步骤顺序必须来自后端实际 step list，而不是静态数组。
6. omni 动态步骤如果产生 LLM 调用记录，前端提示词检查器必须能显示对应步骤按钮和 payload。`av_sync_audit` 的 Doubao 诊断与 Gemini 复核要落 LLM debug refs。
7. `PipelineRunner._run` 收到不存在于当前 step list 的 `start_step` 时必须标记错误并发出 pipeline error，不能静默空跑。
8. 自动重试恢复点必须按本轮实际 step list 判断，不能只靠固定 `_ALL_STEP_NAMES`。

## 非目标

- 不改 multi 生产步骤语义。
- 不改变 `plugin_config` validator 的字段定义。
- 不重做详情页布局；仅让已有工作台按实际步骤显示。
- 不把 `shot_decompose` 强行做提示词检查器，除非该步骤已有可持久化的 LLM 调用 payload。

## 验收

- `asr_normalize` preset 调 `/api/omni-translate/<id>/resume` 时能从 `asr_normalize` 继续，不会映射到不存在的 `asr_clean`。
- `asr_clean` preset 调重选源语言时从 `asr_clean` 继续；`asr_normalize` preset 从 `asr_normalize` 继续。
- `separate`、`shot_decompose`、`av_sync_audit`、`loudness_match` 在真实 step list 中存在时可以作为恢复起点。
- 无效 `start_step` 返回 400；底层 runner 直接收到无效 `start_step` 时进入 `error`，不会保持 `running`。
- omni 详情页 `STEP_ORDER` / `MAIN_STEPS` 与后端真实步骤一致，包含动态步骤并保持正确顺序。
- `av_sync_audit` 跑过后，详情页提示词检查器能读取 Doubao 诊断和 Gemini 复核的请求 payload。
