# 推送管理模块 设计文档

**日期**: 2026-04-18
**作者**: noobird + Claude
**依赖模块**: 素材管理 (`/medias`)、素材开放接口 (`/openapi/materials`)
**相关参考**: `docs/素材信息获取接口API.md`、`web/routes/openapi_materials.py`

## 1. 背景

`web/routes/openapi_materials.py` 已经实现了素材推送所需的 JSON payload 组装逻辑（`/openapi/materials/<product_code>/push-payload`）。该接口按「产品 × 语种」维度聚合输出推送报文，且推送端逻辑作者本地已调通。

当前缺失的是：
- 素材侧缺少**推送状态跟踪**——哪些视频已推送、哪些待推送，靠人脑记。
- 缺少**以推送任务为维度**的管理界面（一条视频素材 = 一条推送任务）。
- 缺少**推送就绪条件的统一判定**（文案、封面、投放链接是否已适配）。
- 缺少**推送历史审计**，无法追溯"什么时候推过、响应是什么、谁点的"。

本模块新增「推送管理」菜单，以视频素材（item）为粒度管理推送任务，状态实时计算 + 历史持久化。

## 2. 范围

### 首版要做

- 新增一级菜单「🚀 推送管理」，路由 `/pushes`
- 以 `media_items` 为行的列表页：筛选（状态/语种/产品/关键词/时间范围）+ 分页 + 就绪状态可视化
- 点击"推送"按钮走**前端浏览器直连内网投放系统**的模式：后端只提供 payload 和标记接口，实际 POST 由浏览器执行
- 推送历史表 `media_push_logs`，记录每次点击的请求/响应/操作者
- 单条素材推送成功后锁定（按钮消失），管理员可手动重置
- 推送前服务端对投放链接做 HEAD 探活（超时 5s），404/超时直接拒绝推送
- 素材编辑弹窗新增"主站已适配语种"多选，用于就绪判定
- 权限：**全员可见列表**，**仅管理员可操作**（推送、重置、查看历史均可见）

### 首版不做

- 批量推送（多选后一键推送）
- 推送定时/队列（当前是纯同步点击触发）
- 跨产品/跨语种的汇总统计面板
- 自动重试
- 推送失败的通知系统（邮件/Webhook）
- 素材就绪后的主动通知（推送提醒）

### 明确不做

- 重新实现推送目标系统对接逻辑（已在作者本地调通）
- 改动 `build_push_payload` 函数的对外语义（仅内部复用组装逻辑）

## 3. 数据模型

### 3.1 新增 1 张表

```sql
CREATE TABLE media_push_logs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  item_id INT NOT NULL,
  operator_user_id INT NOT NULL,
  status ENUM('success','failed') NOT NULL,
  request_payload JSON NOT NULL,
  response_body TEXT DEFAULT NULL,
  error_message TEXT DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_item (item_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 3.2 现有表新增字段

```sql
ALTER TABLE media_products
  ADD COLUMN ad_supported_langs VARCHAR(255) DEFAULT NULL;
-- 逗号分隔的已适配语种代码，如 "de,fr,ja"。null 或空串 = 尚未适配任何语种。

ALTER TABLE media_items
  ADD COLUMN pushed_at DATETIME DEFAULT NULL,
  ADD COLUMN latest_push_id INT DEFAULT NULL;
-- pushed_at 仅在推送成功时赋值；重置后清空。latest_push_id 指向 media_push_logs。
```

迁移文件：`db/migrations/2026_04_18_add_push_management.sql`

### 3.3 状态机（动态计算）

状态不落库，由后端每次查询时基于 `media_items` + 关联数据实时算：

| 状态 | 判定 |
|---|---|
| **已推送** | `pushed_at IS NOT NULL` |
| **推送失败** | `pushed_at IS NULL` 且 `latest_push_id` 指向的 `media_push_logs.status = 'failed'` |
| **待推送** | `pushed_at IS NULL` 且就绪条件全满足 |
| **未就绪** | `pushed_at IS NULL` 且就绪条件任一不满足 |

**就绪条件**（item 级，4 项全满足才算"待推送"）：

1. `media_items.object_key` 非空（素材本身已上传到 TOS）
2. `media_items.cover_object_key` 非空（item 级封面已上传）
3. 存在至少一条 `media_copywritings` 记录满足 `product_id = item.product_id AND lang = item.lang`
4. `item.lang ∈ split(product.ad_supported_langs, ',')`（主站已适配该语种）

"未就绪"状态下，前端在"就绪条件"列展示 4 个小圆点（绿=满足，灰=缺），hover 提示缺哪一项。

## 4. 架构

### 4.1 分层

```
web/routes/pushes.py              -- 蓝图：页面 + JSON API
appcore/pushes.py                 -- 业务逻辑：状态计算、payload 组装、就绪判定、探活、日志写入
appcore/medias.py                 -- 扩展 ad_supported_langs 读写
web/templates/pushes_list.html    -- 列表页
web/static/pushes.js              -- 前端交互：筛选、分页、推送按钮、直连内网
web/templates/_medias_edit_modal.html -- 编辑弹窗补"主站已适配语种"多选
```

### 4.2 推送流程（前后端时序）

```
[浏览器]                               [AutoVideoSrt 后端]              [内网投放系统]

(1) 用户点"推送" →
    GET /pushes/api/items/<id>/payload
                                     → 校验：管理员
                                     → 校验：item 存在且未推送
                                     → 计算就绪条件（4 项）
                                     → 任一缺失 → 400 + 原因
                                     → 对 item.lang 的投放链接做 HEAD
                                       (config.AD_URL_TEMPLATE 拼出，超时 5s)
                                     → 4xx/5xx/timeout → 400 "主站链接未适配：{url}"
                                     → 组装 payload（见 4.3）
                                     ← {payload, push_url}

(2) 浏览器 fetch(push_url, POST, body=payload) ────────────────────────→
                                                                          ← 响应体（结构由投放系统定义）

(3a) 若成功 → POST /pushes/api/items/<id>/mark-pushed
     body: {request_payload, response_body}
                                     → 写 media_push_logs(status='success')
                                     → 更新 item.pushed_at, latest_push_id
                                     ← 204

(3b) 若失败 → POST /pushes/api/items/<id>/mark-failed
     body: {request_payload, response_body?, error_message}
                                     → 写 media_push_logs(status='failed')
                                     → 更新 item.latest_push_id（不改 pushed_at）
                                     ← 204
```

**成功/失败判定**：由浏览器端根据投放系统响应体结构判定（作者会提供样本，在 `web/static/pushes.js` 内实现）。初版的兜底规则：`response.ok && response.status < 300` 视为成功；否则视为失败，`error_message` 取 `response.statusText` 或捕获的网络异常。

### 4.3 payload 组装

基于现有 `build_push_payload(product_code, lang)` 的结构，做两处调整：

1. `videos` 数组只含**当前这 1 条** item（而非该产品该语种下所有）
2. `product_links` **不再只放当前语种**，改为读取 `media_languages` 里所有 `enabled=1 AND code != 'en'` 的语种，按 `AD_URL_TEMPLATE` 拼出完整链接数组

其他字段保持现状：
- `mode`：`"create"`
- `product_name`：`product.name`
- `texts`：`[{"title": "tiktok", "message": "tiktok", "description": "tiktok"}]`（硬编码）
- `source`：`0`
- `level`：`product.importance` 或默认 3
- `author` / `push_admin`：硬编码 `"蔡靖华"`（投放系统约束，不能改）
- `roas`：`1.6`
- `platforms`：`["tiktok"]`
- `selling_point`：`product.selling_points`
- `tags`：`[]`

示例：
```json
{
  "mode": "create",
  "product_name": "Sonic Lens Refresher",
  "texts": [{"title": "tiktok", "message": "tiktok", "description": "tiktok"}],
  "product_links": [
    "https://newjoyloo.com/de/products/sonic-lens-refresher-rjc",
    "https://newjoyloo.com/fr/products/sonic-lens-refresher-rjc",
    "https://newjoyloo.com/es/products/sonic-lens-refresher-rjc",
    "https://newjoyloo.com/pt/products/sonic-lens-refresher-rjc",
    "https://newjoyloo.com/ja/products/sonic-lens-refresher-rjc",
    "https://newjoyloo.com/it/products/sonic-lens-refresher-rjc"
  ],
  "videos": [
    {
      "name": "demo.mp4",
      "size": 1234567,
      "width": 1080,
      "height": 1920,
      "url": "https://signed.example.com/...",
      "image_url": "https://signed.example.com/cover..."
    }
  ],
  "source": 0,
  "level": 3,
  "author": "蔡靖华",
  "push_admin": "蔡靖华",
  "roas": 1.6,
  "platforms": ["tiktok"],
  "selling_point": "...",
  "tags": []
}
```

### 4.4 路由表

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | `/pushes` | 列表页 HTML | 登录用户 |
| GET | `/pushes/api/items` | 列表 JSON（支持筛选参数 `status[]`、`lang[]`、`keyword`、`product`、`date_from`、`date_to`、`page`） | 登录用户 |
| GET | `/pushes/api/items/<id>/payload` | 返回 `{payload, push_url}`（含就绪校验 + 探活） | 管理员 |
| POST | `/pushes/api/items/<id>/mark-pushed` | 标记成功，写 push_log + 更新 `pushed_at` | 管理员 |
| POST | `/pushes/api/items/<id>/mark-failed` | 写失败日志（不改 `pushed_at`） | 管理员 |
| POST | `/pushes/api/items/<id>/reset` | 清空 `pushed_at` 和 `latest_push_id`（历史保留） | 管理员 |
| GET | `/pushes/api/items/<id>/logs` | 该 item 的推送历史 | 登录用户 |

所有接口：`@login_required`。写接口和 payload 接口额外校验 `current_user.is_admin`。

### 4.5 配置

`config.py` 新增：

```python
PUSH_TARGET_URL = _env("PUSH_TARGET_URL", "")
AD_URL_TEMPLATE = _env("AD_URL_TEMPLATE", "https://newjoyloo.com/{lang}/products/{product_code}-rjc")
AD_URL_PROBE_TIMEOUT = int(_env("AD_URL_PROBE_TIMEOUT", "5"))
```

`.env.example` 同步新增 3 项。`PUSH_TARGET_URL` 为空时，前端"推送"按钮全置灰，hover 提示"推送目标未配置，请联系管理员"。

## 5. 前端

### 5.1 列表页 `pushes_list.html`

**顶部工具栏（筛选区）**：
- **状态**：多选下拉，选项「未就绪 / 待推送 / 已推送 / 推送失败」，默认勾「待推送」
- **语种**：多选下拉，选项来自 `/medias/api/languages`（已启用语种）
- **产品**：搜索框，按 `product_name` 或 `product_code` 模糊匹配
- **关键词**：搜索框，按 `display_name` / `filename` 模糊匹配
- **时间范围**：`updated_at` 的开始日期 + 结束日期
- 右侧「重置筛选」按钮

**默认加载**：状态 = 待推送，按 `media_items.updated_at` DESC；若该字段无则用 `created_at`。分页每页 20 条。

**表格列**：

| 列 | 内容 | 宽度 |
|---|---|---|
| 缩略图 | item 级封面（签名 URL），80×80 圆角 | 96 |
| 产品 | `product.name`（主）/ `product_code`（副灰字） | auto |
| 素材 | `display_name`（主）/ 时长 + 文件大小（副灰字） | auto |
| 语种 | 彩色 pill，显示 `media_languages.name_zh` | 80 |
| 就绪 | 4 小圆点横排：素材 / 封面 / 文案 / 链接（绿=满足，灰=缺） | 96 |
| 状态 | Badge：未就绪灰 / 待推送蓝 / 已推送绿 / 失败红 | 96 |
| 更新时间 | `YY-MM-DD HH:mm` + 相对时间小字 | 140 |
| 操作 | 管理员：见下方 | 160 |

**操作列按钮（仅管理员可见，非管理员隐藏整列）**：
- 未就绪：灰色「推送」按钮，disabled，hover tip "缺少：封面 / 文案"
- 待推送：蓝色「推送」按钮 → 走 4.2 流程
- 推送中：loading spinner 替代按钮
- 推送失败：红字「× 失败，重试」按钮 + hover tip 显示 `error_message`
- 已推送：绿色「✓ 已推送 YY-MM-DD」只读文本 + 右侧「⋯」下拉菜单：
  - 重置状态：弹确认框"确认重置？历史保留。"→ 调 `/reset`
  - 查看历史：打开抽屉展示该 item 所有 `media_push_logs`

**分页**：底部数字分页 + 总数显示。

### 5.2 素材编辑弹窗改造

在 `_medias_edit_modal.html` 的产品基础字段区（产品名称/色号人/来源之后），插入一组 checkbox：

```
主站已适配语种：
  □ 德语  □ 法语  □ 西班牙语  □ 葡萄牙语  □ 日语  □ 意大利语
```

选项动态来自 `media_languages` 已启用项（排除 `en`）。保存时随产品字段一起写入 `media_products.ad_supported_langs`（逗号串）。

### 5.3 侧边栏

在 `layout.html` 侧边栏「📦 素材管理」后面插入：

```html
<a href="/pushes" {% if request.path.startswith('/pushes') %}class="active"{% endif %}>
  <span class="nav-icon">🚀</span> 推送管理
</a>
```

## 6. 权限

| 角色 | 列表页 | 查看历史 | 推送 / 重置 |
|---|---|---|---|
| 普通用户 | 全部可见 | 可见 | 不可见 |
| 管理员 | 全部可见 | 可见 | 可见并可操作 |

列表不做 user_id 过滤（所有人看到相同内容）。写接口 `/payload`、`/mark-pushed`、`/mark-failed`、`/reset` 强制 `current_user.is_admin`。

## 7. 错误处理

| 场景 | 处理 |
|---|---|
| `PUSH_TARGET_URL` 未配置 | 列表顶部横幅提示；推送按钮全置灰 |
| 就绪条件不满足 | `/payload` 返回 400 `{"error": "not_ready", "missing": ["cover","copywriting"]}`，前端弹错 |
| 探活 404 / 超时 / 5xx | `/payload` 返回 400 `{"error": "link_not_adapted", "url": "..."}`，前端弹错"主站链接未适配" |
| 浏览器 fetch 失败（CORS / 网络） | 前端捕获异常 → 调 `/mark-failed`，`error_message` 写 `TypeError: Failed to fetch` 等原始错误 |
| 投放系统返回非 2xx | 前端视为失败 → 调 `/mark-failed`，`response_body` 原文记录 |
| 重复点"推送"（已 pushed_at） | `/payload` 返回 409 `{"error": "already_pushed"}`，前端刷新列表 |
| 非管理员调写接口 | 403 |

## 8. 测试计划

### 8.1 单元测试 `tests/appcore/test_pushes_service.py`

- 就绪条件判定：逐项缺失 → 未就绪；全满足 → 待推送
- 状态计算：`pushed_at` 优先 > 最近失败 > 就绪条件
- payload 组装：`videos` 长度 = 1；`product_links` = 启用小语种数 × 链接数
- 探活：mock `requests.head`，覆盖 200 / 404 / timeout

### 8.2 路由测试 `tests/web/test_pushes_routes.py`

- 列表筛选：状态、语种、关键词、时间范围、分页
- 权限：非管理员调 `/payload`、`/mark-pushed`、`/reset` → 403
- 已推送状态下调 `/payload` → 409
- 就绪不满足 → 400 + `missing` 字段
- `mark-pushed` → `item.pushed_at` 更新 + 新 log 行
- `mark-failed` → 新 log 行 + `latest_push_id` 更新，`pushed_at` 不变
- `reset` → 清空 `pushed_at`，历史 log 保留

### 8.3 冒烟

1. 管理员登录 → 侧边栏见「🚀 推送管理」
2. `/pushes` 默认展示"待推送"列表
3. 在素材管理某产品里勾选"主站已适配语种" → 对应 item 状态变"待推送"
4. 点推送按钮 → 浏览器开发者工具看到 POST 到 `PUSH_TARGET_URL` 的请求和返回
5. 成功响应 → 列表刷新，状态变"已推送 YY-MM-DD"
6. 点「⋯ → 查看历史」 → 抽屉展示 log 详情
7. 点「⋯ → 重置状态」 → 回退到"待推送"，历史保留
8. 普通用户登录 → 能看列表，不能看操作列

## 9. 实施拆分（用于后续写 plan）

1. **数据层**：SQL 迁移 + `appcore/medias.py` 扩展 `ad_supported_langs` 读写 + `appcore/pushes.py` 新建（状态计算、payload 组装、探活、日志写入）
2. **后端路由**：`web/routes/pushes.py` 蓝图 + `app.py` / `main.py` 注册
3. **前端列表页**：`web/templates/pushes_list.html` + `web/static/pushes.js`
4. **素材弹窗改造**：`_medias_edit_modal.html` 增"主站已适配语种"多选 + `web/static/medias.js` 保存逻辑同步
5. **侧边栏接入**：`layout.html` 加菜单项
6. **配置同步**：`config.py` + `.env.example` 新增 3 项
7. **测试**：单测 + 路由测 + 冒烟
8. **文档**：本设计文档 + 更新 `docs/素材信息获取接口API.md` 提示 payload 结构调整
