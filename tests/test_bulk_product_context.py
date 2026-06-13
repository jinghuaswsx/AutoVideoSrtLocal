from pathlib import Path


def _item(*, lang: str = "de", source_raw_id: int = 301) -> dict:
    return {
        "idx": 0,
        "kind": "videos",
        "lang": lang,
        "ref": {"source_raw_id": source_raw_id},
        "child_task_id": None,
        "child_task_type": None,
    }


def _prepare_video_child(monkeypatch, tmp_path, mod):
    raw_key = "1/medias/77/raw_sources/raw-demo.mp4"
    created = {}
    updated = {}
    started = []

    monkeypatch.setattr(mod, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(mod, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(
        mod.medias,
        "get_raw_source",
        lambda rid: {
            "id": rid,
            "product_id": 77,
            "user_id": 1,
            "display_name": "raw-demo",
            "video_object_key": raw_key,
            "file_size": 1234,
        },
    )
    monkeypatch.setattr(mod, "execute", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        mod.store,
        "create",
        lambda task_id, video_path, task_dir, original_filename, user_id: created.update({
            "task_id": task_id,
            "video_path": video_path,
            "task_dir": task_dir,
            "original_filename": original_filename,
            "user_id": user_id,
        }),
    )
    monkeypatch.setattr(
        mod.store,
        "update",
        lambda task_id, **fields: updated.setdefault(task_id, fields),
    )
    monkeypatch.setattr(mod.store, "set_preview_file", lambda *args, **kwargs: None)

    def fake_download_to(object_key, destination):
        Path(destination).write_bytes(b"video")
        created["download"] = (object_key, destination)
        return destination

    monkeypatch.setattr(mod.local_media_storage, "download_to", fake_download_to)
    monkeypatch.setattr(
        mod.runner_dispatch,
        "start_omni_translate_runner",
        lambda task_id, user_id=None: started.append((task_id, user_id)) or True,
    )
    return created, updated, started


def test_create_video_child_writes_product_context_from_product_library(monkeypatch, tmp_path):
    from appcore import bulk_translate_runtime as mod

    _created, updated, _started = _prepare_video_child(monkeypatch, tmp_path, mod)
    monkeypatch.setattr(
        mod.medias,
        "get_product",
        lambda product_id: {
            "id": product_id,
            "name": "冰球模具",
            "category_name": "Kitchen",
            "selling_points": "slow melt\n easy release ",
            "brand_terms": "IceMax",
        },
    )
    monkeypatch.setattr(
        mod.medias,
        "list_copywritings",
        lambda product_id, lang: [{"title": "Eisball-Form"}] if lang == "de" else [],
    )

    child_task_id, child_type, status = mod._create_video_child(
        "parent-1",
        _item(lang="de"),
        {
            "product_id": 77,
            "initiator": {"user_id": 1},
            "video_params_snapshot": {},
        },
    )

    assert child_type == "omni_translate"
    assert status == "running"
    ctx = updated[child_task_id]["product_context"]
    assert ctx == {
        "name": "冰球模具",
        "name_target_lang": "Eisball-Form",
        "category": "Kitchen",
        "selling_points": ["slow melt", "easy release"],
        "brand_terms": ["IceMax"],
    }


def test_create_video_child_skips_product_context_when_product_query_fails(monkeypatch, tmp_path):
    from appcore import bulk_translate_runtime as mod

    _created, updated, started = _prepare_video_child(monkeypatch, tmp_path, mod)

    def raise_product_error(product_id):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(mod.medias, "get_product", raise_product_error)
    monkeypatch.setattr(mod.medias, "list_copywritings", lambda product_id, lang: [])

    child_task_id, child_type, status = mod._create_video_child(
        "parent-1",
        _item(lang="de"),
        {
            "product_id": 77,
            "initiator": {"user_id": 1},
            "video_params_snapshot": {},
        },
    )

    assert child_type == "omni_translate"
    assert status == "running"
    assert started == [(child_task_id, 1)]
    assert "product_context" not in updated[child_task_id]
