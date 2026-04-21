import pytest


class _FakeDB:
    def __init__(self, copies=None, details=None, covers=None, raw_sources=None):
        self.copies = copies or []
        self.details = details or []
        self.covers = covers or []
        self.raw_sources = raw_sources or []

    def query(self, sql, args=None):
        s = " ".join(str(sql).lower().split())
        if "from media_copywritings" in s:
            return list(self.copies)
        if "from media_product_detail_images" in s:
            return [{"id": i} for i in self.details]
        if "from media_product_covers" in s:
            return [{"id": i} for i in self.covers]
        if "from media_raw_sources" in s:
            raw_ids = list(args[:-1])
            product_id = args[-1]
            return [
                {"id": row["id"]}
                for row in self.raw_sources
                if row["product_id"] == product_id
                and not row.get("deleted")
                and row["id"] in raw_ids
            ]
        raise AssertionError(f"unexpected query: {sql}")


def _patch(monkeypatch, fake):
    from appcore import bulk_translate_plan as mod

    monkeypatch.setattr(mod, "query", fake.query)


def test_video_from_raw_sources(monkeypatch):
    _patch(monkeypatch, _FakeDB(raw_sources=[
        {"id": 11, "product_id": 77, "deleted": False},
        {"id": 12, "product_id": 77, "deleted": False},
    ]))

    from appcore.bulk_translate_plan import generate_plan

    plan = generate_plan(
        user_id=1,
        product_id=77,
        target_langs=["de", "fr"],
        content_types=["video"],
        force_retranslate=False,
        raw_source_ids=[11, 12],
    )

    assert len(plan) == 4
    assert {item["kind"] for item in plan} == {"video"}
    assert {item["ref"]["source_raw_id"] for item in plan} == {11, 12}


def test_video_refuses_empty_raw_source_ids(monkeypatch):
    _patch(monkeypatch, _FakeDB())

    from appcore.bulk_translate_plan import generate_plan

    with pytest.raises(ValueError, match="raw_source_ids"):
        generate_plan(
            1,
            77,
            ["de"],
            ["video"],
            False,
            raw_source_ids=[],
        )


def test_soft_deleted_raw_source_is_rejected(monkeypatch):
    _patch(monkeypatch, _FakeDB(raw_sources=[
        {"id": 21, "product_id": 77, "deleted": True},
    ]))

    from appcore.bulk_translate_plan import generate_plan

    with pytest.raises(ValueError, match="not found"):
        generate_plan(
            1,
            77,
            ["de"],
            ["video"],
            False,
            raw_source_ids=[21],
        )


def test_copy_and_cover_detail_unchanged(monkeypatch):
    _patch(monkeypatch, _FakeDB())

    from appcore.bulk_translate_plan import generate_plan

    plan = generate_plan(
        1,
        77,
        ["de"],
        ["copy", "cover", "detail"],
        False,
        raw_source_ids=None,
    )

    assert plan == []
