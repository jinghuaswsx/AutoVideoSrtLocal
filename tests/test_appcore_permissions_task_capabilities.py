from appcore.permissions import (
    PERMISSION_CODES, default_permissions_for_role,
    ROLE_ADMIN, ROLE_USER, ROLE_SUPERADMIN,
)


def test_task_center_codes_present():
    for code in ("task_center", "can_process_raw_video", "can_translate"):
        assert code in PERMISSION_CODES


def test_admin_defaults_have_capabilities_on():
    perms = default_permissions_for_role(ROLE_ADMIN)
    assert perms["task_center"] is True
    assert perms["can_process_raw_video"] is True
    assert perms["can_translate"] is True


def test_user_defaults_have_capabilities_off():
    perms = default_permissions_for_role(ROLE_USER)
    assert perms["task_center"] is True            # 菜单可见，看到的内容由后端过滤
    assert perms["can_process_raw_video"] is False
    assert perms["can_translate"] is False


def test_superadmin_always_full():
    perms = default_permissions_for_role(ROLE_SUPERADMIN)
    for code in ("task_center", "can_process_raw_video", "can_translate"):
        assert perms[code] is True
