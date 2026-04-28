from __future__ import annotations

"""Translate & Adapt detail-description image replacement through CDP.

The detail page behaves like a rich article: one translated ``body_html`` field
contains text plus ``<img>`` tags.  The safest automation path is therefore to
replace the whole HTML value in one save, instead of clicking through the rich
text editor image-by-image.
"""

import json
import re
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import websocket
from appcore.payment_screenshot_filter import is_payment_screenshot
from playwright.sync_api import sync_playwright

from tools.shopify_image_localizer import cancellation, locales
from tools.shopify_image_localizer.browser import session
from tools.shopify_image_localizer.rpa import ez_cdp


BODY_HTML_FIELD_PREFIX = "editable_Ym9keV9odG1s"
SOURCE_INDEX_RE = re.compile(r"from_url_en_(\d+)_", re.I)
IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.I | re.S)
IMG_ATTR_RE = re.compile(r"\b(src|alt)\s*=\s*(['\"])(.*?)\2", re.I | re.S)
INSERT_IMAGE_BUTTON_LABELS = ("Insert image", "插入图片")
INSERT_IMAGE_DIALOG_LABELS = ("Insert image", "插入图片")
SAVE_BUTTON_LABELS = ("Save", "保存")
CANCEL_OR_CLOSE_BUTTON_LABELS = ("Cancel", "Close", "取消", "关闭")
FILE_INPUT_SELECTOR = "input[type=file]#image-upload, input[type=file]"


def _js_array(values: tuple[str, ...]) -> str:
    return json.dumps(list(values), ensure_ascii=False)


def build_insert_image_modal_script() -> str:
    button_labels = _js_array(INSERT_IMAGE_BUTTON_LABELS)
    dialog_labels = _js_array(INSERT_IMAGE_DIALOG_LABELS)
    return f"""
(async () => {{
  const buttonLabels = {button_labels};
  const dialogLabels = {dialog_labels};
  const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const labelMatches = (value, labels) => {{
    const normalized = normalize(value);
    return labels.some((label) => normalized === normalize(label) || normalized.includes(normalize(label)));
  }};
  const isEnabled = (button) => button && button.getAttribute('aria-disabled') !== 'true' && !button.disabled;
  const dialogOpen = () => {{
    const openDialog = document.querySelector('[role="dialog"], .Polaris-Modal-Dialog');
    if (!openDialog) return false;
    const text = openDialog.innerText || openDialog.textContent || '';
    return dialogLabels.some((label) => text.includes(label));
  }};
  if (dialogOpen()) return {{ok:true, already:true}};

  const findButton = () => {{
    const buttons = Array.from(document.querySelectorAll('button'));
    const labelMatchesButton = (button) => labelMatches(
      [
        button.getAttribute('aria-label') || '',
        button.getAttribute('title') || '',
        button.innerText || button.textContent || '',
      ].join(' '),
      buttonLabels
    );
    const byLabel = buttons.filter((button) => isEnabled(button) && labelMatchesButton(button));
    if (byLabel.length) return {{button: byLabel[0], strategy: 'label', total: byLabel.length}};

    const byIcon = buttons.filter((button) => (
      isEnabled(button)
      && button.querySelector('s-internal-icon[type="image"]')
    ));
    if (byIcon.length) return {{button: byIcon[0], strategy: 'icon', total: byIcon.length}};
    return {{button: null, strategy: 'missing', total: 0}};
  }};

  let found = findButton();
  if (!found.button) {{
    const overflow = Array.from(document.querySelectorAll('button')).find((button) => (
      isEnabled(button)
      && labelMatches(
        [
          button.getAttribute('aria-label') || '',
          button.getAttribute('title') || '',
          button.innerText || button.textContent || '',
        ].join(' '),
        ['More controls', 'Other controls', '其他控件', '更多控件']
      )
    ));
    if (overflow) {{
      overflow.scrollIntoView({{block:'center'}});
      overflow.click();
      await new Promise((resolve) => setTimeout(resolve, 350));
      found = findButton();
    }}
  }}

  if (!found.button) return {{ok:false, reason:'Insert image button missing'}};
  found.button.scrollIntoView({{block:'center'}});
  found.button.click();
  return {{ok:true, strategy: found.strategy, total: found.total}};
}})()
"""


def build_click_save_script() -> str:
    labels = _js_array(SAVE_BUTTON_LABELS)
    return f"""
(() => {{
  const labels = {labels};
  const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const isEnabled = (button) => button && button.getAttribute('aria-disabled') !== 'true' && !button.disabled;
  const buttons = Array.from(document.querySelectorAll('button')).filter((node) => {{
    const text = normalize(node.innerText || node.textContent || node.getAttribute('aria-label') || '');
    return labels.some((label) => text === normalize(label));
  }});
  const button = buttons.find(isEnabled) || buttons[buttons.length - 1];
  if (!button) return {{ok:false, reason:'Save button missing'}};
  button.scrollIntoView({{block:'center'}});
  button.click();
  return {{ok:true, total: buttons.length, disabled:button.disabled, ariaDisabled:button.getAttribute('aria-disabled')}};
}})()
"""


def build_close_modal_script() -> str:
    labels = _js_array(CANCEL_OR_CLOSE_BUTTON_LABELS)
    return f"""
(() => {{
  const labels = {labels};
  const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  for (const label of labels) {{
    const wanted = normalize(label);
    const button = Array.from(document.querySelectorAll('button')).find((node) => {{
      return normalize(node.innerText || node.textContent || node.getAttribute('aria-label') || '') === wanted;
    }});
    if (button) {{
      button.click();
      return label;
    }}
  }}
  return null;
}})()
"""


def _query_file_input_node_id(cdp: Any) -> int | None:
    root = cdp.call("DOM.getDocument", {"depth": -1, "pierce": True}).payload
    root_id = root["result"]["root"]["nodeId"]
    node = cdp.call(
        "DOM.querySelector",
        {"nodeId": root_id, "selector": FILE_INPUT_SELECTOR},
    ).payload
    node_id = node["result"].get("nodeId")
    return int(node_id) if node_id else None


def _wait_file_input_node_id(
    cdp: Any,
    *,
    timeout_s: float = 10,
    interval_s: float = 0.25,
    cancel_token: cancellation.CancellationToken | None = None,
) -> int:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        cancellation.throw_if_cancelled(cancel_token)
        node_id = _query_file_input_node_id(cdp)
        if node_id:
            return node_id
        cancellation.cancellable_sleep(cancel_token, interval_s)
    raise RuntimeError("Insert image file input not found")


@dataclass
class CdpResponse:
    payload: dict[str, Any]
    events: list[dict[str, Any]]


class RawCdpClient:
    def __init__(self, websocket_url: str) -> None:
        self._ws = websocket.create_connection(
            websocket_url,
            timeout=30,
            suppress_origin=True,
        )
        self._next_id = 0

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass

    def call(self, method: str, params: dict[str, Any] | None = None, *, timeout_s: int = 30) -> CdpResponse:
        self._next_id += 1
        message: dict[str, Any] = {"id": self._next_id, "method": method}
        if params is not None:
            message["params"] = params
        self._ws.send(json.dumps(message))
        deadline = time.time() + timeout_s
        events: list[dict[str, Any]] = []
        while time.time() < deadline:
            self._ws.settimeout(max(0.5, min(5, deadline - time.time())))
            data = json.loads(self._ws.recv())
            if data.get("id") == self._next_id:
                return CdpResponse(data, events)
            events.append(data)
        raise TimeoutError(method)

    def collect_events(self, *, timeout_s: int) -> list[dict[str, Any]]:
        deadline = time.time() + timeout_s
        events: list[dict[str, Any]] = []
        while time.time() < deadline:
            try:
                self._ws.settimeout(max(0.5, min(3, deadline - time.time())))
                events.append(json.loads(self._ws.recv()))
            except Exception:
                continue
        return events

    def evaluate(self, expression: str, *, timeout_s: int = 30) -> Any:
        response = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
            timeout_s=timeout_s,
        ).payload
        result = response.get("result", {}).get("result", {})
        if result.get("subtype") == "error":
            raise RuntimeError(json.dumps(result, ensure_ascii=False))
        return result.get("value")


class TaaSession:
    def __init__(
        self,
        *,
        product_id: str,
        shop_locale: str,
        user_data_dir: str,
        port: int = ez_cdp.DEFAULT_CDP_PORT,
        cancel_token: cancellation.CancellationToken | None = None,
    ) -> None:
        self.product_id = str(product_id).strip()
        self.shop_locale = locales.translate_and_adapt_locale_for(str(shop_locale).strip())
        self.user_data_dir = user_data_dir
        self.port = port
        self.cancel_token = cancel_token
        self.outer_url = session.build_translate_url(self.product_id, self.shop_locale)
        self._playwright = None
        self._browser = None
        self._page = None
        self.cdp: RawCdpClient | None = None

    def __enter__(self) -> "TaaSession":
        cancellation.throw_if_cancelled(self.cancel_token)
        ez_cdp.ensure_cdp_chrome(self.user_data_dir, self.outer_url, port=self.port, cancel_token=self.cancel_token)
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(ez_cdp._cdp_ws_endpoint(self.port))
        context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        self._page = context.new_page()
        cancellation.throw_if_cancelled(self.cancel_token)
        self._page.goto(self.outer_url, wait_until="domcontentloaded", timeout=30000)
        cancellation.throw_if_cancelled(self.cancel_token)
        self._page.wait_for_timeout(8000)
        cancellation.throw_if_cancelled(self.cancel_token)

        page_cdp = context.new_cdp_session(self._page)
        page_target = page_cdp.send("Target.getTargetInfo").get("targetInfo", {})
        page_target_id = page_target.get("targetId")
        iframe_target = self._wait_taa_iframe_target(page_target_id)
        self.cdp = RawCdpClient(iframe_target["webSocketDebuggerUrl"])
        self.cdp.call("Runtime.enable")
        self.cdp.call("DOM.enable")
        self.cdp.call("Network.enable")
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        if self.cdp is not None:
            self.cdp.close()
        if self._page is not None:
            try:
                self._page.close()
            except Exception:
                pass
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass

    def _wait_taa_iframe_target(self, page_target_id: str | None, *, timeout_s: int = 40) -> dict[str, Any]:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            cancellation.throw_if_cancelled(self.cancel_token)
            targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list"))
            matches = []
            for target in targets:
                url = str(target.get("url") or "")
                if target.get("type") != "iframe":
                    continue
                if page_target_id and target.get("parentId") != page_target_id:
                    continue
                if "store-localization.shopifyapps.com/localize/product" not in url:
                    continue
                if f"id={self.product_id}" not in url:
                    continue
                if f"shopLocale={self.shop_locale}" not in url:
                    continue
                matches.append(target)
            if matches:
                return matches[-1]
            cancellation.cancellable_sleep(self.cancel_token, 1)
        raise RuntimeError("Translate & Adapt iframe target not found")

    def evaluate(self, expression: str, *, timeout_s: int = 30) -> Any:
        if self.cdp is None:
            raise RuntimeError("TAA CDP session is not open")
        return self.cdp.evaluate(expression, timeout_s=timeout_s)

    def current_body_html(self) -> str:
        cancellation.throw_if_cancelled(self.cancel_token)
        selector = f'textarea[id^="{BODY_HTML_FIELD_PREFIX}"]'
        value = self.evaluate(
            f"document.querySelector({json.dumps(selector)})?.value || ''",
            timeout_s=20,
        )
        return str(value or "")

    def set_body_html(self, html: str) -> dict[str, Any]:
        cancellation.throw_if_cancelled(self.cancel_token)
        selector = f'textarea[id^="{BODY_HTML_FIELD_PREFIX}"]'
        payload = json.dumps(html)
        script = f"""
(() => {{
  const el = document.querySelector({json.dumps(selector)});
  if (!el) return {{ok:false, reason:'body_html textarea missing'}};
  const value = {payload};
  const desc = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')
    || Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
  desc.set.call(el, value);
  el.dispatchEvent(new Event('input', {{bubbles:true}}));
  el.dispatchEvent(new Event('change', {{bubbles:true}}));
  return {{ok:true, len: el.value.length, imgCount: (el.value.match(/<img\\b/gi) || []).length}};
}})()
"""
        result = self.evaluate(script, timeout_s=20)
        if not result or not result.get("ok"):
            raise RuntimeError(f"failed to set body_html: {json.dumps(result, ensure_ascii=False)}")
        return result

    def click_save(self) -> list[dict[str, Any]]:
        cancellation.throw_if_cancelled(self.cancel_token)
        result = self.evaluate(
            build_click_save_script(),
            timeout_s=20,
        )
        if not result or not result.get("ok"):
            raise RuntimeError(f"failed to click Save: {json.dumps(result, ensure_ascii=False)}")
        if self.cdp is None:
            return []
        events = self.cdp.collect_events(timeout_s=35)
        cancellation.throw_if_cancelled(self.cancel_token)
        return summarize_store_localization_events(events)

    def open_insert_image_modal(self) -> None:
        cancellation.throw_if_cancelled(self.cancel_token)
        result = self.evaluate(
            build_insert_image_modal_script(),
            timeout_s=20,
        )
        if not result or not result.get("ok"):
            raise RuntimeError(f"failed to open insert image modal: {json.dumps(result, ensure_ascii=False)}")
        cancellation.cancellable_sleep(self.cancel_token, 1)

    def close_modal(self) -> None:
        try:
            self.evaluate(
                build_close_modal_script(),
                timeout_s=10,
            )
        except Exception:
            pass

    def _set_file_input(self, local_path: str) -> None:
        if self.cdp is None:
            raise RuntimeError("TAA CDP session is not open")
        cancellation.throw_if_cancelled(self.cancel_token)
        node_id = _wait_file_input_node_id(self.cdp, cancel_token=self.cancel_token)
        self.cdp.call("DOM.setFileInputFiles", {"nodeId": node_id, "files": [str(Path(local_path).resolve())]})

    def upload_image(self, local_path: str, *, timeout_s: int = 70) -> str:
        if self.cdp is None:
            raise RuntimeError("TAA CDP session is not open")
        path = Path(local_path)
        self.open_insert_image_modal()
        cancellation.throw_if_cancelled(self.cancel_token)
        self._set_file_input(str(path))

        deadline = time.time() + timeout_s
        cdn_url = ""
        stem = path.stem
        basename = path.name
        token = ez_cdp.md5_token(basename) or ez_cdp.md5_token(stem) or ""
        seen_cdn_urls: list[str] = []
        while time.time() < deadline:
            cancellation.throw_if_cancelled(self.cancel_token)
            try:
                self.cdp._ws.settimeout(max(0.5, min(5, deadline - time.time())))
                data = json.loads(self.cdp._ws.recv())
            except Exception:
                continue
            if data.get("method") not in ("Network.requestWillBeSent", "Network.responseReceived"):
                continue
            params = data.get("params") or {}
            request = params.get("request") or {}
            response = params.get("response") or {}
            url = str(request.get("url") or response.get("url") or "")
            if "cdn.shopify.com/s/files/" not in url:
                continue
            seen_cdn_urls.append(url)
            token_match = bool(token and (token in url or token[:32] in url))
            if basename not in url and stem not in url and not token_match:
                continue
            if data.get("method") == "Network.responseReceived":
                if int(response.get("status") or 0) == 200:
                    cdn_url = url
                    break
            elif not cdn_url:
                cdn_url = url
        if not cdn_url:
            cdn_url = self._find_uploaded_image_url_in_modal(stem, basename, token)
        if not cdn_url and seen_cdn_urls:
            cdn_url = seen_cdn_urls[-1]
        if not cdn_url:
            raise RuntimeError(f"uploaded CDN URL not found for {basename}")
        return cdn_url

    def _find_uploaded_image_url_in_modal(self, stem: str, basename: str, token: str = "") -> str:
        urls = self.evaluate(
            r"""
(() => Array.from(document.querySelectorAll('[role="dialog"] img, .Polaris-Modal-Dialog img')).map((img) => img.src))()
""",
            timeout_s=10,
        ) or []
        for url in urls:
            token_match = bool(token and (token in url or token[:32] in url))
            if "cdn.shopify.com/s/files/" in url and (stem in url or basename in url or token_match):
                return str(url)
        return ""


def extract_image_srcs(html: str) -> list[str]:
    return [row["src"] for row in extract_image_refs(html)]


def extract_image_refs(html: str) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for tag_match in IMG_TAG_RE.finditer(html or ""):
        attrs: dict[str, str] = {}
        for attr_match in IMG_ATTR_RE.finditer(tag_match.group(0)):
            attrs[attr_match.group(1).lower()] = attr_match.group(3)
        src = attrs.get("src") or ""
        if src:
            refs.append({"src": src, "alt": attrs.get("alt") or ""})
    return refs


def source_index_from_filename(filename: str) -> int | None:
    match = SOURCE_INDEX_RE.search(filename or "")
    return int(match.group(1)) if match else None


def source_name_key(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = urlparse(raw).path if "://" in raw or raw.startswith("//") else raw.split("?", 1)[0]
    name = Path(unquote(path)).name
    if not name:
        return None
    match = SOURCE_INDEX_RE.search(name)
    if match:
        name = name[match.end():]
    stem = Path(name).stem.strip().lower()
    return f"name:{stem}" if stem else None


def _shopify_safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")


def is_already_localized_src(src: str, candidate: dict[str, Any]) -> bool:
    if "cdn.shopify.com/s/files/" not in str(src or ""):
        return False
    filename = str(candidate.get("filename") or candidate.get("local_path") or "")
    stem = Path(filename).stem
    if stem and stem in src:
        return True
    src_token = ez_cdp.md5_token(src) or ""
    candidate_token = str(candidate.get("token") or ez_cdp.md5_token(filename) or "")
    if not src_token or not candidate_token or src_token != candidate_token:
        return False
    source_index = candidate.get("source_index")
    source_index_match = source_index is None or f"from_url_en_{int(source_index):02d}" in src
    if not source_index_match:
        return False
    safe_stem = _shopify_safe_name(stem)
    safe_src = _shopify_safe_name(src)
    return bool(safe_stem and safe_stem in safe_src)


def build_localized_candidates(localized_images: list[dict]) -> dict[str, list[dict[str, Any]]]:
    candidates: dict[str, list[dict[str, Any]]] = {}
    for item in localized_images or []:
        filename = str(item.get("filename") or Path(str(item.get("local_path") or "")).name)
        token = ez_cdp.md5_token(filename)
        local_path = str(item.get("local_path") or "")
        if not token or not local_path:
            continue
        row = {
            **item,
            "token": token,
            "source_index": source_index_from_filename(filename),
            "local_path": local_path,
            "filename": filename,
        }
        candidates.setdefault(token, []).append(row)
    for rows in candidates.values():
        rows.sort(key=lambda row: (row.get("source_index") is None, row.get("source_index") or 9999, row.get("filename") or ""))
    return candidates


def build_localized_candidates_by_source_index(localized_images: list[dict]) -> dict[int, list[dict[str, Any]]]:
    candidates: dict[int, list[dict[str, Any]]] = {}
    for item in localized_images or []:
        filename = str(item.get("filename") or Path(str(item.get("local_path") or "")).name)
        local_path = str(item.get("local_path") or "")
        source_index = source_index_from_filename(filename)
        if source_index is None or not local_path:
            continue
        row = {
            **item,
            "token": ez_cdp.md5_token(filename),
            "source_index": source_index,
            "source_name_key": source_name_key(filename),
            "local_path": local_path,
            "filename": filename,
        }
        candidates.setdefault(source_index, []).append(row)
    for rows in candidates.values():
        rows.sort(key=lambda row: (row.get("filename") or ""))
    return candidates


def parse_source_index_map(raw: str | None) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for part in (raw or "").split(","):
        if not part.strip():
            continue
        if "=" not in part:
            raise ValueError(f"invalid source index mapping item: {part}")
        token, value = part.split("=", 1)
        normalized_token = str(token or "").strip().lower()
        if not normalized_token:
            raise ValueError(f"invalid source index mapping item: {part}")
        mapping[normalized_token] = int(str(value).strip())
    return mapping


def choose_localized_image(
    src: str,
    candidates_by_token: dict[str, list[dict[str, Any]]],
    *,
    source_index_by_token: dict[str, int] | None = None,
) -> dict[str, Any]:
    token = ez_cdp.md5_token(src)
    if not token:
        raise ValueError(f"image src does not contain source token: {src}")
    candidates = candidates_by_token.get(token) or []
    if not candidates:
        raise ValueError(f"no localized candidate for token {token}")

    source_index = source_index_from_filename(src)
    if source_index is None:
        source_index = (source_index_by_token or {}).get(token)
    if source_index is not None:
        exact = [row for row in candidates if row.get("source_index") == source_index]
        if exact:
            return exact[0]
        raise ValueError(f"no localized candidate for token {token} source index {source_index:02d}")

    if len(candidates) == 1:
        return candidates[0]

    options = [
        f"{row.get('source_index')}:{row.get('filename')}"
        for row in candidates
    ]
    raise ValueError(
        f"ambiguous localized candidates for token {token}; provide source index map. "
        f"Candidates: {options}"
    )


def choose_localized_image_by_source_index(
    src: str,
    candidates_by_source_index: dict[int, list[dict[str, Any]]],
    source_index: int,
) -> dict[str, Any]:
    candidates = candidates_by_source_index.get(source_index) or []
    if not candidates:
        raise ValueError(f"no localized candidate for source index {source_index:02d}")
    key = source_name_key(src)
    if key:
        exact_name = [row for row in candidates if row.get("source_name_key") == key]
        if exact_name:
            return exact_name[0]
    if len(candidates) == 1:
        return candidates[0]
    options = [str(row.get("filename") or "") for row in candidates]
    raise ValueError(f"ambiguous localized candidates for source index {source_index:02d}: {options}")


def plan_body_html_replacements(
    html: str,
    localized_images: list[dict],
    *,
    source_index_by_token: dict[str, int] | None = None,
    forced_replacements_by_src: dict[str, dict[str, Any]] | None = None,
    replace_shopify_cdn: bool = False,
) -> dict[str, Any]:
    candidates_by_token = build_localized_candidates(localized_images)
    candidates_by_source_index = build_localized_candidates_by_source_index(localized_images)
    forced_replacements = forced_replacements_by_src or {}
    srcs = [
        row["src"]
        for row in extract_image_refs(html)
        if not is_payment_screenshot(row["src"], row.get("alt") or "")
    ]
    replacements: list[dict[str, Any]] = []
    skipped_existing: list[dict[str, Any]] = []
    skipped_missing: list[dict[str, Any]] = []
    for src in srcs:
        token = ez_cdp.md5_token(src)
        source_index = source_index_from_filename(src)
        match_method = "token"
        try:
            forced_candidate = forced_replacements.get(src)
            if forced_candidate:
                candidate = {
                    **forced_candidate,
                    "local_path": str(forced_candidate.get("local_path") or ""),
                    "filename": str(forced_candidate.get("filename") or Path(str(forced_candidate.get("local_path") or "")).name),
                }
                if not candidate.get("local_path"):
                    raise ValueError(f"visual candidate missing local_path for src: {src}")
                match_method = "visual"
            else:
                if source_index is None:
                    if token:
                        source_index = (source_index_by_token or {}).get(token)
                    if source_index is None:
                        key = source_name_key(src)
                        source_index = (source_index_by_token or {}).get(key or "")
                if token:
                    candidate = choose_localized_image(
                        src,
                        candidates_by_token,
                        source_index_by_token=source_index_by_token,
                    )
                elif source_index is not None:
                    candidate = choose_localized_image_by_source_index(
                        src,
                        candidates_by_source_index,
                        source_index,
                    )
                    match_method = "source_index"
                else:
                    raise ValueError(f"image src has no source token or source index mapping: {src}")
        except ValueError as exc:
            skipped_missing.append({
                "token": token,
                "src": src,
                "source_index": source_index,
                "reason": str(exc),
            })
            continue
        is_shopify_cdn = "cdn.shopify.com/s/files/" in src
        already_localized = is_already_localized_src(src, candidate)
        if already_localized or (is_shopify_cdn and not replace_shopify_cdn):
            skipped_existing.append({
                "token": token,
                "src": src,
                "reason": "already localized" if already_localized else "shopify cdn image skipped",
                "candidate": candidate,
            })
            continue
        replacements.append({
            "token": token,
            "old": src,
            "candidate": candidate,
            "match_method": match_method,
        })
    return {
        "image_count": len(srcs),
        "replacements": replacements,
        "skipped_existing": skipped_existing,
        "skipped_missing": skipped_missing,
    }


def _set_or_append_style(tag: str, declarations: dict[str, str]) -> str:
    style_match = re.search(r"\bstyle\s*=\s*(['\"])(.*?)\1", tag, re.I | re.S)
    existing: dict[str, str] = {}
    order: list[str] = []
    if style_match:
        for part in style_match.group(2).split(";"):
            if ":" not in part:
                continue
            key, value = part.split(":", 1)
            normalized = key.strip().lower()
            if not normalized:
                continue
            if normalized not in existing:
                order.append(normalized)
            existing[normalized] = value.strip()
    for key, value in declarations.items():
        normalized = key.strip().lower()
        if normalized not in existing:
            order.append(normalized)
        existing[normalized] = value.strip()
    style_value = "; ".join(f"{key}: {existing[key]}" for key in order if existing.get(key))
    if style_value:
        style_value = f"{style_value};"
    if style_match:
        return f"{tag[:style_match.start(2)]}{style_value}{tag[style_match.end(2):]}"
    insert_at = tag.rfind(">")
    if insert_at < 0:
        return tag
    return f'{tag[:insert_at]} style="{style_value}"{tag[insert_at:]}'


def _apply_display_size_to_img_tag(tag: str, size: dict[str, Any] | None) -> str:
    if not size:
        return tag
    try:
        width = int(round(float(size.get("width") or 0)))
    except (TypeError, ValueError):
        width = 0
    if width <= 0:
        return tag
    return _set_or_append_style(
        tag,
        {
            "width": f"{width}px",
            "max-width": "100%",
            "height": "auto",
        },
    )


def _replace_img_src_preserving_tag(
    html: str,
    old_src: str,
    new_src: str,
    *,
    display_size: dict[str, Any] | None = None,
) -> str:
    old_src = str(old_src or "")
    new_src = str(new_src or "")
    if not old_src or not new_src:
        return html
    pattern = re.compile(
        r"<img\b[^>]*\bsrc\s*=\s*(['\"])" + re.escape(old_src) + r"\1[^>]*>",
        re.I | re.S,
    )

    def repl(match: re.Match) -> str:
        tag = match.group(0)
        quote = match.group(1)
        tag = tag.replace(f"{quote}{old_src}{quote}", f"{quote}{new_src}{quote}", 1)
        return _apply_display_size_to_img_tag(tag, display_size)

    updated, count = pattern.subn(repl, html)
    if count:
        return updated
    return html.replace(old_src, new_src)


def apply_uploaded_replacements(
    html: str,
    replacements: list[dict[str, Any]],
    *,
    display_size_by_src: dict[str, dict[str, Any]] | None = None,
) -> str:
    updated = html
    for row in replacements:
        new_url = str(row.get("new") or "")
        if not new_url:
            raise ValueError(f"replacement missing new URL: {row}")
        old_url = str(row["old"])
        updated = _replace_img_src_preserving_tag(
            updated,
            old_url,
            new_url,
            display_size=(display_size_by_src or {}).get(old_url),
        )
    return updated


def summarize_store_localization_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = []
    for event in events:
        if event.get("method") not in ("Network.requestWillBeSent", "Network.responseReceived"):
            continue
        params = event.get("params") or {}
        request = params.get("request") or {}
        response = params.get("response") or {}
        url = str(request.get("url") or response.get("url") or "")
        if "store-localization.shopifyapps.com" not in url and "graphql" not in url:
            continue
        summary.append({
            "method": event.get("method"),
            "url": url[:300],
            "status": response.get("status"),
            "type": params.get("type"),
        })
    return summary


def replace_detail_images(
    *,
    product_id: str,
    shop_locale: str,
    user_data_dir: str,
    localized_images: list[dict],
    source_index_by_token: dict[str, int] | None = None,
    forced_replacements_by_src: dict[str, dict[str, Any]] | None = None,
    display_size_by_src: dict[str, dict[str, Any]] | None = None,
    port: int = ez_cdp.DEFAULT_CDP_PORT,
    replace_shopify_cdn: bool = False,
    verify_reload: bool = True,
    cancel_token: cancellation.CancellationToken | None = None,
) -> dict[str, Any]:
    with TaaSession(
        product_id=product_id,
        shop_locale=shop_locale,
        user_data_dir=user_data_dir,
        port=port,
        cancel_token=cancel_token,
    ) as taa:
        cancellation.throw_if_cancelled(cancel_token)
        html_before = taa.current_body_html()
        plan = plan_body_html_replacements(
            html_before,
            localized_images,
            source_index_by_token=source_index_by_token,
            forced_replacements_by_src=forced_replacements_by_src,
            replace_shopify_cdn=replace_shopify_cdn,
        )
        uploaded_replacements: list[dict[str, Any]] = []
        for row in plan["replacements"]:
            cancellation.throw_if_cancelled(cancel_token)
            cdn_url = taa.upload_image(str(row["candidate"]["local_path"]))
            uploaded_replacements.append({
                "token": row["token"],
                "old": row["old"],
                "new": cdn_url,
                "local_path": row["candidate"]["local_path"],
                "source_index": row["candidate"].get("source_index"),
                "match_method": row.get("match_method"),
            })
        taa.close_modal()

        save_events: list[dict[str, Any]] = []
        html_after = html_before
        if uploaded_replacements:
            cancellation.throw_if_cancelled(cancel_token)
            html_after = apply_uploaded_replacements(
                html_before,
                uploaded_replacements,
                display_size_by_src=display_size_by_src,
            )
            taa.set_body_html(html_after)
            save_events = taa.click_save()
            readback_html = taa.current_body_html()
        else:
            readback_html = html_before

    verify_html = readback_html
    if verify_reload:
        cancellation.throw_if_cancelled(cancel_token)
        with TaaSession(
            product_id=product_id,
            shop_locale=shop_locale,
            user_data_dir=user_data_dir,
            port=port,
            cancel_token=cancel_token,
        ) as taa:
            verify_html = taa.current_body_html()

    expected_urls = [row["new"] for row in uploaded_replacements]
    return {
        "status": "done" if uploaded_replacements else "skipped",
        "image_count": plan["image_count"],
        "replacement_count": len(uploaded_replacements),
        "skipped_existing_count": len(plan["skipped_existing"]),
        "skipped_missing_count": len(plan["skipped_missing"]),
        "replacements": uploaded_replacements,
        "skipped_existing": [
            {
                "token": row["token"],
                "src": row["src"],
                "reason": row["reason"],
                "local_path": row["candidate"].get("local_path"),
                "source_index": row["candidate"].get("source_index"),
            }
            for row in plan["skipped_existing"]
        ],
        "skipped_missing": [
            {
                "token": row["token"],
                "src": row["src"],
                "reason": row["reason"],
                "source_index": row.get("source_index"),
            }
            for row in plan["skipped_missing"]
        ],
        "save_network": save_events,
        "verify": {
            "expected_new_urls_present": sum(1 for url in expected_urls if url in verify_html),
            "expected_total": len(expected_urls),
            "old_non_shopify_count": sum(
                1 for src in extract_image_srcs(verify_html)
                if "cdn.shopify.com/s/files/" not in src
            ),
            "html": verify_html,
        },
    }
