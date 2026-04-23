# 商品详情图翻译强制回填设计

## 背景

批量翻译任务里的商品详情图翻译目前采用保守策略：只要图片翻译子任务中存在任意失败项，自动回填就会整体跳过，父任务里的这一项也会停在失败态。实际业务里经常出现“20 张图里只有 1 张永久失败，其他 19 张都已成功”的情况，这会让整批任务无法收口，运营只能反复重跑，效率很低。

现有代码已经具备“把一个图片翻译任务里已成功的图片部分回填到详情图”的能力，但这个能力只存在于商品详情图编辑面板，不在批量翻译任务管理页里，也不会同步修正父任务状态和费用汇总。

## 目标

在批量翻译任务管理相关页面中，为“商品详情图翻译”子项增加 `强制回填` 操作，让运营可以在确认失败图片可以忽略时：

- 直接把该图片子任务中已成功的图片回填到对应语种的详情图
- 跳过失败图片，不要求整批图片全部成功
- 把该父任务子项标记为已完成
- 重新计算父任务整体状态，让整个批量任务可以继续收口
- 保留操作痕迹，能追溯是谁做了强制回填

## 非目标

- 这次不为视频封面翻译、视频翻译、文案翻译增加“强制完成”或“强制回填”
- 不做通用的“人工完成任意子任务”框架
- 不修改图片翻译运行时的默认自动回填策略，自动策略仍保持“全成才回填”

## 用户体验

### 显示位置

- 在商品级“翻译任务管理”页的子任务操作区，为符合条件的商品详情图翻译项显示 `强制回填`
- 在批量翻译父任务详情页的同类子任务卡片上同步显示 `强制回填`
- 按钮与 `查看详情`、`重跑此项` 保持同一层级，避免用户在两个入口看到不一致的操作集

### 显示条件

仅当以下条件同时满足时显示：

- 子项类型是 `detail_images`
- 子项 `child_task_type` 是 `image_translate`
- 子项当前状态是 `failed`
- 对应图片翻译子任务已经结束，不在运行中
- 对应图片翻译子任务里至少有 1 个成功项可回填

### 交互文案

- 按钮文案：`强制回填`
- 二次确认：`将把该图片任务中已成功的图片立即回填，并忽略失败图片；当前子项会被标记为已完成。确定继续吗？`
- 成功后刷新列表，子项状态改为 `已完成`
- 子项摘要补充人工兜底结果，例如：`强制回填：已回填 19 张，忽略失败 1 张`

## 方案

### 1. 父任务侧新增强制回填动作

在 `bulk_translate` 路由下新增一个父任务级动作接口，语义是“对某个父任务的某个子项执行强制回填”。

建议接口形态：

- `POST /api/bulk-translate/<task_id>/force-backfill-item`
- 请求体：`{ "idx": <int> }`

接口校验规则：

- 任务必须属于当前用户
- `idx` 必须存在且落在父任务 plan 范围内
- 目标子项必须是 `detail_images`
- 目标子项必须关联 `image_translate` 子任务
- 图片翻译子任务必须已结束，不能仍在运行
- 图片翻译子任务必须至少存在 1 个 `done` item

### 2. 复用已有部分回填能力

强制回填时直接复用现有能力：

- `appcore.image_translate_runtime.apply_translated_detail_images_from_task(task, allow_partial=True)`

这会复用当前详情图替换逻辑：

- 只回填成功生成的图片
- 跳过失败图片
- 只替换 `origin_type='image_translate'` 的旧译图
- 保留人工上传和链接下载的详情图

### 3. 父任务子项状态修正

强制回填成功后，父任务里的目标子项需要从失败态改成完成态，并补齐状态字段：

- `status = done`
- `result_synced = true`
- `finished_at = 当前时间`
- `error = None`

同时额外写入一段结构化结果，供投影层和前端摘要展示，例如：

- `forced_backfill = true`
- `forced_backfill_applied_count = N`
- `forced_backfill_skipped_failed_count = M`
- `forced_backfill_child_task_id = <child_task_id>`
- `forced_backfill_at = ISO 时间`

### 4. 父任务整体状态回算

强制回填成功后，需要基于现有 `_derive_parent_status(...)` 重新计算父任务状态：

- 若其他子项仍失败，父任务保持 `failed`
- 若只剩这一个失败项，此项完成后父任务可转为 `done`
- 若仍有 `awaiting_voice` 项，则父任务应回到 `waiting_manual`
- 若仍有执行中项，则父任务应保持 `running`

这一步必须在同一次保存中完成，避免前端刷新时出现“子项 done、父任务还 failed”的短暂脏状态。

### 5. 成本汇总补记

正常图片子项只有在 `_sync_child_result(...)` 成功时才会触发 `_roll_up_cost(...)`。强制回填跳过了这条标准路径，因此需要在父任务动作里显式补记一次图片成本，避免：

- 父任务实际费用偏低
- 管理页费用统计和真实已处理图片数量不一致

补记方式沿用现有规则：商品详情图翻译按源图片数量累计到 `cost_tracking.actual.image_processed`，再重算 `actual_cost_cny`。

为避免重复累计，需要在子项级别记一个“成本已结转”的标记；如果后续再次点击强制回填，应直接拒绝，而不是重复记费。

### 6. 审计与可追溯性

父任务 `audit_events` 追加一条新记录：

- `action = force_backfill_item`
- `detail.idx`
- `detail.child_task_id`
- `detail.applied_count`
- `detail.skipped_failed_count`
- `detail.apply_status`

这样后续查看父任务详情时，可以明确知道这不是系统正常全量成功，而是人工允许的部分成功收口。

## 投影与前端改动

### 投影层

`appcore.bulk_translate_projection` 需要为子项追加新字段，避免前端自己猜测：

- `force_backfillable`
- `force_backfill_summary`

`force_backfillable` 为真时，前端才展示按钮。投影判断可以通过读取子项现有状态以及关联图片子任务快照完成。

### 页面脚本

以下两个入口统一支持新动作：

- `web/static/medias_translation_tasks.js`
- `web/static/bulk_translate_detail.js`

改动点：

- 新增 `force-backfill-item` 按钮渲染
- 新增确认文案
- 调用新接口
- 成功后刷新页面数据
- 在子项摘要中显示人工强制回填结果

## 边界与失败处理

- 若图片子任务还在运行，返回 `409`
- 若图片子任务没有任何成功项，返回 `409`
- 若当前子项不是商品详情图翻译，返回 `400`
- 若已经执行过强制回填并完成，不再允许重复触发
- 若回填过程中本地图片文件已丢失，返回 `409` 并保留原失败态
- 若回填成功但摘要字段写入失败，应整体视为失败回滚，不允许“库里已回填、父任务仍失败”的半成功状态

## 测试

### 后端

- 新增父任务强制回填动作的单元测试
- 覆盖“部分成功可回填、零成功不可回填、运行中不可回填、重复回填不可重复记费”
- 覆盖父任务整体状态从 `failed -> done`、`failed -> waiting_manual`、`failed -> failed` 的回算
- 覆盖 `audit_events` 和 `cost_tracking.actual` 的更新

### 投影层

- 覆盖 `force_backfillable` 和 `force_backfill_summary` 的序列化

### 前端静态脚本

- 覆盖两个页面都渲染 `强制回填`
- 覆盖确认文案和按钮显示条件

## 基线说明

本次设计对应区域的基线测试里：

- `tests/test_medias_routes.py` 当前通过
- `tests/test_image_translate_runtime.py` 当前存在一组既有失败，主要因为测试仍在 patch 已不存在的 `appcore.image_translate_runtime.tos_clients`

这属于当前分支已有问题，不是本次功能新增造成。实现阶段需要明确是“先顺手修复这组基线”还是“先只隔离本次功能改动并补充新测试”。
