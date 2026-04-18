from unittest.mock import patch

from appcore import llm_prompt_configs as dao


def test_upsert_and_get():
    with patch("appcore.llm_prompt_configs.query_one") as m_one, \
         patch("appcore.llm_prompt_configs.execute") as m_exec:
        m_one.return_value = None
        dao.upsert("base_translation", "de",
                   provider="openrouter", model="gpt-4o-mini",
                   content="You are a German creator", updated_by=1)
        m_exec.assert_called_once()
        sql = m_exec.call_args.args[0]
        assert "INSERT INTO llm_prompt_configs" in sql
        assert "ON DUPLICATE KEY UPDATE" in sql


def test_resolve_prompt_config_hits_db():
    row = {
        "slot": "base_translation", "lang": "de",
        "model_provider": "openrouter", "model_name": "gpt-4o-mini",
        "content": "content-from-db", "enabled": 1,
    }
    with patch("appcore.llm_prompt_configs.query_one", return_value=row):
        cfg = dao.resolve_prompt_config("base_translation", "de")
    assert cfg == {
        "provider": "openrouter",
        "model": "gpt-4o-mini",
        "content": "content-from-db",
    }


def test_resolve_prompt_config_fallback_to_defaults_and_seeds():
    with patch("appcore.llm_prompt_configs.query_one", return_value=None), \
         patch("appcore.llm_prompt_configs.execute") as m_exec, \
         patch("appcore.llm_prompt_configs._get_default",
               return_value={"provider": "openrouter", "model": "dflt",
                             "content": "dflt-content"}):
        cfg = dao.resolve_prompt_config("base_translation", "de")
    assert cfg["provider"] == "openrouter"
    assert cfg["content"] == "dflt-content"
    m_exec.assert_called_once()


def test_resolve_ecommerce_plugin_lang_is_null():
    with patch("appcore.llm_prompt_configs.query_one") as m_one:
        m_one.return_value = {
            "slot": "ecommerce_plugin", "lang": None,
            "model_provider": "openrouter", "model_name": "gpt-4o-mini",
            "content": "plugin", "enabled": 1,
        }
        cfg = dao.resolve_prompt_config("ecommerce_plugin", None)
    sql = m_one.call_args.args[0]
    assert "lang IS NULL" in sql
    assert cfg["content"] == "plugin"
