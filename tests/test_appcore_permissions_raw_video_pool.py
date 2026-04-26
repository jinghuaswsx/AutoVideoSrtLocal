from appcore.permissions import (
    PERMISSION_CODES, default_permissions_for_role,
    ROLE_ADMIN, ROLE_USER, ROLE_SUPERADMIN,
)


def test_raw_video_pool_in_codes():
    assert "raw_video_pool" in PERMISSION_CODES


def test_raw_video_pool_admin_default_true():
    assert default_permissions_for_role(ROLE_ADMIN)["raw_video_pool"] is True


def test_raw_video_pool_user_default_true():
    # 处理人是 role=user + can_process_raw_video=true，菜单本身允许 user 看
    assert default_permissions_for_role(ROLE_USER)["raw_video_pool"] is True


def test_raw_video_pool_superadmin_true():
    assert default_permissions_for_role(ROLE_SUPERADMIN)["raw_video_pool"] is True
