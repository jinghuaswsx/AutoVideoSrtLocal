from __future__ import annotations

import base64

from web.services.task_voice_rematch import rematch_task_voice


def test_rematch_task_voice_matches_candidates_excluding_default_voice():
    updates = []
    match_calls = []
    embedding = base64.b64encode(b"embedding").decode("ascii")

    def match_candidates(vec, **kwargs):
        match_calls.append((vec, kwargs))
        return [
            {"voice_id": "voice-a", "similarity": "0.92"},
            {"voice_id": "voice-b", "similarity": 0.81},
        ]

    outcome = rematch_task_voice(
        "task-1",
        {"target_lang": "de", "voice_match_query_embedding": embedding},
        {"gender": "female"},
        user_id=7,
        deserialize_embedding=lambda raw: {"raw": raw},
        resolve_default_voice=lambda lang, user_id: "default-voice",
        match_voice_candidates=match_candidates,
        fetch_voices_by_ids=lambda language, voice_ids: [{"voice_id": voice_ids[0]}],
        update_task=lambda *args, **kwargs: updates.append((args, kwargs)),
    )

    assert outcome.status_code == 200
    assert outcome.payload["ok"] is True
    assert outcome.payload["gender"] == "female"
    assert outcome.payload["candidates"][0]["similarity"] == 0.92
    assert outcome.payload["extra_items"] == [{"voice_id": "voice-a"}]
    assert match_calls == [
        (
            {"raw": b"embedding"},
            {
                "language": "de",
                "gender": "female",
                "top_k": 10,
                "exclude_voice_ids": {"default-voice"},
            },
        )
    ]
    assert updates == [(("task-1",), {"voice_match_candidates": outcome.payload["candidates"]})]


def test_rematch_task_voice_rejects_invalid_state_without_writes():
    updates = []

    no_lang = rematch_task_voice(
        "task-1",
        {"voice_match_query_embedding": base64.b64encode(b"embedding").decode("ascii")},
        {},
        user_id=7,
        update_task=lambda *args, **kwargs: updates.append((args, kwargs)),
    )
    invalid_gender = rematch_task_voice(
        "task-1",
        {"target_lang": "de", "voice_match_query_embedding": base64.b64encode(b"embedding").decode("ascii")},
        {"gender": "other"},
        user_id=7,
        update_task=lambda *args, **kwargs: updates.append((args, kwargs)),
    )
    no_embedding = rematch_task_voice(
        "task-1",
        {"target_lang": "de"},
        {},
        user_id=7,
        update_task=lambda *args, **kwargs: updates.append((args, kwargs)),
    )

    assert no_lang.status_code == 400
    assert invalid_gender.status_code == 400
    assert no_embedding.status_code == 409
    assert updates == []
