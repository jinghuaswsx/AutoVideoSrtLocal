"""Smoke test: launch Chrome with bundled extension, verify extension connects,
and dump the EZ tab's frame tree. Used to confirm Shopify does NOT block the
embedded iframe when the extension is loaded.
"""
from __future__ import annotations

import json
import subprocess
import time
from tools.shopify_image_localizer.browser import session
from tools.shopify_image_localizer.ext_bridge import ExtensionBridge, find_tab_matching


def main() -> None:
    # 1. start WS bridge
    bridge = ExtensionBridge(port=7778)
    bridge.start()
    time.sleep(0.3)

    # 2. ensure clean chrome slate
    session.kill_chrome_for_profile(r"C:\chrome-shopify-image")
    time.sleep(2)

    # 3. launch Chrome with extension + EZ URL
    session.start_chrome(
        r"C:\chrome-shopify-image",
        initial_urls=[
            "https://admin.shopify.com/store/0ixug9-pv/apps/ez-product-image-translate/product/8552296546477",
        ],
        window_position="40,40",
        window_size="1400,2100",
    )

    # 4. wait for extension WS hello
    print("[smoke] waiting for extension to connect to WS...")
    if not bridge.wait_client(timeout_s=30):
        print("[smoke] TIMEOUT waiting for extension WS hello")
        bridge.stop()
        return
    print("[smoke] extension connected")

    # sanity ping
    try:
        print("[smoke] ping:", bridge.call("ping", {}))
    except Exception as e:
        print("[smoke] ping failed:", e)

    # 5. find EZ tab
    tab = find_tab_matching(bridge, "ez-product-image-translate", timeout_s=30)
    if not tab:
        print("[smoke] EZ tab not found")
        bridge.stop()
        return
    print("[smoke] EZ tab:", tab)

    # 6. attach debugger
    try:
        print("[smoke] attach:", bridge.call("attach", {"tabId": tab["id"]}))
    except Exception as e:
        print("[smoke] attach failed:", e)
        bridge.stop()
        return

    # 7. poll frame tree for up to 30s, looking for freshify iframe
    found_plugin = False
    for t in range(15):
        time.sleep(2)
        try:
            tree = bridge.call("get_frame_tree", {"tabId": tab["id"]})
        except Exception as e:
            print(f"[smoke {t*2}s] frame tree err: {e}")
            continue
        frames = []
        def walk(node):
            if not node or not node.get("frame"): return
            frames.append(node["frame"])
            for c in node.get("childFrames") or []:
                walk(c)
        walk(tree.get("frameTree") if tree else {})
        urls = [fr.get("url", "")[:100] for fr in frames]
        has_plugin = any("freshify" in u for u in urls)
        print(f"[smoke {t*2+2}s] frames={len(frames)} plugin={has_plugin}")
        if t == 0:
            for u in urls: print(f"   {u}")
        if has_plugin:
            found_plugin = True
            break

    print(f"\n[smoke] plugin iframe loaded under --load-extension: {found_plugin}")

    if found_plugin:
        # 8. sanity: count img.actual-image in plugin iframe
        try:
            res = bridge.call("query_selector_in_frame", {
                "tabId": tab["id"],
                "selector": "img.actual-image",
                "frame_url_contains": "freshify",
            })
            print("[smoke] img.actual-image match:", json.dumps(res, indent=2)[:800])
        except Exception as e:
            print("[smoke] query err:", e)

    bridge.stop()


if __name__ == "__main__":
    main()
