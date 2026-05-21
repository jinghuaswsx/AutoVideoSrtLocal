# 任务中心去字幕原始视频素材自审设计

- 日期：2026-05-21
- 上位锚点：
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-assignment-and-niuma-automation-fix.md`
  - `docs/superpowers/specs/2026-05-20-task-center-step-review-assets-design.md`
  - `docs/superpowers/specs/2026-05-21-task-center-niuma-status-link-design.md`
  - `docs/superpowers/specs/2026-05-21-task-center-raw-source-reuse-design.md`

## 背景

父任务已经会在牛马去字幕完成后把结果视频覆盖到绑定的英文 `media_items`，并进入 `raw_review`。现有审核入口仍偏管理员审核，处理人需要等待管理员通过后，结果视频才会由 `approve_raw()` 入库到产品的原始视频素材库。

业务希望牛马去字幕出第一版结果后，处理该任务的账号可以自己审核：结果可用时直接通过并入库；结果有问题时，处理人可手动处理后上传一个可用视频作为最终结果，并立即完成这一步、入库到产品原始视频素材库。

## 目标

1. “原素材处理”相关展示统一称为“去字幕原始视频素材处理”。
2. 父任务处于 `raw_review` 时，任务负责人和管理员都可以通过父任务审核；非负责人普通用户不能审核。
3. 牛马去字幕有结果后，在任务详情抽屉的该步骤底部展示两个大按钮：
   - `审核通过，结果视频入库`
   - `手动提交修改后的结果视频`
4. 点击通过按钮调用父任务通过接口，复用 `approve_raw()`，让当前绑定结果视频进入产品原始视频素材库，并解锁后续翻译子任务。
5. 点击手动提交按钮，在抽屉区域中心打开 modal，上传处理人自己修改后的视频；上传成功后直接把新视频作为最终结果入库，不再等待二次审核。
6. modal 提交成功后展示返回结果；关闭 modal 后，抽屉在按钮下方刷新出新提交的视频，当前步骤视为已通过。

## 非目标

- 不新增数据库表或字段。
- 不改变牛马字幕移除详情页。
- 不改变子任务翻译验收的管理员审核规则。
- 不引入本地 MySQL 验证。

## 设计

后端保留 `approve_raw()` 作为唯一入库入口，新增“父任务审核是否可由当前用户执行”的服务校验：管理员可以通过；父任务负责人可以通过；其他用户返回 403。现有 `/tasks/api/parent/<id>/approve` 去掉管理员硬门禁，改为委托服务层校验，避免前端绕过。

手动提交复用原始素材任务库已有的 `replace_processed_video()` 上传逻辑，并新增任务中心路由 `POST /tasks/api/parent/<id>/manual_result`。该路由仅允许父任务负责人提交视频；保存文件、写入 `raw_manual_uploaded` 与 `raw_uploaded` 后，继续调用 `approve_raw()`，让该视频直接进入原始视频素材库。响应返回文件大小和入库结果，供 modal 展示。

前端在 `raw_niuma_done` 步骤卡片底部渲染两个大按钮。按钮只在父任务 `raw_review` 且当前用户是负责人或管理员时出现。手动上传 modal 固定在详情抽屉区域中心，使用 `FormData` 上传视频；成功后先展示“已提交并入库”，关闭 modal 时刷新当前抽屉，因此按钮下方会显示最新 `raw_manual_uploaded` / `approved` / `raw_source_*` 流程记录和新视频。

## 验证

1. 路由单测覆盖：负责人可调用父任务通过接口；非负责人普通用户 403；管理员仍可通过。
2. 路由单测覆盖：任务中心手动提交视频委托上传服务，并在上传成功后调用 `approve_raw()`。
3. 模板断言覆盖：新命名、两个大按钮、手动提交 modal 和上传函数存在。
4. 聚焦验证命令：
   - `pytest tests/test_tasks_routes.py tests/test_raw_video_pool_service_unit.py -q`
   - `python -m compileall appcore/tasks.py appcore/raw_video_pool.py web/routes/tasks.py`
