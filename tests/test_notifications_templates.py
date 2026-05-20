from pathlib import Path


def test_layout_contains_notification_center_entrypoint():
    source = Path("web/templates/layout.html").read_text(encoding="utf-8")

    assert 'id="notificationCenterToggle"' in source
    assert 'id="notificationCenterBadge"' in source
    assert 'id="notificationCenterMenu"' in source
    assert "/notifications/api/summary" in source
    assert "/notifications/api/list" in source
    assert "/notifications/api/${encodeURIComponent" in source
    assert "X-CSRFToken" in source
    assert 'aria-label="消息中心"' in source
