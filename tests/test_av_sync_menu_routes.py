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


def test_dashboard_sidebar_prioritizes_primary_translation_entries(
    authed_client_no_db,
):
    resp = authed_client_no_db.get("/video-translate-av-sync")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    nav_html = html[html.index('<nav class="sidebar-nav">'):html.index("</nav>")]

    medias_idx = nav_html.index('href="/medias"')
    multi_translate_idx = nav_html.index('href="/multi-translate"')
    title_translate_idx = nav_html.index('href="/title-translate"')
    image_translate_idx = nav_html.index('href="/image-translate"')
    subtitle_removal_idx = nav_html.index('href="/subtitle-removal"')
    mk_selection_idx = nav_html.index('href="/medias/mk-selection"')
    pushes_idx = nav_html.index('href="/pushes"')

    assert medias_idx < multi_translate_idx < title_translate_idx
    assert title_translate_idx < image_translate_idx < subtitle_removal_idx
    assert subtitle_removal_idx < mk_selection_idx
    assert subtitle_removal_idx < pushes_idx


def test_dashboard_sidebar_moves_lab_group_to_bottom(authed_client_no_db):
    resp = authed_client_no_db.get("/video-translate-av-sync")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    nav_html = html[html.index('<nav class="sidebar-nav">'):html.index("</nav>")]

    lab_group_marker = '<details class="sidebar-group sidebar-lab-group"'
    lab_group_idx = nav_html.index(lab_group_marker)
    video_translate_idx = nav_html.index('href="/"')
    order_analytics_idx = nav_html.index('href="/order-analytics"')
    voice_library_idx = nav_html.index('href="/voice-library"')
    prompt_library_idx = nav_html.index('href="/prompt-library"')
    copywriting_idx = nav_html.index('href="/copywriting"')
    text_translate_idx = nav_html.index('href="/text-translate"')
    video_creation_idx = nav_html.index('href="/video-creation"')
    video_review_idx = nav_html.index('href="/video-review"')
    link_check_idx = nav_html.index('href="/link-check"')
    translate_lab_idx = nav_html.index('href="/translate-lab"')
    ja_translate_idx = nav_html.index('href="/ja-translate"')
    av_sync_idx = nav_html.index('href="/video-translate-av-sync"')

    assert "实验室" in nav_html
    assert lab_group_idx > order_analytics_idx > video_translate_idx
    assert lab_group_idx == nav_html.rfind(lab_group_marker)
    assert nav_html.index("<details") > nav_html.rfind('<a href="/order-analytics"')
    assert voice_library_idx > lab_group_idx
    assert voice_library_idx < prompt_library_idx < copywriting_idx
    assert copywriting_idx < text_translate_idx < video_creation_idx
    assert video_creation_idx < video_review_idx < link_check_idx
    assert link_check_idx < translate_lab_idx < ja_translate_idx < av_sync_idx
    assert '<details class="sidebar-group sidebar-lab-group" open' not in nav_html


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
