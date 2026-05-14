"""Tests for appcore.link_availability HTTP probe + DAO.

Probes are exercised against an in-process http.server so the assertions hit
real urllib semantics (HEAD / GET / 301 / 404 / 405 / timeout) without any
network. The DAO calls are funneled through monkeypatched query / execute
stubs because the production helper imports appcore.db lazily.
"""
from __future__ import annotations

import http.server
import socket
import threading
import time
from contextlib import contextmanager
from http import HTTPStatus
from urllib import error as urllib_error

import pytest

from appcore import link_availability


@contextmanager
def _http_server(handler_cls):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()


class _Quiet(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A003 - silence stdout noise
        return


def test_probe_returns_200_for_head_success():
    class H(_Quiet):
        def do_HEAD(self):  # noqa: N802
            self.send_response(HTTPStatus.OK)
            self.end_headers()

    with _http_server(H) as base:
        result = link_availability.probe(base + "/foo")
    assert result["http_status"] == 200
    assert result["ok"] is True
    assert result["error"] is None
    assert isinstance(result["elapsed_ms"], int)


def test_probe_follows_301_redirect_to_200():
    # urllib's redirect handler converts HEAD → GET on 301/302, mirroring how
    # most servers expect redirects to be re-issued. Implement both handlers
    # so the test reflects real-world redirect behavior.
    class H(_Quiet):
        def _respond(self):
            if self.path == "/redirect":
                self.send_response(HTTPStatus.MOVED_PERMANENTLY)
                self.send_header("Location", "/final")
                self.end_headers()
            else:
                self.send_response(HTTPStatus.OK)
                self.end_headers()

        def do_HEAD(self):  # noqa: N802
            self._respond()

        def do_GET(self):  # noqa: N802
            self._respond()

    with _http_server(H) as base:
        result = link_availability.probe(base + "/redirect")
    assert result["http_status"] == 200
    assert result["ok"] is True


def test_probe_returns_404():
    class H(_Quiet):
        def do_HEAD(self):  # noqa: N802
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()

    with _http_server(H) as base:
        result = link_availability.probe(base + "/missing")
    assert result["http_status"] == 404
    assert result["ok"] is False
    assert result["error"] == "http 404"


def test_probe_returns_403():
    class H(_Quiet):
        def do_HEAD(self):  # noqa: N802
            self.send_response(HTTPStatus.FORBIDDEN)
            self.end_headers()

    with _http_server(H) as base:
        result = link_availability.probe(base + "/forbidden")
    assert result["http_status"] == 403
    assert result["ok"] is False
    assert result["error"] == "http 403"


def test_probe_returns_500_as_failure():
    class H(_Quiet):
        def do_HEAD(self):  # noqa: N802
            self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
            self.end_headers()

    with _http_server(H) as base:
        result = link_availability.probe(base + "/boom")
    assert result["http_status"] == 500
    assert result["ok"] is False


def test_probe_falls_back_to_get_on_405():
    """HEAD 405 → retry GET (the spec says we accept the GET response)."""
    state = {"head_count": 0, "get_count": 0}

    class H(_Quiet):
        def do_HEAD(self):  # noqa: N802
            state["head_count"] += 1
            self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
            self.end_headers()

        def do_GET(self):  # noqa: N802
            state["get_count"] += 1
            self.send_response(HTTPStatus.OK)
            self.end_headers()

    with _http_server(H) as base:
        result = link_availability.probe(base + "/no-head")
    assert state == {"head_count": 1, "get_count": 1}
    assert result["http_status"] == 200
    assert result["ok"] is True


def test_probe_returns_timeout_on_slow_server():
    class H(_Quiet):
        def do_HEAD(self):  # noqa: N802
            time.sleep(0.5)
            self.send_response(HTTPStatus.OK)
            self.end_headers()

    with _http_server(H) as base:
        result = link_availability.probe(base + "/slow", timeout=0.1)
    assert result["http_status"] is None
    assert result["ok"] is False
    assert result["error"] == "timeout"


def test_probe_handles_network_error(monkeypatch):
    # Reach an unroutable port.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()

    result = link_availability.probe(f"http://127.0.0.1:{port}/", timeout=1.0)
    assert result["http_status"] is None
    assert result["ok"] is False
    assert result["error"].startswith("network:") or result["error"] == "timeout"


def test_upsert_result_inserts_and_updates(monkeypatch):
    captured: list[tuple[str, tuple]] = []

    def fake_execute(sql: str, args: tuple = ()):
        captured.append((sql, args))
        return 1

    monkeypatch.setattr(link_availability, "_execute", fake_execute)

    link_availability.upsert_result(
        product_id=42,
        lang="DE",
        domain="Newjoyloo.com",
        link_url="https://newjoyloo.com/de/products/x",
        result={
            "http_status": 200,
            "ok": True,
            "error": None,
            "elapsed_ms": 123,
        },
    )

    assert len(captured) == 1
    sql, args = captured[0]
    assert "INSERT INTO media_product_link_availability" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert args[0] == 42
    assert args[1] == "de"
    assert args[2] == "newjoyloo.com"
    assert args[3] == "https://newjoyloo.com/de/products/x"
    assert args[4] == 200
    assert args[5] == 1
    assert args[6] is None
    assert args[7] == 123


def test_upsert_result_records_error_for_failed_probe(monkeypatch):
    captured: list[tuple] = []
    monkeypatch.setattr(link_availability, "_execute", lambda sql, args=(): captured.append(args) or 1)

    link_availability.upsert_result(
        product_id=7,
        lang="en",
        domain="omurio.com",
        link_url="https://omurio.com/products/x",
        result={"http_status": 404, "ok": False, "error": "http 404", "elapsed_ms": 50},
    )

    assert captured[0][4] == 404
    assert captured[0][5] == 0
    assert captured[0][6] == "http 404"


def test_manual_confirm_result_marks_domain_ok(monkeypatch):
    captured: list[tuple] = []
    monkeypatch.setattr(link_availability, "_execute", lambda sql, args=(): captured.append(args) or 1)

    link_availability.manual_confirm_result(
        product_id=7,
        lang="DE",
        domain="NewJoyLoo.com",
        link_url="https://newjoyloo.com/de/products/demo",
    )

    assert len(captured) == 1
    args = captured[0]
    assert args[0] == 7
    assert args[1] == "de"
    assert args[2] == "newjoyloo.com"
    assert args[3] == "https://newjoyloo.com/de/products/demo"
    assert args[4] == 200
    assert args[5] == 1
    assert args[6] == "manual_confirmed"
    assert args[7] == 0


def test_upsert_result_no_op_for_invalid_input(monkeypatch):
    monkeypatch.setattr(
        link_availability,
        "_execute",
        lambda sql, args=(): pytest.fail("should not write"),
    )
    link_availability.upsert_result(
        product_id=0,
        lang="en",
        domain="omurio.com",
        link_url="https://omurio.com/products/x",
        result={"http_status": 200, "ok": True, "error": None, "elapsed_ms": 10},
    )


def test_list_results_returns_serialized_rows(monkeypatch):
    monkeypatch.setattr(
        link_availability,
        "_query",
        lambda sql, args=(): [
            {
                "product_id": 11,
                "lang": "de",
                "domain": "newjoyloo.com",
                "link_url": "https://newjoyloo.com/de/products/x",
                "http_status": 200,
                "ok": 1,
                "error": None,
                "elapsed_ms": 250,
                "checked_at": None,
            }
        ],
    )
    rows = link_availability.list_results(11, "DE")
    assert rows[0]["domain"] == "newjoyloo.com"
    assert rows[0]["ok"] is True
    assert rows[0]["http_status"] == 200


def test_get_result_for_domain_returns_none_when_missing(monkeypatch):
    monkeypatch.setattr(link_availability, "_query", lambda sql, args=(): [])
    assert link_availability.get_result_for_domain(7, "de", "x.com") is None


def test_probe_and_record_orders_results_per_input(monkeypatch):
    # Force serial path (workers=1) and a deterministic probe order.
    captured: list[dict] = []

    def fake_probe(url: str, *, timeout: float = link_availability.DEFAULT_TIMEOUT):
        return {
            "http_status": 200 if url.endswith("/ok") else 404,
            "ok": url.endswith("/ok"),
            "error": None if url.endswith("/ok") else "http 404",
            "elapsed_ms": 10,
        }

    monkeypatch.setattr(link_availability, "_execute", lambda sql, args=(): captured.append(args) or 1)
    monkeypatch.setattr(
        link_availability,
        "get_result_for_domain",
        lambda product_id, lang, domain: {
            "domain": domain,
            "link_url": "",
            "http_status": 200 if domain == "a.com" else 404,
            "ok": domain == "a.com",
            "error": None if domain == "a.com" else "http 404",
            "elapsed_ms": 10,
            "checked_at": "2026-05-09T00:00:00",
        },
    )

    out = link_availability.probe_and_record(
        product_id=7,
        lang="de",
        rows=[
            {"domain": "a.com", "url": "http://a.com/ok"},
            {"domain": "b.com", "url": "http://b.com/missing"},
        ],
        max_workers=1,
        probe_fn=fake_probe,
    )
    assert [item["domain"] for item in out] == ["a.com", "b.com"]
    assert out[0]["ok"] is True
    assert out[1]["ok"] is False


def test_probe_and_record_returns_empty_for_no_targets():
    assert link_availability.probe_and_record(product_id=1, lang="de", rows=[]) == []
