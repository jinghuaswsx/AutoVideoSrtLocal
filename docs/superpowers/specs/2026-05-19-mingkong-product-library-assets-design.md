# 明空选品产品库商品图与中文名补全

## 背景

明空选品的“产品库”目前只展示店小秘 Listing 销量归档里的商品名、商品链接、销量和明空素材消耗。运营需要在同一行直接看到商品主图，并且希望采集 Listing 时同步补齐详情图线索和中文产品名。

## 范围

- `dianxiaomi_rankings` 继续作为产品库快照事实表，新增商品素材补充字段。
- 店小秘 Listing 采集时：
  - 从商品链接解析 `product_code`。
  - 访问商品链接，解析商品主图和详情图 URL。
  - 将主图下载到本地 media storage，保存 object key。
  - 使用 `product_code` 搜索明空素材库，取第一个匹配素材文件名，解析中文产品名。
- 产品库 API 返回本地主图 URL、详情图 URL 列表、中文名和 product_code。
- 前端产品库新增 200x200 商品图列；产品名称和 product_code 均最多两行显示，并提供复制按钮。

## 数据字段

在 `dianxiaomi_rankings` 上追加：

- `product_code`
- `product_main_image_url`
- `product_main_image_object_key`
- `product_detail_images_json`
- `product_assets_error`
- `product_cn_name`
- `mk_first_material_name`
- `mk_first_material_path`
- `mk_first_material_url`
- `mk_material_error`
- `product_assets_synced_at`

旧库未迁移时，产品库 API 保持兼容，缺字段返回空值。

## 采集策略

采集脚本对每条 Listing 行做 best-effort 补齐。商品页或明空接口失败不会让整次采集失败，只记录错误字段并继续处理其他商品。

商品图本地缓存使用确定性 key，基于 `product_code/product_id/image_url` 哈希生成，重复采集时如果本地文件已存在则跳过重新下载。

## 中文名解析

明空素材文件名优先按 `YYYY.MM.DD-中文名-原素材-...mp4` 解析，取日期后到 `-原素材` 前的文本。没有 `原素材` 标记时，退回取日期后的第一段。

## 前端

产品库表格在序号后新增“商品图”列，固定 200x200 框。图片源只允许 `/medias/object?...` 或 http(s) 安全 URL；无图时显示空态。商品名称与 product_code 各自两行截断，末尾复制按钮调用 Clipboard API，失败时退回临时 textarea。

## 验证

- 单元测试覆盖 Listing 行归一化、商品页图片解析、明空素材中文名解析、本地 object key 回填 SQL、产品库 API 兼容返回。
- 前端模板测试覆盖 200x200 图片列和复制按钮渲染逻辑。
