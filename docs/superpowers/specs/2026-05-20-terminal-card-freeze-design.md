# 终态卡片冻结设计

日期：2026-05-20

## 锚点

- `AGENTS.md`：文档驱动代码、常规改动必须在隔离 worktree、改动后运行相关 pytest。
- `web/templates/CLAUDE.md`：多语种 / 全能视频翻译详情页必须通过 `_translate_detail_shell.html` 的 `detail_extra` 扩展，不在 shell 外追加 raw HTML。
- `docs/superpowers/specs/2026-05-07-voice-separation-card-placement-design.md`：多语种 / 全能视频翻译的人声分离卡片复用 `_task_workbench.html` 的 step 外壳，`_separation_card.html` 只负责往 `#preview-separate` 和 `#preview-loudness_match` 注入详细预览。
- `docs/superpowers/specs/2026-05-18-english-redub-speed-aware-voice-match-design.md`：`/english-redub` 复用 multi / omni 详情页壳和音色匹配 UI，API 前缀为 `/api/english-redub`。

## 问题

英文重新配音任务详情页中，人声分离结果已经完成后，卡片仍会在“通用 step preview”和“专用分离详情 preview”之间来回变化。

根因有两层：

1. `_task_workbench_scripts.html` 的 `specializedPreviewOwnsStep()` 只把 `/api/multi-translate` 和 `/api/omni-translate` 交给 `_separation_card.html` 接管，漏掉 `/api/english-redub`。
2. 主 workbench 在任一 step 为 `running` 或 `waiting` 时每 5 秒刷新整个 task state，并重渲染所有 step preview；`_separation_card.html` 也每 10 秒独立拉取 task state。已经有结果且不需要后续更新的卡片仍会被重写 DOM。

同类风险不限于人声分离。TTS 时长日志、普通 step preview、音色选择 / 音色匹配候选列表也会因为后续步骤刷新而被反复重绘。

## 目标

1. 已经进入终态且没有后续自动更新需求的卡片不再被后台轮询重绘。
2. 终态包括 `done`、`error`、`failed`、`interrupted`、`cancelled`、`timeout`、`disabled`、`unavailable`、`silence`。
3. `waiting` 不再驱动主 workbench 的 5 秒轮询。等待用户确认的卡片内容已经可见，后续变化应由用户操作或 socket 事件触发。
4. `/english-redub` 的人声分离 / 响度匹配预览与 multi / omni 一样由 `_separation_card.html` 接管。
5. 人声分离结果 `done` 后固定；失败、禁用、超时等终态也固定。只有仍在 `running`，或响度匹配结果尚未生成且下游仍可能补充 `tts_loudness` 时，才继续拉取最新 task state。
6. TTS 时长日志在 `converged`、`done`、`failed`、`source_video_passthrough`、`clipped_output` 等终态后冻结。
7. 音色选择器在 `voice_match_ready=true` 后固定当前候选列表，不再自动轮询或因后台状态刷新重绘；用户主动搜索、筛选、重选、性别重算仍允许本地或显式请求更新。

## 非目标

- 不改变后端 pipeline 步骤顺序、状态语义、artifact 结构或下载接口。
- 不停止仍在运行中的 step 刷新；运行态仍需要显示进度、耗时、socket 更新和错误状态。
- 不禁用用户主动操作触发的重绘，例如搜索音色、切换筛选、点击重算候选、展开 / 收拢 TTS 卡片。
- 不部署、不重启服务、不连接本机 MySQL。

## 设计

### 主 workbench

新增前端 helper：

- `isTerminalStepStatus(status)`：判断 step 是否已终态。
- `isRefreshableStepStatus(status)`：只把 `running` 视作需要后台轮询的状态。
- `shouldFreezeStepPreview(step)`：当该 step 已终态且 `#preview-<step>` 已经渲染过内容时，跳过 `renderStepPreviews()` 的 DOM 写入。

`taskNeedsLiveRefresh()` 改为只在存在 `running` step 时开启 5 秒轮询，不再因为 `waiting` 保持轮询。

`specializedPreviewOwnsStep()` 把 `/api/english-redub` 纳入接管范围，避免英文重新配音的人声分离 preview 被通用渲染覆盖。

### TTS 时长日志

`renderTtsDurationLog()` 在终态且 `#ttsDurationLog` 已渲染过时直接返回，避免后续轮询重建 audio、按钮、折叠头和日志 DOM。

运行态仍可更新轮次、phase、收敛曲线和最终摘要。终态第一次渲染必须保留，不能提前跳过。

### 人声分离 / 响度匹配

`_separation_card.html` 新增终态判断：

- `renderSeparate()` 在终态第一次渲染后给 `#preview-separate` 打冻结标记。
- `renderLoudnessMatch()` 在响度匹配已有终态内容后给 `#preview-loudness_match` 打冻结标记。
- `pollLatest()` 如果分离和响度两块都不再需要更新，则停止 interval。

`running` 状态继续刷新 elapsed 和服务端状态。`done` 但 `tts_loudness` 尚未出现时，允许继续短轮询以等待响度匹配产物；产物出现后冻结。

### 音色选择器

`voice_selector_multi.js` 在 `voice_match_ready=true` 的第一次渲染后停止自动轮询并记录 ready 快照。后续后台刷新不主动重拉库、不重绘列表。

保留用户主动操作：

- 搜索、推荐筛选、打开弹窗、选择音色，只做本地重绘。
- 性别胶囊 `/rematch` 是显式用户请求，允许更新候选。

## 验证

聚焦测试：

```bash
pytest tests/test_translate_detail_shell_templates.py tests/test_prompt_inspector_assets.py tests/test_voice_selector_multi_assets.py tests/test_english_redub_templates.py -q
```

相关路由 / 资产回归：

```bash
pytest tests/test_multi_translate_routes.py::test_voice_selector_multi_uses_configured_api_base_for_shared_endpoints tests/test_multi_translate_routes.py::test_voice_selector_multi_does_not_autoplay_result_video_after_compose 'tests/test_omni_translate_routes.py::test_resume_accepts_dynamic_steps_from_plugin_config[loudness_match]' tests/test_omni_translate_routes.py::test_resume_from_translate_clears_omni_current_and_downstream_state tests/test_english_redub_routes.py -q
```
