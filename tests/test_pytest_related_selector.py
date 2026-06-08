from pathlib import Path

from scripts import pytest_related


def _write(root: Path, relative: str, content: str = "") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_changed_test_file_is_selected(tmp_path):
    _write(tmp_path, "tests/test_media_detail.py", "def test_ok(): pass\n")

    selected = pytest_related.select_related_tests(["tests/test_media_detail.py"], tmp_path)

    assert selected == ["tests/test_media_detail.py"]


def test_specific_project_import_selects_related_test(tmp_path):
    _write(tmp_path, "appcore/order_analytics/data_quality.py")
    _write(
        tmp_path,
        "tests/test_order_analytics_data_quality.py",
        "from appcore.order_analytics import data_quality\n",
    )

    selected = pytest_related.select_related_tests(
        ["appcore/order_analytics/data_quality.py"],
        tmp_path,
    )

    assert selected == ["tests/test_order_analytics_data_quality.py"]


def test_top_level_package_import_does_not_select_everything(tmp_path):
    _write(tmp_path, "appcore/unrelated_feature.py")
    _write(tmp_path, "tests/test_generic_appcore.py", "import appcore\n")

    selected = pytest_related.select_related_tests(
        ["appcore/unrelated_feature.py"],
        tmp_path,
    )

    assert selected == []


def test_broad_route_package_import_does_not_select_everything(tmp_path):
    _write(tmp_path, "web/routes/image_translate.py")
    _write(tmp_path, "tests/test_unrelated_route.py", "import web.routes\n")
    _write(tmp_path, "tests/test_image_translate_routes.py", "def test_ok(): pass\n")

    selected = pytest_related.select_related_tests(
        ["web/routes/image_translate.py"],
        tmp_path,
    )

    assert selected == ["tests/test_image_translate_routes.py"]


def test_common_module_names_use_parent_context(tmp_path):
    _write(tmp_path, "appcore/meta_hot_posts/store.py")
    _write(tmp_path, "tests/test_meta_hot_posts_store.py", "def test_ok(): pass\n")
    _write(tmp_path, "tests/test_store.py", "def test_too_broad(): pass\n")

    selected = pytest_related.select_related_tests(
        ["appcore/meta_hot_posts/store.py"],
        tmp_path,
    )

    assert selected == ["tests/test_meta_hot_posts_store.py"]


def test_direct_guard_selects_selector_tests(tmp_path):
    _write(tmp_path, "tests/test_pytest_related_selector.py", "def test_ok(): pass\n")

    selected = pytest_related.select_related_tests(["scripts/pytest_related.py"], tmp_path)

    assert selected == ["tests/test_pytest_related_selector.py"]


def test_no_related_target_does_not_expand_to_full_suite(tmp_path):
    _write(tmp_path, "docs/notes.md", "# notes\n")
    _write(tmp_path, "tests/test_existing.py", "def test_ok(): pass\n")

    selected = pytest_related.select_related_tests(["docs/notes.md"], tmp_path)

    assert selected == []


def test_top_level_agent_markdown_does_not_select_provider_tests(tmp_path):
    _write(tmp_path, "GEMINI.md", "# Gemini rules\n")
    _write(tmp_path, "tests/test_llm_providers_gemini.py", "def test_ok(): pass\n")

    selected = pytest_related.select_related_tests(["GEMINI.md"], tmp_path)

    assert selected == []


def test_default_collection_excludes_e2e_dirs_from_conftest(tmp_path):
    _write(
        tmp_path,
        "tests/conftest.py",
        "_EXTERNAL_TEST_DIRS = {'e2e'}\n_EXTERNAL_TEST_FILES = set()\n_LIVE_DB_TEST_FILES = set()\n",
    )
    _write(tmp_path, "tests/e2e/test_flow.py", "def test_ok(): pass\n")
    _write(tmp_path, "tests/test_web_routes.py", "def test_ok(): pass\n")

    selected = pytest_related.select_related_tests(["web/app.py"], tmp_path)

    assert selected == ["tests/test_web_routes.py"]
