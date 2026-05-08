# Omni 详情页 preset 配置标注设计

日期：2026-05-08

## 文档锚点

- `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md#45-task-表存储`：omni 任务创建时把当前能力点配置展开存入 `task.plugin_config`，详情页读取该快照，不回查 preset。
- `AGENTS.md#翻译详情页-Jinja-模板继承防呆`：omni 详情页追加内容必须留在 `_translate_detail_shell.html` 的 block / shell 体系内，不能在完整 HTML include 后追加裸 HTML。
- `AGENTS.md#Frontend-Design-System--Ocean-Blue-Admin`：新增 UI 使用 Ocean Blue token，避免紫色/靛蓝。

## 背景

Omni 新建任务时用户可以选择系统级或用户级 preset，也可以微调能力点后直接创建。任务落库后只有 `plugin_config` 快照是事实来源；后续 preset 被改名、修改或删除，不应影响已有任务详情页的展示。

当前 `/omni-translate/<id>` 顶部只显示返回、强制重开和原始语言，用户无法在详情页快速确认本任务实际使用了哪套能力点组合。截图红框位置需要补一个当前任务配置标注。

## 设计

在 omni 详情页顶部工具条的原始语言选择器右侧渲染一个紧凑的配置摘要：

```text
当前配置  omni-current  ASR 原样清洗 / 标准翻译 / Source anchored / 五轮重写 / ASR 对齐字幕 / 人声分离 / 响度匹配
```

展示规则：

1. 优先读取当前任务 `state.plugin_config`，经 `validate_plugin_config()` 规范化后展示。
2. 配置快照缺失时，按运行时已有兜底链展示全站默认 preset 或硬编码默认配置，并标注 `默认配置`，避免误认为任务有明确快照。
3. 若规范化后的配置匹配四套内置基线，显示基线名：`multi-like`、`omni-current`、`av-sync-current`、`lab-current`。
4. 若不匹配基线，显示 `自定义配置`。
5. 标注只做只读展示，不提供详情页修改能力；强制重开仍沿用任务当前配置快照。
6. 文案展示当前已支持的 9 个能力点，包括 `av_sync_audit`。审计为 `off` 时可简写为 `审计关闭`，其他值展示实际模式。

## 非目标

- 不改变任务创建、resume、restart、runner step dispatch 逻辑。
- 不把任务绑定回某个 preset id；历史任务只认 `plugin_config` 快照。
- 不重做顶部工具条布局，只在截图红框位置补充摘要。

## 验收

- `/omni-translate/<id>` 详情页能渲染 `当前配置` 标注。
- `state.plugin_config` 等于 `omni-current` 基线时显示 `omni-current`。
- 非基线组合显示 `自定义配置` 并展示关键能力点文案。
- 旧任务缺少 `plugin_config` 时页面不报错，并显示 `默认配置`。
- 模板仍通过 `_translate_detail_shell.html` 体系渲染，没有回到 include 完整 shell 后追加内容的反模式。
