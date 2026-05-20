# 明空加入素材库资产落库修复

## 背景

`加入素材库` 会在素材库没有对应产品时自动新建 `media_products` 和英文 `media_items`。线上产品
`tool-free-robotics-building-set-rjc` 暴露出三个断点：

- 产品主图为空，但选品中心产品资产表已有 `product_main_image_url` / `product_main_image_object_key`。
- 英文文案为空，但明空产品详情 `mk_id=3528` 的 `texts[0]` 有英文素材。
- 视频文件已下载到 `UPLOAD_DIR`，但素材管理播放路由只读 `OUTPUT_DIR/media_store`，因此预览不可用；封面也没有写入 `cover_object_key`。

## 根因

`appcore.mk_import.import_mk_video()` 仍按早期实现把视频移动到 `UPLOAD_DIR/mk-import/...`，而当前素材管理统一使用 `appcore.local_media_storage`。同时 `_download_cover()` 的结果只停留在临时目录，没有绑定到 `media_items.cover_object_key` 或 `media_product_covers`。

## 修复范围

1. 明空视频入库时，视频对象使用 `object_keys.build_media_object_key()` 并写入 `local_media_storage`。
2. 明空视频封面优先复用选品中心本地封面对象，写入标准素材对象并绑定到 `media_items.cover_object_key`。
3. 新建产品时按去掉 `-rjc` 后的 product code 查询 `dianxiaomi_product_assets`，补产品主图 URL 和 EN 产品主图对象。
4. 新建产品时用 `mk_id` 拉取明空详情，取第一条 `texts` 格式化为英文文案写入 `media_copywritings`。
5. 资产补齐失败不阻断视频入库，但要返回 warnings，方便前端弹窗展示。

## 非目标

- 不改变 `加入素材库` 是否创建任务的行为。
- 不覆盖已有产品的人工主图和文案。
- 不把 Shopify 链接 404 变成普通入库阻断。
