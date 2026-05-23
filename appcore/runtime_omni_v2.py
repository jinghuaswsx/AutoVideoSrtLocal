from __future__ import annotations

import logging
from appcore.runtime_omni import OmniTranslateRunner

log = logging.getLogger(__name__)


class OmniV2TranslateRunner(OmniTranslateRunner):
    """Multi-source-language video translation runner V2 (Optimized)."""

    project_type: str = "omni_translate_v2"
    profile_code: str = "omni_v2"
