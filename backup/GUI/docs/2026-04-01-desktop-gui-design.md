# Desktop GUI Design

## Goal

在保留现有核心流水线能力的前提下，为 AutoVideoSrt 增加一个可在 Windows 上运行、可打包为 `.exe` 的原生桌面 GUI 版本。桌面版优先覆盖当前 Web 版的核心使用路径：

- 选择本地视频
- 配置音色与字幕位置
- 启动任务
- 查看步骤状态和中间产物
- 对比 `normal` / `hook_cta` 两个英文版本
- 预览音频、视频、字幕
- 下载结果
- 将 CapCut 草稿显式部署到剪映草稿目录

目标不是做一个“套壳网页”，而是构建一个长期可维护的原生 PySide6 客户端，同时把现有 Web 版的业务执行逻辑抽成共享运行时，避免未来出现两套流水线逻辑分叉。

## Problem

当前项目已经具备比较完整的处理链路，但运行时组织方式高度偏向 Web：

- 任务状态主要通过 `web/store.py` 的进程内字典维护
- 任务执行主要耦合在 `web/services/pipeline_runner.py`
- 状态更新通过 `socketio.emit(...)` 推送给网页
- 页面预览完全依赖 Flask 路由和 HTML/JS 渲染

这意味着如果直接做桌面 GUI，会遇到三个问题：

1. 任务执行层与 Web 传输层耦合过深，无法直接复用到 Qt 信号/槽体系
2. 任务状态结构同时承担“业务状态”和“Web 预览适配”两种职责，边界不清晰
3. 如果桌面版直接复制现在的 Web 逻辑，很快会变成两套互相漂移的实现

因此，桌面版的关键不只是“做一个窗口”，而是先抽出共享运行时，再在其上构建原生 GUI。

## Scope

本次桌面版设计覆盖：

- 抽出共享任务运行时
- 抽出共享任务状态模型和事件模型
- 在此基础上实现 PySide6 原生 GUI
- 保持现有 Web 版可继续运行
- 增加 Windows `.exe` 打包方案

本次不覆盖：

- 多任务历史库或数据库持久化
- 波形级编辑器
- GUI 内复杂时间线编辑
- Mac / Linux 桌面适配
- 自动升级器

## Design Principles

1. **共享核心，分离适配层**
   流水线、状态、事件必须收敛到共享运行时；Web 和 GUI 只做展示与交互适配。

2. **先抽边界，再做界面**
   先解开 `pipeline_runner + store + socketio` 的耦合，再接 PySide6；不在耦合层上继续堆功能。

3. **第一版聚焦核心流程**
   桌面版优先覆盖“可运行、可观察、可对比、可导出、可部署到剪映”，不追求一次做完全部辅助功能。

4. **GUI 复用状态结构，不复用 HTML**
   页面结构和信息组织可以参考 Web 版，但 GUI 组件必须是原生 Qt，而不是嵌一个浏览器。

5. **桌面体验优先**
   文件选择、长任务、播放器、错误提示、部署到剪映目录等能力必须符合 Windows 桌面软件预期。

## Architecture

整体分为三层：

### 1. Shared Runtime Layer

新增一个独立于 Web 的共享运行时层，例如：

- `appcore/runtime.py`
- `appcore/task_state.py`
- `appcore/events.py`
- `appcore/artifacts.py`

职责：

- 创建任务
- 维护任务状态
- 按步骤执行流水线
- 记录中间产物路径
- 派发统一事件

这一层不能直接依赖：

- Flask
- Socket.IO
- HTML/JS
- PySide6

它只依赖现有 `pipeline/` 核心模块和轻量的 Python 标准库/工具模块。

### 2. Adapter Layer

在共享运行时之上，分别实现两个适配器：

- `web/` adapter
- `desktop/` adapter

Web adapter 负责：

- 将 runtime 事件映射为 Socket.IO 事件
- 将 task state 映射为 HTTP API 返回结构
- 将 artifacts 映射为 Web 预览 payload

Desktop adapter 负责：

- 将 runtime 事件映射为 Qt signals
- 将 task state 映射为 Qt model / widget 数据
- 将 artifacts 映射为原生预览组件

### 3. Presentation Layer

#### Web Presentation

现有 Flask + HTML 页面继续存在，但不再直接控制流水线。

#### Desktop Presentation

新增 PySide6 原生界面：

- `desktop/main.py`
- `desktop/app.py`
- `desktop/window.py`
- `desktop/widgets/`

## Shared Runtime Design

### Task State

共享任务状态将成为桌面版与网页版共同依赖的单一真相源。建议保留当前任务结构的大部分字段，但从“纯业务状态”角度重新组织。

保留核心字段：

- `id`
- `status`
- `video_path`
- `task_dir`
- `original_filename`
- `steps`
- `utterances`
- `scene_cuts`
- `alignment`
- `script_segments`
- `source_full_text_zh`
- `variants.normal`
- `variants.hook_cta`

其中 `variants.*` 继续承载：

- `localized_translation`
- `tts_script`
- `tts_result`
- `english_asr_result`
- `corrected_subtitle`
- `timeline_manifest`
- `result`
- `exports`
- `artifacts`
- `preview_files`

### Event Model

共享运行时不再直接 `emit socketio event`，而是发统一的事件对象，例如：

- `task_started`
- `step_update`
- `artifact_ready`
- `alignment_result`
- `translate_result`
- `tts_script_ready`
- `english_asr_result`
- `subtitle_ready`
- `capcut_ready`
- `pipeline_done`
- `pipeline_error`

建议事件结构统一为：

```python
{
  "type": "step_update",
  "task_id": "...",
  "payload": {
    "step": "tts",
    "status": "running",
    "message": "正在生成语音..."
  }
}
```

这样 Web 和 GUI 都只关心消费事件，不关心流水线内部细节。

### Runner

现有 `web/services/pipeline_runner.py` 需要拆成两部分：

- `appcore/runtime.py`
  - 真正执行 `_step_extract/_step_asr/...`
  - 更新共享 task state
  - 发标准事件

- `web/services/pipeline_runner.py`
  - 退化为 Web adapter
  - 订阅 runtime 事件并转发到 socketio

桌面版则通过 Qt adapter 将这些事件转成信号。

## Desktop GUI Design

### Main Window Layout

第一版建议采用单窗口、多面板布局：

#### Left Panel

负责输入与控制：

- 视频文件选择
- 当前任务摘要
- 音色选择
- 字幕位置选择
- 开始处理按钮

#### Center Panel

负责步骤状态：

- 与 Web 版相同的 8 步卡片
- 每步展示状态、提示文案
- 当前步骤自动高亮

#### Right / Bottom Preview Panel

负责中间产物预览：

- 文本：原生文本区域
- 音频：Qt 音频播放器
- 视频：Qt 视频播放器
- 字幕：SRT / 字幕块文本查看

当步骤是单版本内容时显示单列；
当步骤是英文双版本内容时显示左右两列对比：

- `normal`
- `hook_cta`

### GUI Widgets

建议拆分以下组件：

- `TaskConfigPanel`
- `StepListWidget`
- `ArtifactPreviewWidget`
- `VariantCompareWidget`
- `AudioPreviewWidget`
- `VideoPreviewWidget`
- `SubtitlePreviewWidget`
- `CapcutExportWidget`

每个组件只负责一类显示和交互，不直接执行流水线。

### Media Preview

PySide6 第一版需要支持：

- 音频播放：`QMediaPlayer + QAudioOutput`
- 视频播放：`QMediaPlayer + QVideoWidget`

需要重点验证 Windows 打包后依赖是否齐全。

### CapCut Deploy UX

桌面版沿用当前 Web 版的新规则：

- 导出阶段默认只在任务目录生成草稿
- 不自动复制到剪映目录
- 在 `CapCut 导出` 区域为每个 variant 提供单独按钮：
  - `部署普通版到剪映`
  - `部署黄金3秒 + CTA版到剪映`

按钮点击后，调用共享部署动作，把对应草稿复制到：

`C:\Users\admin\AppData\Local\JianyingPro\User Data\Projects\com.lveditor.draft`

若用户设置了 `JIANYING_PROJECT_DIR`，则优先使用该目录。

## Packaging Strategy

### Tooling

建议使用 `PyInstaller` 打包。

原因：

- 生态成熟
- 与 PySide6 搭配常见
- 适合快速生成 Windows `.exe`

### Packaging Requirements

打包时需要显式纳入：

- PySide6 相关模块
- 多媒体相关插件
- `voices/voices.json`
- `capcut_example/`
- 静态资源（如果桌面版有图标、样式、占位图）

### Runtime Resource Strategy

GUI 版启动后仍然读取：

- `.env`
- `OUTPUT_DIR`
- `UPLOAD_DIR`
- `VOICES_FILE`
- `CAPCUT_TEMPLATE_DIR`
- `JIANYING_PROJECT_DIR`

如果缺少关键配置，要弹出桌面错误提示，而不是只在控制台打印。

### Entry Points

新增桌面入口：

- `desktop/main.py`

Web 入口保持：

- `main.py`

这样可以长期保持两套入口，但共用同一套 runtime。

## Migration Plan

实现顺序建议如下：

### Phase 1: Runtime Extraction

- 抽共享 task state
- 抽共享 event bus
- 抽共享 runtime runner
- 让 Web 版先接到新 runtime 上

这一步完成后，Web 应继续可用。

### Phase 2: Desktop Shell

- 建立 PySide6 主窗口
- 接入文件选择、配置、启动处理
- 接入步骤状态列表

### Phase 3: Artifact Preview

- 文本预览
- 音频播放器
- 视频播放器
- 双版本对比区
- CapCut 导出与部署按钮

### Phase 4: Packaging

- PyInstaller 配置
- Windows 可执行文件产出
- 首轮手工验证

## File Structure

新增：

- `appcore/runtime.py`
- `appcore/task_state.py`
- `appcore/events.py`
- `appcore/artifacts.py`
- `desktop/main.py`
- `desktop/app.py`
- `desktop/window.py`
- `desktop/widgets/`
- `desktop/resources/`
- `packaging/pyinstaller.spec`

修改：

- `web/services/pipeline_runner.py`
- `web/store.py`
- `web/routes/task.py`
- `web/preview_artifacts.py`
- `main.py`
- `requirements.txt`
- `readme_codex.md`

## Testing

### Unit Tests

- runtime 事件派发测试
- task state 更新测试
- Web adapter 回归测试
- CapCut 手动部署动作测试

### GUI Tests

第一版可以先做到：

- 基础窗口初始化测试
- 任务状态映射测试
- 预览组件渲染测试

不强制一开始就做完整 UI 自动化点击测试。

### Packaging Verification

至少需要人工验证：

- `.exe` 能启动
- 能选择视频并启动任务
- 能看到处理中状态
- 能播放音频和视频
- 能导出 CapCut 草稿
- 能点击按钮部署到剪映目录

## Risks

### 1. Runtime Extraction Risk

这是最大的工程风险。如果抽 runtime 时边界没切干净，桌面版会把 Web 版的历史耦合再次复制一份。

### 2. Qt Multimedia Packaging Risk

Windows 下 `QMediaPlayer` 在开发环境可用，不代表打包后就稳定可用，需要尽早做打包验证。

### 3. Scope Creep Risk

桌面版很容易被拉成“再顺手做任务历史、批处理、时间线编辑器”。第一版必须严格守住核心流程。

## Success Criteria

以下条件同时满足，视为桌面版第一阶段设计成功：

1. Web 与 Desktop 共用同一套 runtime
2. Desktop 可原生运行，不依赖浏览器和本地 Flask 服务
3. 用户可在桌面版完成核心处理流程
4. `normal / hook_cta` 双版本可在桌面版中直接对比
5. 能打包为 Windows `.exe`
6. CapCut 草稿可通过按钮显式部署到剪映目录
