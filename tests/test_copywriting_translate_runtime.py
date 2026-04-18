"""copywriting_translate_runtime Task 6 单元测试。

只测 translate_copy_text 这一薄封装层(mock _llm_translate,不打真实 LLM)。
完整 Runner 集成测试在 Task 7 追加。
"""
import pytest


def test_translate_copy_text_empty_returns_empty(monkeypatch):
    """空/空白输入直接返回,不调 LLM。"""
    calls = []
    from appcore import copywriting_translate_runtime as mod
    monkeypatch.setattr(mod, "_llm_translate",
                         lambda *a, **kw: calls.append(a) or ("should_not_reach", 0))

    text, tokens = mod.translate_copy_text("", "en", "de")
    assert text == ""
    assert tokens == 0
    assert calls == []

    text, tokens = mod.translate_copy_text("   ", "en", "de")
    assert text == ""
    assert tokens == 0


def test_translate_copy_text_delegates_to_llm(monkeypatch):
    """非空输入调 _llm_translate,参数/返回值完整透传。"""
    captured = {}

    def fake(source_text, source_lang, target_lang):
        captured.update({
            "text": source_text, "src": source_lang, "tgt": target_lang,
        })
        return "Willkommen zu unserem Produkt", 120

    from appcore import copywriting_translate_runtime as mod
    monkeypatch.setattr(mod, "_llm_translate", fake)

    text, tokens = mod.translate_copy_text("Welcome to our product", "en", "de")
    assert text == "Willkommen zu unserem Produkt"
    assert tokens == 120
    assert captured == {"text": "Welcome to our product", "src": "en", "tgt": "de"}


def test_llm_translate_sums_input_and_output_tokens(monkeypatch):
    """内部 _llm_translate 把 input + output tokens 合成总数。"""
    def fake_translate_text(text, src, tgt, **kw):
        return {"text": "Willkommen", "input_tokens": 40, "output_tokens": 12}

    from appcore import copywriting_translate_runtime as mod
    monkeypatch.setattr(mod, "translate_text", fake_translate_text)

    text, tokens = mod._llm_translate("Welcome", "en", "de")
    assert text == "Willkommen"
    assert tokens == 52   # 40 + 12


def test_llm_translate_handles_missing_token_keys(monkeypatch):
    """pipeline 返回里缺 token 字段也不应崩溃。"""
    def fake_translate_text(text, src, tgt, **kw):
        return {"text": "Willkommen"}

    from appcore import copywriting_translate_runtime as mod
    monkeypatch.setattr(mod, "translate_text", fake_translate_text)

    text, tokens = mod._llm_translate("Welcome", "en", "de")
    assert text == "Willkommen"
    assert tokens == 0


# ============================================================
# CopywritingTranslateRunner — 用 mock 完全隔离 DB 做单元测试
# ============================================================

class _FakeDB:
    """集成到 monkeypatch 用的 fake DB。一次测试内可预埋一组 src,拦截写入。"""

    def __init__(self, sources=None):
        self.sources = {s["id"]: s for s in (sources or [])}
        self.inserted = []                # INSERT 记录列表
        self.updates = []                 # (sql, args) 列表
        self.marks = []                   # mark_auto_translated 记录
        self._next_id = 1000

    def query_one(self, sql, args=None):
        sql_upper = sql.upper()
        # 读 projects
        if "FROM PROJECTS" in sql_upper:
            return {"user_id": 7, "state_json": self._pending_state_json}
        # 读 media_copywritings
        if "FROM MEDIA_COPYWRITINGS" in sql_upper:
            cid = args[0]
            return self.sources.get(cid)
        raise AssertionError(f"unexpected query_one: {sql}")

    def execute(self, sql, args=None):
        sql_upper = sql.upper()
        if "INSERT INTO MEDIA_COPYWRITINGS" in sql_upper:
            new_id = self._next_id
            self._next_id += 1
            self.inserted.append({"id": new_id, "args": args})
            return new_id
        if "UPDATE PROJECTS" in sql_upper:
            self.updates.append((sql, args))
            return 1
        raise AssertionError(f"unexpected execute: {sql}")


def _make_fake(monkeypatch, sources, initial_state):
    """把 runtime 模块内的 DB/关联/LLM 调用全替换为 fake。"""
    from appcore import copywriting_translate_runtime as mod

    fake = _FakeDB(sources=sources)
    fake._pending_state_json = initial_state

    monkeypatch.setattr(mod, "query_one", fake.query_one)
    monkeypatch.setattr(mod, "execute", fake.execute)

    def fake_mark(**kw):
        fake.marks.append(kw)
        return 1
    monkeypatch.setattr(mod, "mark_auto_translated", fake_mark)

    def fake_translate(text, src, tgt):
        if not text:
            return "", 0
        return f"[{tgt}] {text}", len(text)
    monkeypatch.setattr(mod, "translate_copy_text", fake_translate)

    return fake


def test_runner_happy_path_inserts_and_marks(monkeypatch):
    """完整正常路径:读源 → 翻译多字段 → 插入 → 标记关联 → status=done。"""
    import json
    src = {
        "id": 101, "product_id": 55, "idx": 1, "lang": "en",
        "title": "Welcome", "body": "Short body",
        "description": "A description",
        "ad_carrier": None, "ad_copy": "", "ad_keywords": None,
    }
    state = json.dumps({
        "source_copy_id": 101,
        "source_lang": "en", "target_lang": "de",
        "parent_task_id": "parent_xxx",
    })

    fake = _make_fake(monkeypatch, sources=[src], initial_state=state)

    from appcore.copywriting_translate_runtime import CopywritingTranslateRunner
    runner = CopywritingTranslateRunner("task_abc")
    runner.start()

    # 插入了一行 media_copywritings
    assert len(fake.inserted) == 1
    ins = fake.inserted[0]
    # args 顺序: product_id, lang, idx, title, body, description,
    #           ad_carrier, ad_copy, ad_keywords
    args = ins["args"]
    assert args[0] == 55       # product_id
    assert args[1] == "de"     # target lang
    assert args[2] == 1        # idx
    assert args[3] == "[de] Welcome"
    assert args[4] == "[de] Short body"
    assert args[5] == "[de] A description"
    # 空字段不翻译,保留原值
    assert args[6] is None     # ad_carrier 原来就 None
    assert args[7] == ""       # ad_copy 空串
    assert args[8] is None     # ad_keywords

    # mark_auto_translated 被调用,参数正确
    assert fake.marks == [{
        "table": "media_copywritings",
        "target_id": ins["id"],
        "source_ref_id": 101,
        "bulk_task_id": "parent_xxx",
    }]

    # 最后一次 UPDATE projects 设 status=done
    status_updates = [u for u in fake.updates if "SET STATUS" in u[0].upper()]
    assert status_updates, "应有 UPDATE projects SET status=..."
    final_status_update = status_updates[-1]
    assert final_status_update[1][0] == "done"


def test_runner_failure_marks_error_and_reraises(monkeypatch):
    """LLM 抛异常时:项 status=error,异常向上抛,state_json 记录 last_error。"""
    import json
    src = {
        "id": 101, "product_id": 55, "idx": 1, "lang": "en",
        "title": "Welcome", "body": None, "description": None,
        "ad_carrier": None, "ad_copy": None, "ad_keywords": None,
    }
    state = json.dumps({
        "source_copy_id": 101,
        "source_lang": "en", "target_lang": "de",
        "parent_task_id": None,
    })
    fake = _make_fake(monkeypatch, sources=[src], initial_state=state)

    from appcore import copywriting_translate_runtime as mod
    def fail_translate(*a, **kw):
        raise RuntimeError("LLM timeout")
    monkeypatch.setattr(mod, "translate_copy_text", fail_translate)

    runner = mod.CopywritingTranslateRunner("task_err")
    with pytest.raises(RuntimeError, match="LLM timeout"):
        runner.start()

    # 应无 INSERT(翻译失败未走到插入)
    assert fake.inserted == []
    # 最终 status = error
    status_updates = [u for u in fake.updates if "SET STATUS" in u[0].upper()]
    assert status_updates[-1][1][0] == "error"


def test_runner_skips_empty_fields_and_no_llm_call(monkeypatch):
    """空/None 字段不应调 LLM。"""
    import json
    src = {
        "id": 99, "product_id": 42, "idx": 1, "lang": "en",
        "title": "Only title",
        "body": None, "description": "", "ad_carrier": None,
        "ad_copy": None, "ad_keywords": None,
    }
    state = json.dumps({
        "source_copy_id": 99,
        "source_lang": "en", "target_lang": "fr",
        "parent_task_id": "p1",
    })
    fake = _make_fake(monkeypatch, sources=[src], initial_state=state)

    from appcore import copywriting_translate_runtime as mod
    calls = []
    def counting(text, src_l, tgt):
        calls.append(text)
        return f"[{tgt}] {text}", 5
    monkeypatch.setattr(mod, "translate_copy_text", counting)

    runner = mod.CopywritingTranslateRunner("task_skip")
    runner.start()

    # 只有 "Only title" 被翻译
    assert calls == ["Only title"]
    ins = fake.inserted[0]
    args = ins["args"]
    assert args[3] == "[fr] Only title"
    assert args[4] is None        # body
    assert args[5] == ""          # description


def test_runner_missing_source_raises_before_insert(monkeypatch):
    """源文案不存在时,立刻抛错且不写任何表。"""
    import json
    state = json.dumps({
        "source_copy_id": 999_999,   # 不存在
        "source_lang": "en", "target_lang": "de",
        "parent_task_id": None,
    })
    fake = _make_fake(monkeypatch, sources=[], initial_state=state)

    from appcore.copywriting_translate_runtime import CopywritingTranslateRunner
    runner = CopywritingTranslateRunner("task_missing")
    with pytest.raises(ValueError, match="Source copywriting"):
        runner.start()

    assert fake.inserted == []
    assert fake.marks == []
    status_updates = [u for u in fake.updates if "SET STATUS" in u[0].upper()]
    assert status_updates[-1][1][0] == "error"
