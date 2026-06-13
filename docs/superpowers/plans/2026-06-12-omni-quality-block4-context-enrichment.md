# Block 4 — 产品上下文注入 + 批间上下文 实施计划

> **For agentic workers:** 按 Task 顺序 TDD 执行。Spec：[specs/2026-06-12-omni-quality-block4-context-enrichment-design.md](../specs/2026-06-12-omni-quality-block4-context-enrichment-design.md)；红线：[specs/2026-06-12-omni-quality-overview.md](../specs/2026-06-12-omni-quality-overview.md)。

**Goal:** 批量链路把产品事实（名称/类目/卖点/品牌词）注入翻译与 rewrite prompt；长视频分批翻译批间带全局原文与前批译文衔接。

**Architecture:** `pipeline/localization.py`（`build_product_context_block` + `batch_context` 参数）、`pipeline/translate.py`（批间上下文编排）、`appcore/runtime_omni_steps.py` / `appcore/runtime_omni.py`（注入点）、`appcore/bulk_translate_runtime.py`（数据源）。

**分支**: 从 `origin/audit/video-translate-quality`（或已合 master）切出 `fix/omni-quality-block4-context`。

---

### Task 1: `build_product_context_block`（纯函数 + 测试）

**Files:**
- Modify: `pipeline/localization.py`
- Create: `tests/test_product_context_block.py`

- [ ] **Step 1: 写失败测试**

```python
from pipeline.localization import build_product_context_block


def test_empty_context_returns_empty():
    assert build_product_context_block({}) == ""
    assert build_product_context_block(None) == ""
    assert build_product_context_block({"name": "", "selling_points": []}) == ""


def test_minimal_name_only():
    block = build_product_context_block({"name": "Ice Ball Mold"})
    assert "PRODUCT CONTEXT" in block
    assert "Ice Ball Mold" in block
    assert "Official name" not in block


def test_full_context():
    block = build_product_context_block({
        "name": "冰球模具", "name_target_lang": "Eisball-Form",
        "category": "Kitchen", "selling_points": ["slow melt", "easy release"],
        "brand_terms": ["IceMax"],
    })
    assert "Eisball-Form" in block and "Kitchen" in block
    assert "slow melt; easy release" in block
    assert "IceMax" in block and "Never translate brand terms" in block
```

- [ ] **Step 2: 实现**

```python
def build_product_context_block(product_context: dict | None) -> str:
    ctx = product_context or {}
    name = str(ctx.get("name") or "").strip()
    name_target = str(ctx.get("name_target_lang") or "").strip()
    category = str(ctx.get("category") or "").strip()
    points = [str(p).strip() for p in (ctx.get("selling_points") or []) if str(p).strip()]
    brands = [str(b).strip() for b in (ctx.get("brand_terms") or []) if str(b).strip()]
    if not any([name, name_target, category, points, brands]):
        return ""
    lines = ["PRODUCT CONTEXT (authoritative product facts — use them to translate",
             "product references correctly):"]
    if name:
        lines.append(f"- Product name: {name}")
    if name_target:
        lines.append(f"- Official name in target language: {name_target}")
    if category:
        lines.append(f"- Category: {category}")
    if points:
        lines.append(f"- Key selling points: {'; '.join(points)}")
    if brands:
        lines.append(f"- Brand terms to keep verbatim: {', '.join(brands)}")
    lines.append("When the script mentions the product, use the official "
                 "target-language name verbatim. Never translate brand terms.")
    return "\n".join(lines)
```

- [ ] **Step 3**: 跑 PASS → `git commit -am "feat(block4): build_product_context_block helper"`

### Task 2: 初译注入

**Files:**
- Modify: `appcore/runtime_omni_steps.py::step_translate_standard`
- Test: `tests/test_translate_standard_product_context.py`

- [ ] **Step 1: 写失败测试**（mock `generate_localized_translation` 捕获 `custom_system_prompt`，task_state mock 返回含 `product_context` 的 task）：

```python
def test_system_prompt_carries_product_context(monkeypatch):
    captured = {}
    def fake_generate(source, segs, **kw):
        captured["prompt"] = kw["custom_system_prompt"]
        return {"full_text": "x", "sentences": [
            {"index": 0, "text": "x", "source_segment_indices": [0]}]}
    # monkeypatch generate_localized_translation / task_state.get / runner stub …
    # 断言:
    assert "PRODUCT CONTEXT" in captured["prompt"]
    assert captured["prompt"].index("INPUT NOTICE") < captured["prompt"].index("PRODUCT CONTEXT")


def test_no_context_prompt_unchanged(...):
    assert "PRODUCT CONTEXT" not in captured["prompt"]
```

（测试基建参考 `tests/` 中现有 runtime_omni_steps 相关测试的 runner stub 写法；若无先例则构造最小 FakeRunner——`_resolve_target_lang` / `_build_system_prompt` / `_set_step` / `_emit` / `user_id` stub 即可。）
- [ ] **Step 2: 实现**。`step_translate_standard` 中 `system_prompt` 组装末尾：

```python
    from pipeline.localization import build_product_context_block
    product_block = build_product_context_block(task.get("product_context"))
    if product_block:
        system_prompt = f"{system_prompt}\n\n{product_block}"
```

- [ ] **Step 3**: 跑 PASS → `git commit -am "feat(block4): inject product context into initial translation prompt"`

### Task 3: rewrite 链路注入

**Files:**
- Modify: `appcore/runtime_omni.py`（`OmniLocalizationAdapter`）
- Test: 追加到 `tests/test_translate_standard_product_context.py`

- [ ] **Step 1**: `OmniLocalizationAdapter.__init__` 加参数 `product_context: dict | None = None` 存为 `self.product_context`；`OmniTranslateRunner._get_localization_module` 三个构造点都传 `product_context=task.get("product_context")`（`OmniJapaneseLocalizationAdapter` / `OmniModuleLocalizationAdapter` 的 `__init__` 透传 super）。
- [ ] **Step 2**: `build_localized_rewrite_messages` 的 `user_content` 开头：

```python
        from pipeline.localization import build_product_context_block
        product_block = build_product_context_block(self.product_context)
        prefix = f"{product_block}\n\n" if product_block else ""
        user_content = (
            f"{prefix}ORIGINAL VIDEO TRANSCRIPT ({src_label}, ground truth — what the video actually says):\n"
            ...
        )
```

- [ ] **Step 3**: 测试断言 rewrite messages 含 PRODUCT CONTEXT；无 context 时 messages 与现状一致。跑 PASS → `git commit -am "feat(block4): product context in omni rewrite messages"`

### Task 4: 批量链路数据源

**Files:**
- Modify: `appcore/bulk_translate_runtime.py`
- Test: `tests/test_bulk_product_context.py`

- [ ] **Step 1: 调研**（结论写进 commit message）：`grep -rn "product" appcore/bulk_translate_runtime.py | head -30` 找子任务创建函数与 product_id 流转；`grep -rn "def get_product\|FROM products" appcore/ web/services/ | head` 找产品 DAO；确认产品表有哪些可用字段（名称/类目/多语言名/卖点）。
- [ ] **Step 2: 实现** `_build_product_context(product_id: int, target_lang: str) -> dict | None`：查产品库组装 Spec R1 结构（最低 name+category；多语言名表存在则填 `name_target_lang`）；任何异常 → log.warning + return None。子任务创建参数 / task_state 写入 `product_context`（仅非 None 时）。
- [ ] **Step 3**: 测试（mock DAO）：有产品 → 子任务带 context；DAO 抛错 → 子任务正常创建且无该字段。跑 PASS → `git commit -am "feat(block4): bulk pipeline auto-fills product_context from product library"`

### Task 5: 批间上下文

**Files:**
- Modify: `pipeline/localization.py`（`build_localized_translation_messages` 加 `batch_context` 参数）、`pipeline/translate.py`（批循环组装）
- Test: `tests/test_batch_context.py`

- [ ] **Step 1: 写失败测试**

```python
from unittest.mock import patch
from pipeline.translate import _generate_localized_translation_batched


def _fake_single_factory(calls):
    def fake(source, batch, **kw):
        calls.append(kw.get("batch_context"))
        return {"full_text": "t", "sentences": [
            {"index": 0, "text": "t", "source_segment_indices": [int(batch[0]["index"])]}],
            "_messages": []}
    return fake


def test_second_batch_gets_global_context():
    segs = [{"index": i, "text": f"seg {i}"} for i in range(24)]
    calls = []
    with patch("pipeline.translate._generate_localized_translation_single",
               side_effect=_fake_single_factory(calls)):
        _generate_localized_translation_batched(
            "full text", segs, variant="normal", custom_system_prompt=None,
            use_case="video_translate.localize", user_id=None, batch_size=12)
    assert calls[0] is None
    assert "GLOBAL CONTEXT" in calls[1]
    assert "Previous batch translation" in calls[1]
```

- [ ] **Step 2: 实现**：
  - `build_localized_translation_messages(..., batch_context: str | None = None)`：非空时 user content 末尾 `\n\n{batch_context}`。
  - `_generate_localized_translation_single` 加 `batch_context=None` 透传。
  - `_generate_localized_translation_batched` 批循环内（batch_idx ≥ 1 且 `all_sentences` 非空时）：

```python
        batch_context = None
        if batch_idx > 0 and all_sentences:
            tail = " ".join(s.get("text", "") for s in all_sentences[-3:])
            src = source_full_text_zh
            if len(src) > 4000:
                src = src[:2000] + "\n...\n" + src[-1000:]
            batch_context = (
                "GLOBAL CONTEXT (for consistency; translate ONLY the segments above):\n"
                f"Full source script:\n{src}\n\n"
                "Previous batch translation (continue seamlessly from here, keep "
                f"terminology and tone consistent):\n{tail}"
            )
```

  - 注意 checkpoint 续跑：从 checkpoint 恢复时 `all_sentences` 已含前批句子，逻辑自然成立。
- [ ] **Step 3**: 跑 PASS；补一条"messages 不含 batch_context 时与现状一致"的回归断言。`git commit -am "feat(block4): inter-batch global context for long-video translation"`

### Task 6: 收尾验证

- [ ] `python3 scripts/pytest_related.py --base origin/master --run` 全 PASS。
- [ ] diff 自查：无 `_pipeline_runner.py` / multi / 时长逻辑改动。
- [ ] push `fix/omni-quality-block4-context`，停下等验收。汇报附一条批量任务的 `localized_translate_messages.json` 截图/摘录（PRODUCT CONTEXT 可见）。
