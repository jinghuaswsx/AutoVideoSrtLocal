from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

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
        raise AssertionError(normalized)

    monkeypatch.setattr(medias, "query", _fake_query)

    refs = medias.collect_media_object_references()

    assert refs == [
        {"object_key": "1/medias/10/a.jpg", "sources": ["item_cover", "product_cover"]},
        {"object_key": "1/medias/10/a.mp4", "sources": ["item"]},
        {"object_key": "1/medias/10/cover.jpg", "sources": ["product_cover"]},
        {"object_key": "1/medias/10/legacy.jpg", "sources": ["legacy_product_cover"]},
    ]


def test_run_apply_copies_and_reports_objects(tmp_path, monkeypatch):
    migration = importlib.import_module("scripts.migrate_media_tos_bucket")
    monkeypatch.setattr(
        medias,
        "collect_media_object_references",
        lambda: [{"object_key": "1/medias/7/demo.mp4", "sources": ["item"]}],
    )
    calls = []

    def _fake_copy(object_key, temp_dir, old_bucket, new_bucket):
        calls.append((object_key, temp_dir, old_bucket, new_bucket))
        return {"object_key": object_key, "status": "migrated", "reason": ""}

    monkeypatch.setattr(migration, "copy_object_between_buckets", _fake_copy)

    report = migration.run_apply(
        old_bucket="video-save",
        new_bucket="auto-video-srt-product-video-manage",
        temp_dir=tmp_path,
    )

    assert report["summary"] == {
        "total": 1,
        "migrated": 1,
        "skipped": 0,
        "missing": 0,
        "failed": 0,
    }
    assert report["results"] == [
        {
            "object_key": "1/medias/7/demo.mp4",
            "status": "migrated",
            "reason": "",
            "sources": ["item"],
        }
    ]
    assert calls == [
        (
            "1/medias/7/demo.mp4",
            tmp_path,
            "video-save",
            "auto-video-srt-product-video-manage",
        )
    ]


def test_cleanup_remote_objects_deletes_only_migrated(monkeypatch):
    migration = importlib.import_module("scripts.migrate_media_tos_bucket")
    deleted = []

    monkeypatch.setattr(
        tos_clients,
        "delete_media_object",
        lambda object_key, bucket=None: deleted.append((bucket, object_key)),
    )

    removed = migration.cleanup_remote_objects(
        {
            "results": [
                {"object_key": "1/medias/7/demo.mp4", "status": "migrated", "reason": ""},
                {"object_key": "1/medias/7/missing.mp4", "status": "missing", "reason": "not found"},
                {"object_key": "1/medias/7/error.mp4", "status": "failed", "reason": "boom"},
            ]
        },
        old_bucket="video-save",
    )

    assert removed == 1
    assert deleted == [("video-save", "1/medias/7/demo.mp4")]


def test_configure_media_bucket_cors_writes_rule(monkeypatch):
    calls = {}

    class _Client:
        def put_bucket_cors(self, bucket, rules):
            calls["bucket"] = bucket
            calls["rules"] = rules

    monkeypatch.setattr(tos_clients, "get_server_client", lambda: _Client())
    monkeypatch.setattr(tos_clients.config, "TOS_MEDIA_BUCKET", "auto-video-srt-product-video-manage")

    tos_clients.configure_media_bucket_cors(
        origins=["http://14.103.220.208:8888", "https://14.103.220.208:8888"],
    )

    assert calls["bucket"] == "auto-video-srt-product-video-manage"
    assert len(calls["rules"]) == 1
    rule = calls["rules"][0]
    assert rule.allowed_origins == [
        "http://14.103.220.208:8888",
        "https://14.103.220.208:8888",
    ]
    assert rule.allowed_methods == ["GET", "HEAD", "PUT", "POST", "DELETE"]
    assert rule.allowed_headers == ["*"]
    assert "ETag" in rule.expose_headers
    assert rule.max_age_seconds == 3600


def test_configure_media_bucket_cors_rejects_empty_origins(monkeypatch):
    monkeypatch.setattr(tos_clients, "get_server_client", lambda: pytest.fail("must not call"))
    with pytest.raises(ValueError):
        tos_clients.configure_media_bucket_cors(origins=[])


def test_migrate_script_configure_cors_subcommand(monkeypatch, capsys):
    migration = importlib.import_module("scripts.migrate_media_tos_bucket")
    captured = {}

    def _fake_apply(origins, bucket=None, **_):
        captured["origins"] = list(origins)
        captured["bucket"] = bucket

    monkeypatch.setattr(tos_clients, "configure_media_bucket_cors", _fake_apply)
    monkeypatch.setattr(migration.tos_clients, "configure_media_bucket_cors", _fake_apply)
    monkeypatch.setattr(migration.config, "TOS_MEDIA_BUCKET", "auto-video-srt-product-video-manage")

    exit_code = migration.main(["--configure-cors"])

    assert exit_code == 0
    assert captured["bucket"] == "auto-video-srt-product-video-manage"
    assert captured["origins"] == [
        "http://14.103.220.208:8888",
        "https://14.103.220.208:8888",
    ]


def test_migrate_script_configure_cors_accepts_custom_origin(monkeypatch):
    migration = importlib.import_module("scripts.migrate_media_tos_bucket")
    captured = {}

    def _fake_apply(origins, bucket=None, **_):
        captured["origins"] = list(origins)
        captured["bucket"] = bucket

    monkeypatch.setattr(tos_clients, "configure_media_bucket_cors", _fake_apply)
    monkeypatch.setattr(migration.tos_clients, "configure_media_bucket_cors", _fake_apply)

    exit_code = migration.main([
        "--configure-cors",
        "--new-bucket", "custom-bucket",
        "--origin", "https://example.com",
        "--origin", "https://example.org",
    ])

    assert exit_code == 0
    assert captured["bucket"] == "custom-bucket"
    assert captured["origins"] == ["https://example.com", "https://example.org"]


def test_cleanup_local_cache_removes_media_thumbs(tmp_path):
    migration = importlib.import_module("scripts.migrate_media_tos_bucket")
    cache_file = tmp_path / "media_thumbs" / "7" / "thumb.jpg"
    temp_file = tmp_path / "migration-temp" / "demo.mp4"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_bytes(b"x")
    temp_file.write_bytes(b"y")

    removed = migration.cleanup_local_paths(
        cache_root=tmp_path / "media_thumbs",
        temp_root=tmp_path / "migration-temp",
    )

    assert removed == 2
    assert not cache_file.exists()
    assert not temp_file.exists()
