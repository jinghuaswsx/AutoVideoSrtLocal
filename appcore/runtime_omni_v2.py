from __future__ import annotations

import logging
from appcore.runtime_omni import OmniTranslateRunner

log = logging.getLogger(__name__)


class OmniV2TranslateRunner(OmniTranslateRunner):
    """Multi-source-language video translation runner V2 (Optimized)."""

    project_type: str = "omni_translate_v2"
    profile_code: str = "omni_v2"

    def _resolve_plugin_config(self, task_id: str) -> dict:
        from appcore import task_state
        from appcore.omni_v2_config import stored_or_fixed_plugin_config

        task = task_state.get(task_id) or {}
        return stored_or_fixed_plugin_config(task)

    def _resolve_plugin_config_for_task_state(self, task: dict | None) -> dict:
        from appcore.omni_v2_config import stored_or_fixed_plugin_config

        return stored_or_fixed_plugin_config(task)
