"""bulk_translate plan 生成器测试。完整 mock DB。"""
import pytest


class _FakeDB:
    def __init__(self, copies=None, details=None, covers=None, raw_sources=None):
        self.copies = copies or []   # [{"id": N}, ...]
        self.details = details or [] # [N, ...]
        self.covers = covers or []
        self.raw_sources = raw_sources or []   # [{"id": N}, ...]

    def query(self, sql, args=None):
        s = sql.lower()
        if "from media_copywritings" in s:
            return list(self.copies)
        if "from media_product_detail_images" in s:
            return [{"id": i} for i in self.details]
        if "from media_product_covers" in s:
            return [{"id": i} for i in self.covers]
        if "from media_raw_sources" in s:
            return list(self.raw_sources)
        raise AssertionError(f"unexpected query: {sql}")


def _patch(monkeypatch, fake):
    from appcore import bulk_translate_plan as mod
    monkeypatch.setattr(mod, "query", fake.query)


# ------------------------------------------------------------

def test_copy_only_cross_product(monkeypatch):
    """2 英文文案 × 2 目标语言 = 4 plan 项。"""
    _patch(monkeypatch, _FakeDB(copies=[{"id": 10}, {"id": 11}]))

    from appcore.bulk_translate_plan import generate_plan
    plan = generate_plan(1, 77, ["de", "fr"], ["copy"], False)

    assert len(plan) == 4
    kinds = {p["kind"] for p in plan}
    assert kinds == {"copy"}
    langs = sorted(set(p["lang"] for p in plan))
    assert langs == ["de", "fr"]
    # idx 连续且从 0
    assert [p["idx"] for p in plan] == [0, 1, 2, 3]
    # 所有 status 默认 pending
    assert all(p["status"] == "pending" for p in plan)


def test_detail_batch_one_per_lang(monkeypatch):
    """3 英文详情图 × 2 语言 → 2 个 batch plan 项,每个含 3 源 id。"""
    _patch(monkeypatch, _FakeDB(details=[100, 101, 102]))

    from appcore.bulk_translate_plan import generate_plan
    plan = generate_plan(1, 77, ["de", "fr"], ["detail"], False)

    assert len(plan) == 2
    assert {p["lang"] for p in plan} == {"de", "fr"}
    for p in plan:
        assert p["kind"] == "detail"
        assert p["ref"]["source_detail_ids"] == [100, 101, 102]


def test_cover_batch_one_per_lang(monkeypatch):
    _patch(monkeypatch, _FakeDB(covers=[200, 201]))

    from appcore.bulk_translate_plan import generate_plan
    plan = generate_plan(1, 77, ["de"], ["cover"], False)

    assert len(plan) == 1
    assert plan[0]["kind"] == "cover"
    assert plan[0]["ref"]["source_cover_ids"] == [200, 201]


def test_video_only_de_fr_generates_items(monkeypatch):
    """视频 target_lang ∈ {de, fr} 才生成 plan 项;其他语言 skip 规划。"""
    _patch(monkeypatch, _FakeDB(raw_sources=[{"id": 1}, {"id": 2}]))

    from appcore.bulk_translate_plan import generate_plan
    plan = generate_plan(
        1, 77, ["de", "fr", "es", "it"], ["video"], False, raw_source_ids=[1, 2],
    )

    # 2 视频 × 2 支持语种 = 4,es/it 不规划
    video_items = [p for p in plan if p["kind"] == "video"]
    assert len(video_items) == 4
    assert {p["lang"] for p in video_items} == {"de", "fr"}


def test_mixed_content_types(monkeypatch):
    """4 种 kind 都勾时的组合展开。"""
    _patch(monkeypatch, _FakeDB(
        copies=[{"id": 10}],
        details=[100, 101],
        covers=[200],
        raw_sources=[{"id": 1}],
    ))

    from appcore.bulk_translate_plan import generate_plan
    plan = generate_plan(
        1, 77, ["de", "fr"],
        ["copy", "detail", "cover", "video"], False, raw_source_ids=[1],
    )

    by_kind = {}
    for p in plan:
        by_kind.setdefault(p["kind"], []).append(p)

    # copy: 1 条 × 2 语种 = 2
    assert len(by_kind["copy"]) == 2
    # detail: batch,1/语种 = 2
    assert len(by_kind["detail"]) == 2
    # cover: batch,1/语种 = 2
    assert len(by_kind["cover"]) == 2
    # video: 1 视频 × 2 支持语种 = 2
    assert len(by_kind["video"]) == 2
    assert len(plan) == 8

    # idx 从 0 开始连续
    assert [p["idx"] for p in plan] == list(range(8))


def test_empty_product_empty_plan(monkeypatch):
    _patch(monkeypatch, _FakeDB())

    from appcore.bulk_translate_plan import generate_plan
    plan = generate_plan(1, 77, ["de"],
                           ["copy", "detail", "cover"], False)
    assert plan == []


def test_no_detail_images_no_batch_item(monkeypatch):
    """详情图为空时,不生成 detail batch plan 项。"""
    _patch(monkeypatch, _FakeDB(copies=[{"id": 10}]))

    from appcore.bulk_translate_plan import generate_plan
    plan = generate_plan(1, 77, ["de"], ["copy", "detail"], False)
    assert all(p["kind"] != "detail" for p in plan)
    assert len(plan) == 1


def test_plan_item_schema(monkeypatch):
    _patch(monkeypatch, _FakeDB(copies=[{"id": 10}]))

    from appcore.bulk_translate_plan import generate_plan
    plan = generate_plan(1, 77, ["de"], ["copy"], False)
    item = plan[0]

    required_keys = {"idx", "kind", "lang", "ref",
                      "sub_task_id", "status", "error",
                      "started_at", "finished_at"}
    assert required_keys.issubset(item.keys())
    assert item["sub_task_id"] is None
    assert item["status"] == "pending"
    assert item["error"] is None
    assert item["started_at"] is None
    assert item["finished_at"] is None
