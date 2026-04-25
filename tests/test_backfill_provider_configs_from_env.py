"""Tests for scripts.backfill_provider_configs_from_env."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from appcore.llm_provider_configs import LlmProviderConfig
from scripts import backfill_provider_configs_from_env as backfill


def _empty_cfg(code: str) -> LlmProviderConfig:
    return LlmProviderConfig(
        provider_code=code, display_name=code, group_code="image", api_key=None,
    )


def _filled_cfg(code: str, key: str = "existing-key") -> LlmProviderConfig:
    return LlmProviderConfig(
        provider_code=code, display_name=code, group_code="image", api_key=key,
    )


def test_backfill_writes_when_env_present_and_db_empty() -> None:
    env = {"APIMART_IMAGE_API_KEY": "sk-from-env"}
    with patch.object(
        backfill, "get_provider_config",
        side_effect=lambda code: _empty_cfg(code),
    ), patch.object(backfill, "save_provider_config") as mock_save:
        result = backfill.backfill(env=env)

    assert ("apimart_image", "APIMART_IMAGE_API_KEY") in result["backfilled"]
    mock_save.assert_any_call(
        "apimart_image", {"api_key": "sk-from-env"}, updated_by=None,
    )


def test_backfill_skips_when_db_already_filled() -> None:
    env = {"APIMART_IMAGE_API_KEY": "sk-from-env"}
    with patch.object(
        backfill, "get_provider_config",
        side_effect=lambda code: _filled_cfg(code, "already-set"),
    ), patch.object(backfill, "save_provider_config") as mock_save:
        result = backfill.backfill(env=env)

    assert "apimart_image" in result["skipped_db_filled"]
    for call in mock_save.mock_calls:
        assert call.args[0] != "apimart_image"


def test_backfill_skips_when_no_env_candidate() -> None:
    with patch.object(
        backfill, "get_provider_config",
        side_effect=lambda code: _empty_cfg(code),
    ), patch.object(backfill, "save_provider_config") as mock_save:
        result = backfill.backfill(env={})

    assert "apimart_image" in result["skipped_no_env"]
    mock_save.assert_not_called()


def test_backfill_uses_fallback_env_for_doubao_seedance() -> None:
    """DOUBAO_LLM_API_KEY 兜底 VOLC_API_KEY；SEEDANCE_API_KEY 进一步兜底前者。"""
    env = {"VOLC_API_KEY": "volc-key"}  # only VOLC set
    saved: dict[str, str] = {}

    def _save(code: str, fields: dict, updated_by) -> None:  # noqa: ARG001
        saved[code] = fields["api_key"]

    with patch.object(
        backfill, "get_provider_config",
        side_effect=lambda code: _empty_cfg(code),
    ), patch.object(backfill, "save_provider_config", side_effect=_save):
        backfill.backfill(env=env)

    assert saved.get("doubao_llm") == "volc-key"
    assert saved.get("doubao_asr") == "volc-key"
    assert saved.get("seedance_video") == "volc-key"


def test_backfill_prefers_specific_over_fallback() -> None:
    """优先用更具体的变量；具体变量为空时再回落。"""
    env = {
        "DOUBAO_LLM_API_KEY": "doubao-specific",
        "VOLC_API_KEY":       "volc-fallback",
        "SEEDANCE_API_KEY":   "seedance-specific",
    }
    saved: dict[str, str] = {}

    def _save(code: str, fields: dict, updated_by) -> None:  # noqa: ARG001
        saved[code] = fields["api_key"]

    with patch.object(
        backfill, "get_provider_config",
        side_effect=lambda code: _empty_cfg(code),
    ), patch.object(backfill, "save_provider_config", side_effect=_save):
        backfill.backfill(env=env)

    assert saved["doubao_llm"] == "doubao-specific"
    assert saved["doubao_asr"] == "volc-fallback"
    assert saved["seedance_video"] == "seedance-specific"


def test_backfill_treats_whitespace_only_db_value_as_empty() -> None:
    env = {"APIMART_IMAGE_API_KEY": "sk-real"}
    saves: list[tuple[str, dict]] = []

    def _get(code: str):
        if code == "apimart_image":
            return LlmProviderConfig(
                provider_code=code, display_name=code, group_code="image",
                api_key="   ",  # whitespace only
            )
        return _empty_cfg(code)

    def _save(code: str, fields: dict, updated_by) -> None:  # noqa: ARG001
        saves.append((code, fields))

    with patch.object(backfill, "get_provider_config", side_effect=_get), \
         patch.object(backfill, "save_provider_config", side_effect=_save):
        backfill.backfill(env=env)

    apimart_saves = [f for code, f in saves if code == "apimart_image"]
    assert apimart_saves == [{"api_key": "sk-real"}]


def test_backfill_returns_no_doubao_seedream_in_no_env_when_not_in_candidates() -> None:
    """doubao_seedream 不在候选表里，理论上不会出现在 skipped_no_env。"""
    with patch.object(
        backfill, "get_provider_config",
        side_effect=lambda code: _empty_cfg(code),
    ), patch.object(backfill, "save_provider_config"):
        result = backfill.backfill(env={})

    assert "doubao_seedream" not in result["skipped_no_env"]
    assert "doubao_seedream" not in result["skipped_db_filled"]
