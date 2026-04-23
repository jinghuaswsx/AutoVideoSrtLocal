import re
from html.parser import HTMLParser


class _SidebarNavParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._in_sidebar_nav = False
        self._sidebar_nav_depth = 0
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "nav" and attrs_dict.get("class") == "sidebar-nav":
            self._in_sidebar_nav = True
            self._sidebar_nav_depth = 1
            return

        if self._in_sidebar_nav:
            self._sidebar_nav_depth += 1
            if tag == "a":
                self.links.append(attrs_dict)

    def handle_endtag(self, tag):
        if not self._in_sidebar_nav:
            return
        self._sidebar_nav_depth -= 1
        if self._sidebar_nav_depth <= 0:
            self._in_sidebar_nav = False


def test_av_sync_menu_page_renders_shared_workbench(authed_client_no_db):
    resp = authed_client_no_db.get("/video-translate-av-sync")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "视频翻译音画同步" in html
    assert "音画同步配置" in html
    assert 'href="/video-translate-av-sync"' in html


def test_dashboard_sidebar_places_video_translate_near_sidebar_bottom(authed_client_no_db):
    resp = authed_client_no_db.get("/video-translate-av-sync")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    nav_html = html[html.index('<nav class="sidebar-nav">'):html.index("</nav>")]

    lab_idx = nav_html.index('href="/translate-lab"')
    av_sync_idx = nav_html.index('href="/video-translate-av-sync"')
    video_translate_idx = nav_html.index('href="/"')
    video_translate_anchor_idx = nav_html.rfind('<a href="/"')
    order_analytics_idx = nav_html.index('href="/order-analytics"')
    order_analytics_anchor_idx = nav_html.rfind('<a href="/order-analytics"')
    last_anchor_idx = nav_html.rfind("<a ")

    assert lab_idx < av_sync_idx < video_translate_idx < order_analytics_idx
    assert order_analytics_anchor_idx == last_anchor_idx
    assert video_translate_anchor_idx < order_analytics_anchor_idx


def test_dashboard_sidebar_menu_links_open_new_tabs(authed_client_no_db):
    resp = authed_client_no_db.get("/video-translate-av-sync")
    assert resp.status_code == 200
    parser = _SidebarNavParser()
    parser.feed(resp.get_data(as_text=True))

    assert parser.links
    for link in parser.links:
        assert link["target"] == "_blank", link.get("href")
        assert link["rel"] == "noopener noreferrer", link.get("href")


def test_dashboard_sidebar_av_sync_uses_icon_instead_of_av_text(authed_client_no_db):
    resp = authed_client_no_db.get("/video-translate-av-sync")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    match = re.search(
        r'<a href="/video-translate-av-sync"[^>]*>\s*<span class="nav-icon">([^<]+)</span>',
        html,
    )
    assert match
    assert match.group(1).strip() != "AV"


def test_dashboard_sidebar_marks_av_sync_entry_active(authed_client_no_db):
    resp = authed_client_no_db.get("/video-translate-av-sync")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert re.search(r'<a href="/video-translate-av-sync"[^>]*class="active"', html)
