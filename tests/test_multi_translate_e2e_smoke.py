"""多语种视频翻译端到端烟雾测试（heavy mocked）。

验证 _step_voice_match → _step_translate 主链路不抛异常，
且 task_state 里写入了 voice_match_candidates 和 localized_translation。
"""
from unittest.mock import patch

import numpy as np


def test_smoke_de_pipeline_doesnt_crash():
    import appcore.task_state as task_state
    from appcore.events import EventBus
    from appcore.runtime_multi import MultiTranslateRunner

    task_id = "smoke_de_" + "x" * 8
    # 直接写入内存 task state（不走 DB）
    task_state._tasks[task_id] = {
        "task_id": task_id,
        "task_dir": "/tmp/smoke",
        "target_lang": "de",
        "source_language": "en",
        "script_segments": [{"index": 0, "text": "hello"}],
        "utterances": [{"start_time": 0, "end_time": 10, "text": "hello"}],
        "video_path": "/tmp/smoke/src.mp4",
        "interactive_review": False,
        "variants": {},
        "steps": {},
        "step_messages": {},
    }

    runner = MultiTranslateRunner(bus=EventBus(), user_id=1)

    patches = [
        patch(
            "appcore.runtime_multi.resolve_prompt_config",
            side_effect=lambda slot, lang: {
                "provider": "openrouter",
                "model": "gpt",
                "content": f"{slot}-{lang}",
            },
        ),
        patch(
            "appcore.runtime_multi.generate_localized_translation",
            return_value={
                "full_text": "Hallo",
                "sentences": [
                    {"index": 0, "text": "Hallo", "source_segment_indices": [0]}
                ],
                "_usage": {},
            },
        ),
        patch("appcore.runtime_multi._resolve_translate_provider", return_value="openrouter"),
        patch("appcore.runtime_multi.get_model_display_name", return_value="gpt"),
        patch("appcore.runtime_multi._save_json"),
        patch("appcore.runtime_multi._log_usage"),
        patch("appcore.runtime_multi._build_review_segments", return_value=[]),
        patch("appcore.runtime_multi.build_asr_artifact", return_value={}),
        patch("appcore.runtime_multi.build_translate_artifact", return_value={}),
        patch(
            "appcore.runtime_multi.extract_sample_from_utterances",
            return_value="/tmp/smoke/clip.wav",
        ),
        patch(
            "appcore.runtime_multi.embed_audio_file",
            return_value=np.zeros(256, dtype=np.float32),
        ),
        patch(
            "appcore.runtime_multi.match_candidates",
            return_value=[
                {
                    "voice_id": "v1",
                    "name": "A",
                    "similarity": 0.8,
                    "gender": "male",
                    "preview_url": "u",
                }
            ],
        ),
    ]
    for p in patches:
        p.start()
    try:
        runner._step_voice_match(task_id)
        runner._step_translate(task_id)
    finally:
        for p in patches:
            p.stop()

    task = task_state.get(task_id)
    assert task.get("voice_match_candidates")[0]["voice_id"] == "v1"
    assert task.get("localized_translation")["full_text"] == "Hallo"
    task_state._tasks.pop(task_id, None)
