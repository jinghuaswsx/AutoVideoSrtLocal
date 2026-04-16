# 德语/法语视频翻译：移除软字幕视频 + AI 分析改为可选手动触发

日期：2026-04-16
分支：feature/image-translate

## 背景

德语（`DeTranslateRunner`）和法语（`FrTranslateRunner`）视频翻译模块目前主流程有 9 步：

```
extract → asr → alignment → translate → tts → subtitle → compose → analysis → export
```

现状存在两个问题：

1. **软字幕视频仍在生成与展示**
   - `compose_video()` 每次都生成一份带软字幕的 mp4 + 一份硬字幕 mp4。
   - 此前 commit `e852943` 已把 `build_compose_artifact()` 里的 `soft_video` 条目删掉，但：
     - 老项目 artifact 缓存里还保留着 `soft_video` 条目 → 前端仍然会渲染两个视频。
     - 后端仍然在生成 soft mp4、写 `result.soft_video`、设置 `preview_files.soft_video`、保留 `/download/soft` 接口，占用时间与磁盘。
2. **AI 视频分析阻塞主流程**
   - `analysis` 是主流程固定步骤，位于 compose 与 export 之间。
   - 虽然内部对 `score` 与 `csk` 各自 try/except，但仍是自动触发，主流程要等它跑完才能进入 export。
   - 用户希望把它当成"参考用的附加数据"，成功或失败都不打紧，且默认不跑，由用户手动触发。

本次只改德语和法语两个模块，不影响英语（`PipelineRunner` 基类默认行为保持不变）与 v2 流水线（`runtime_v2`）。

## 目标

1. 德语/法语主流程不再生成软字幕 mp4，也不再展示软字幕视频；老项目（artifact 里还有 `soft_video` 条目的）UI 立刻不再出现软字幕视频。
2. 德语/法语 AI 视频分析从主流程中移除，compose 完成后直接跑 export；AI 分析保留为时间线第 8 步卡片里的"运行 AI 分析"按钮，按需触发，成功/失败都不影响整体 `status`。
3. 英语主流程（`PipelineRunner`）行为保持不变。

## 非目标

- 不动 v2 流水线（`runtime_v2.py`）相关逻辑。
- 不做 DB 数据迁移脚本（老项目软字幕 artifact 条目通过前端防御式过滤兼容）。
- 不改 AI 分析本身的实现（评分 + CSK 的 Gemini 调用逻辑不变）。

## 主流程变化

```
改前（de/fr）：extract → asr → alignment → translate → tts → subtitle → compose → analysis → export
改后（de/fr）：extract → asr → alignment → translate → tts → subtitle → compose → export
                                                                                 + analysis（附加，手动触发）
```

- compose 完成后自动进入 export，整体 `status` 推进到 `done`。
- `analysis` 步骤仍然以"第 8 步"的顺位展示在时间线上（在 compose 之后、export 之前），但默认状态为 `idle`，不自动跑。
- 用户点击 analysis 卡片上的「运行 AI 分析」按钮才会触发；运行中状态在卡片内展示，完成后 artifact 在卡片 preview 区展示。
- analysis 的成功/失败只更新 `steps.analysis` 和 analysis artifact，**不修改 task 整体 `status`、`error` 字段**。

## 详细设计

### 1. `pipeline/compose.py`

给 `compose_video()` 新增参数 `with_soft: bool = True`（默认保持向后兼容）。

- 当 `with_soft=True`（默认，英语流水线保持此行为）：按现有逻辑生成软字幕 mp4 + 硬字幕 mp4，返回值 `result["soft_video"]` = 软字幕视频路径。
- 当 `with_soft=False`：跳过软字幕合成阶段，`result["soft_video"] = None`，其他返回字段不变。

### 2. `appcore/runtime.py`

- `PipelineRunner` 基类新增两个类属性：
  - `include_soft_video: bool = True`
  - `include_analysis_in_main_flow: bool = True`
- `_step_compose(...)` 调用 `compose_video(..., with_soft=self.include_soft_video)`。
- `_step_compose` 里 `if result.get("soft_video"):` 的 `set_preview_file` 分支不变（`with_soft=False` 时 `result["soft_video"]` 为 None，自然不会进分支）。
- `_run()` 按 `self.include_analysis_in_main_flow` 过滤 steps 列表；False 时跳过 `("analysis", ...)` 元组。
- 新增模块级函数 `run_analysis_only(task_id: str, user_id: int | None = None, runner_cls: type[PipelineRunner] = PipelineRunner) -> None`：
  - 单独启动后台线程。
  - 构造 runner 实例（带 EventBus 和 socketio handler 订阅，复用 de/fr 的 pipeline_runner 注册机制）。
  - 执行 `runner._step_analysis(task_id)`，内部异常捕获后只做 `_set_step(task_id, "analysis", "error", str(exc))` + 记录 artifact 的 `score_error`/`csk_error`，**绝不改 status**。
  - 实际上 `_step_analysis` 内部的 try/except 已经分别处理 score 和 csk，只需再包一层外层兜底即可。

### 3. `appcore/runtime_de.py` / `appcore/runtime_fr.py`

两个子类各自设置类属性：

```python
class DeTranslateRunner(PipelineRunner):
    project_type = "de_translate"
    include_soft_video = False
    include_analysis_in_main_flow = False
```

（`FrTranslateRunner` 同理。）

### 4. `web/services/de_pipeline_runner.py` / `fr_pipeline_runner.py`

- 新增 `run_analysis(task_id, user_id=None)` 入口函数，与现有 `start/resume` 平行：
  - 订阅 EventBus → socketio emit（复用 `_make_socketio_handler`）。
  - 后台线程执行 runner 的 analysis 步骤（通过 `run_analysis_only` 或直接 `runner._step_analysis`）。

### 5. 路由层 `web/routes/de_translate.py` / `web/routes/fr_translate.py`

- **删除** `GET /api/{de|fr}-translate/<task_id>/download/soft` 路由（若在 de/fr 自身路由里定义了；通过 task blueprint 通用下发的不删，见第 6 节）。
- **新增** `POST /api/{de|fr}-translate/<task_id>/analysis/run`：
  - 权限校验同现有 endpoint。
  - 检查 `task.steps.analysis` 不处于 `running`（幂等防重）。
  - 调用 pipeline_runner.run_analysis(task_id, user_id)。
  - 返回 `{"ok": true}`。

（如果 de/fr 路由里没有独立的 `/download/soft`（通过 task blueprint 统一处理），则需要看 [web/routes/task.py](web/routes/task.py) 里 `soft_video` 的处理；第 6 节补充。）

### 6. 下载接口兼容

搜索结果显示 `web/routes/task.py:118 / 125` 有 `soft_video` 的文件名映射，`web/services/artifact_download.py:31 / 132` 也引用了 `"soft"`。

- 若 task blueprint 的 `/download/<key>` 接口支持 `soft`：de/fr 调用时前端已不传 `soft`，不主动访问就不会 404；但为了彻底，新项目此后 `result.soft_video = None`，即使被调用也会返回 404。
- 不删 task.py 和 artifact_download.py 里的 soft 映射（保留英语流水线兼容性）。
- 纯搜索确认没有前端或其他地方对 de/fr 的 `soft` 下载发起请求。

### 7. 初始化任务 `steps.analysis` 状态

- 新建 de/fr 任务时，`steps.analysis` 从默认的 `"pending"` 改为 `"idle"`。
- 其他步骤保持 `"pending"`。
- 具体改动点：de/fr 的任务创建路由（或 task_state 的新建逻辑里，判断 `project_type` 给 `steps.analysis` 特殊初值）。

### 8. 前端 `_task_workbench.html`

- step-analysis 卡片增加按钮区：

```html
<div class="step-actions" id="actions-analysis">
  <button class="btn btn-primary btn-sm" id="runAnalysisBtn">运行 AI 分析</button>
</div>
```

按钮 default hidden，由脚本按 step 状态决定何时显示。

### 9. 前端 `_task_workbench_scripts.html`

- 步骤状态机新增 `idle`：
  - `idle`：显示「运行 AI 分析」按钮；按钮点击 → POST `/analysis/run`。
  - `running`：按钮 hidden，显示"AI 分析中..."。
  - `done`：展示结果 artifact + 显示「重新分析」按钮（样式次要，允许用户重跑）。
  - `error`：显示错误消息 + 「重新分析」按钮。
  - 「重新分析」和「运行 AI 分析」都走同一个 POST `/analysis/run` 路径。
- 删除 `if (currentTask.result?.soft_video) downloads.soft = _apiUrl('/download/soft');` 行。
- 进度条/整体状态计算：de/fr 项目时 analysis 步骤不计入主流程进度（通过 `STEP_ORDER` 过滤或判断 `include_analysis_in_main_flow`）。
  - 实现方式：前端 `TASK_WORKBENCH_CONFIG` 增加 `optionalSteps: ["analysis"]`；de/fr 模板注入时传入。
  - 主流程 `status === "done"` 的判断不再依赖 analysis 完成。
- 防御性过滤 compose artifact：渲染 compose items 时过滤 `item.artifact === "soft_video"`。老项目数据进来立刻不展示软字幕视频。

### 10. de/fr 详情页模板 `de_translate_detail.html` / `fr_translate_detail.html`

- 注入 `optional_steps: ["analysis"]` 到 JS config，或通过 `project_type` 让脚本自行判断。
- 注入 `api_base` 已存在（`/api/de-translate` / `/api/fr-translate`），`runAnalysis` 前端调用 `${api_base}/${task_id}/analysis/run`。

## 数据兼容策略

- **老项目 compose artifact 里的 `soft_video` 条目** → 前端渲染时过滤 `item.artifact === "soft_video"`。无需 DB 迁移。
- **老项目 `steps.analysis` 状态为 `"pending"` / `"running"` / `"done"`** → 兼容处理：
  - `pending`（老代码默认值） → 前端渲染时若属 de/fr 项目且步骤是 analysis，视为 `idle`，显示运行按钮。
  - `done` → 正常展示结果 artifact + 显示「重新分析」按钮。
  - `running` → 显示 running 状态（正常进行中）。
- **老项目 `result.soft_video` 已有值** → 前端已不再渲染也不再生成下载链接；保留原数据不强制清理。

## 测试策略

### 单元测试

- `tests/test_compose.py`（新增或增补）：
  - `compose_video(..., with_soft=False)` 不生成 soft 视频文件，`result["soft_video"]` 为 None。
  - `compose_video(..., with_soft=True)`（默认）行为不变。
- `tests/test_runner_de.py` / `tests/test_runner_fr.py`（新增或增补）：
  - `_run()` 执行步骤列表不含 `analysis`。
  - compose 步骤完成后自动进入 export。
  - 手动触发 `run_analysis_only()` 会执行 `_step_analysis` 且异常时不修改 `status`。

### 集成/路由测试

- `tests/test_de_translate_routes.py` / `test_fr_translate_routes.py`（新增或增补）：
  - POST `/analysis/run` 触发分析流水线；幂等防重。
  - GET `/download/soft` 返回 404（或该接口已不存在）。

### 手工 QA 点

1. 新建一个 de 项目，跑完主流程：
   - compose 卡片只展示硬字幕视频。
   - 没有生成 `*_soft.mp4` 文件。
   - compose 完成后立刻进入 export 并整体 `status === "done"`。
   - analysis 卡片显示「运行 AI 分析」按钮。
   - 点击按钮，analysis 正常跑完，结果在卡片展示。
   - 人为制造 analysis 失败（假 token），step 显示 error，task `status` 仍为 `done`。
2. 打开一个老的 de 项目：
   - compose 卡片只剩硬字幕视频（软字幕已被前端过滤）。
   - 下载栏不显示软字幕下载。
   - analysis 状态依据现存数据正常展示。
3. 新建 fr 项目重复上述 QA。
4. 新建 en 项目（走 `PipelineRunner` 默认）：
   - compose 仍生成软字幕视频（向后兼容）。
   - analysis 仍自动跑（向后兼容）。

## 风险与对策

- **compose_video 新参数向后兼容**：默认 True → 英语流水线和 v2 流水线行为完全不变。
- **老项目数据里 analysis 状态混乱**：通过前端状态机兼容，无需迁移。
- **手动触发并发**：同一任务连续点按钮 → 路由层检查 `steps.analysis === "running"` 拒绝重复启动。
- **analysis 过程中用户关闭页面**：后台线程仍会跑完，结果通过 socketio emit 推送；下次打开页面从 task_state 读取结果。

## 文件变更清单

- 新增/修改
  - [pipeline/compose.py](pipeline/compose.py) — `compose_video` 新增 `with_soft` 参数
  - [appcore/runtime.py](appcore/runtime.py) — 类属性 + steps 过滤 + `run_analysis_only`
  - [appcore/runtime_de.py](appcore/runtime_de.py) — 类属性覆写
  - [appcore/runtime_fr.py](appcore/runtime_fr.py) — 类属性覆写
  - [web/services/de_pipeline_runner.py](web/services/de_pipeline_runner.py) — 新增 `run_analysis`
  - [web/services/fr_pipeline_runner.py](web/services/fr_pipeline_runner.py) — 新增 `run_analysis`
  - [web/routes/de_translate.py](web/routes/de_translate.py) — `POST /analysis/run`；`steps.analysis` 初值 `idle`；可选删除 `/download/soft`
  - [web/routes/fr_translate.py](web/routes/fr_translate.py) — 同上
  - [web/templates/_task_workbench.html](web/templates/_task_workbench.html) — step-analysis 按钮
  - [web/templates/_task_workbench_scripts.html](web/templates/_task_workbench_scripts.html) — idle 状态、过滤 soft_video、optionalSteps
  - [web/templates/de_translate_detail.html](web/templates/de_translate_detail.html) — 注入 optional_steps
  - [web/templates/fr_translate_detail.html](web/templates/fr_translate_detail.html) — 注入 optional_steps
- 测试
  - [tests/test_compose.py](tests/test_compose.py)
  - [tests/test_runner_de.py](tests/test_runner_de.py) 或现有文件补充
  - [tests/test_runner_fr.py](tests/test_runner_fr.py) 或现有文件补充
  - [tests/test_de_translate_routes.py](tests/test_de_translate_routes.py) 或现有文件补充
  - [tests/test_fr_translate_routes.py](tests/test_fr_translate_routes.py) 或现有文件补充

## 落地顺序（后续实现计划的大致阶段）

1. pipeline.compose_video 新增 `with_soft` 参数 + 测试。
2. runner 基类加类属性、`_run` 过滤、`run_analysis_only` 模块函数。
3. de/fr runner 子类覆写类属性。
4. de/fr pipeline_runner 服务层新增 `run_analysis`。
5. de/fr 路由新增 `POST /analysis/run`、修正 `steps.analysis` 初值。
6. 前端 `_task_workbench.html` / `_task_workbench_scripts.html` 按钮 + 状态机 + 过滤 + 进度排除。
7. de/fr detail 模板注入 `optionalSteps`。
8. 老项目兼容冒烟测试。
