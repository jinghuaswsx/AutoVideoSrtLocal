# 推送携带英文文案（texts 字段真实化）

- 作者：Claude
- 日期：2026-04-21
- 相关文档：[2026-04-18-push-management-design.md](./2026-04-18-push-management-design.md)、[2026-04-20-push-module-mvp-design.md](./2026-04-20-push-module-mvp-design.md)

## 1. 背景

AutoPush 子项目通过主项目 OpenAPI 拉取「推送 payload」后，把 payload 直发下游推送服务（`http://172.17.254.77:22400/dify/shopify/medias`）。下游接收的 JSON 里有一个 `texts` 字段，格式是：

```json
"texts": [
  {
    "title":       "Ready. Aim. LAUNCH! 🌪️",
    "message":     "Experience the thrill! 🤩 Instant mechanical launch. Durable & crash-proof. The coolest gift for ages 3+.",
    "description": "Fly High Today ✈️"
  }
]
```

主项目当前 **硬编码**了一个桩：

```python
_FIXED_TEXTS = [{"title": "tiktok", "message": "tiktok", "description": "tiktok"}]
```

出现在两处：

- [appcore/pushes.py:100](../../appcore/pushes.py#L100)（`build_item_payload` — by-keys 接口用）
- [web/routes/openapi_materials.py:160](../../web/routes/openapi_materials.py#L160)（`/push-payload` 旧接口）

下游虽然接收了，但内容是假的，没法在投放素材里看到真实的文案。

## 2. 目标

推送 payload 里的 `texts` 字段改为从数据库真实的**英文文案**生成，且对格式不合规的产品直接拒绝推送。

## 3. 数据现状

- 表 `media_copywritings` 的列：`product_id / lang / idx / title / body / description / ad_carrier / ad_copy / ad_keywords`
- 当前前端（[web/static/medias.js:733-746](../../web/static/medias.js#L733-L746)）只暴露一个 `body` textarea，**用户把「标题/文案/描述」三段都塞在 `body` 里**。`title`/`description` 两列在新流程里基本为空。
- `body` 的实际存储形式例：

  ```
  标题: Ready. Aim. LAUNCH! 🌪️
  文案：Experience the thrill! 🤩 Instant mechanical launch. Durable & crash-proof. The coolest gift for ages 3+.
  描述: Fly High Today ✈️
  ```

  注意：
  - 冒号可能是英文 `:` 也可能是中文 `：`
  - 标签与冒号之间可能有空格
  - 字段值可能跨多行
  - 字段顺序不保证（但业务习惯上是 标题 → 文案 → 描述）

## 4. 设计

### 4.1 解析器

新增纯函数 `parse_copywriting_body(body: str) -> dict[str, str]`，位置放在 [appcore/pushes.py](../../appcore/pushes.py) 里（紧邻 `build_item_payload`，不单独建模块）。

**契约：**
- 入参：英文文案的 `body` 字符串。
- 返回：`{"title": str, "message": str, "description": str}`，三个字段都必须非空。
- 失败：抛 `CopywritingParseError`，异常消息里包含失败原因（缺哪个字段 / 整段都没标签）。

**实现要点：**
- 用一个正则扫标签位置：`r'(标题|文案|描述)\s*[:：]\s*'`
- 按标签在原文里的位置切片，上一标签的 `end()` 到下一标签的 `start()`（或文末）为该字段值。
- 三个标签都必须出现，且提取出的值 `.strip()` 后非空。否则抛错。

### 4.2 读取 & 组装

改 `build_item_payload`（[appcore/pushes.py:103](../../appcore/pushes.py#L103)）：

```python
def build_item_payload(item, product):
    ...
    en_copy = query_one(
        "SELECT body FROM media_copywritings "
        "WHERE product_id=%s AND lang='en' AND idx=1",
        (product["id"],),
    )
    if not en_copy:
        raise CopywritingMissingError(
            f"产品 {product.get('product_code')} 没有英文 idx=1 文案"
        )
    parsed = parse_copywriting_body(en_copy["body"] or "")  # 抛 CopywritingParseError
    texts = [parsed]  # 只取 idx=1 这一条
    ...
```

删除 `_FIXED_TEXTS`。

### 4.3 异常类

新增两个异常（与解析函数一起放 pushes.py）：

```python
class CopywritingMissingError(Exception):
    """产品没有英文 idx=1 文案。"""

class CopywritingParseError(Exception):
    """英文 idx=1 文案 body 无法解析出三段合规字段。"""
```

### 4.4 就绪度与状态

`compute_readiness`（[appcore/pushes.py:19-43](../../appcore/pushes.py#L19-L43)）的 `has_copywriting` 当前只检查「该 product_id+lang 有任意记录」。

**一起升级**：让 `has_copywriting = True` 要求「英文 idx=1 文案能 parse 成功」。

- 不直接复用 `parse_copywriting_body`（避免列表渲染时反复捕获异常），抽一个只返回 bool 的轻量版：`_has_valid_en_copywriting(product_id) -> bool`
- 查一次 `SELECT body FROM media_copywritings WHERE product_id=? AND lang='en' AND idx=1 LIMIT 1`
- 命中后 try/except 走 `parse_copywriting_body`，抛错就返回 False

这样 `/push-items` 列表会把「没写英文文案 / 文案格式不对」的 item 显示为「制作中」，`readiness.has_copywriting=False`。用户在列表就能看到缺项。

**注意**：当前 `compute_readiness` 的第三个字段是 `has_copywriting`，按**素材 item 自身的 lang** 检查（item lang 可能是 `de`, `fr` 等）。这次改动的语义是：无论 item 是哪种语种，推送下游要的 `texts` 都是**英文**，所以要同时要求「英文 idx=1 文案合规」。

决定：**新增一个 `has_push_texts` 就绪项**，不动原来的 `has_copywriting`（它服务于"该语种有没有本语种文案"的判断，用在别处可能有意义）。`compute_status → is_ready` 的入参变成 5 项，都为真才算就绪。

### 4.5 接口层错误处理

`/push-items/by-keys`（[web/routes/openapi_materials.py:526-560](../../web/routes/openapi_materials.py#L526-L560)）的 `pushes.build_item_payload(item, product)` 现在会抛异常。需要捕获并返回 409 Conflict JSON：

```python
try:
    payload = pushes.build_item_payload(item, product)
except (pushes.CopywritingMissingError, pushes.CopywritingParseError) as exc:
    return jsonify({"error": str(exc), "code": "copywriting_not_ready"}), 409
```

`/push-payload` 旧接口（[web/routes/openapi_materials.py:138-196](../../web/routes/openapi_materials.py#L138-L196)）同样处理：把硬编码 texts 替换为走新逻辑，失败返回 409。为了避免重复代码，直接在旧接口里调 `build_item_payload` 的 texts 分支，或抽一个更小的 helper `resolve_push_texts(product_id) -> list[dict]`，被两处 import。

推荐：抽 helper，让两处都调用。

### 4.6 AutoPush 前端错误展示

- 载荷弹窗（[AutoPush/static/app.js:228-254](../../AutoPush/static/app.js#L228-L254)）的 payload 加载失败路径已经把错误展示在 `ap-error` banner 里，409 的 `detail` 会直接展示给用户。**无需改动前端**，只要后端错误消息够清楚就行。
- 「去载荷」tab 的 `doFetch` 同理。

## 5. 测试

新增 `tests/test_copywriting_parser.py`，覆盖：

1. 正常三段（英文冒号 + 换行）
2. 中文冒号 `：`
3. 冒号前后有空格
4. 字段值跨多行
5. 标签顺序倒换
6. 缺「标题」/缺「文案」/缺「描述」 → 抛错
7. 整段 body 没有任何标签 → 抛错
8. 字段值空白（只有冒号后换行） → 抛错
9. emoji / 特殊符号正常保留

`tests/test_pushes_build_payload.py`（或扩展现有的 pushes 测试）：

1. 产品有合规英文 idx=1 文案 → texts 正常返回一项
2. 产品无英文文案 → `CopywritingMissingError`
3. 产品有英文文案但 body 不合规 → `CopywritingParseError`
4. `has_push_texts` 就绪项的正反两种场景

## 6. 非目标

- 不解析 `lang != 'en'` 的文案（下游 texts 只要英文）
- 不动前端编辑器（不拆 body 为三个输入框）
- 不回填 `title` / `description` 数据库列（保留当前只写 body 的习惯）
- 不处理 idx≥2 的英文文案（只取 idx=1）
- 不做 fuzzy / LLM 兜底解析
- AutoPush 前端不改（错误消息由后端返回，现有 banner 已能展示）

## 7. 风险

- 改了 `compute_readiness`，以前显示"待推送"的素材现在可能变成"制作中"。影响面：旧数据里如果 `body` 没按「标题/文案/描述」三段格式写，推送列表会批量退回 `not_ready`。需要让用户知道这一点。
- 如果 body 里的文本本身含「标题」「文案」「描述」这几个字（例如"描述：产品描述如下…"），但前面没有冒号，regex 不会误匹配（regex 要求后面必须是 `:` 或 `：`）。但如果正文含"标题：xxx" 这样的串，会被误当作新字段的起点 → 低概率，接受。
