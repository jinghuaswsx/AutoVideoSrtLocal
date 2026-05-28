from __future__ import annotations

from appcore.permissions import (
    PERMISSION_CODES,
    PERMISSION_META,
    ROLE_TRANSLATOR,
    default_permissions_for_role,
)


def test_dialogue_translate_permission_registered():
    assert "dialogue_translate" in PERMISSION_CODES
    assert PERMISSION_META["dialogue_translate"]["group"] == "business"
    assert PERMISSION_META["dialogue_translate"]["label"] == "对话式视频翻译"


def test_translator_role_defaults_include_dialogue_translate():
    perms = default_permissions_for_role(ROLE_TRANSLATOR)

    assert perms["dialogue_translate"] is True
