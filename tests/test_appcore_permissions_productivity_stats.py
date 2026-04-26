from appcore.permissions import (
    PERMISSION_CODES, default_permissions_for_role,
    ROLE_ADMIN, ROLE_USER, ROLE_SUPERADMIN,
)


def test_productivity_stats_in_codes():
    assert "productivity_stats" in PERMISSION_CODES


def test_admin_default_true():
    assert default_permissions_for_role(ROLE_ADMIN)["productivity_stats"] is True


def test_user_default_false():
    assert default_permissions_for_role(ROLE_USER)["productivity_stats"] is False


def test_superadmin_true():
    assert default_permissions_for_role(ROLE_SUPERADMIN)["productivity_stats"] is True
