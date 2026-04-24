from __future__ import annotations

from tools.shopify_image_localizer.gui import ShopifyImageLocalizerApp


def main() -> None:
    app = ShopifyImageLocalizerApp()
    app.root.mainloop()


if __name__ == "__main__":
    main()
