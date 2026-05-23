from __future__ import annotations

from typing import TYPE_CHECKING
from .omni_profile import OmniProfile

if TYPE_CHECKING:
    from appcore.runtime import PipelineRunner


class OmniV2Profile(OmniProfile):
    code = "omni_v2"
    name = "全能实验V2（优化版）"

    def tts(self, runner: "PipelineRunner", task_id: str, task_dir: str) -> None:
        cfg = runner._resolve_plugin_config(task_id)
        if cfg.get("tts_strategy") == "sentence_reconcile":
            from appcore.tts_strategies.sentence_reconcile_v2 import SentenceReconcileStrategyV2
            strategy = SentenceReconcileStrategyV2()
            strategy.run(runner, self, task_id, task_dir)
        else:
            from appcore.tts_strategies import get_strategy
            strategy = get_strategy(cfg["tts_strategy"])
            strategy.run(runner, self, task_id, task_dir)
