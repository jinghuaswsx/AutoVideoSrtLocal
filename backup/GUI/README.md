# AutoVideoSrt Desktop GUI — Backup

存档日期：2026-04-01  
状态：已停止开发，转向 Web 端。

## 目录结构

```
backup/GUI/
├── desktop/                   # PySide6 桌面应用主体
│   ├── main.py                # 入口：config 校验 + 单实例锁 + 启动窗口
│   ├── window.py              # MainWindow：QSplitter 三栏布局，接收 EventBus 事件
│   ├── event_bridge.py        # 线程安全桥：pipeline 工作线程 → Qt 主线程
│   └── widgets/
│       ├── task_config.py     # 左栏：视频选择、音色、字幕位置、开始按钮
│       ├── step_list.py       # 中栏：步骤状态列表，点击触发预览
│       ├── artifact_preview.py # 右栏：文本/音频/视频预览，支持 artifact dict 渲染
│       ├── capcut_export.py   # 普通版/hook_cta 版一键部署到剪映
│       └── variant_compare.py # 双栏对比：normal vs hook_cta
├── desktop.spec               # PyInstaller onefile 打包配置
├── tests/                     # 单元测试（全部 mock，不依赖真实 pipeline）
│   ├── test_desktop_main.py
│   ├── test_desktop_capcut_widget.py
│   └── test_desktop_variant_compare.py
└── docs/
    ├── 2026-04-01-desktop-gui-design.md      # 设计规格
    └── 2026-04-01-desktop-gui-implementation.md  # 实施计划
```

## 架构

```
pipeline worker thread
        │ EventBus.publish()
        ▼
  EventBridge.emit_event()   ← Qt Signal，跨线程安全
        │
        ▼ (Qt main thread)
  MainWindow._handle_event()
        │
        ├─ EVT_STEP_UPDATE  → StepListWidget.update_step()
        ├─ EVT_PIPELINE_DONE → 恢复开始按钮
        └─ EVT_PIPELINE_ERROR → 显示错误

  用户点击步骤按钮
        │ step_clicked(step_id)
        ▼
  MainWindow._on_step_clicked()
        │ task_state.get(task_id)["artifacts"][step_id]
        ▼
  ArtifactPreviewWidget.show_artifact(artifact, preview_files)
```

## 已知问题（停止开发时未解决）

1. **exe 内音频/视频预览不显示** — `ffmpegmediaplugin.dll` 已打包进 exe，
   已在 `main.py` 设置 `QT_PLUGIN_PATH` 和 `QCoreApplication.addLibraryPath`，
   但 `QMediaPlayer` 在 frozen 环境下仍无法加载 multimedia backend。
   排查方向：检查 `_MEIPASS` 目录下 plugin 实际解压路径是否匹配。

2. **界面样式简陋** — 仅用默认 Qt 样式，无自定义 QSS。

3. **无法在处理中途停止任务** — pipeline 跑在 daemon thread，无取消机制。

## 恢复开发所需依赖

```
PySide6>=6.6.0
pyinstaller
```

打包命令：
```
pyinstaller desktop.spec --noconfirm
```
