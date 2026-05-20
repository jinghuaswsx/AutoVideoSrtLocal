# 人声分离结果卡片刷新修复设计

日期：2026-05-20

## 背景

英语重新配音任务 `/english-redub/fc774e13-cab8-4543-9b52-36a9e0195792`
在人声分离完成后，详情页仍显示“调上游 GPU 分离 ...”这类过程文案。用户期望该卡片切换为包含处理过程和实际分离结果的结果页卡片。

同一套详情页壳体同时服务英语重新配音、多语种视频翻译和全能视频翻译：

- `web/templates/english_redub_detail.html`
- `web/templates/multi_translate_detail.html`
- `web/templates/omni_translate_detail.html`

三者都通过 `detail_extra` 引入 `web/templates/_separation_card.html`，因此修复必须覆盖三条路径。

## 锚点

- `AGENTS.md`：开发必须先有文档锚点；常规改动在隔离 worktree；每次改动后执行相关 pytest 和静态检查。
- `web/templates/CLAUDE.md`：翻译详情页追加内容必须走 `_translate_detail_shell.html` 的 `detail_extra`，不得 include shell 后追加 raw HTML。
- `docs/superpowers/specs/2026-05-07-voice-separation-card-placement-design.md`：人声分离卡片复用 `_task_workbench.html` 的 `#step-separate` 和 `#preview-separate`。
- `docs/superpowers/specs/2026-05-14-audio-separation-background-preserve-design.md`：`task["separation"]` 的完成态包含 `vocals_path` / `accompaniment_path` / `elapsed_seconds` / `vocals_lufs` / `video_lufs`，前端应展示双轨结果。

## 根因

`_separation_card.html` 初始化时只读取服务端首屏注入的 `state.separation`。如果用户在分离步骤开始前已经打开页面，首屏 `state.separation` 为空，专用 separation 卡片不会启动轮询。

随后 `_task_workbench_scripts.html` 通过 socket / 轮询拿到 step 更新，并把通用 step preview 渲染成占位文案。等后续 API 返回 `currentTask.separation` 后，通用工作台会因为 `specializedPreviewOwnsStep("separate")` 直接跳过该 preview，但 `_separation_card.html` 没有收到新的 task state，因此 `#preview-separate` 继续停在过程占位文案。

## 目标

1. 页面启动时没有 `state.separation`，后续 `currentTask.separation` 到达后，专用卡片必须立即接管 `#preview-separate`。
2. `separation.status === "done"` 时，人声分离卡片显示结果态：耗时、preset/goal、LUFS、vocals 与 accompaniment 两个 audio 结果，以及后续消费说明。
3. `running`、`timeout`、`unavailable`、`failed`、`silence`、`disabled` 等状态仍按现有专用卡片展示。
4. 修复覆盖 English redub、multi-translate、omni-translate 三条共享路径。
5. 不改变后端分离流程、artifact 命名、下载接口、响度匹配算法或页面卡片位置。

## 设计

`_separation_card.html` 暴露一个幂等的全局刷新入口，例如：

```js
window.refreshSeparationCardFromTask(task)
```

该入口接收最新 task state：

- 如果 task 带有 `separation` 字段，则更新 partial 内部 `separation` 变量。
- 同步 `steps.separate`、`steps.loudness_match`、`loudness_profile`、`loudness_manual_boost_pct`。
- 调用 `refreshAll()` 渲染 `#preview-separate` 和 `#preview-loudness_match`。
- 根据最新状态启动或停止 separation 专用轮询。

`_task_workbench_scripts.html` 在每次 `renderTaskState()` 结束前调用这个入口。这样无论 task state 来自首屏、socket、主动轮询还是 `pipeline_done` 刷新，separation 专用卡片都会拿到同一份最新状态。

为避免通用 step preview 继续覆盖专用结果，`specializedPreviewOwnsStep` 必须继续覆盖 `/api/english-redub`、`/api/multi-translate`、`/api/omni-translate`，且只要 `currentTask.separation` 存在即跳过通用 preview。

## 验收

- 模板测试覆盖全局刷新入口存在。
- 模板测试覆盖 `renderTaskState()` 会调用 separation 刷新入口。
- 模板测试覆盖入口会从 `task.separation`、`task.steps.separate`、`task.steps.loudness_match` 同步状态，并执行 `refreshAll()`。
- 聚焦验证命令：
  `pytest tests/test_translate_detail_shell_templates.py -q`
- 相关路由回归命令：
  `pytest tests/test_english_redub_routes.py tests/test_multi_translate_routes.py tests/test_omni_translate_routes.py -q`
