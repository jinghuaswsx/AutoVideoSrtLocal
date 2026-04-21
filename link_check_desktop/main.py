from __future__ import annotations

from link_check_desktop.gui import LinkCheckApp


def main() -> None:
    app = LinkCheckApp()
    app.root.mainloop()


if __name__ == "__main__":
    main()
