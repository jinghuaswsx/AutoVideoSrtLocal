"""Deprecated local-only Mingkong pairing entrypoint."""
from __future__ import annotations

from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    _ = argv
    print(
        "tools/mingkong_local_pairing_15d.py is deprecated. "
        "Use tools/mingkong_recent_15d_full_sync.py for the confirmed DXM03 full sync."
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
