from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import appcore.medias as medias
import appcore.tos_clients as tos_clients


def test_media_bucket_default_is_dedicated(monkeypatch):
    monkeypatch.delenv("TOS_MEDIA_BUCKET", raising=False)
    import config

    importlib.reload(config)

    assert config.TOS_MEDIA_BUCKET == "auto-video-srt-product-video-manage"


def test_generate_signed_media_download_url_uses_media_bucket(monkeypatch):
    captured = {}

    class _Client:
        def pre_signed_url(self, method, bucket, object_key, expires):
            captured["bucket"] = bucket
            captured["object_key"] = object_key
            captured["expires"] = expires
            return SimpleNamespace(signed_url=f"https://signed.example/{object_key}")

    monkeypatch.setattr(tos_clients.config, "TOS_MEDIA_BUCKET", "auto-video-srt-product-video-manage")
    monkeypatch.setattr(tos_clients, "get_public_client", lambda: _Client())

    url = tos_clients.generate_signed_media_download_url("1/medias/2/demo.mp4")

    assert url == "https://signed.example/1/medias/2/demo.mp4"
    assert captured == {
        "bucket": "auto-video-srt-product-video-manage",
        "object_key": "1/medias/2/demo.mp4",
        "expires": tos_clients.config.TOS_SIGNED_URL_EXPIRES,
    }


def test_download_media_file_can_target_override_bucket(tmp_path, monkeypatch):
    calls = {}

    class _Client:
        def get_object_to_file(self, bucket, object_key, local_path):
            calls["bucket"] = bucket
            calls["object_key"] = object_key
            Path(local_path).write_bytes(b"ok")

    monkeypatch.setattr(tos_clients, "get_server_client", lambda: _Client())

    dest = tmp_path / "demo.bin"
    tos_clients.download_media_file("a/b.mp4", dest, bucket="legacy-media-bucket")

    assert calls == {"bucket": "legacy-media-bucket", "object_key": "a/b.mp4"}
    assert dest.read_bytes() == b"ok"


def test_collect_media_object_references_deduplicates_keys(monkeypatch):
    def _fake_query(sql, args=None):
        normalized = " ".join(str(sql).split())
        if "FROM media_items" in normalized:
            return [
                {"source": "item", "object_key": "1/medias/10/a.mp4"},
                {"source": "item_cover", "object_key": "1/medias/10/a.jpg"},
                {"source": "item", "object_key": "1/medias/10/a.mp4"},
                {"source": "item_cover", "object_key": ""},
            ]
        if "FROM media_product_covers" in normalized:
            return [
                {"source": "product_cover", "object_key": "1/medias/10/a.jpg"},
                {"source": "product_cover", "object_key": "1/medias/10/cover.jpg"},
            ]
        if "FROM media_products" in normalized:
            return [
                {"source": "legacy_product_cover", "object_key": None},
                {"source": "legacy_product_cover", "object_key": "1/medias/10/legacy.jpg"},
            ]
        if "FROM media_product_detail_images" in normalized:
            return []
        if "FROM media_raw_sources" in normalized:
            return []
        raise AssertionError(normalized)

    monkeypatch.setattr(medias, "query", _fake_query)

    refs = medias.collect_media_object_references()

    assert refs == [
        {"object_key": "1/medias/10/a.jpg", "sources": ["item_cover", "product_cover"]},
        {"object_key": "1/medias/10/a.mp4", "sources": ["item"]},
        {"object_key": "1/medias/10/cover.jpg", "sources": ["product_cover"]},
        {"object_key": "1/medias/10/legacy.jpg", "sources": ["legacy_product_cover"]},
    ]


def test_migrate_media_tos_bucket_script_is_disabled(capsys):
    migration = importlib.import_module("scripts.migrate_media_tos_bucket")

    exit_code = migration.main(["--dry-run"])
    out = capsys.readouterr().out

    assert exit_code == 2
    assert "migrate_local_storage_media_assets.py" in out
