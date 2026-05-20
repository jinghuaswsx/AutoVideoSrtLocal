# 明空素材入库 Shopify 链接门禁范围修正

日期：2026-05-20

## 背景

`/xuanpin/mk#videos` 的素材卡片有两个动作：

- `加入素材库`：把明空原始视频下载并写入本地素材管理库。
- `做小语种`：入库后立即创建任务中心父任务和国家子任务。

2026-05-20 的任务中心原始素材自动化把 Shopify 商品链接可访问性门禁接入到 `appcore.mk_import.import_mk_video()`。该门禁用于防止创建任务时产生无法上线的商品链接，但它同时影响了普通 `加入素材库`：当明空返回的商品链接或系统规范化后的 Shopify 链接尚未发布、返回 HTTP 404 时，素材入库会被提前阻断。

## 锚点

- `docs/superpowers/specs/2026-04-26-mk-import-design.md`：`mk-import` 的目标是同步入库素材，不自动建任务；错误处理只要求覆盖视频下载、存储、DB 和重复文件名。
- `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`：`做小语种` 走 `appcore.tasks.import_and_create_task()` 链式完成入库和任务创建。
- `docs/superpowers/plans/2026-05-20-task-center-raw-source-automation.md`：Shopify 链接门禁属于任务创建链路，目标是“create missing products ... before product creation or task creation continues”。

## 目标

1. 普通 `POST /mk-import/video` 只负责素材入库，不因 Shopify 商品链接 HTTP 404 阻断。
2. `POST /tasks/api/import-and-create` 也不因 Shopify 商品链接 HTTP 404 阻断；先完成素材入库和任务创建。
3. 保持产品创建时写入规范化 `product_code + "-rjc"` 和 canonical product link 的现有行为。
4. 保持视频下载失败、重复文件名、存储失败和 DB 失败的现有响应语义。
5. Shopify 商品链接可访问性校验暂不放在明空入库/做小语种链路，后续按业务场景另行设计。

## 非目标

- 不改变明空视频下载地址、封面地址、素材库状态图标或前端卡片渲染。
- 不新增数据库字段、迁移或定时任务。
- 不删除或调整素材管理、AI 评估、链接检测等其它模块已有的商品链接校验。

## 设计

- `appcore.mk_import.import_mk_video()` 不探测 Shopify 商品链接，只保留 canonical product link 写入。
- `appcore.tasks.import_and_create_task()` 不向入库层传链接可访问性门禁参数。
- `find_existing_product_item_by_meta()` 只按 product code 找已有产品和英文素材，不探测 Shopify 商品链接。
- `/mk-import/video` 和 `/tasks/api/import-and-create` 不再映射 `product_link_unavailable` 作为本链路错误。

## 验收

1. Shopify 商品链接返回 404 时，`import_mk_video()` 默认仍会继续进入 MP4 下载/素材创建流程。
2. Shopify 商品链接返回 404 时，`tasks.import_and_create_task()` 仍会继续创建父子任务。
3. 已入库视频走 `find_existing_product_item_by_meta()` 回退时，不探测 Shopify 商品链接。
4. 相关路由不再返回 `product_link_unavailable`。

## 验证

```bash
pytest tests/test_appcore_mk_import.py tests/test_appcore_tasks.py tests/test_mk_import_routes.py tests/test_tasks_routes.py -q
python -m compileall appcore web tests -q
git diff --check
```
