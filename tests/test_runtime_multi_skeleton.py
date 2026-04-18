import pytest

from appcore.events import EventBus
from appcore.runtime_multi import MultiTranslateRunner


def test_class_attrs():
    assert MultiTranslateRunner.project_type == "multi_translate"
    assert MultiTranslateRunner.tts_model_id == "eleven_multilingual_v2"


def test_resolve_lang_from_task_state():
    runner = MultiTranslateRunner(bus=EventBus(), user_id=1)
    task = {"target_lang": "de"}
    assert runner._resolve_target_lang(task) == "de"


def test_resolve_lang_raises_when_missing():
    runner = MultiTranslateRunner(bus=EventBus(), user_id=1)
    with pytest.raises(ValueError):
        runner._resolve_target_lang({})
