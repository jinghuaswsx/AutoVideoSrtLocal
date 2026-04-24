from __future__ import annotations

"""Python ↔ Chrome Extension HTTP polling bridge.

Runs an HTTP server on 127.0.0.1:<port> that the companion Chrome extension
polls. Python enqueues commands; the extension picks them up via GET /poll and
returns results via POST /result.

Endpoints:
  GET  /poll       -> {"id":..., "method":..., "params":{...}} or 204 idle
  POST /result     body: {"id":..., "result":...} | {"id":..., "error":{"message":...}}
  POST /hello      body: {"version":..., "ts":...}    -> 200 "ok"

Python API:
  bridge = ExtensionBridge()
  bridge.start()
  bridge.wait_client()
  bridge.call("list_tabs", {"url_contains":"..."})
  bridge.stop()
"""

import json
import queue
import threading
import time
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


DEFAULT_PORT = 7778
RPC_TIMEOUT_S = 30


class ExtensionBridge:
    def __init__(self, port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> None:
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._outbox: queue.Queue = queue.Queue()
        self._pending: dict[int, Future] = {}
        self._next_id = 1
        self._client_seen = threading.Event()

    # ----- lifecycle -----
    def start(self) -> None:
        handler = _make_handler(self)
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        print(f"[ext_bridge] listening on http://{self.host}:{self.port}/")

    def wait_client(self, timeout_s: float = 60) -> bool:
        return self._client_seen.wait(timeout=timeout_s)

    def stop(self) -> None:
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None

    # ----- RPC -----
    def call(self, method: str, params: dict | None = None, *, timeout_s: int = RPC_TIMEOUT_S) -> Any:
        rid = self._next_id
        self._next_id += 1
        fut: Future = Future()
        self._pending[rid] = fut
        self._outbox.put({"id": rid, "method": method, "params": params or {}})
        try:
            return fut.result(timeout=timeout_s)
        except TimeoutError:
            self._pending.pop(rid, None)
            raise RuntimeError(f"rpc {method} timed out")

    # ----- internals called from HTTP handler -----
    def _pop_outbox(self) -> dict | None:
        try:
            return self._outbox.get_nowait()
        except queue.Empty:
            return None

    def _resolve(self, msg: dict) -> None:
        rid = msg.get("id")
        if rid is None:
            return
        fut = self._pending.pop(rid, None)
        if fut is None:
            return
        if "error" in msg:
            fut.set_exception(RuntimeError(
                (msg.get("error") or {}).get("message") or "rpc error"
            ))
        else:
            fut.set_result(msg.get("result"))


def _make_handler(bridge: "ExtensionBridge"):
    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, code: int, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _send_empty(self, code: int):
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

        def log_message(self, fmt, *args):
            try:
                print(f"[ext_bridge http] {self.address_string()} {fmt % args}")
            except Exception:
                pass

        def do_GET(self):
            if self.path.startswith("/poll"):
                bridge._client_seen.set()
                msg = bridge._pop_outbox()
                if msg is None:
                    return self._send_empty(204)
                return self._send_json(200, msg)
            if self.path == "/health":
                return self._send_json(200, {"ok": True})
            self._send_empty(404)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                data = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                data = {}
            if self.path == "/result":
                bridge._resolve(data)
                return self._send_json(200, {"ok": True})
            if self.path == "/hello":
                bridge._client_seen.set()
                print(f"[ext_bridge] hello from ext: {data}")
                return self._send_json(200, {"ok": True})
            self._send_empty(404)

    return Handler


def find_tab_matching(bridge: ExtensionBridge, url_contains: str, timeout_s: float = 30) -> dict | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            tabs = bridge.call("list_tabs", {"url_contains": url_contains}) or []
        except Exception:
            tabs = []
        if tabs:
            return tabs[0]
        time.sleep(0.5)
    return None
