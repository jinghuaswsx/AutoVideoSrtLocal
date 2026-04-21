import re


def test_av_sync_menu_page_renders_shared_workbench(authed_client_no_db):
    resp = authed_client_no_db.get("/video-translate-av-sync")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "视频翻译音画同步" in html
    assert "音画同步配置" in html
    assert 'href="/video-translate-av-sync"' in html


def test_dashboard_sidebar_places_av_sync_below_video_translate(authed_client_no_db):
    resp = authed_client_no_db.get("/video-translate-av-sync")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    video_idx = html.index('href="/"')
    av_sync_idx = html.index('href="/video-translate-av-sync"')
    multi_idx = html.index('href="/multi-translate"')

    assert video_idx < av_sync_idx < multi_idx


def test_dashboard_sidebar_marks_av_sync_entry_active(authed_client_no_db):
    resp = authed_client_no_db.get("/video-translate-av-sync")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert re.search(r'<a href="/video-translate-av-sync"[^>]*class="active"', html)
