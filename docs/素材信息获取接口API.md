# 素材信息获取接口 API

对外提供的只读开放接口，用于按 `product_code` 查询单个产品下的完整素材信息（产品基础信息、多语言主图、多语言文案、视频素材列表），并直接返回 TOS 临时签名下载地址。

## 接口地址

```
GET /openapi/materials/<product_code>
```

示例：

```
GET /openapi/materials/sonic-lens-refresher
```

## 认证方式

请求头：

```
X-API-Key: <your-api-key>
```

- API Key 由服务提供方分发，对应服务端 `.env` 的 `OPENAPI_MEDIA_API_KEY`
- 第一版共用同一个固定密钥，调用方不区分

## 请求示例

```bash
curl -H "X-API-Key: $OPENAPI_MEDIA_API_KEY" \
  "http://your-host/openapi/materials/sonic-lens-refresher"
```

## 成功响应（HTTP 200）

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
      "object_key": "1/medias/123/cover_en.jpg",
      "download_url": "https://signed.example.com/...",
      "expires_in": 3600
    },
    "de": {
      "object_key": "1/medias/123/cover_de.jpg",
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
      "object_key": "1/medias/123/demo.mp4",
      "video_download_url": "https://signed.example.com/...",
      "cover_object_key": "1/medias/123/item_cover.jpg",
      "video_cover_download_url": "https://signed.example.com/...",
      "duration_seconds": 12.3,
      "file_size": 1234567,
      "created_at": "2026-04-16T10:05:00"
    },
    {
      "id": 457,
      "lang": "en",
      "filename": "demo-2.mp4",
      "display_name": "demo-2.mp4",
      "object_key": "1/medias/123/demo-2.mp4",
      "video_download_url": "https://signed.example.com/...",
      "cover_object_key": null,
      "video_cover_download_url": null,
      "duration_seconds": 8.8,
      "file_size": 7654321,
      "created_at": "2026-04-16T10:06:00"
    }
  ],
  "expires_in": 3600
}
```

## 字段说明

### `product`

产品基础信息。

| 字段 | 说明 |
| --- | --- |
| `id` | 产品在后台的内部 ID |
| `product_code` | 业务主键，用于本接口查询 |
| `name` | 产品名称 |
| `archived` | 是否归档（`true` / `false`） |
| `created_at` / `updated_at` | 创建 / 更新时间（ISO 8601 字符串，无对应记录时为 `null`） |

### `covers`

按语言聚合的产品主图。

- key 为语言代码（如 `en`、`de`、`fr` 等），只包含实际存在的语言
- 每项包含对象键、TOS 签名下载地址和签名有效秒数
- 接口不做语言回退，调用方自行决定优先级（比如先取目标语言，没有时再取 `en`）

### `copywritings`

按语言分组的文案数组。

每个语言下可能有多条文案，字段：

| 字段 | 说明 |
| --- | --- |
| `title` / `body` / `description` | 文案三要素 |
| `ad_carrier` / `ad_copy` / `ad_keywords` | 投放相关字段，没有时为 `null` |

### `items`

该产品下全部未删除视频素材。

| 字段 | 说明 |
| --- | --- |
| `id` | 素材内部 ID |
| `lang` | 视频语言 |
| `filename` / `display_name` | 文件名 / 展示名 |
| `object_key` | 视频在 TOS 的对象键 |
| `video_download_url` | 视频文件的 TOS 临时签名下载地址 |
| `cover_object_key` | 自定义封面对象键，没有时为 `null` |
| `video_cover_download_url` | 自定义封面的 TOS 签名下载地址，没有封面时为 `null` |
| `duration_seconds` | 视频时长（秒） |
| `file_size` | 文件大小（字节） |
| `created_at` | 创建时间（ISO 8601） |

### `expires_in`

顶层返回的 `expires_in` 为当前签名地址的有效秒数（默认 3600）。调用方应在该时长内消费 URL，或在即将过期时重新请求本接口。

## 错误响应

### HTTP 401 — 未授权

```json
{
  "error": "invalid api key"
}
```

触发条件：

- 请求头缺少 `X-API-Key`
- `X-API-Key` 与服务端配置不匹配

### HTTP 404 — 产品不存在

```json
{
  "error": "product not found"
}
```

触发条件：

- `product_code` 不存在
- 产品已被软删除

## 使用约定

- 所有 `*_download_url` 都是 TOS 临时签名地址，调用方拿到后应尽快下载或落盘原始内容
- 签名地址过期后，直接重新调用本接口即可拿到新地址
- 没有自定义视频封面的素材，`video_cover_download_url` 为 `null`，调用方应视为「无视频封面」，不要用本地缩略图等其他地址冒充
- 当前接口不做限流，共用同一个 `X-API-Key`；如遇到调用异常请直接联系服务提供方
