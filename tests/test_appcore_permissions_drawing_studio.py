from appcore.permissions import (
    PERMISSION_CODES,
    grouped_permissions,
)


def test_drawing_studio_is_not_a_configurable_menu_permission():
    assert "drawing_studio" not in PERMISSION_CODES
    labels = [
        item["label"]
        for _group_code, _group_label, items in grouped_permissions()
        for item in items
    ]
    assert "画图工作室" not in labels
