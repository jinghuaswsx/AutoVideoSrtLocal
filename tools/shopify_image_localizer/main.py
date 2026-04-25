from __future__ import annotations

from tools.shopify_image_localizer import settings
from tools.shopify_image_localizer.browser import session
from tools.shopify_image_localizer.gui import ShopifyImageLocalizerApp


def main() -> None:
    runtime_config = settings.load_runtime_config()
    session.kill_chrome_for_profile(runtime_config["browser_user_data_dir"])
    app = ShopifyImageLocalizerApp()
    app.root.mainloop()


if __name__ == "__main__":
    main()
