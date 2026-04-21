from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

try:
    import win32crypt  # type: ignore
except ImportError:  # pragma: no cover - non-Windows fallback
    win32crypt = None

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # pragma: no cover - optional dependency
    AESGCM = None


log = logging.getLogger(__name__)
_JWT_RE = re.compile(
    rb"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
)
_STORAGE_TOKEN_HINTS = (b"access_token", b"token", b"auth_token")


def resolve_chrome_auth_headers(target_url: str) -> dict[str, str]:
    """Read Chrome cookies for the target host and derive push auth headers.

    We prefer the browser's live login state so users do not need to keep
    refreshing a manually copied bearer token. When a `token` cookie exists,
    we also derive an `Authorization: Bearer ...` header from it.
    """
    host = (urlparse(target_url).hostname or "").strip().lower()
    if not host:
        return {}

    user_data_dir = _chrome_user_data_dir()
    if not user_data_dir:
        return {}

    headers: dict[str, str] = {}
    try:
        cookies = _load_best_profile_cookies(user_data_dir, host)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("failed to load Chrome cookies for %s: %s", host, exc)
        cookies = {}

    cookie_header = _build_cookie_header(cookies)
    if cookie_header:
        headers["Cookie"] = cookie_header

    token = cookies.get("token", "").strip()
    if not token:
        token = _load_best_profile_storage_token(user_data_dir, host)

    if token:
        headers["Authorization"] = _format_bearer_token(token)
        if "Cookie" not in headers:
            headers["Cookie"] = f"token={token}"
    return headers


def _chrome_user_data_dir() -> Path | None:
    local_appdata = os.getenv("LOCALAPPDATA", "").strip()
    if not local_appdata:
        return None
    path = Path(local_appdata) / "Google" / "Chrome" / "User Data"
    return path if path.is_dir() else None


def _load_best_profile_cookies(user_data_dir: Path, host: str) -> dict[str, str]:
    master_key = _load_chromium_master_key(user_data_dir)
    for cookie_db in _iter_chrome_cookie_dbs(user_data_dir):
        cookies = _read_cookies_from_db(cookie_db, host, master_key)
        if cookies:
            return cookies
    return {}


def _iter_chrome_cookie_dbs(user_data_dir: Path) -> list[Path]:
    cookie_dbs: list[Path] = []
    for profile_dir in user_data_dir.iterdir():
        if not profile_dir.is_dir():
            continue
        if profile_dir.name != "Default" and not profile_dir.name.startswith("Profile "):
            continue
        for relative in (Path("Network") / "Cookies", Path("Cookies")):
            cookie_db = profile_dir / relative
            if cookie_db.is_file():
                cookie_dbs.append(cookie_db)
                break
    return sorted(
        cookie_dbs,
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )


def _iter_chrome_storage_files(user_data_dir: Path) -> list[Path]:
    files: list[Path] = []
    for profile_dir in user_data_dir.iterdir():
        if not profile_dir.is_dir():
            continue
        if profile_dir.name != "Default" and not profile_dir.name.startswith("Profile "):
            continue
        for storage_dir in (
            profile_dir / "Local Storage" / "leveldb",
            profile_dir / "Session Storage",
        ):
            if not storage_dir.is_dir():
                continue
            for pattern in ("*.ldb", "*.log"):
                files.extend(storage_dir.glob(pattern))
    return sorted(
        files,
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )


def _load_chromium_master_key(user_data_dir: Path) -> bytes:
    local_state_path = user_data_dir / "Local State"
    if not local_state_path.is_file():
        return b""

    try:
        local_state = json.loads(local_state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return b""

    encrypted_key_b64 = (
        local_state.get("os_crypt", {}).get("encrypted_key", "").strip()
    )
    if not encrypted_key_b64:
        return b""

    try:
        encrypted_key = base64.b64decode(encrypted_key_b64)
    except (ValueError, TypeError):
        return b""
    if encrypted_key.startswith(b"DPAPI"):
        encrypted_key = encrypted_key[5:]
    return _unprotect_windows_data(encrypted_key)


def _read_cookies_from_db(
    cookie_db_path: Path, host: str, master_key: bytes
) -> dict[str, str]:
    temp_copy = _copy_sqlite_db(cookie_db_path)
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(temp_copy))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT host_key, name, value, encrypted_value
            FROM cookies
            WHERE host_key = ?
               OR host_key = ?
               OR host_key LIKE ?
            ORDER BY
              CASE
                WHEN host_key = ? THEN 0
                WHEN host_key = ? THEN 1
                ELSE 2
              END ASC,
              LENGTH(host_key) DESC,
              name ASC
            """,
            (host, f".{host}", f"%.{host}", host, f".{host}"),
        ).fetchall()
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
        temp_copy.unlink(missing_ok=True)

    cookies: dict[str, str] = {}
    for row in rows:
        cookie_host = str(row["host_key"] or "").strip().lower()
        if not _cookie_matches_host(cookie_host, host):
            continue
        name = str(row["name"] or "").strip()
        if not name or name in cookies:
            continue
        value = _decrypt_cookie_value(
            raw_value=row["value"],
            encrypted_value=row["encrypted_value"],
            master_key=master_key,
        )
        if value:
            cookies[name] = value
    return cookies


def _copy_sqlite_db(path: Path) -> Path:
    fd, temp_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    temp_copy = Path(temp_path)
    shutil.copy2(path, temp_copy)
    return temp_copy


def _load_best_profile_storage_token(user_data_dir: Path, host: str) -> str:
    candidates: dict[str, tuple[int, int, float, str]] = {}
    storage_files = _iter_chrome_storage_files(user_data_dir)
    for require_host_marker in (True, False):
        if candidates:
            break
        for storage_file in storage_files:
            candidate = _extract_best_storage_token(
                storage_file,
                host,
                require_host_marker=require_host_marker,
            )
            if not candidate:
                continue
            score, exp_ts, modified_at, token = candidate
            existing = candidates.get(token)
            if existing is None or (score, exp_ts, modified_at) > existing[:3]:
                candidates[token] = (score, exp_ts, modified_at, token)
    if not candidates:
        return ""

    now = int(time.time())

    def _sort_key(item: tuple[int, int, float, str]) -> tuple[int, int, int, float, int]:
        score, exp_ts, modified_at, token = item
        is_valid = 1 if exp_ts >= now else 0
        return (is_valid, exp_ts, score, modified_at, len(token))

    return max(candidates.values(), key=_sort_key)[3]


def _extract_best_storage_token(
    storage_file: Path,
    host: str,
    *,
    require_host_marker: bool,
) -> tuple[int, int, float, str] | None:
    try:
        data = storage_file.read_bytes()
    except OSError:
        return None

    host_bytes = host.encode("utf-8")
    has_host_marker = host_bytes in data
    if require_host_marker and not has_host_marker:
        return None
    if not has_host_marker and not any(hint in data for hint in _STORAGE_TOKEN_HINTS):
        return None

    modified_at = storage_file.stat().st_mtime
    best: tuple[int, int, float, str] | None = None
    for match in _JWT_RE.finditer(data):
        token_bytes = match.group(0)
        window_start = max(0, match.start() - 256)
        window_end = min(len(data), match.end() + 256)
        window = data[window_start:window_end]
        score = 0
        if has_host_marker:
            score += 80
        if host_bytes in window:
            score += 100
        if any(hint in window for hint in _STORAGE_TOKEN_HINTS):
            score += 50
        if b"access_token" in window:
            score += 100
        if b"access_token" in data:
            score += 30
        if any(hint in data for hint in _STORAGE_TOKEN_HINTS):
            score += 10

        token = token_bytes.decode("ascii", errors="ignore").strip()
        exp_ts = _decode_jwt_exp(token)
        candidate = (score, exp_ts, modified_at, token)
        if best is None or candidate[:3] > best[:3]:
            best = candidate
    return best


def _cookie_matches_host(cookie_host: str, target_host: str) -> bool:
    normalized_cookie_host = cookie_host.lstrip(".")
    return (
        normalized_cookie_host == target_host
        or normalized_cookie_host.endswith(f".{target_host}")
    )


def _decrypt_cookie_value(
    *, raw_value: object, encrypted_value: object, master_key: bytes
) -> str:
    value = str(raw_value or "").strip()
    if value:
        return value

    encrypted = bytes(encrypted_value or b"")
    if not encrypted:
        return ""


def _decode_jwt_exp(token: str) -> int:
    parts = token.split(".")
    if len(parts) != 3:
        return 0
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload + padding)
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return 0
    try:
        return int(data.get("exp") or 0)
    except (TypeError, ValueError):
        return 0

    if encrypted.startswith((b"v10", b"v11")):
        if not master_key or AESGCM is None:
            return ""
        nonce = encrypted[3:15]
        ciphertext = encrypted[15:]
        try:
            decrypted = AESGCM(master_key).decrypt(nonce, ciphertext, None)
        except Exception:
            return ""
        return decrypted.decode("utf-8", errors="ignore").strip()

    try:
        return _unprotect_windows_data(encrypted).decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""


def _unprotect_windows_data(encrypted: bytes) -> bytes:
    if not encrypted:
        return b""
    if win32crypt is None:  # pragma: no cover - non-Windows fallback
        raise RuntimeError("win32crypt is unavailable")
    return win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)[1]


def _format_bearer_token(token: str) -> str:
    return token if token.lower().startswith("bearer ") else f"Bearer {token}"


def _build_cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in sorted(cookies.items()))
