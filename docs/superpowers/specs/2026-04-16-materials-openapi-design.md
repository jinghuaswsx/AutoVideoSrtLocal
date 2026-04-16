# 素材信息获取开放接口设计

日期：2026-04-16

## 背景

当前素材管理的数据已经在后台统一管理，产品按 `product_code` 组织，包含：

- 产品基础信息
- 多语言产品主图
- 多语言文案
- 视频素材列表
- 视频素材自定义封面

现在需要给外部系统提供一个稳定的查询接口。外部系统通过 `product_code` 获取单个产品下的完整素材信息，并直接拿到当下可用的 TOS 临时签名下载地址，不依赖后台登录态。

## 目标

新增一个面向外部系统的只读开放接口，满足以下目标：

1. 通过业务主键 `product_code` 查询单个产品
2. 返回该产品下所有相关数据
3. 直接返回 TOS 临时签名下载地址，外部系统拿到即可下载
4. 认证走独立 `apikey`，不复用站内登录态
5. 完成后补一份给外部使用的说明文档 `素材信息获取接口API.md`

## 非目标

本次不做以下内容：

- 不做批量 `product_code` 查询
- 不做写接口
- 不做复杂的权限分层
- 不做接口管理后台
- 不补历史封面缺失数据

## 方案选择

### 方案 A：单个聚合接口

新增单个聚合接口：

`GET /openapi/materials/<product_code>`

一次返回产品、主图、文案、视频素材及其下载地址。

优点：

- 外部系统调用最简单
- 一次请求即可完成同步
- 与当前“按产品维度管理素材”的模型完全一致

缺点：

- 返回体会比拆分接口更大

### 方案 B：详情接口和资源签名接口拆分

先返回对象键，再额外请求签名地址。

优点：

- 接口职责更细

缺点：

- 外部调用更繁琐
- 第一版收益不高

### 结论

采用方案 A。第一版只提供单产品聚合接口，先把外部系统最核心的读需求打通。

## 认证设计

接口不走登录态，改用请求头 `X-API-Key`。

服务端从 `.env` 读取：

`OPENAPI_MEDIA_API_KEY`

第一版先直接在 `.env` 中放一个固定值，后续需要轮换时再改配置。

校验规则：

- 缺少 `X-API-Key`：返回 `401`
- `X-API-Key` 不匹配：返回 `401`
- 不区分用户角色，只要 `apikey` 正确即可访问

## 路由设计

新增路由：

`GET /openapi/materials/<product_code>`

示例：

`GET /openapi/materials/sonic-lens-refresher`

请求头：

```http
X-API-Key: your-shared-api-key
```

## 返回结构

返回体统一为 JSON。

```json
{
  "product": {
    "id": 123,
    "product_code": "sonic-lens-refresher",
    "name": "Sonic Lens Refresher",
    "archived": false,
    "created_at": "2026-04-16T10:00:00",
    "updated_at": "2026-04-16T12:00:00"
  },
  "covers": {
    "en": {
      "object_key": "media/1/123/cover_en_demo.jpg",
      "download_url": "https://signed.example.com/...",
      "expires_in": 3600
    }
  },
  "copywritings": {
    "en": [
      {
        "title": "Title",
        "body": "Body",
        "description": "Description",
        "ad_carrier": null,
        "ad_copy": null,
        "ad_keywords": null
      }
    ]
  },
  "items": [
    {
      "id": 456,
      "lang": "en",
      "filename": "demo.mp4",
      "display_name": "demo.mp4",
      "object_key": "media/1/123/demo.mp4",
      "video_download_url": "https://signed.example.com/...",
      "cover_object_key": "media/1/123/item_cover_demo.jpg",
      "video_cover_download_url": "https://signed.example.com/...",
      "duration_seconds": 12.3,
      "file_size": 1234567,
      "created_at": "2026-04-16T10:05:00"
    }
  ],
  "expires_in": 3600
}
```

## 字段语义

### `product`

返回素材产品本身的基础信息，便于外部系统做主记录映射。

### `covers`

按语言聚合产品主图。`download_url` 必须是实时生成的 TOS 临时签名地址。

说明：

- 只返回实际存在的语言主图
- 不额外补出回退语言项
- 外部系统自行按需优先取 `en` 或目标语言

### `copywritings`

按语言分组返回文案数组，保留当前数据库已有字段。

说明：

- 第一版不做裁剪
- 外部系统可以直接按语言消费

### `items`

返回该产品下全部未删除视频素材。

核心字段：

- `video_download_url`：视频文件的 TOS 临时签名下载地址
- `video_cover_download_url`：视频自定义封面的 TOS 临时签名下载地址

说明：

- `video_download_url` 必返
- `video_cover_download_url` 仅当 `cover_object_key` 存在时返回，否则为 `null`
- 第一版不把本地缩略图 `/medias/thumb/<id>` 伪装成 TOS 地址
- 如果某条素材没有自定义视频封面，外部系统应视为“无视频封面”

### `expires_in`

返回当前签名地址有效秒数，便于外部系统决定何时重新请求。

## 错误语义

### 401 未授权

```json
{
  "error": "invalid api key"
}
```

触发条件：

- 缺少 `X-API-Key`
- `X-API-Key` 错误

### 404 不存在

```json
{
  "error": "product not found"
}
```

触发条件：

- `product_code` 不存在
- 产品已被软删除

## 实现设计

### 配置

在配置层新增：

- `OPENAPI_MEDIA_API_KEY`

并从 `.env` 中读取。

### 路由组织

新增一个独立 blueprint，例如：

- `web/routes/openapi_materials.py`

原因：

- 与站内 `medias` 登录态接口隔离
- 权限模型不同
- 后续如果继续补其他外部接口，结构更清晰

### 数据获取

路由内部按以下步骤组装数据：

1. 通过 `product_code` 查产品
2. 获取产品主图映射
3. 获取全部文案
4. 获取全部视频素材
5. 为主图和视频生成 TOS 临时签名下载地址
6. 将文案按语言聚合
7. 返回统一 JSON

### 地址生成

主图和视频封面都使用对象键调用现有 TOS 签名方法生成下载地址。

约束：

- 只有真正存储在 TOS 的对象才返回签名地址
- 不混入站内代理地址
- 不返回本地文件绝对路径

## 测试设计

新增路由测试，至少覆盖：

1. `apikey` 缺失返回 `401`
2. `apikey` 错误返回 `401`
3. `product_code` 不存在返回 `404`
4. 成功返回产品、主图、文案、视频素材聚合数据
5. 视频素材存在封面时返回 `video_cover_download_url`
6. 视频素材无封面时 `video_cover_download_url` 为 `null`
7. 签名地址生成调用次数与对象数匹配

## 文档交付

功能完成后，补一份对外文档：

`素材信息获取接口API.md`

该文档面向外部调用方，内容包括：

- 接口地址
- 请求方法
- 认证方式
- 请求示例
- 响应示例
- 字段说明
- 错误码说明
- 签名地址有效期说明

## 风险与约束

1. 视频封面只有在 `cover_object_key` 存在时才能给出 TOS 下载地址，历史仅有本地缩略图的数据不会自动补成 TOS 地址
2. 该接口默认返回整个产品下的全部素材，如果单个产品素材很多，返回体会较大
3. 当前 `apikey` 先放 `.env` 固定值，后续如果要多调用方隔离，需要再升级为多 key 管理

## 验收标准

满足以下条件即可验收：

1. 外部系统可通过 `product_code` 查询单个产品
2. 请求头携带正确 `X-API-Key` 时返回 `200`
3. 返回中包含产品主图 TOS 下载地址
4. 返回中包含视频 TOS 下载地址
5. 已配置视频封面的素材返回视频封面 TOS 下载地址
6. 错误场景返回明确 `401` 或 `404`
7. 提供可直接给外部使用的 `素材信息获取接口API.md`
