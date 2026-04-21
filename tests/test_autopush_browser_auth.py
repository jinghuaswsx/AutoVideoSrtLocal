from __future__ import annotations

import base64
import json
import sqlite3
import sys
from pathlib import Path


AUTOPUSH_DIR = Path(__file__).resolve().parents[1] / "AutoPush"
if str(AUTOPUSH_DIR) not in sys.path:
    sys.path.insert(0, str(AUTOPUSH_DIR))


def _build_jwt(exp: int) -> str:
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8")
    ).decode("ascii").rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"iss": "zhifa", "exp": exp, "iat": exp - 3600, "jti": "35"}).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"{header}.{payload}.signature-demo-token"


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


def test_resolve_chrome_auth_headers_falls_back_to_local_storage_token(tmp_path, monkeypatch):
    from backend import browser_auth

    user_data_dir = tmp_path / "Google" / "Chrome" / "User Data"
    leveldb_dir = user_data_dir / "Default" / "Local Storage" / "leveldb"
    leveldb_dir.mkdir(parents=True, exist_ok=True)
    token = _build_jwt(1779245833)
    (leveldb_dir / "000001.ldb").write_bytes(
        (
            b"namespace-demo-https://os.wedev.vip/\x00"
            b"access_token\x00"
            + token.encode("ascii")
        )
    )

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    headers = browser_auth.resolve_chrome_auth_headers(
        "https://os.wedev.vip/api/marketing/medias/3719/texts"
    )

    assert headers == {
        "Authorization": f"Bearer {token}",
        "Cookie": f"token={token}",
    }


def test_resolve_chrome_auth_headers_prefers_newest_valid_local_storage_token(
    tmp_path, monkeypatch,
):
    from backend import browser_auth

    user_data_dir = tmp_path / "Google" / "Chrome" / "User Data"
    leveldb_dir = user_data_dir / "Default" / "Local Storage" / "leveldb"
    leveldb_dir.mkdir(parents=True, exist_ok=True)
    older = _build_jwt(1778486882)
    newer = _build_jwt(1779245833)
    (leveldb_dir / "000001.ldb").write_bytes(
        (
            b"namespace-demo-https://os.wedev.vip/\x00"
            b"access_token\x00"
            + older.encode("ascii")
            + b"\x00token\x00"
            + newer.encode("ascii")
        )
    )

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    headers = browser_auth.resolve_chrome_auth_headers(
        "https://os.wedev.vip/api/marketing/medias/3719/texts"
    )

    assert headers["Authorization"] == f"Bearer {newer}"
    assert headers["Cookie"] == f"token={newer}"


def test_resolve_chrome_auth_headers_uses_storage_token_when_host_and_token_are_split(
    tmp_path, monkeypatch,
):
    from backend import browser_auth

    user_data_dir = tmp_path / "Google" / "Chrome" / "User Data"
    leveldb_dir = user_data_dir / "Default" / "Local Storage" / "leveldb"
    leveldb_dir.mkdir(parents=True, exist_ok=True)
    token = _build_jwt(1779245833)
    (leveldb_dir / "000001.ldb").write_bytes(
        b"namespace-demo-https://os.wedev.vip/\x00theme\x00light"
    )
    (leveldb_dir / "000002.ldb").write_bytes(
        b"access_token\x00" + token.encode("ascii")
    )

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    headers = browser_auth.resolve_chrome_auth_headers(
        "https://os.wedev.vip/api/marketing/medias/3719/texts"
    )

    assert headers["Authorization"] == f"Bearer {token}"
    assert headers["Cookie"] == f"token={token}"
