from appcore.permissions import (
    PERMISSION_CODES,
    ROLE_ADMIN,
    ROLE_SUPERADMIN,
    ROLE_USER,
    default_permissions_for_role,
)


def test_drawing_studio_permission_is_registered():
    assert "drawing_studio" in PERMISSION_CODES


def test_drawing_studio_defaults_on_for_admin_and_user():
    assert default_permissions_for_role(ROLE_ADMIN)["drawing_studio"] is True
    assert default_permissions_for_role(ROLE_USER)["drawing_studio"] is True
    assert default_permissions_for_role(ROLE_SUPERADMIN)["drawing_studio"] is True
