from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


AUTOPUSH_DIR = Path(__file__).resolve().parents[1] / "AutoPush"
if str(AUTOPUSH_DIR) not in sys.path:
    sys.path.insert(0, str(AUTOPUSH_DIR))


def test_resolve_chrome_auth_headers_reads_token_cookie(tmp_path, monkeypatch):
    from backend import browser_auth

    user_data_dir = tmp_path / "Google" / "Chrome" / "User Data"
    cookie_db = user_data_dir / "Default" / "Network" / "Cookies"
    cookie_db.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(cookie_db)
    conn.execute(
        """
        CREATE TABLE cookies (
            host_key TEXT,
            name TEXT,
            value TEXT,
            encrypted_value BLOB
        )
        """
    )
    conn.executemany(
        "INSERT INTO cookies (host_key, name, value, encrypted_value) VALUES (?, ?, ?, ?)",
        [
            (".os.wedev.vip", "token", "demo-jwt", b""),
            (".os.wedev.vip", "x-hng", "lang=zh-CN&domain=os.wedev.vip", b""),
        ],
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    headers = browser_auth.resolve_chrome_auth_headers(
        "https://os.wedev.vip/api/marketing/medias/3719/texts"
    )

    assert headers == {
        "Authorization": "Bearer demo-jwt",
        "Cookie": "token=demo-jwt; x-hng=lang=zh-CN&domain=os.wedev.vip",
    }
