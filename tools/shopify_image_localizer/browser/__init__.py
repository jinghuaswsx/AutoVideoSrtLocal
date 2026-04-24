from __future__ import annotations

__all__: list[str] = []


def __getattr__(name: str):
    if name == "run_shopify_localizer":
        from tools.shopify_image_localizer.browser.orchestrator import run_shopify_localizer

        return run_shopify_localizer
    raise AttributeError(name)
