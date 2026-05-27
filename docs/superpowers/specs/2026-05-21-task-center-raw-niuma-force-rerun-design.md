# 任务中心牛马去字幕强制重跑设计

- 日期：2026-05-21
- 上位锚点：`AGENTS.md`、`docs/superpowers/specs/2026-05-21-task-center-niuma-status-link-design.md`、`docs/superpowers/specs/2026-05-21-task-center-raw-self-review-design.md`

## 背景

父任务创建后会自动提交牛马去字幕。外部任务偶尔会卡在提交、排队、轮询或结果回填阶段，任务中心目前只能查看“字幕移除任务页”，不能在任务中心直接把该父任务的去字幕链路从头再跑一遍。

## 目标

1. 父任务详情抽屉在 `raw_in_progress` 状态展示“强制重跑”入口。
2. 负责人或管理员可触发强制重跑；其他用户不能触发。
3. 强制重跑不删除旧字幕移除任务，不改历史事件，只追加 `raw_niuma_force_rerun` 记录并创建新的 `raw_niuma_submitted` 记录。
4. 重跑会把父任务拉回 `raw_in_progress`，继续保持子任务阻塞，等待新牛马结果进入原有审核链路。
5. 完成、取消、已进入翻译阶段的任务不允许强制重跑，避免覆盖已经验收或已翻译的素材。

## 设计

后端新增 `POST /tasks/api/parent/<id>/force_niuma_rerun`。路由只要求登录，权限由服务层判断。服务层读取父任务、负责人和当前状态：管理员可为任意符合状态的父任务重跑；负责人可重跑自己的父任务。触发时先写入 `raw_niuma_force_rerun`，记录发起人和最近一次字幕移除任务 ID，然后把父任务状态重置为 `raw_in_progress`，再复用现有 `start_niuma_processing_for_parent_task()` 创建新的牛马字幕移除任务。

前端在父任务详情按钮区增加“强制重跑”按钮。按钮仅在当前任务是父任务、状态为 `raw_in_progress`，且当前用户是负责人或管理员时显示。点击前弹出确认；成功后刷新列表和当前抽屉。

2026-05-27 补充：任务中心创建的字幕移除任务 ID 形如 `tcraw-<父任务ID>-<随机串>`。字幕移除详情页在识别到该来源且后端为 `niuma` 时，也展示红色“重跑”按钮；点击后复用 `POST /tasks/api/parent/<id>/force_niuma_rerun`，成功后跳转到新创建的字幕移除任务详情页。旧任务不被删除，仍作为历史排查记录保留。

## 验证

1. `pytest tests/test_task_raw_video_processing.py tests/test_tasks_routes.py -q`
2. `python -m compileall appcore/task_raw_video_processing.py web/routes/tasks.py`
3. 手工打开 `/tasks/`，进入卡住的去字幕父任务详情，确认按钮可见、非授权用户不可见，点击后出现新的“强制重跑”和“提交牛马去字幕”流程记录。
