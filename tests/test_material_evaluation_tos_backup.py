from pathlib import Path

import pytest

from appcore.safe_paths import PathSafetyError


def test_materialize_media_downloads_local_copy_when_exists_is_remote_only(monkeypatch, tmp_path):
    from appcore import material_evaluation

    local_path = tmp_path / "media_store" / "a.jpg"
    monkeypatch.setattr(material_evaluation.local_media_storage, "MEDIA_STORE_DIR", local_path.parent)
    monkeypatch.setattr(material_evaluation.local_media_storage, "exists", lambda key: True)
    monkeypatch.setattr(material_evaluation.local_media_storage, "local_path_for", lambda key: local_path)

    def fake_download_to(object_key, destination):
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(destination).write_bytes(b"image")
        return str(destination)

    monkeypatch.setattr(material_evaluation.local_media_storage, "download_to", fake_download_to)

    result = material_evaluation._materialize_media("a.jpg")

    assert result == local_path
    assert local_path.read_bytes() == b"image"


def test_materialize_media_rejects_local_paths_outside_media_store(monkeypatch, tmp_path):
    from appcore import material_evaluation

    media_root = tmp_path / "media_store"
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "a.jpg"
    outside_file.write_bytes(b"image")

    monkeypatch.setattr(material_evaluation.local_media_storage, "MEDIA_STORE_DIR", media_root)
    monkeypatch.setattr(material_evaluation.local_media_storage, "exists", lambda key: True)
    monkeypatch.setattr(material_evaluation.local_media_storage, "local_path_for", lambda key: outside_file)
    monkeypatch.setattr(
        material_evaluation.local_media_storage,
        "download_to",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not download unsafe path")),
    )
    monkeypatch.setattr(
        material_evaluation.tos_clients,
        "download_media_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not fallback unsafe path")),
    )

    with pytest.raises(PathSafetyError):
        material_evaluation._materialize_media("a.jpg")

    assert outside_file.read_bytes() == b"image"
