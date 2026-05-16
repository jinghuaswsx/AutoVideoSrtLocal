from dataclasses import dataclass
from pathlib import Path


def test_build_inventory_rows_classifies_and_sizes_refs(monkeypatch, tmp_path):
    from appcore import tos_file_management as mgr
    from appcore.tos_backup_references import ProtectedFileRef
    from appcore.tos_channel_migration import TosChannelConfig

    video = tmp_path / "a.mp4"
    video.write_bytes(b"12345")
    fake_target = TosChannelConfig(
        code="tos_wj",
        access_key="ak",
        secret_key="sk",
        region="cn-shanghai",
        bucket="avs-rjc",
        public_endpoint="tos-cn-shanghai.volces.com",
        private_endpoint="tos-cn-shanghai.ivolces.com",
    )

    monkeypatch.setattr(mgr.tos_channel_migration, "load_tos_channel_config", lambda code: fake_target)
    monkeypatch.setattr(
        mgr.tos_backup_references,
        "collect_protected_file_refs",
        lambda: [ProtectedFileRef(str(video), ("raw_source_video",), ("media/raw/a.mp4",))],
    )
    monkeypatch.setattr(
        mgr.tos_backup_storage,
        "backup_object_key_for_local_path",
        lambda local_path: "FILES/prod/data/a.mp4",
    )
    monkeypatch.setattr(mgr, "_head_target_object", lambda *args, **kwargs: {"exists": True, "size_bytes": 5})

    rows = mgr.build_inventory_rows(target_channel_code="tos_wj")

    assert len(rows) == 1
    row = rows[0]
    assert row.module_code == "raw_sources"
    assert row.module_name == "原始素材"
    assert row.file_type == "video"
    assert row.local_exists is True
    assert row.local_size_bytes == 5
    assert row.target_exists is True
    assert row.target_size_bytes == 5
    assert row.sync_status == "synced"


def test_build_inventory_rows_classifies_meta_hot_post_videos(monkeypatch, tmp_path):
    from appcore import tos_file_management as mgr
    from appcore.tos_backup_references import ProtectedFileRef
    from appcore.tos_channel_migration import TosChannelConfig

    video = tmp_path / "output" / "meta_hot_posts" / "videos" / "meta_hot_post_20.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"12345")
    fake_target = TosChannelConfig(
        code="tos_wj",
        access_key="ak",
        secret_key="sk",
        region="cn-shanghai",
        bucket="avs-rjc",
        public_endpoint="tos-cn-shanghai.volces.com",
        private_endpoint="tos-cn-shanghai.ivolces.com",
    )

    monkeypatch.setattr(mgr.tos_channel_migration, "load_tos_channel_config", lambda code: fake_target)
    monkeypatch.setattr(
        mgr.tos_backup_references,
        "collect_protected_file_refs",
        lambda: [ProtectedFileRef(str(video), ("meta_hot_post_video",), ())],
    )
    monkeypatch.setattr(
        mgr.tos_backup_storage,
        "backup_object_key_for_local_path",
        lambda local_path: "FILES/prod/opt/autovideosrt/output/meta_hot_posts/videos/meta_hot_post_20.mp4",
    )
    monkeypatch.setattr(mgr, "_head_target_object", lambda *args, **kwargs: {"exists": False, "size_bytes": 0})

    row = mgr.build_inventory_rows(target_channel_code="tos_wj")[0]

    assert row.module_code == "meta_hot_posts"
    assert row.file_type == "video"
    assert row.source_labels == ("meta_hot_post_video",)
    assert row.sync_status == "missing_target"


def test_summarize_inventory_counts_module_missing_target():
    from appcore import tos_file_management as mgr

    rows = [
        mgr.TosFileInventoryRow(
            module_code="raw_sources",
            module_name="原始素材",
            file_type="video",
            source_labels=("raw_source_video",),
            source_object_keys=(),
            local_path="/data/a.mp4",
            local_path_hash="a" * 64,
            local_exists=True,
            local_size_bytes=1024,
            backup_object_key="FILES/prod/data/a.mp4",
            target_channel_code="tos_wj",
            target_bucket="avs-rjc",
            target_object_key="FILES/prod/data/a.mp4",
            target_exists=False,
            target_size_bytes=0,
            sync_status="missing_target",
            last_error="",
        )
    ]

    summary = mgr.summarize_inventory(rows)

    assert summary["total_files"] == 1
    assert summary["total_bytes"] == 1024
    assert summary["target_missing_count"] == 1
    assert summary["modules"][0]["module_code"] == "raw_sources"


def test_run_inventory_scan_persists_scan_and_mapping_rows(monkeypatch, tmp_path):
    from appcore import tos_file_management as mgr

    writes = []
    monkeypatch.setattr(mgr, "execute", lambda sql, params=(): writes.append((sql, params)) or 101)
    monkeypatch.setattr(mgr, "build_inventory_rows", lambda target_channel_code: [])
    monkeypatch.setattr(mgr, "upsert_mapping", lambda row, scan_run_id: None)

    result = mgr.run_inventory_scan(target_channel_code="tos_wj", triggered_by=1)

    assert result["scan_run_id"] == 101
    assert any("tos_file_scan_runs" in sql for sql, _ in writes)
