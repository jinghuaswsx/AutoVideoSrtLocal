#!/usr/bin/env python3
"""Select the smallest useful pytest target set for changed files."""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path


PROJECT_IMPORT_ROOTS = {
    "appcore",
    "link_check_desktop",
    "pipeline",
    "scripts",
    "tools",
    "web",
}

TOP_LEVEL_MODULES = {
    "config.py": "config",
    "main.py": "main",
    "server_config.py": "server_config",
}

COMMON_MODULE_NAMES = {
    "__init__",
    "_helpers",
    "base",
    "client",
    "dao",
    "helpers",
    "models",
    "routes",
    "service",
    "settings",
    "store",
    "utils",
}

NAME_MATCH_SUFFIXES = {".css", ".html", ".js", ".jinja", ".jinja2", ".py", ".sql"}

BROAD_IMPORTS = {
    "appcore.meta_hot_posts",
    "appcore.order_analytics",
    "appcore.runtime",
    "tools.shopify_image_localizer",
    "web.routes",
    "web.services",
}

DIRECT_GUARDS = {
    "pytest.ini": {"tests/test_pytest_related_selector.py"},
    "tests/conftest.py": {"tests/test_pytest_related_selector.py"},
    "scripts/pytest_related.py": {"tests/test_pytest_related_selector.py"},
    "web/app.py": {"tests/test_web_routes.py", "tests/test_web_service_tuning.py"},
    "web/auth.py": {"tests/test_auth_audit.py"},
    "appcore/db.py": {"tests/test_db_pool_config.py", "tests/test_db_schema_safety.py"},
    "appcore/permissions.py": {
        "tests/test_appcore_permissions_task_capabilities.py",
        "tests/test_auth_audit.py",
    },
    "appcore/scheduled_tasks.py": {"tests/test_appcore_scheduled_tasks.py"},
    "appcore/llm_client.py": {
        "tests/test_llm_client.py",
        "tests/test_llm_use_cases.py",
        "tests/test_translate_use_case_kwarg.py",
    },
    "appcore/llm_use_cases.py": {
        "tests/test_llm_use_cases.py",
        "tests/test_translate_use_case_kwarg.py",
    },
    "config.py": {"tests/test_config.py"},
}

PREFIX_GUARDS = {
    "db/migrations/": {"tests/test_db_schema_safety.py"},
    "appcore/llm_providers/": {
        "tests/test_llm_providers_dao.py",
        "tests/test_llm_providers_gemini_vertex.py",
    },
    "web/templates/": {"tests/test_blank_target_security.py"},
}


def _run_git(args: list[str], root: Path) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def changed_files(root: Path, base: str) -> list[str]:
    candidates: list[str] = []
    candidates.extend(_run_git(["diff", "--name-only", f"{base}...HEAD"], root))
    candidates.extend(_run_git(["diff", "--name-only", "HEAD", "--"], root))
    candidates.extend(_run_git(["ls-files", "--others", "--exclude-standard"], root))

    seen: set[str] = set()
    ordered: list[str] = []
    for item in candidates:
        normalized = Path(item).as_posix()
        if normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def is_test_file(path: str) -> bool:
    p = Path(path)
    return (
        len(p.parts) >= 2
        and p.parts[0] == "tests"
        and p.name.startswith("test")
        and p.suffix == ".py"
    )


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _literal_set_from_conftest(root: Path, name: str) -> set[str]:
    conftest = root / "tests" / "conftest.py"
    if not conftest.is_file():
        return set()
    try:
        tree = ast.parse(conftest.read_text(encoding="utf-8-sig"))
    except SyntaxError:
        return set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (SyntaxError, ValueError):
            return set()
        if isinstance(value, set):
            return {str(item) for item in value}
    return set()


@lru_cache(maxsize=8)
def default_exclusions(root: Path) -> tuple[set[str], set[str], set[str]]:
    return (
        _literal_set_from_conftest(root, "_EXTERNAL_TEST_DIRS"),
        _literal_set_from_conftest(root, "_EXTERNAL_TEST_FILES"),
        _literal_set_from_conftest(root, "_LIVE_DB_TEST_FILES"),
    )


def is_default_collectable(path: str, root: Path) -> bool:
    p = Path(path)
    if not is_test_file(path):
        return False
    parts = p.parts
    external_dirs, external_files, live_db_files = default_exclusions(root)

    if not _truthy_env("AUTOVIDEOSRT_RUN_EXTERNAL_TESTS"):
        if len(parts) >= 2 and parts[1] in external_dirs:
            return False
        if path in external_files:
            return False

    if not _truthy_env("AUTOVIDEOSRT_RUN_LIVE_DB_TESTS"):
        if path in live_db_files:
            return False

    return True


def module_for_file(path: str) -> str | None:
    p = Path(path)
    if p.name in TOP_LEVEL_MODULES:
        return TOP_LEVEL_MODULES[p.name]
    if p.suffix != ".py" or not p.parts:
        return None
    if p.parts[0] not in PROJECT_IMPORT_ROOTS:
        return None
    parts = list(p.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else None


def project_imports_from_test(test_path: Path) -> set[str]:
    try:
        tree = ast.parse(test_path.read_text(encoding="utf-8-sig"))
    except SyntaxError:
        return set()

    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in PROJECT_IMPORT_ROOTS or alias.name in TOP_LEVEL_MODULES.values():
                    imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", 1)[0]
            if root in PROJECT_IMPORT_ROOTS or node.module in TOP_LEVEL_MODULES.values():
                imports.add(node.module)
    return imports


def import_matches_module(imported: str, changed_module: str) -> bool:
    import_depth = len(imported.split("."))
    if import_depth <= 1:
        return imported == changed_module
    if imported in BROAD_IMPORTS:
        return imported == changed_module
    return (
        imported == changed_module
        or imported.startswith(f"{changed_module}.")
        or changed_module.startswith(f"{imported}.")
    )


def _normalized_name(value: str) -> str:
    return value.replace("-", "_").replace(".", "_").lower()


def name_candidates_for_file(path: str) -> set[str]:
    p = Path(path)
    if p.suffix.lower() not in NAME_MATCH_SUFFIXES:
        return set()
    parts = [_normalized_name(part) for part in p.with_suffix("").parts]
    if not parts:
        return set()

    stem = parts[-1]
    parent = parts[-2] if len(parts) >= 2 else ""
    grandparent = parts[-3] if len(parts) >= 3 else ""
    candidates: set[str] = set()

    if stem not in COMMON_MODULE_NAMES and len(stem) >= 4:
        candidates.add(stem)
    if parent:
        candidates.add(f"{parent}_{stem}")
    if grandparent and parent in {"routes", "services", "tools"}:
        candidates.add(f"{grandparent}_{stem}")
    if grandparent and stem in COMMON_MODULE_NAMES:
        candidates.add(f"{grandparent}_{parent}_{stem}")
        candidates.add(f"{grandparent}_{parent}")
    return {item for item in candidates if item and item != "test"}


def _existing(paths: set[str], root: Path) -> set[str]:
    return {path for path in paths if (root / path).is_file() and is_default_collectable(path, root)}


def guard_tests_for_file(path: str, root: Path) -> set[str]:
    selected = set(DIRECT_GUARDS.get(path, set()))
    for prefix, tests in PREFIX_GUARDS.items():
        if path.startswith(prefix):
            selected.update(tests)
    return _existing(selected, root)


def discover_tests(root: Path) -> list[Path]:
    tests_root = root / "tests"
    if not tests_root.exists():
        return []
    return sorted(
        test
        for test in tests_root.rglob("test*.py")
        if is_default_collectable(test.relative_to(root).as_posix(), root)
    )


def select_related_tests(changed: list[str], root: Path) -> list[str]:
    test_files = discover_tests(root)
    imports_by_test = {test: project_imports_from_test(test) for test in test_files}
    selected: set[str] = set()

    changed_modules = [module_for_file(path) for path in changed]
    changed_modules = [module for module in changed_modules if module]
    name_candidates: set[str] = set()

    for path in changed:
        if is_default_collectable(path, root) and (root / path).is_file():
            selected.add(path)
        selected.update(guard_tests_for_file(path, root))
        name_candidates.update(name_candidates_for_file(path))

    for test_path, imports in imports_by_test.items():
        rel = test_path.relative_to(root).as_posix()
        for changed_module in changed_modules:
            if any(import_matches_module(imported, changed_module) for imported in imports):
                selected.add(rel)
                break

    normalized_test_names = {
        test.relative_to(root).as_posix(): _normalized_name(test.stem.removeprefix("test_"))
        for test in test_files
    }
    for rel, normalized in normalized_test_names.items():
        if any(candidate in normalized for candidate in name_candidates):
            selected.add(rel)

    return sorted(path for path in selected if (root / path).is_file())


def build_pytest_command(targets: list[str]) -> list[str]:
    return [sys.executable, "-m", "pytest", *targets, "-q"]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/master", help="git base ref for committed changes")
    parser.add_argument(
        "--changed",
        nargs="*",
        help="explicit changed files, for testing or manual selection",
    )
    parser.add_argument("--run", action="store_true", help="run selected pytest targets")
    parser.add_argument(
        "--print-command",
        action="store_true",
        help="print the pytest command instead of only the selected files",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    root = Path.cwd()
    changed = args.changed if args.changed is not None else changed_files(root, args.base)
    targets = select_related_tests(changed, root)

    if not targets:
        print("No related pytest targets found.")
        print("Run the smallest useful non-pytest verification and report that no direct pytest coverage was selected.")
        return 0

    if args.print_command:
        print(" ".join(build_pytest_command(targets)))
    else:
        for target in targets:
            print(target)

    if not args.run:
        return 0

    completed = subprocess.run(build_pytest_command(targets), cwd=root, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
