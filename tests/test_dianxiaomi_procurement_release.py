from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_release_script():
    path = ROOT / "scripts" / "build_chrome_extension_release.py"
    spec = importlib.util.spec_from_file_location("build_chrome_extension_release", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_procurement_release_info_reads_and_formats_json(monkeypatch):
    from appcore import dianxiaomi_procurement_insights_release as release

    monkeypatch.setattr(
        release.tool_release_info.system_settings,
        "get_setting",
        lambda key: json.dumps(
            {
                "version": "0.1.0",
                "released_at": "2026-06-09T02:03:04Z",
                "release_note": "首版",
                "download_url": "/static/downloads/tools/DianxiaomiProcurementInsights-chrome-0.1.0.zip",
                "filename": "DianxiaomiProcurementInsights-chrome-0.1.0.zip",
            }
        ),
    )

    info = release.get_release_info()

    assert info["version"] == "0.1.0"
    assert info["released_at_display"] == "0609-100304"
    assert info["download_url"].endswith("DianxiaomiProcurementInsights-chrome-0.1.0.zip")


def test_procurement_release_info_writes_expected_setting(monkeypatch):
    from appcore import dianxiaomi_procurement_insights_release as release

    saved = {}
    monkeypatch.setattr(
        release.tool_release_info.system_settings,
        "set_setting",
        lambda key, value: saved.update({key: value}),
    )

    info = release.set_release_info(
        version="0.1.0",
        released_at="0609-101112",
        release_note="首版",
        download_url="/static/downloads/tools/DianxiaomiProcurementInsights-chrome-0.1.0.zip",
        filename="DianxiaomiProcurementInsights-chrome-0.1.0.zip",
    )

    assert release.SETTING_KEY in saved
    assert json.loads(saved[release.SETTING_KEY]) == info


def test_medias_page_shows_procurement_plugin_left_of_shopify_tool(
    authed_client_no_db,
    monkeypatch,
):
    from web.routes import medias as medias_route

    monkeypatch.setattr(
        medias_route.dianxiaomi_procurement_insights_release,
        "get_release_info",
        lambda: {
            "version": "0.1.0",
            "released_at": "0609-101112",
            "released_at_display": "0609-101112",
            "download_url": "/static/downloads/tools/DianxiaomiProcurementInsights-chrome-0.1.0.zip",
            "release_note": "店小秘采购洞察首版",
            "filename": "DianxiaomiProcurementInsights-chrome-0.1.0.zip",
        },
    )
    monkeypatch.setattr(
        medias_route.shopify_image_localizer_release,
        "get_release_info",
        lambda: {
            "version": "7.5",
            "released_at": "0608-120000",
            "released_at_display": "0608-120000",
            "download_url": "/static/downloads/tools/ShopifyImageLocalizer-portable-7.5.zip",
            "release_note": "自动换图工具",
            "filename": "ShopifyImageLocalizer-portable-7.5.zip",
        },
    )

    response = authed_client_no_db.get("/medias/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "下载采购洞察插件" in body
    assert "下载自动换图工具" in body
    assert body.index("下载采购洞察插件") < body.index("下载自动换图工具")
    assert "DianxiaomiProcurementInsights-chrome-0.1.0.zip" in body
    assert "版本号：0.1.0" in body
    assert "时间：0609-101112" in body


def test_chrome_extension_release_archive_contains_manifest_and_release_manifest(tmp_path):
    from tools.dianxiaomi_procurement_insights.version import RELEASE_VERSION

    script = _load_release_script()
    archive_path = tmp_path / f"DianxiaomiProcurementInsights-chrome-{RELEASE_VERSION}.zip"
    root_dir = f"DianxiaomiProcurementInsights-chrome-{RELEASE_VERSION}"

    script.build_archive(
        source_dir=ROOT / "tools" / "dianxiaomi_procurement_insights" / "chrome_ext",
        archive_path=archive_path,
        root_dir_name=root_dir,
        release_manifest={
            "tool": "dianxiaomi_procurement_insights",
            "version": RELEASE_VERSION,
            "source_commit": "abc123",
            "origin_master_commit": "abc123",
            "release_standard": "docs/superpowers/specs/2026-06-09-chrome-extension-tool-release-standard.md",
            "built_at": "2026-06-09T00:00:00+00:00",
        },
    )
    script.validate_archive(archive_path, root_dir, RELEASE_VERSION)

    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        assert f"{root_dir}/manifest.json" in names
        assert f"{root_dir}/content.js" in names
        assert f"{root_dir}/release_manifest.json" in names
        manifest = json.loads(archive.read(f"{root_dir}/manifest.json").decode("utf-8"))

    assert manifest["version"] == RELEASE_VERSION


def test_procurement_extension_version_sources_match_manifest():
    from tools.dianxiaomi_procurement_insights.version import RELEASE_VERSION

    manifest = json.loads(
        (ROOT / "tools" / "dianxiaomi_procurement_insights" / "chrome_ext" / "manifest.json")
        .read_text(encoding="utf-8")
    )

    assert manifest["version"] == RELEASE_VERSION
