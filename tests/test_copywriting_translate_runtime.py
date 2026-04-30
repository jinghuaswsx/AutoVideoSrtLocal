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


def test_translate_copy_text_normalizes_localized_copywriting_labels(monkeypatch):
    """结构化标题文案翻译后仍应保留中文字段标签。"""
    source = "标题: Ready. Aim. LAUNCH!\n文案: Experience the thrill.\n描述: Fly High Today"

    def fake(_source_text, _source_lang, _target_lang):
        return (
            "Titolo: Pronti. Mirare. LANCIO!\n"
            "Testo: Vivi l'emozione.\n"
            "Descrizione: Vola alto oggi",
            88,
        )

    from appcore import copywriting_translate_runtime as mod
    monkeypatch.setattr(mod, "_llm_translate", fake)

    text, tokens = mod.translate_copy_text(source, "en", "it")

    assert text == (
        "标题: Pronti. Mirare. LANCIO!\n"
        "文案: Vivi l'emozione.\n"
        "描述: Vola alto oggi"
    )
    assert tokens == 88


def test_translate_copy_text_strips_nested_localized_title_label(monkeypatch):
    """兼容模型输出“标题: Titolo: ...”这类嵌套标签。"""
    source = "标题: Ready\n文案: Do it\n描述: Go"

    def fake(_source_text, _source_lang, _target_lang):
        return "标题: Titolo: Pronti\n文案: Testo: Fallo\n描述: Descrizione: Vai", 42

    from appcore import copywriting_translate_runtime as mod
    monkeypatch.setattr(mod, "_llm_translate", fake)

    text, tokens = mod.translate_copy_text(source, "en", "it")

    assert text == "标题: Pronti\n文案: Fallo\n描述: Vai"
    assert tokens == 42


def _patch_llm_client(monkeypatch, fake_invoke):
    from appcore import copywriting_translate_runtime as mod

    class _Stub:
        @staticmethod
        def invoke_chat(*args, **kwargs):
            return fake_invoke(*args, **kwargs)

    monkeypatch.setattr(mod, "llm_client", _Stub)
    return mod


def _patch_get_prompt(monkeypatch, fn):
    from appcore import copywriting_translate_runtime as mod

    class _Stub:
        @staticmethod
        def get_prompt(code):
            return fn(code)

    monkeypatch.setattr(mod, "title_translate_settings", _Stub)
    return mod


def test_llm_translate_sums_input_and_output_tokens(monkeypatch):
    """内部 _llm_translate 把 input + output tokens 合成总数。"""
    def fake_invoke(use_case_code, **kwargs):
        assert use_case_code == "title_translate.generate"
        return {
            "text": "标题: Willkommen\n文案: -\n描述: -",
            "usage": {"input_tokens": 40, "output_tokens": 12},
        }

    _patch_get_prompt(monkeypatch, lambda code: "PROMPT_FOR_" + code + " {{SOURCE_TEXT}}")
    mod = _patch_llm_client(monkeypatch, fake_invoke)

    text, tokens = mod._llm_translate("Welcome", "en", "de")
    # 单字段输入会被包成三段式，模型返回的也是三段式，从 `标题:` 槽位取出。
    assert text == "Willkommen"
    assert tokens == 52   # 40 + 12


def test_llm_translate_handles_missing_token_keys(monkeypatch):
    """LLM 返回里缺 usage 字段也不应崩溃。"""
    def fake_invoke(*args, **kwargs):
        return {"text": "标题: Willkommen\n文案: -\n描述: -"}

    _patch_get_prompt(monkeypatch, lambda code: "PROMPT {{SOURCE_TEXT}}")
    mod = _patch_llm_client(monkeypatch, fake_invoke)

    text, tokens = mod._llm_translate("Welcome", "en", "de")
    assert text == "Willkommen"
    assert tokens == 0


def test_llm_translate_uses_title_translate_use_case_with_per_lang_prompt(monkeypatch):
    """关键不回归：bulk 翻译路径必须走 title_translate.generate use case，
    并通过 title_translate_settings.get_prompt(target_lang) 拼出强约束 prompt。
    避免回到旧的 text_translate.generate 路径，让 ja 复现「標題:」嵌套 bug。"""
    captured = {}

    def fake_get_prompt(code):
        captured["code"] = code
        return f"<PROMPT-{code}>\n{{{{SOURCE_TEXT}}}}\n<END>"

    def fake_invoke(use_case_code, **kwargs):
        captured["use_case_code"] = use_case_code
        captured["messages"] = kwargs.get("messages")
        captured["temperature"] = kwargs.get("temperature")
        return {
            "text": "标题: タイトル訳\n文案: 本文訳\n描述: 説明訳",
            "usage": {"input_tokens": 1, "output_tokens": 2},
        }

    _patch_get_prompt(monkeypatch, fake_get_prompt)
    mod = _patch_llm_client(monkeypatch, fake_invoke)

    src = "标题: What's on Your Keychain?\n文案: Mine opens bottles.\n描述: Discover the 3-in-1"
    text, tokens = mod._llm_translate(src, "en", "ja")

    assert captured["use_case_code"] == "title_translate.generate"
    assert captured["code"] == "ja"
    assert captured["temperature"] == 0.0
    assert len(captured["messages"]) == 1
    assert captured["messages"][0]["role"] == "user"
    # 三段式输入应原样塞进 prompt 的 {{SOURCE_TEXT}} 占位符。
    assert "What's on Your Keychain?" in captured["messages"][0]["content"]
    assert "<PROMPT-ja>" in captured["messages"][0]["content"]
    assert "{{SOURCE_TEXT}}" not in captured["messages"][0]["content"]

    # 三段式输出原样透传（由调用方 _normalize_copywriting_translation 再做行首规整）。
    assert text == "标题: タイトル訳\n文案: 本文訳\n描述: 説明訳"
    assert tokens == 3


def test_llm_translate_wraps_single_field_into_block_and_extracts_title(monkeypatch):
    """非三段式输入（例如 title 字段单独一行 'Welcome'）必须被包成三段式发送，
    解析模型返回的「标题:」槽位作为译文返回——防止把整段三段式塞进 title 列。"""
    captured = {}

    def fake_get_prompt(code):
        return "PROMPT_" + code + " {{SOURCE_TEXT}}"

    def fake_invoke(use_case_code, **kwargs):
        captured["content"] = kwargs["messages"][0]["content"]
        return {
            "text": "标题: Willkommen\n文案: -\n描述: -",
            "usage": {"input_tokens": 5, "output_tokens": 5},
        }

    _patch_get_prompt(monkeypatch, fake_get_prompt)
    mod = _patch_llm_client(monkeypatch, fake_invoke)

    text, tokens = mod._llm_translate("Welcome", "en", "de")

    # 单字段被包成三段式：标题位是原文，其它位用 "-" 占位。
    assert "标题: Welcome" in captured["content"]
    assert "文案: -" in captured["content"]
    assert "描述: -" in captured["content"]

    # 仅返回标题槽位的译文。
    assert text == "Willkommen"
    assert tokens == 10


# ============================================================
# CopywritingTranslateRunner — 用 mock 完全隔离 DB 做单元测试
# ============================================================

class _FakeDB:
    """集成到 monkeypatch 用的 fake DB。一次测试内可预埋一组 src,拦截写入。"""

    def __init__(self, sources=None):
        self.sources = {s["id"]: s for s in (sources or [])}
        self.inserted = []                # INSERT 记录列表
        self.updates = []                 # (sql, args) 列表
        self.deletes = []                 # (sql, args) 列表
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
        if "DELETE FROM MEDIA_COPYWRITINGS" in sql_upper:
            self.deletes.append((sql, args))
            return 1
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


def test_runner_replaces_existing_target_language_copywriting(monkeypatch):
    """产出目标语种译文时,应先清掉该产品该语种旧文案,最终只保留新译文。"""
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
    CopywritingTranslateRunner("task_replace").start()

    assert len(fake.deletes) == 1
    assert "WHERE product_id=%s AND lang=%s" in fake.deletes[0][0]
    assert fake.deletes[0][1] == (55, "de")
    assert len(fake.inserted) == 1


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


# ============================================================
# Task 9: SocketIO/EventBus 进度事件
# ============================================================

def test_runner_publishes_running_and_done_events(monkeypatch):
    """happy path 应发 running + done 两条事件。"""
    import json
    from appcore.events import EventBus, Event, EVT_CT_PROGRESS

    src = {
        "id": 201, "product_id": 55, "idx": 1, "lang": "en",
        "title": "Welcome", "body": None, "description": None,
        "ad_carrier": None, "ad_copy": None, "ad_keywords": None,
    }
    state = json.dumps({
        "source_copy_id": 201,
        "source_lang": "en", "target_lang": "de",
        "parent_task_id": "parent_abc",
    })
    _make_fake(monkeypatch, sources=[src], initial_state=state)

    events = []
    bus = EventBus()
    bus.subscribe(lambda e: events.append(e))

    from appcore.copywriting_translate_runtime import CopywritingTranslateRunner
    CopywritingTranslateRunner("task_evt_1", bus=bus).start()

    types_and_statuses = [(e.type, e.payload["status"]) for e in events]
    assert (EVT_CT_PROGRESS, "running") in types_and_statuses
    assert (EVT_CT_PROGRESS, "done") in types_and_statuses

    # done 事件带 tokens_used / target_copy_id / target_lang / parent_task_id
    done = next(e for e in events if e.payload["status"] == "done")
    assert done.payload["target_lang"] == "de"
    assert done.payload["parent_task_id"] == "parent_abc"
    assert done.payload["tokens_used"] > 0
    assert done.payload["target_copy_id"] > 0
    assert done.task_id == "task_evt_1"


def test_runner_publishes_error_event_on_failure(monkeypatch):
    """失败时发 running + error 两条事件。"""
    import json
    from appcore.events import EventBus, EVT_CT_PROGRESS

    src = {
        "id": 202, "product_id": 55, "idx": 1, "lang": "en",
        "title": "Welcome", "body": None, "description": None,
        "ad_carrier": None, "ad_copy": None, "ad_keywords": None,
    }
    state = json.dumps({
        "source_copy_id": 202,
        "source_lang": "en", "target_lang": "de",
        "parent_task_id": None,
    })
    _make_fake(monkeypatch, sources=[src], initial_state=state)

    from appcore import copywriting_translate_runtime as mod
    monkeypatch.setattr(mod, "translate_copy_text",
                         lambda *a, **k: (_ for _ in ()).throw(RuntimeError("X")))

    events = []
    bus = EventBus()
    bus.subscribe(lambda e: events.append(e))

    with pytest.raises(RuntimeError):
        mod.CopywritingTranslateRunner("task_evt_2", bus=bus).start()

    statuses = [e.payload["status"] for e in events]
    assert "running" in statuses
    assert "error" in statuses

    err = next(e for e in events if e.payload["status"] == "error")
    assert err.payload["error"] == "X"


def test_runner_without_bus_does_not_crash(monkeypatch):
    """bus=None 时不发事件,跑正常流程不崩溃(父任务调度器有时不挂 bus)。"""
    import json
    src = {
        "id": 203, "product_id": 55, "idx": 1, "lang": "en",
        "title": "Welcome", "body": None, "description": None,
        "ad_carrier": None, "ad_copy": None, "ad_keywords": None,
    }
    state = json.dumps({
        "source_copy_id": 203,
        "source_lang": "en", "target_lang": "de",
        "parent_task_id": None,
    })
    _make_fake(monkeypatch, sources=[src], initial_state=state)

    from appcore.copywriting_translate_runtime import CopywritingTranslateRunner
    CopywritingTranslateRunner("task_no_bus", bus=None).start()
    # 没 crash 就 pass
