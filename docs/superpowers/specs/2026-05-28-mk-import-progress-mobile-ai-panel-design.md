# 明空入库进度弹窗移动端 AI 评估适配设计

- **日期**：2026-05-28
- **范围**：选品中心 / 明空选品 / 视频素材库卡片的“加入素材库”进度弹窗
- **状态**：用户已确认
- **锚点来源**：用户确认“加入素材库做移动端适配；AI 评估信息滑不动，移动端从上往下展示，或者左右滑动顺畅也行”

## 上位锚点

- `AGENTS.md`：文档驱动代码、选品/任务/素材流程闭环、移动端验证要求。
- `docs/superpowers/specs/2026-05-20-mk-import-progress-modal-design.md`：入库进度弹窗必须展示当前步骤、失败原因和后续入口。
- `docs/superpowers/specs/2026-05-22-mk-import-progress-card-contained-actions-design.md`：进度弹窗步骤卡片不限制固定高度，内容可以自然撑开并由弹窗滚动。
- `docs/superpowers/specs/2026-05-22-single-product-five-country-ai-evaluation-design.md`：精细 AI 评估结果固定展示 `DE`、`FR`、`IT`、`ES`、`JP` 五国结构化结果。

## 背景

“加入素材库”进度弹窗中的 `AI 精细评估建议` 当前按五国横向表格渲染。桌面上横向对比效率高，但在手机弹窗内，表格最小宽度较大，用户容易只能看到左侧和前几个国家，且横向滑动手势会被弹窗纵向滚动抢占。

## 目标

1. 手机宽度下，AI 精细评估建议改为从上到下展示，避免依赖横向滑动。
2. 桌面端保留现有横向五国对比表，不改变信息密度。
3. 移动端每个国家都展示国家名/代码、AI 评分、评估结果、详细说明、风险与建议。
4. 移动端卡片顺序固定为 `DE`、`FR`、`IT`、`ES`、`JP`，与精细评估设计一致。
5. 不改变入库流程、AI 评估接口、评估结果结构、按钮行为或弹窗步骤状态。

## 非目标

- 不重做精细 AI 独立页。
- 不改变小语种任务弹窗国家勾选逻辑。
- 不改变 AI 评估生成、缓存、重跑或外部链接 run 的业务逻辑。
- 不新增后端接口或数据库字段。

## 交互设计

- `mkiImportProgressFineAiTable(result)` 同时输出桌面表格容器和移动端国家卡片容器。
- 桌面宽度继续显示 `.mki-progress-fine-ai-scroll` 内的横向表格。
- `max-width: 560px` 下隐藏横向表格，显示 `.mki-progress-fine-ai-mobile-list`。
- 每张移动端国家卡片使用该国家对应的决策色调，并包含：
  - 国家标题：中文国家名 + 国家码。
  - 评分行：`AI 评分` + 数值和进度条。
  - 结果行：`评估结果` + 决策 pill。
  - 说明行：`详细说明` + 结论摘要。
  - 建议行：`风险与建议` + 最多 3 条建议列表。
- 无结果国家仍展示占位卡片，提示先完成该国家精细评估。

## 验收标准

1. 模板中存在 `.mki-progress-fine-ai-mobile-list` 和 `.mki-progress-fine-ai-mobile-card`。
2. `mkiImportProgressFineAiTable(result)` 输出移动端卡片容器，且卡片使用 `mkiFineAiProgressTableRows(result)` 的五国顺序。
3. `max-width: 560px` 下隐藏 `.mki-progress-fine-ai-scroll` 并显示 `.mki-progress-fine-ai-mobile-list`。
4. 桌面端默认隐藏 `.mki-progress-fine-ai-mobile-list`，继续显示横向表格。
5. 静态测试覆盖移动端容器、卡片 class 和响应式 CSS。
6. 聚焦 pytest、`python3 -m compileall web tests -q`、`git diff --check` 通过。
