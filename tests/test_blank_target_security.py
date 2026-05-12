import re
from pathlib import Path


def test_blank_target_links_use_noopener_noreferrer():
    bad_links = []
    for base in (Path("web/templates"), Path("web/static")):
        for path in list(base.rglob("*.html")) + list(base.rglob("*.js")):
            source = path.read_text(encoding="utf-8", errors="ignore")
            for match in re.finditer(
                r"<a\b[^>]*target=[\"']_blank[\"'][^>]*>",
                source,
                re.IGNORECASE,
            ):
                tag = match.group(0).lower()
                if (
                    "rel=" not in tag
                    or "noopener" not in tag
                    or "noreferrer" not in tag
                ):
                    line = source.count("\n", 0, match.start()) + 1
                    bad_links.append(f"{path}:{line}:{match.group(0)[:160]}")

    assert bad_links == []
