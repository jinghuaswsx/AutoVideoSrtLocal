from __future__ import annotations

import os
import sys

try:
    import psutil as _psutil
except ModuleNotFoundError:
    _psutil = None

from tools.shopify_image_localizer import settings
from tools.shopify_image_localizer.gui import ShopifyImageLocalizerApp
from tools.shopify_image_localizer.rpa import ez_cdp


def _kill_other_shopify_localizer_instances() -> None:
    """Best-effort cleanup for stale ShopifyImageLocalizer instances."""
    if _psutil is None:
        return

    current_pid = os.getpid()

    for proc in _psutil.process_iter(["pid", "name", "exe"]):
        try:
            if proc.pid == current_pid:
                continue
            name = str(proc.info.get("name") or "").lower()
            if "shopifyimagelocalizer" in name:
                proc.kill()
                continue
            if "python" in name or "pythonw" in name:
                try:
                    cmdline = proc.cmdline()
                    cmd_str = " ".join(cmdline).lower()
                    if "shopify_image_localizer" in cmd_str or "tools.shopify_image_localizer" in cmd_str:
                        proc.kill()
                except (_psutil.AccessDenied, _psutil.ZombieProcess):
                    pass
        except (_psutil.NoSuchProcess, _psutil.AccessDenied, _psutil.ZombieProcess):
            pass


def main() -> None:
    try:
        _kill_other_shopify_localizer_instances()
    except Exception:
        pass

    runtime_config = settings.load_runtime_config()
    profile_for_domain = getattr(
        settings,
        "browser_user_data_dir_for_domain",
        lambda base_dir, _domain: base_dir,
    )
    ez_cdp.ensure_cdp_chrome(
        profile_for_domain(
            runtime_config["browser_user_data_dir"],
            runtime_config.get("shopify_domain"),
        )
    )
    app = ShopifyImageLocalizerApp()
    app.root.mainloop()


if __name__ == "__main__":
    main()
