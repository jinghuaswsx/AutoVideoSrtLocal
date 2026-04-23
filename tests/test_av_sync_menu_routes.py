import re


def test_av_sync_menu_page_renders_shared_workbench(authed_client_no_db):
    resp = authed_client_no_db.get("/video-translate-av-sync")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "视频翻译音画同步" in html
    assert "音画同步配置" in html
    assert 'href="/video-translate-av-sync"' in html


def test_dashboard_sidebar_places_video_translate_at_sidebar_bottom(authed_client_no_db):
    resp = authed_client_no_db.get("/video-translate-av-sync")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    nav_html = html[html.index('<nav class="sidebar-nav">'):html.index("</nav>")]

    lab_idx = nav_html.index('href="/translate-lab"')
    av_sync_idx = nav_html.index('href="/video-translate-av-sync"')
    video_translate_idx = nav_html.index('href="/"')
    video_translate_anchor_idx = nav_html.rfind('<a href="/"')
    last_anchor_idx = nav_html.rfind("<a ")

    assert lab_idx < av_sync_idx < video_translate_idx
    assert video_translate_anchor_idx == last_anchor_idx


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
