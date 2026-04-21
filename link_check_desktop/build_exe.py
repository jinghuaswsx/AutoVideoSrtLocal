from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _resolve_build_python(repo_root: Path) -> Path:
    candidate = repo_root / ".venv_link_check_build" / "Scripts" / "python.exe"
    if candidate.is_file():
        return candidate
    return Path(sys.executable)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    spec_path = repo_root / "link_check_desktop" / "packaging" / "link_check_desktop.spec"
    python_exe = _resolve_build_python(repo_root)
    env = dict(os.environ)
    env["PYTHONNOUSERSITE"] = "1"
    subprocess.run(
        [str(python_exe), "-s", "-m", "PyInstaller", "--noconfirm", str(spec_path)],
        cwd=repo_root,
        env=env,
        check=True,
    )


if __name__ == "__main__":
    main()
