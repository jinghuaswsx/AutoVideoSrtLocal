import re
from html.parser import HTMLParser
from pathlib import Path


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
    resp = authed_client_no_db.get("/video-translate-av-sync", follow_redirects=True)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "视频翻译音画同步" in html
    assert 'href="/video-translate-av-sync"' in html


def test_av_sync_menu_page_uses_multilingual_list_shell(authed_client_no_db):
    resp = authed_client_no_db.get("/video-translate-av-sync", follow_redirects=True)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="viewGrid"' in html
    assert 'id="modalLangPills"' in html
    assert 'fetch("/api/tasks"' in html
    assert 'var fallbackUrl = "/sentence_translate" + \'/\' + encodeURIComponent(data.task_id || \'\');' in html
    assert "音画同步配置" not in html


def test_dashboard_sidebar_prioritizes_primary_translation_entries(
    authed_client_no_db,
):
    resp = authed_client_no_db.get("/video-translate-av-sync", follow_redirects=True)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    nav_html = html[html.index('<nav class="sidebar-nav">'):html.index("</nav>")]

    medias_idx = nav_html.index('href="/medias"')
    pushes_idx = nav_html.index('href="/pushes"')
    data_group_idx = nav_html.index("sidebar-data-dashboard-group")
    material_group_idx = nav_html.index("sidebar-material-creation-group")
    video_group_idx = nav_html.index("sidebar-video-translate-group")
    order_analytics_idx = nav_html.index('href="/order-analytics"')
    product_profit_idx = nav_html.index('href="/product-profit"')
    order_profit_idx = nav_html.index('href="/order-profit"')
    multi_translate_idx = nav_html.index('href="/multi-translate"')
    omni_translate_idx = nav_html.index('href="/omni-translate"')
    title_translate_idx = nav_html.index('href="/title-translate"')
    image_translate_idx = nav_html.index('href="/image-translate"')
    subtitle_removal_idx = nav_html.index('href="/subtitle-removal"')
    mk_selection_idx = nav_html.index('href="/xuanpin/mk"')
    task_group_idx = nav_html.index("sidebar-task-group")
    settings_group_idx = nav_html.index("sidebar-settings-group")
    lab_group_idx = nav_html.index("sidebar-lab-group")

    assert medias_idx < pushes_idx < data_group_idx < material_group_idx < video_group_idx
    assert data_group_idx < order_analytics_idx < product_profit_idx < order_profit_idx
    assert material_group_idx < image_translate_idx < subtitle_removal_idx < title_translate_idx
    assert video_group_idx < multi_translate_idx < omni_translate_idx
    assert video_group_idx < mk_selection_idx < task_group_idx
    assert task_group_idx < settings_group_idx < lab_group_idx


def test_dashboard_sidebar_moves_lab_group_to_bottom():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "layout.html").read_text(encoding="utf-8")
    nav_html = template[template.index('<nav class="sidebar-nav">'):template.index("</nav>")]

    lab_group_marker = '<details class="sidebar-group sidebar-lab-group"'
    lab_group_idx = nav_html.index(lab_group_marker)
    video_translate_idx = nav_html.index("url_for('projects.index')")
    settings_group_idx = nav_html.index('<details class="sidebar-group sidebar-settings-group"')
    voice_library_idx = nav_html.index('href="/voice-library"')
    prompt_library_idx = nav_html.index('href="/prompt-library"')
    copywriting_idx = nav_html.index('href="/copywriting"')
    video_creation_idx = nav_html.index('href="/video-creation"')
    video_review_idx = nav_html.index('href="/video-review"')
    link_check_idx = nav_html.index("url_for('link_check.page')")
    av_sync_idx = nav_html.index("url_for('projects.av_sync_page')")

    assert "实验室" in nav_html
    assert lab_group_idx > video_translate_idx
    assert lab_group_idx > settings_group_idx
    assert lab_group_idx == nav_html.rfind(lab_group_marker)
    assert voice_library_idx > lab_group_idx
    assert voice_library_idx < prompt_library_idx < copywriting_idx
    assert copywriting_idx < video_creation_idx
    assert video_creation_idx < video_review_idx < link_check_idx < av_sync_idx
    assert '<details class="sidebar-group sidebar-lab-group" open' not in nav_html


def test_dashboard_sidebar_settings_group_includes_browser_monitor():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "layout.html").read_text(encoding="utf-8")
    nav_html = template[template.index('<nav class="sidebar-nav">'):template.index("</nav>")]

    settings_group_idx = nav_html.index('<details class="sidebar-group sidebar-settings-group"')
    lab_group_idx = nav_html.index('<details class="sidebar-group sidebar-lab-group"')
    browser_monitor_idx = nav_html.index("url_for('browser_monitor.page')", settings_group_idx)

    assert "浏览器监控" in nav_html
    assert settings_group_idx < browser_monitor_idx
    assert nav_html.index("浏览器监控", browser_monitor_idx) < lab_group_idx


def test_dashboard_sidebar_groups_task_center_entries():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "layout.html").read_text(encoding="utf-8")
    nav_html = template[template.index('<nav class="sidebar-nav">'):template.index("</nav>")]

    task_group_idx = nav_html.index('<details class="sidebar-group sidebar-task-group"')
    raw_pool_idx = nav_html.index('href="/raw-video-pool/"', task_group_idx)
    task_center_idx = nav_html.index('href="/tasks/"', task_group_idx)
    bulk_admin_idx = nav_html.index("url_for('bulk_translate_pages.admin_tasks_page')", task_group_idx)
    settings_group_idx = nav_html.index('<details class="sidebar-group sidebar-settings-group"')

    assert "原始素材任务库" in nav_html
    assert "任务中心" in nav_html
    assert "批量翻译任务管理" in nav_html
    assert task_group_idx < raw_pool_idx < task_center_idx < bulk_admin_idx < settings_group_idx
    assert 'data-default-href="{{ task_group_href }}"' in nav_html
    assert '<details class="sidebar-group sidebar-task-group" open' not in nav_html


def test_dashboard_sidebar_task_group_opens_when_child_active(authed_client_no_db):
    resp = authed_client_no_db.get("/raw-video-pool/")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert '<details class="sidebar-group sidebar-task-group" open>' in html
    assert re.search(r'<summary[^>]*class="active"[^>]*>\s*<span class="nav-icon">📋</span>', html)
    assert re.search(r'<a href="/raw-video-pool/"[^>]*class="active"', html)


def test_dashboard_sidebar_hides_offline_video_translation_entries():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "layout.html").read_text(encoding="utf-8")

    assert 'href="/translate-lab"' not in template
    assert 'href="/ja-translate"' not in template
    assert 'href="/de-translate"' not in template
    assert 'href="/fr-translate"' not in template


def test_dashboard_sidebar_menu_links_open_new_tabs(authed_client_no_db):
    resp = authed_client_no_db.get("/video-translate-av-sync", follow_redirects=True)
    assert resp.status_code == 200
    parser = _SidebarNavParser()
    parser.feed(resp.get_data(as_text=True))

    assert parser.links
    for link in parser.links:
        assert link["target"] == "_blank", link.get("href")
        assert link["rel"] == "noopener noreferrer", link.get("href")


def test_dashboard_sidebar_av_sync_uses_icon_instead_of_av_text(authed_client_no_db):
    resp = authed_client_no_db.get("/video-translate-av-sync", follow_redirects=True)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    match = re.search(
        r'<a href="/video-translate-av-sync"[^>]*>\s*<span class="nav-icon">([^<]+)</span>',
        html,
    )
    assert match
    assert match.group(1).strip() != "AV"


def test_browser_monitor_menu_entry_is_active(authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.browser_monitor.scheduled_tasks.latest_run", lambda task_code: None)

    resp = authed_client_no_db.get("/browser-monitor")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert re.search(r'<a href="/browser-monitor"[^>]*class="active"', html)


def test_dashboard_sidebar_marks_av_sync_entry_active(authed_client_no_db):
    resp = authed_client_no_db.get("/video-translate-av-sync", follow_redirects=True)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert re.search(r'<a href="/video-translate-av-sync"[^>]*class="active"', html)
