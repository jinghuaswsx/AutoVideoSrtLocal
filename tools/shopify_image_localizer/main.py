from __future__ import annotations

from tools.shopify_image_localizer import settings
from tools.shopify_image_localizer.browser import session
from tools.shopify_image_localizer.gui import ShopifyImageLocalizerApp


def main() -> None:
    runtime_config = settings.load_runtime_config()
    profile_for_domain = getattr(
        settings,
        "browser_user_data_dir_for_domain",
        lambda base_dir, _domain: base_dir,
    )
    session.kill_chrome_for_profile(
        profile_for_domain(
            runtime_config["browser_user_data_dir"],
            runtime_config.get("shopify_domain"),
        )
    )
    app = ShopifyImageLocalizerApp()
    app.root.mainloop()


if __name__ == "__main__":
    main()
