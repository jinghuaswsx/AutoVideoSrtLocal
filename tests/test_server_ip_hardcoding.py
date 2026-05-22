from __future__ import annotations

from pathlib import Path

from server_config import DEFAULT_SERVER_HOST


RUNTIME_ROOTS = (
    ".env.example",
    "config.py",
    "server_config.py",
    "appcore",
    "web",
    "tools",
    "AutoPush/backend",
    "link_check_desktop",
    "scripts",
    "deploy",
    "tests",
    "AutoPush/.env.example",
)
TEXT_SUFFIXES = {".bat", ".html", ".js", ".ps1", ".py", ".sh"}
ALLOWED_CURRENT_SERVER_IP_FILES = {Path("server_config.py")}
OLD_SERVER_HOST = ".".join(["172", "30", "254", "14"])


def _iter_runtime_files(repo_root: Path):
    for root_name in RUNTIME_ROOTS:
        root = repo_root / root_name
        if root.is_file():
            yield root
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts:
                continue
            if path.suffix.lower() in TEXT_SUFFIXES:
                yield path


def test_server_ips_are_not_hardcoded_outside_global_config():
    repo_root = Path(__file__).resolve().parents[1]
    violations: list[str] = []

    for path in _iter_runtime_files(repo_root):
        relative = path.relative_to(repo_root)
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if OLD_SERVER_HOST in line:
                violations.append(f"{relative}:{line_no} contains old server host")
            if DEFAULT_SERVER_HOST in line and relative not in ALLOWED_CURRENT_SERVER_IP_FILES:
                violations.append(f"{relative}:{line_no} contains current server host outside server_config.py")

    assert not violations, "\n".join(violations)
