"""Read-only XHR probe for Meta Ads Manager.

Goal: figure out which internal endpoints (GraphQL / REST) carry the
Campaign / Ad Set / Ad table rows that the Ads Manager UI renders. We
need this to design an alternative sync channel that doesn't depend on
the brittle "Export CSV" download flow.

Behaviour:
- Acquires the shared Meta Ads CDP lock so we don't fight roi_hourly_sync.
- Connects to the existing Chrome at 127.0.0.1:9222.
- Opens a NEW tab (does NOT touch any existing tabs), navigates to a
  Campaign list page for newjoyloo for today.
- Listens passively to every response for ~25s, then closes the tab.
- Dumps URL + status + content-type + size + a small body sample for
  every facebook.com response into drafts/meta_ads_xhr_probe_<ts>.jsonl.
- Also writes a short summary at drafts/meta_ads_xhr_probe_<ts>.summary.txt
  ranking endpoints by total response bytes (heuristic for "big table
  data lives here").

Run:
    /opt/autovideosrt/venv/bin/python drafts/meta_ads_xhr_probe.py [campaigns|adsets|ads]

Default level is "campaigns".
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from playwright.sync_api import sync_playwright

from appcore.meta_ads_cdp import DEFAULT_META_ADS_CDP_URL, meta_ads_cdp_lock

ACCOUNT_ID = "1861285821213497"      # newjoyloo
BUSINESS_ID = "476723373113063"
LISTEN_SECONDS = 25
BODY_SAMPLE_BYTES = 4096
LEVELS = ("campaigns", "adsets", "ads")


def build_url(level: str, day: date) -> str:
    ds = day.isoformat()
    return (
        f"https://adsmanager.facebook.com/adsmanager/manage/{level}?"
        f"act={ACCOUNT_ID}&business_id={BUSINESS_ID}&global_scope_id={BUSINESS_ID}"
        f"&attribution_windows=default&column_preset=1658418688523178"
        f"&date={ds}_{ds}&insights_date={ds}_{ds}&insights_selected_metrics=cpm"
    )


def main() -> int:
    level = sys.argv[1] if len(sys.argv) > 1 else "campaigns"
    if level not in LEVELS:
        print(f"unknown level {level!r}, must be one of {LEVELS}", file=sys.stderr)
        return 2

    today = date.today()
    target_url = build_url(level, today)
    ts = time.strftime("%Y%m%d_%H%M%S")
    jsonl_out = REPO_ROOT / "drafts" / f"meta_ads_xhr_probe_{level}_{ts}.jsonl"
    summary_out = REPO_ROOT / "drafts" / f"meta_ads_xhr_probe_{level}_{ts}.summary.txt"

    captured: list[dict] = []
    by_path: dict[str, dict] = {}

    def on_response(resp):
        try:
            url = resp.url
            host = urlparse(url).hostname or ""
            if "facebook" not in host and "fbcdn" not in host:
                return
            ctype = (resp.headers or {}).get("content-type", "")
            # we only care about XHR/JSON-ish stuff, skip images/fonts/css/js bundles
            if any(k in ctype for k in ("image/", "font/", "text/css")):
                return
            if any(url.endswith(ext) for ext in (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".woff", ".woff2", ".svg")):
                return
            try:
                body = resp.body()
            except Exception as exc:  # noqa: BLE001
                body = None
                body_err = f"{type(exc).__name__}: {str(exc)[:120]}"
            else:
                body_err = None
            sample = ""
            if body is not None:
                try:
                    sample = body[:BODY_SAMPLE_BYTES].decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    sample = ""
            entry = {
                "url": url,
                "path": urlparse(url).path,
                "status": resp.status,
                "content_type": ctype,
                "size": len(body) if body is not None else None,
                "body_error": body_err,
                "sample": sample,
            }
            captured.append(entry)
            path_key = urlparse(url).path
            agg = by_path.setdefault(path_key, {"count": 0, "total_size": 0, "ctype": ctype})
            agg["count"] += 1
            agg["total_size"] += entry["size"] or 0
        except Exception:  # noqa: BLE001 - never break the listener
            pass

    print(f"target url: {target_url}", flush=True)
    print(f"output: {jsonl_out}", flush=True)

    with meta_ads_cdp_lock(task_code="meta_ads_xhr_probe", timeout_seconds=120, retry_seconds=5):
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(DEFAULT_META_ADS_CDP_URL)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()
            page.on("response", on_response)
            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            except Exception as exc:  # noqa: BLE001 - keep probing even if nav slow
                print(f"goto warning: {type(exc).__name__}: {exc}", flush=True)
            # let the page settle and stream all its XHR
            for i in range(LISTEN_SECONDS):
                page.wait_for_timeout(1000)
                if i == 5:
                    # nudge: scroll the table to trigger any virtualized re-fetch
                    try:
                        page.mouse.wheel(0, 1500)
                    except Exception:  # noqa: BLE001
                        pass
                if i == 12:
                    try:
                        page.mouse.wheel(0, 3000)
                    except Exception:  # noqa: BLE001
                        pass
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass

    jsonl_out.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_out.open("w", encoding="utf-8") as fh:
        for entry in captured:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    ranked = sorted(by_path.items(), key=lambda kv: kv[1]["total_size"], reverse=True)
    lines = [
        f"target_url={target_url}",
        f"level={level}",
        f"captured_responses={len(captured)}",
        f"unique_paths={len(by_path)}",
        "",
        "top paths by total response bytes:",
    ]
    for path_key, agg in ranked[:30]:
        lines.append(f"  {agg['total_size']:>10}B  x{agg['count']:>3}  {agg['ctype'][:40]:40}  {path_key}")
    summary_out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)
    print(f"\nwrote {jsonl_out}", flush=True)
    print(f"wrote {summary_out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
