from pathlib import Path


def test_materialize_media_downloads_local_copy_when_exists_is_remote_only(monkeypatch, tmp_path):
    from appcore import material_evaluation

    local_path = tmp_path / "media_store" / "a.jpg"
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
