# 推送携带英文文案 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 推送 payload 的 `texts` 字段从数据库 `media_copywritings(lang='en', idx=1)` 的 `body` 解析而来；body 无法解析时推送被拒，列表就绪度同步反映。

**Architecture:**
1. 在 `appcore/pushes.py` 新增纯函数 `parse_copywriting_body` + 两个异常类 + 一个 helper `resolve_push_texts(product_id)`。
2. `build_item_payload` 在组装 `texts` 时调 `resolve_push_texts`（失败抛异常）；`compute_readiness` 新增 `has_push_texts` 就绪项。
3. `web/routes/openapi_materials.py` 两个生成 payload 的接口捕获新异常返回 409。

**Tech Stack:** Python 3.10+ / Flask / MySQL / pytest（走真实 DB，conftest 有环境变量 fixture）。

**Spec:** [docs/superpowers/specs/2026-04-21-push-english-texts-design.md](../specs/2026-04-21-push-english-texts-design.md)

---

## File Structure

| 路径 | 类型 | 职责 |
| --- | --- | --- |
| `appcore/pushes.py` | Modify | 新增解析器 + 异常 + helper；改 `build_item_payload` 和 `compute_readiness` |
| `web/routes/openapi_materials.py` | Modify | `/push-items/by-keys`、`/push-payload` 两个路由捕获新异常返回 409 |
| `tests/test_copywriting_parser.py` | Create | 纯函数 `parse_copywriting_body` 的单元测试 |
| `tests/test_appcore_pushes.py` | Modify | 新增 `build_item_payload` / `compute_readiness` 对英文文案就绪的覆盖 |

---

## Task 1：新增 `parse_copywriting_body` 纯函数与异常类

**Files:**
- Modify: `appcore/pushes.py`（在 `_FIXED_AUTHOR` 附近新增解析器模块）
- Create: `tests/test_copywriting_parser.py`

- [ ] **Step 1.1 — 写失败测试：正常三段英文冒号**

创建 `tests/test_copywriting_parser.py`：

```python
import pytest

from appcore.pushes import (
    CopywritingParseError,
    parse_copywriting_body,
)


def test_parse_body_normal_english_colon():
    body = (
        "标题: Ready. Aim. LAUNCH! 🌪️\n"
        "文案: Experience the thrill! 🤩 Instant launch.\n"
        "描述: Fly High Today ✈️"
    )
    assert parse_copywriting_body(body) == {
        "title": "Ready. Aim. LAUNCH! 🌪️",
        "message": "Experience the thrill! 🤩 Instant launch.",
        "description": "Fly High Today ✈️",
    }
```

- [ ] **Step 1.2 — 运行测试确认失败**

Run:

```bash
python -m pytest tests/test_copywriting_parser.py -v
```

Expected: `ImportError` 或 `AttributeError`（`parse_copywriting_body` / `CopywritingParseError` 未定义）。

- [ ] **Step 1.3 — 实现 parser 与异常类**

编辑 `appcore/pushes.py`：

在文件顶部 `import` 区加：

```python
import re
```

在 `log = logging.getLogger(__name__)` 下新增：

```python
class CopywritingMissingError(Exception):
    """产品没有英文 idx=1 文案。"""


class CopywritingParseError(Exception):
    """英文 idx=1 文案 body 无法解析出三段合规字段。"""


_COPY_LABEL_RE = re.compile(r"(标题|文案|描述)\s*[:：]\s*")
_COPY_LABEL_TO_FIELD = {
    "标题": "title",
    "文案": "message",
    "描述": "description",
}


def parse_copywriting_body(body: str) -> dict[str, str]:
    """从英文文案 body 里提取 {title, message, description}。

    要求三个标签（标题 / 文案 / 描述）全部出现，每段 strip() 后非空。
    冒号兼容英文 `:` 和中文 `：`。
    """
    text = body or ""
    matches = list(_COPY_LABEL_RE.finditer(text))
    if not matches:
        raise CopywritingParseError("未找到任何「标题/文案/描述」标签")

    fields: dict[str, str] = {}
    for idx, m in enumerate(matches):
        label = m.group(1)
        field = _COPY_LABEL_TO_FIELD[label]
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        fields[field] = text[start:end].strip()

    missing = [
        k for k in ("title", "message", "description") if k not in fields
    ]
    if missing:
        raise CopywritingParseError(
            f"文案缺少字段：{', '.join(missing)}"
        )

    empty = [k for k, v in fields.items() if not v]
    if empty:
        raise CopywritingParseError(
            f"文案字段为空：{', '.join(empty)}"
        )
    return fields
```

- [ ] **Step 1.4 — 测试通过**

Run:

```bash
python -m pytest tests/test_copywriting_parser.py -v
```

Expected: `test_parse_body_normal_english_colon PASSED`。

- [ ] **Step 1.5 — 补足测试覆盖（中文冒号 / 多行 / 顺序变化 / 失败路径 / emoji）**

在 `tests/test_copywriting_parser.py` 追加：

```python
def test_parse_body_chinese_colon_and_whitespace():
    body = (
        "标题 ：  Hello World\n"
        "文案：  Line one\n"
        "描述 ： End"
    )
    assert parse_copywriting_body(body) == {
        "title": "Hello World",
        "message": "Line one",
        "description": "End",
    }


def test_parse_body_multiline_value():
    body = (
        "标题: Line1\n"
        "文案: Line A\n"
        "Line B\n"
        "Line C\n"
        "描述: Tail"
    )
    assert parse_copywriting_body(body) == {
        "title": "Line1",
        "message": "Line A\nLine B\nLine C",
        "description": "Tail",
    }


def test_parse_body_order_swapped():
    body = (
        "描述: D\n"
        "标题: T\n"
        "文案: M"
    )
    assert parse_copywriting_body(body) == {
        "title": "T",
        "message": "M",
        "description": "D",
    }


def test_parse_body_missing_title_raises():
    body = "文案: M\n描述: D"
    with pytest.raises(CopywritingParseError, match="title"):
        parse_copywriting_body(body)


def test_parse_body_missing_description_raises():
    body = "标题: T\n文案: M"
    with pytest.raises(CopywritingParseError, match="description"):
        parse_copywriting_body(body)


def test_parse_body_no_labels_raises():
    body = "just a paragraph without labels"
    with pytest.raises(CopywritingParseError, match="未找到"):
        parse_copywriting_body(body)


def test_parse_body_empty_field_raises():
    body = "标题:\n文案: M\n描述: D"
    with pytest.raises(CopywritingParseError, match="为空"):
        parse_copywriting_body(body)


def test_parse_body_empty_string_raises():
    with pytest.raises(CopywritingParseError):
        parse_copywriting_body("")


def test_parse_body_preserves_emoji_and_punctuation():
    body = (
        "标题: Ready. Aim. LAUNCH! 🌪️\n"
        "文案: Durable & crash-proof.\n"
        "描述: Fly ✈️"
    )
    parsed = parse_copywriting_body(body)
    assert parsed["title"] == "Ready. Aim. LAUNCH! 🌪️"
    assert parsed["message"] == "Durable & crash-proof."
    assert parsed["description"] == "Fly ✈️"
```

- [ ] **Step 1.6 — 运行全部解析器测试**

Run:

```bash
python -m pytest tests/test_copywriting_parser.py -v
```

Expected: 9 个用例全部 PASSED。

- [ ] **Step 1.7 — 提交**

```bash
git add appcore/pushes.py tests/test_copywriting_parser.py
git commit -m "feat(push): 新增英文文案三段解析器与异常类"
```

---

## Task 2：新增 `resolve_push_texts` helper

**Files:**
- Modify: `appcore/pushes.py`
- Modify: `tests/test_appcore_pushes.py`

- [ ] **Step 2.1 — 写失败测试：resolve_push_texts 正常返回**

在 `tests/test_appcore_pushes.py` 顶部确认 `pushes` 已 import，然后追加：

```python
def test_resolve_push_texts_returns_parsed(product_with_item):
    pid, _item_id = product_with_item
    body = (
        "标题: Ready\n"
        "文案: Do it\n"
        "描述: Go"
    )
    medias.replace_copywritings(pid, [{"body": body}], lang="en")
    texts = pushes.resolve_push_texts(pid)
    assert texts == [{"title": "Ready", "message": "Do it", "description": "Go"}]


def test_resolve_push_texts_missing_raises(product_with_item):
    pid, _ = product_with_item
    # fixture 只写了 lang='de' 的文案，英文没有
    with pytest.raises(pushes.CopywritingMissingError):
        pushes.resolve_push_texts(pid)


def test_resolve_push_texts_parse_error(product_with_item):
    pid, _ = product_with_item
    medias.replace_copywritings(
        pid, [{"body": "随便一段没有标签的中文"}], lang="en",
    )
    with pytest.raises(pushes.CopywritingParseError):
        pushes.resolve_push_texts(pid)
```

在文件顶部 import 已含 `from appcore import medias, pushes`，无需修改。

- [ ] **Step 2.2 — 运行测试确认失败**

Run:

```bash
python -m pytest tests/test_appcore_pushes.py::test_resolve_push_texts_returns_parsed -v
```

Expected: `AttributeError: module 'appcore.pushes' has no attribute 'resolve_push_texts'`。

- [ ] **Step 2.3 — 实现 helper**

编辑 `appcore/pushes.py`，在 `parse_copywriting_body` 下面新增：

```python
def resolve_push_texts(product_id: int) -> list[dict[str, str]]:
    """查 media_copywritings(lang='en', idx=1).body 并解析成 texts 数组。

    Raises:
        CopywritingMissingError: 产品没有英文 idx=1 文案。
        CopywritingParseError: body 无法解析出合规三段。
    """
    row = query_one(
        "SELECT body FROM media_copywritings "
        "WHERE product_id=%s AND lang='en' AND idx=1 LIMIT 1",
        (product_id,),
    )
    if not row:
        raise CopywritingMissingError(
            f"产品 {product_id} 缺少英文 idx=1 文案"
        )
    parsed = parse_copywriting_body(row.get("body") or "")
    return [parsed]
```

- [ ] **Step 2.4 — 测试通过**

Run:

```bash
python -m pytest tests/test_appcore_pushes.py::test_resolve_push_texts_returns_parsed tests/test_appcore_pushes.py::test_resolve_push_texts_missing_raises tests/test_appcore_pushes.py::test_resolve_push_texts_parse_error -v
```

Expected: 3 个用例 PASSED。

- [ ] **Step 2.5 — 提交**

```bash
git add appcore/pushes.py tests/test_appcore_pushes.py
git commit -m "feat(push): 新增 resolve_push_texts helper"
```

---

## Task 3：`build_item_payload` 接入真实 texts

**Files:**
- Modify: `appcore/pushes.py:103`（`build_item_payload`）
- Modify: `tests/test_appcore_pushes.py`

- [ ] **Step 3.1 — 写失败测试：payload 带真实文案**

在 `tests/test_appcore_pushes.py` 追加：

```python
def test_build_item_payload_uses_real_texts(product_with_item):
    pid, item_id = product_with_item
    body = (
        "标题: Ready. Aim. LAUNCH!\n"
        "文案: Experience the thrill.\n"
        "描述: Fly High Today"
    )
    medias.replace_copywritings(pid, [{"body": body}], lang="en")
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    payload = pushes.build_item_payload(item, product)
    assert payload["texts"] == [
        {
            "title": "Ready. Aim. LAUNCH!",
            "message": "Experience the thrill.",
            "description": "Fly High Today",
        }
    ]


def test_build_item_payload_raises_when_no_en_copy(product_with_item):
    pid, item_id = product_with_item
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    with pytest.raises(pushes.CopywritingMissingError):
        pushes.build_item_payload(item, product)
```

- [ ] **Step 3.2 — 运行测试确认失败**

Run:

```bash
python -m pytest tests/test_appcore_pushes.py::test_build_item_payload_uses_real_texts tests/test_appcore_pushes.py::test_build_item_payload_raises_when_no_en_copy -v
```

Expected: 第一条 FAIL（texts 仍是 `[{"title":"tiktok",...}]`）；第二条 FAIL（没抛异常）。

- [ ] **Step 3.3 — 修改 `build_item_payload`**

编辑 `appcore/pushes.py`，找到：

```python
_FIXED_AUTHOR = "蔡靖华"
_FIXED_TEXTS = [{"title": "tiktok", "message": "tiktok", "description": "tiktok"}]
```

改为：

```python
_FIXED_AUTHOR = "蔡靖华"
```

然后在 `build_item_payload` 里找到：

```python
return {
    "mode": "create",
    "product_name": product.get("name") or "",
    "texts": list(_FIXED_TEXTS),
    ...
}
```

改为：

```python
texts = resolve_push_texts(product["id"])
return {
    "mode": "create",
    "product_name": product.get("name") or "",
    "texts": texts,
    ...
}
```

- [ ] **Step 3.4 — 测试通过**

Run:

```bash
python -m pytest tests/test_appcore_pushes.py::test_build_item_payload_uses_real_texts tests/test_appcore_pushes.py::test_build_item_payload_raises_when_no_en_copy -v
```

Expected: 两条 PASSED。

- [ ] **Step 3.5 — 跑全量 pushes 测试，确认老用例未被打断**

Run:

```bash
python -m pytest tests/test_appcore_pushes.py -v
```

Expected: 老用例里任何用到 `build_item_payload` 的在 fixture 里没写英文文案的，会因新逻辑抛 `CopywritingMissingError`。需要把那些用例 fixture 提前插入英文文案。

**若出现失败**：在触发 `build_item_payload` 的测试前，加：

```python
medias.replace_copywritings(
    pid,
    [{"body": "标题: T\n文案: M\n描述: D"}],
    lang="en",
)
```

- [ ] **Step 3.6 — 提交**

```bash
git add appcore/pushes.py tests/test_appcore_pushes.py
git commit -m "feat(push): build_item_payload 接入英文文案解析"
```

---

## Task 4：`compute_readiness` 新增 `has_push_texts`

**Files:**
- Modify: `appcore/pushes.py`（`compute_readiness` + 新增轻量检查函数）
- Modify: `tests/test_appcore_pushes.py`

- [ ] **Step 4.1 — 写失败测试：has_push_texts 就绪正反两种**

在 `tests/test_appcore_pushes.py` 追加：

```python
def test_compute_readiness_has_push_texts_true(product_with_item):
    pid, item_id = product_with_item
    medias.replace_copywritings(
        pid,
        [{"body": "标题: T\n文案: M\n描述: D"}],
        lang="en",
    )
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    r = pushes.compute_readiness(item, product)
    assert r["has_push_texts"] is True


def test_compute_readiness_has_push_texts_false_when_no_en(product_with_item):
    pid, item_id = product_with_item
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    r = pushes.compute_readiness(item, product)
    assert r["has_push_texts"] is False


def test_compute_readiness_has_push_texts_false_when_unparseable(product_with_item):
    pid, item_id = product_with_item
    medias.replace_copywritings(
        pid, [{"body": "no labels here"}], lang="en",
    )
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    r = pushes.compute_readiness(item, product)
    assert r["has_push_texts"] is False


def test_compute_status_not_ready_without_push_texts(product_with_item):
    pid, item_id = product_with_item
    # 没写英文文案，别的都 ok
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    assert pushes.compute_status(item, product) == pushes.STATUS_NOT_READY
```

- [ ] **Step 4.2 — 运行测试确认失败**

Run:

```bash
python -m pytest tests/test_appcore_pushes.py -k "push_texts or compute_status_not_ready_without" -v
```

Expected: 4 条都 FAIL（`has_push_texts` 未出现在 readiness 字典；status 测试因老 readiness 返回 pending）。

- [ ] **Step 4.3 — 实现轻量检查与改 compute_readiness**

编辑 `appcore/pushes.py`。

在 `resolve_push_texts` 下新增：

```python
def _has_valid_en_push_texts(product_id: int) -> bool:
    try:
        resolve_push_texts(product_id)
    except (CopywritingMissingError, CopywritingParseError):
        return False
    return True
```

改 `compute_readiness`（在返回前追加 `has_push_texts`）：

```python
def compute_readiness(item: dict, product: dict) -> dict:
    has_object = bool((item or {}).get("object_key"))
    has_cover = bool((item or {}).get("cover_object_key"))

    lang = (item or {}).get("lang") or "en"
    pid = (item or {}).get("product_id")
    has_copywriting = False
    if pid and lang:
        row = query_one(
            "SELECT 1 AS ok FROM media_copywritings "
            "WHERE product_id=%s AND lang=%s LIMIT 1",
            (pid, lang),
        )
        has_copywriting = bool(row)

    supported = medias.parse_ad_supported_langs((product or {}).get("ad_supported_langs"))
    lang_supported = lang in supported

    has_push_texts = _has_valid_en_push_texts(pid) if pid else False

    return {
        "has_object": has_object,
        "has_cover": has_cover,
        "has_copywriting": has_copywriting,
        "lang_supported": lang_supported,
        "has_push_texts": has_push_texts,
    }
```

**注意：** `is_ready` 是 `all(readiness.values())`，新增字段自然参与；无需改 `is_ready`。

- [ ] **Step 4.4 — 测试通过**

Run:

```bash
python -m pytest tests/test_appcore_pushes.py -v
```

Expected: 所有用例 PASSED。

如果有老用例原本断言 `r == {...四项...}`，直接更新断言加上第五项。

- [ ] **Step 4.5 — 提交**

```bash
git add appcore/pushes.py tests/test_appcore_pushes.py
git commit -m "feat(push): compute_readiness 新增 has_push_texts 就绪项"
```

---

## Task 5：两个 OpenAPI 路由返回 409

**Files:**
- Modify: `web/routes/openapi_materials.py`（`/push-items/by-keys` 和 `/push-payload`）

- [ ] **Step 5.1 — 改 `/push-items/by-keys`（line ~526）**

编辑 `web/routes/openapi_materials.py`，找到 `get_push_item_payload_by_keys` 中：

```python
payload = pushes.build_item_payload(item, product)
return jsonify({
    "item_id": item["id"],
    "item": _serialize_push_item(item, product),
    "payload": payload,
})
```

改为：

```python
try:
    payload = pushes.build_item_payload(item, product)
except (pushes.CopywritingMissingError, pushes.CopywritingParseError) as exc:
    return jsonify({
        "error": str(exc),
        "code": "copywriting_not_ready",
    }), 409
return jsonify({
    "item_id": item["id"],
    "item": _serialize_push_item(item, product),
    "payload": payload,
})
```

- [ ] **Step 5.2 — 改 `/push-payload`（line 138-196）**

`build_push_payload` 里当前硬编码：

```python
texts = [{"title": "tiktok", "message": "tiktok", "description": "tiktok"}]
```

改成调用 helper：

```python
try:
    texts = pushes.resolve_push_texts(product_id)
except (pushes.CopywritingMissingError, pushes.CopywritingParseError) as exc:
    return jsonify({
        "error": str(exc),
        "code": "copywriting_not_ready",
    }), 409
```

注意：这段代码在当前文件里 import 的是 `from appcore import medias, pushes, tos_clients`，已经有 `pushes`，直接用。

- [ ] **Step 5.3 — 手工冒烟：跑老的 routes 测试，确保没打断**

Run:

```bash
python -m pytest tests/test_pushes_routes.py -v
```

Expected: 原有用例全 PASSED（这些用例不走 OpenAPI 路由）。

如果有 `tests/test_openapi*.py` 也跑一下：

```bash
python -m pytest tests/ -k openapi -v
```

- [ ] **Step 5.4 — 手工冒烟：验证 409 路径（可选但建议）**

启动本地服务后：

```bash
curl -s "http://localhost:PORT/openapi/materials/dino-glider-launcher-toy-rjc/push-payload?lang=en" \
    -H "X-API-Key: $OPENAPI_MEDIA_API_KEY" -w "\n%{http_code}\n"
```

期望：如果该产品英文文案合规 → 200 + 真实 texts；不合规 → 409 + `{"error":"...","code":"copywriting_not_ready"}`。

- [ ] **Step 5.5 — 提交**

```bash
git add web/routes/openapi_materials.py
git commit -m "feat(push): 文案不合规时接口返回 409"
```

---

## Task 6：AutoPush 手工端到端冒烟

**Files:** 无代码改动

- [ ] **Step 6.1 — 启动 AutoPush**

Run：

```bash
cd AutoPush && python main.py
```

然后浏览器打开 <http://127.0.0.1:8787> → 切到「推送列表」。

- [ ] **Step 6.2 — 校验「制作中」分组**

对一款**没有英文文案**的产品，期望状态显示为「制作中」，hover 操作按钮能看到缺项（新增的 `has_push_texts` 会体现在 readiness tooltip 里）。

**注意：** 前端 [AutoPush/static/app.js:593-606](../../AutoPush/static/app.js#L593-L606) 的 `buildActionButton` 把 readiness key 映射到中文，目前 map 里没有 `has_push_texts`。让它 fallback 到 key 原文（`has_push_texts`）。如果想展示中文，可**可选**加一行：

```js
has_push_texts: "英文文案",
```

这不是阻塞项，留给后续优化。

- [ ] **Step 6.3 — 校验「去推送」错误弹窗**

找一款英文文案**不合规**的产品 → 点「去推送」→ 弹窗里应该看到 409 的错误消息（例如 "文案缺少字段：title"）。

如果英文文案合规 → 弹窗加载 payload，`texts` 展示真实三段文字。

- [ ] **Step 6.4 — 用真实产品 `dino-glider-launcher-toy-rjc` 验证**

按需求输入产品 `dino-glider-launcher-toy-rjc`，英文 idx=1 文案应能解析成：

```json
"texts": [
  {
    "title": "Ready. Aim. LAUNCH! 🌪️",
    "message": "Experience the thrill! 🤩 Instant mechanical launch. Durable & crash-proof. The coolest gift for ages 3+.",
    "description": "Fly High Today ✈️"
  }
]
```

- [ ] **Step 6.5 — 最终全套测试回归**

Run:

```bash
python -m pytest tests/ -v
```

Expected: 全绿，没有 regression。

---

## Self-Review

已按 spec 覆盖：

| Spec 节 | 对应 Task |
| --- | --- |
| 4.1 解析器 | Task 1 |
| 4.2 读取 & 组装（resolve helper + build_item_payload 接入） | Task 2 + Task 3 |
| 4.3 异常类 | Task 1 |
| 4.4 就绪度新增 `has_push_texts` | Task 4 |
| 4.5 接口层 409 | Task 5 |
| 4.6 AutoPush 前端 | Task 6（冒烟验证；无代码改动） |
| 5 测试 | Task 1/2/3/4 的 TDD |
| 6 非目标 | 计划未涉及前端拆分、非 en 文案、LLM 兜底 |
| 7 风险 | Step 6.2 提示列表会把未写文案的产品显示为制作中 |

无 TBD / TODO / "see above"。所有类型、函数名在任务间一致（`resolve_push_texts`、`parse_copywriting_body`、`CopywritingMissingError`、`CopywritingParseError`、`has_push_texts`）。
