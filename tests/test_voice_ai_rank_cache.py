from appcore.voice_ai_rank_cache import (
    apply_cached_rank_result,
    cache_rank_result,
    candidate_signature,
    derive_rank_result_from_all_cache,
    get_cached_rank_result,
    normalize_rank_condition,
)


def test_voice_ai_rank_cache_keys_are_limited_to_all_male_female():
    assert normalize_rank_condition(None) == "all"
    assert normalize_rank_condition("") == "all"
    assert normalize_rank_condition("male") == "male"
    assert normalize_rank_condition("female") == "female"
    assert normalize_rank_condition("other") == "all"


def test_voice_ai_rank_cache_applies_only_matching_candidate_signature():
    candidates = [{"voice_id": "v1", "similarity": 0.91, "speed_match_score": 0.8}]
    ranked_candidates = [
        {
            "voice_id": "v1",
            "similarity": 0.91,
            "speed_match_score": 0.8,
            "llm_rank": 1,
            "llm_reason_summary": "贴近原声",
        }
    ]
    state = {}

    entry = cache_rank_result(
        state,
        key="female",
        candidates=ranked_candidates,
        rankings=[{"voice_id": "v1", "llm_rank": 1, "reason_summary": "贴近原声"}],
        status="done",
        model="google/gemini-3.5-flash",
        provider="openrouter",
        debug={"status": "done"},
        candidate_limit=3,
    )

    assert entry["candidate_signature"] == candidate_signature(candidates)
    assert get_cached_rank_result(state, "female", candidates)["candidates"][0]["llm_rank"] == 1
    assert get_cached_rank_result(state, "male", candidates) is None
    assert get_cached_rank_result(state, "female", [{"voice_id": "v2", "similarity": 0.8}]) is None


def test_apply_cached_rank_result_updates_active_top_level_fields():
    state = {}
    entry = {
        "candidate_signature": "sig",
        "candidates": [{"voice_id": "v1", "llm_rank": 2}],
        "rankings": [{"voice_id": "v1", "llm_rank": 2, "reason_summary": "ok"}],
        "status": "done",
        "model": "model",
        "provider": "provider",
        "debug": {"status": "done"},
        "candidate_limit": 3,
    }

    apply_cached_rank_result(state, "male", entry)

    assert state["voice_ai_rank_active_key"] == "male"
    assert state["voice_match_candidates"][0]["llm_rank"] == 2
    assert state["voice_ai_rankings"][0]["llm_rank"] == 2
    assert state["voice_ai_rank_debug"]["status"] == "done"
    assert state["voice_ai_rank_candidate_limit"] == 3


def test_derive_rank_result_from_all_cache_filters_and_rebases_current_gender_candidates():
    state = {}
    cache_rank_result(
        state,
        key="all",
        candidates=[
            {"voice_id": "m1", "llm_rank": 1, "llm_reason_summary": "男声第一"},
            {"voice_id": "f1", "llm_rank": 2, "llm_reason_summary": "女声第一"},
            {"voice_id": "m2", "llm_rank": 3, "llm_reason_summary": "男声第二"},
            {"voice_id": "f2", "llm_rank": 4, "llm_reason_summary": "女声第二"},
        ],
        rankings=[
            {"voice_id": "m1", "llm_rank": 1, "reason_summary": "男声第一"},
            {"voice_id": "f1", "llm_rank": 2, "reason_summary": "女声第一"},
            {"voice_id": "m2", "llm_rank": 3, "reason_summary": "男声第二"},
            {"voice_id": "f2", "llm_rank": 4, "reason_summary": "女声第二"},
        ],
        status="done",
        model="model",
        provider="provider",
        debug={"result": {"visual": {"rankings": []}}},
    )

    derived = derive_rank_result_from_all_cache(
        state,
        key="female",
        candidates=[
            {"voice_id": "f1", "similarity": 0.9},
            {"voice_id": "f2", "similarity": 0.8},
            {"voice_id": "f3", "similarity": 0.7},
        ],
    )

    assert derived is not None
    assert [(row["voice_id"], row["llm_rank"]) for row in derived["rankings"]] == [
        ("f1", 1),
        ("f2", 2),
    ]
    assert [(row["voice_id"], row.get("llm_rank")) for row in derived["candidates"]] == [
        ("f1", 1),
        ("f2", 2),
        ("f3", None),
    ]
    assert derived["status"] == "derived_from_all"
    assert derived["source"] == "derived_from_all"
