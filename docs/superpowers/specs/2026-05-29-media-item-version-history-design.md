# 小语种视频素材覆盖与历史版本设计

最后更新：2026-05-29

## 背景

任务中心手动提交小语种视频时，之前出现过两类相反风险：

- 按 `product_id + lang` 覆盖会导致同产品不同源素材互相串覆盖。
- 完全 append-only 又会让同一个源素材同一个语种的“重新翻译”变成多张当前素材卡片，不符合素材管理心智。

新的目标是：同一个产品下，同一个源素材做的同一个小语种视频，应该是一张当前素材卡片；重新翻译时覆盖当前卡片，但覆盖前必须保存历史版本。不同源素材之间不能互相覆盖。

## 目标

1. 视频卡片保持现有 UI 和布局，不重做卡片结构。
2. 只在视频卡片操作区新增“历史版本”入口。
3. 以 `product_id + source_raw_id + lang` 作为小语种视频当前卡片的归并身份。
4. 重新提交同一身份的视频时，先归档旧版本，再更新当前 `media_items` 记录。
5. 不同 `source_raw_id` 的小语种视频永远不能互相覆盖。
6. 一组素材包含一个视频和一张封面；历史版本也必须同时保存旧视频和旧封面。
7. 普通员工不能删除小语种当前视频，也不能删除历史版本。
8. 管理员可以删除当前视频卡片；删除当前卡片不级联删除历史版本。
9. 管理员可以在历史版本弹窗中逐条删除旧版本。

## 非目标

- 不改素材卡片的主布局、预览方式、语言分组和现有编辑交互。
- 不做历史版本恢复为当前版本；本期只支持查看和管理员删除。
- 不回填所有历史被覆盖版本的真实文件内容；能回填的只限当前数据库仍可识别的版本关系。

## 数据模型

新增表 `media_item_versions`，用于保存被覆盖前的旧版本。建议字段：

- `id`
- `media_item_id`：当前卡片对应的 `media_items.id`
- `product_id`
- `lang`
- `source_raw_id`
- `version_no`
- `filename`
- `display_name`
- `object_key`
- `cover_object_key`
- `file_size`
- `duration_seconds`
- `thumbnail_path`
- `task_id`
- `archived_by`
- `archived_at`
- `archive_reason`
- `deleted_at`
- `deleted_by`
- `deleted_object_key`
- `deleted_cover_object_key`

索引建议：

- `(media_item_id, deleted_at, version_no)`
- `(product_id, source_raw_id, lang, deleted_at)`

`media_items` 继续作为“当前版本”表，不新增多张当前卡片。

## 一致性要求

- 覆盖时，写入 `media_item_versions` 和更新当前 `media_items` 必须在同一个数据库事务中完成。
- 被归档的旧视频对象和旧封面对象不能在覆盖时删除，因为历史版本弹窗仍需要预览它们。
- 删除当前卡片时，只删除当前 `media_items` 正在引用的视频对象和封面对象。
- 删除历史版本时，只删除该条 `media_item_versions` 正在引用的视频对象和封面对象。

## 覆盖规则

任务中心手动提交视频时：

1. 根据子任务找到 `product_id`、`lang`、源素材 `source_raw_id`。
2. 如果没有 `source_raw_id`，不执行覆盖归并，创建新记录，避免误串。
3. 查询当前记录：`product_id + source_raw_id + lang + deleted_at IS NULL`。
4. 找到当前记录时：
   - 把当前记录的视频、封面、文件名、任务号等写入 `media_item_versions`。
   - 更新同一条 `media_items` 为新视频信息。
   - 保留同一个 `media_items.id`，让素材卡片仍是同一张。
5. 找不到当前记录时：
   - 创建新的 `media_items` 当前记录，并写入 `source_raw_id`、`task_id`。

手动提交封面时：

- 只更新当前任务或当前源素材对应的视频记录的 `cover_object_key`。
- 不允许把封面写到其他源素材或其他语种的视频记录上。

## 删除规则

当前视频卡片删除：

- 普通员工：拒绝删除小语种视频。
- 管理员：允许软删当前 `media_items` 记录，并删除当前视频对象和当前封面对象。
- 删除当前卡片不级联删除 `media_item_versions`，历史版本保留。

历史版本删除：

- 普通员工：拒绝。
- 管理员：允许软删单条 `media_item_versions`，并删除该历史版本的视频对象和封面对象。
- 删除历史版本不影响当前 `media_items`。

## API 设计

新增：

- `GET /medias/api/items/<item_id>/versions`
  - 返回当前素材卡片的历史版本列表。
  - 每条包含版本号、文件名、任务号、归档时间、视频预览 URL、封面 URL、是否可删除。

- `DELETE /medias/api/item-versions/<version_id>`
  - 管理员删除单条历史版本。
  - 删除视频对象和封面对象后软删版本记录。

调整：

- `DELETE /medias/api/items/<item_id>`
  - 传入当前用户管理员身份。
  - 管理员删除小语种当前视频时，同时处理当前视频和当前封面对象。

## UI 设计

素材卡片保持现有布局，只增加一个“历史版本”按钮：

- 无历史版本时可以隐藏按钮，或显示为禁用态；推荐隐藏，减少噪音。
- 有历史版本时显示“历史版本”或“历史版本 (N)”。
- 点击后打开弹窗，按版本倒序展示：
  - 版本号
  - 文件名
  - 任务号
  - 提交/归档时间
  - 旧视频预览
  - 旧封面预览
  - 管理员删除按钮

历史弹窗只读展示为主，不提供恢复。

## 兼容与回填

已有数据中：

- `source_raw_id` 明确的记录，可以按 `product_id + source_raw_id + lang` 进入新覆盖模型。
- `source_raw_id` 为空的旧小语种视频，不参与自动覆盖归并，避免误覆盖。
- 已经被旧逻辑覆盖掉且数据库中没有保留旧对象 key 的版本，无法完整恢复为历史版本。

## 测试计划

单元测试：

- 同 `product_id + source_raw_id + lang` 手动提交视频时，归档旧版本并更新当前 `media_items`。
- 不同 `source_raw_id` 手动提交视频时，不覆盖旧记录。
- 覆盖时旧 `object_key` 和旧 `cover_object_key` 都写入历史版本。
- 普通员工不能删除当前小语种视频。
- 管理员可以删除当前小语种视频，且删除当前视频和当前封面对象。
- 删除当前视频不删除历史版本。
- 普通员工不能删除历史版本。
- 管理员可以删除历史版本，且删除历史视频和历史封面对象。
- 历史版本列表 API 只返回未删除版本。

前端测试/轻量验证：

- 有历史版本的卡片显示“历史版本”入口。
- 点击入口展示历史版本弹窗。
- 管理员可见历史删除按钮；普通员工不可见。

## 发布注意

- 新 migration 必须放入 `db/migrations/`，由 `appcore/db_migrations.py` 启动时自动应用。
- 生产发布前先在测试环境确认 migration 应用成功。
- 发布后抽查一个真实产品：同源同语种重提交后当前卡片保持一张，历史版本弹窗能看到旧视频和旧封面。
