# 明空素材入库 product_code 唯一冲突复用修复

日期：2026-05-20

## 背景

`/xuanpin/mk#videos` 点击 `加入素材库` 时，`appcore.mk_import.import_mk_video()` 会先按明空 `product_code` 判断本地素材库里是否已有产品。截图中的入库流程在 `检查产品与链接` 阶段失败：

```text
create product failed: (1062, "Duplicate entry 'tool-free-robotics-building-set-rjc' for key 'media_products.uk_media_products_product_code'")
```

这说明 `media_products.product_code` 唯一键已经有该产品，但入库流程仍走了新品插入分支。

## 锚点

- `docs/superpowers/specs/2026-04-26-mk-import-design.md#6.5-老品翻译员复用`：产品已存在时，忽略本次传入的 `translator_id`，沿用 `media_products.user_id` 并在老品下新增英文素材。
- `docs/superpowers/specs/2026-04-15-medias-add-single-page-design.md#产品-id`：`media_products.product_code` 全库唯一，冲突应按业务语义处理。
- `docs/superpowers/specs/2026-05-20-mk-import-product-link-gate-scope-fix.md#目标`：普通 `POST /mk-import/video` 只负责素材入库，保持 DB 失败语义，但不应把可复用老品误判成新品。

## 目标

1. 创建新品时如果命中 `uk_media_products_product_code` 唯一冲突，立即按该 `product_code` 重新查找未删除产品。
2. 如果查到未删除产品，切换为老品分支：沿用该产品 `user_id`，继续下载明空视频并写入该产品下的英文素材。
3. 如果只存在软删除产品或仍无法查到未删除产品，继续返回 `db_failed`，不自动恢复软删除产品。
4. 不改变视频 filename 去重、视频下载、素材写入、产品链接 warning、域名选择弹窗和任务创建链路。

## 非目标

- 不修改 `media_products` 唯一键或增加迁移。
- 不硬删除或恢复软删除产品。
- 不改素材卡片状态缓存、定时任务或广告状态逻辑。

## 验收

1. `execute(INSERT INTO media_products...)` 抛出 `uk_media_products_product_code` duplicate 时，若未删除产品存在，`import_mk_video()` 返回 `is_new_product=false`。
2. 复用老品后，`media_items.user_id` 使用已有产品负责人，不使用本次选择的翻译员。
3. 其它 DB 异常仍映射为 `DBError` / 路由 `db_failed`。

## 验证

```bash
pytest tests/test_appcore_mk_import.py::test_import_mk_video_reuses_existing_product_after_product_code_duplicate_race -q
pytest tests/test_mk_import_routes.py -q
pytest tests/test_appcore_mk_import.py -q -k 'not db_test and not download_mp4_streams_to_path and not download_mp4_404_raises and not import_mk_video_new_product and not import_mk_video_old_product_ignores_translator and not import_mk_video_dedupes_by_filename and not find_existing_product_matches_normalized_code and not find_existing_product_no_match and not is_video_already_imported_yes_no'
python3 -m compileall appcore web tests -q
git diff --check
```

说明：`tests/test_appcore_mk_import.py` 中部分旧用例会直连项目数据库；本修复的本地验证只跑无真实 DB 的单元路径，避免触发本地 MySQL 禁止规则。
