from pathlib import Path
from unittest.mock import patch


def test_normalize_voice_ai_rankings_keeps_top10_unique_and_trims_reason():
    from appcore.voice_ai_ranking import (
        apply_voice_ai_rankings,
        normalize_voice_ai_rankings,
    )

    candidates = [{"voice_id": f"v{i}"} for i in range(1, 12)]
    raw = {
        "rankings": [
            {"voice_id": "v2", "llm_rank": "1", "reason_summary": "natural voice that is much too long"},
            {"voice_id": "v11", "llm_rank": 2, "reason_summary": "not in top ten"},
            {"voice_id": "v3", "llm_rank": 2, "reason_summary": "clear"},
            {"voice_id": "v2", "llm_rank": 4, "reason_summary": "duplicate"},
            {"voice_id": "", "llm_rank": 5, "reason_summary": "missing"},
        ],
    }

    rankings = normalize_voice_ai_rankings(raw, candidates)
    enriched = apply_voice_ai_rankings(candidates, rankings)

    assert [row["voice_id"] for row in rankings] == ["v2", "v3"]
    assert rankings[0]["llm_rank"] == 1
    assert len(rankings[0]["reason_summary"]) <= 30
    assert enriched[1]["llm_rank"] == 1
    assert enriched[1]["llm_reason_summary"] == rankings[0]["reason_summary"]
    assert "llm_rank" not in enriched[10]


def test_normalize_voice_ai_rankings_fills_missing_rank_from_response_order():
    from appcore.voice_ai_ranking import normalize_voice_ai_rankings

    candidates = [{"voice_id": "v1"}, {"voice_id": "v2"}, {"voice_id": "v3"}]
    raw = {
        "rankings": [
            {"voice_id": "v2"},
            {"voice_id": "v1", "reason_summary": "更贴近原声"},
        ],
    }

    rankings = normalize_voice_ai_rankings(raw, candidates)

    assert rankings == [
        {"voice_id": "v2", "llm_rank": 1, "reason_summary": "模型未给原因"},
        {"voice_id": "v1", "llm_rank": 2, "reason_summary": "更贴近原声"},
    ]


def test_normalize_voice_ai_rankings_maps_candidate_key_to_voice_id():
    from appcore.voice_ai_ranking import normalize_voice_ai_rankings

    candidates = [{"voice_id": "v1"}, {"voice_id": "v2"}, {"voice_id": "v3"}]
    raw = {
        "rankings": [
            {"candidate_key": "C3", "llm_rank": 1, "reason_summary": "best"},
            {"candidate_key": "C2", "llm_rank": 2, "reason_summary": "ok"},
        ],
    }

    rankings = normalize_voice_ai_rankings(raw, candidates)

    assert rankings == [
        {"candidate_key": "C3", "voice_id": "v3", "llm_rank": 1, "reason_summary": "best"},
        {"candidate_key": "C2", "voice_id": "v2", "llm_rank": 2, "reason_summary": "ok"},
    ]


def test_normalize_voice_ai_rankings_rebases_non_contiguous_model_ranks():
    from appcore.voice_ai_ranking import normalize_voice_ai_rankings

    candidates = [{"voice_id": "v1"}, {"voice_id": "v2"}, {"voice_id": "v3"}]
    raw = {
        "rankings": [
            {"candidate_key": "C3", "llm_rank": 2, "reason_summary": "best"},
            {"candidate_key": "C1", "llm_rank": 3, "reason_summary": "clear"},
            {"candidate_key": "C2", "llm_rank": 4, "reason_summary": "slow"},
        ],
    }

    rankings = normalize_voice_ai_rankings(raw, candidates)

    assert [(row["voice_id"], row["llm_rank"]) for row in rankings] == [
        ("v3", 1),
        ("v1", 2),
        ("v2", 3),
    ]


def test_rank_voice_candidates_invokes_openrouter_gemini_35_flash_with_audio_media(tmp_path):
    from appcore.voice_ai_ranking import rank_voice_candidates

    source = tmp_path / "source.wav"
    source.write_bytes(b"source-audio")
    candidates = [
        {"voice_id": "v1", "name": "A", "similarity": 0.91, "preview_url": "https://cdn.test/v1.mp3"},
        {"voice_id": "v2", "name": "B", "similarity": 0.89, "preview_url": "https://cdn.test/v2.mp3"},
    ]

    def fake_download(url: str, dest: Path) -> Path:
        dest.write_bytes(f"audio:{url}".encode("utf-8"))
        return dest

    trim_calls = []

    def fake_trim(src: Path, dest: Path, **kwargs) -> Path:
        trim_calls.append({"src": src, "dest": dest, **kwargs})
        dest.write_bytes(src.read_bytes())
        return dest

    with patch("appcore.voice_ai_ranking.invoke_generate") as m_generate:
        m_generate.return_value = {
            "json": {
                "rankings": [
                    {"voice_id": "v2", "llm_rank": 1, "reason_summary": "more expressive"},
                    {"voice_id": "v1", "llm_rank": 2, "reason_summary": "slightly flat"},
                ]
            }
        }

        result = rank_voice_candidates(
            task_id="task-1",
            task={"target_lang": "de", "utterances": [{"text": "hello world"}]},
            candidates=candidates,
            source_audio_path=source,
            task_dir=tmp_path,
            user_id=7,
            preview_downloader=fake_download,
            audio_trimmer=fake_trim,
        )

    assert result["status"] == "done"
    assert result["candidates"][0]["llm_rank"] == 2
    assert result["candidates"][0]["voice_ai_preview_audio_relpath"].endswith("_sample.mp3")
    assert result["candidates"][1]["llm_rank"] == 1
    assert result["rankings"][0]["voice_id"] == "v2"
    assert result["debug"]["request"]["visual"]["media"][1]["role"] == "candidate_preview"
    assert result["debug"]["request"]["raw"]["max_output_tokens"] == 4096
    assert result["debug"]["result"]["visual"]["rankings"][0]["voice_id"] == "v2"
    assert m_generate.call_args.args == ("voice_selection.assess",)
    kwargs = m_generate.call_args.kwargs
    assert kwargs["provider_override"] == "openrouter"
    assert kwargs["model_override"] == "google/gemini-3.5-flash"
    assert kwargs["project_id"] == "task-1"
    assert kwargs["user_id"] == 7
    assert len(kwargs["media"]) == 3
    assert all(str(path).endswith(".mp3") for path in kwargs["media"])
    assert '"candidate_count": 2' in kwargs["prompt"]
    assert "Return exactly 2 ranking rows" in kwargs["prompt"]
    assert '"candidate_key": "C1"' in kwargs["prompt"]
    assert "Return JSON only with rankings[]. Each row must contain candidate_key" in kwargs["prompt"]
    assert kwargs["response_schema"]["properties"]["rankings"]["items"]["required"][0] == "candidate_key"
    assert kwargs["response_schema"]["properties"]["rankings"]["minItems"] == 2
    assert kwargs["response_schema"]["properties"]["rankings"]["maxItems"] == 2
    assert kwargs["max_output_tokens"] == 4096
    assert "v1" in kwargs["prompt"]
    assert any(call.get("min_seconds") == 3.0 and call.get("max_seconds") == 10.0 for call in trim_calls)


def test_rank_voice_candidates_can_limit_smoke_run_to_top3(tmp_path):
    from appcore.voice_ai_ranking import rank_voice_candidates

    source = tmp_path / "source.wav"
    source.write_bytes(b"source-audio")
    candidates = [
        {"voice_id": f"v{i}", "name": f"Voice {i}", "similarity": 1 - i / 100}
        for i in range(1, 6)
    ]

    def fake_trim(src: Path, dest: Path, **kwargs) -> Path:
        dest.write_bytes(src.read_bytes())
        return dest

    with patch("appcore.voice_ai_ranking.invoke_generate") as m_generate:
        m_generate.return_value = {
            "json": {
                "rankings": [
                    {"voice_id": "v3"},
                    {"voice_id": "v1", "llm_rank": 2, "reason_summary": "更贴近原声"},
                ]
            }
        }
        result = rank_voice_candidates(
            task_id="task-1",
            task={"target_lang": "en", "utterances": [{"text": "hello"}]},
            candidates=candidates,
            source_audio_path=source,
            task_dir=tmp_path,
            user_id=7,
            candidate_limit=3,
            audio_trimmer=fake_trim,
        )

    kwargs = m_generate.call_args.kwargs
    assert len(result["candidates"]) == 5
    assert result["rankings"][0]["voice_id"] == "v3"
    assert result["rankings"][0]["llm_rank"] == 1
    assert result["candidates"][2]["llm_rank"] == 1
    assert result["candidates"][3].get("llm_rank") is None
    assert len(kwargs["media"]) == 1
    assert '"candidate_count": 3' in kwargs["prompt"]
    assert "v4" not in kwargs["prompt"]


def test_rank_voice_candidates_skips_when_no_candidates(tmp_path):
    from appcore.voice_ai_ranking import rank_voice_candidates

    source = tmp_path / "source.wav"
    source.write_bytes(b"source-audio")

    result = rank_voice_candidates(
        task_id="task-1",
        task={"target_lang": "de"},
        candidates=[],
        source_audio_path=source,
        task_dir=tmp_path,
        user_id=7,
    )

    assert result["status"] == "skipped"
    assert result["rankings"] == []
    assert result["candidates"] == []


def test_rank_voice_candidates_prefers_local_preview_audio_over_download(tmp_path):
    from appcore.voice_ai_ranking import rank_voice_candidates

    source = tmp_path / "source.wav"
    source.write_bytes(b"source-audio")
    local_preview = tmp_path / "cache" / "voice-a.mp3"
    local_preview.parent.mkdir()
    local_preview.write_bytes(b"local-preview")

    def fail_download(url: str, dest: Path) -> Path:
        raise AssertionError("local preview audio should be used before preview_url")

    def fake_trim(src: Path, dest: Path, **kwargs) -> Path:
        dest.write_bytes(src.read_bytes())
        return dest

    with patch("appcore.voice_ai_ranking.invoke_generate") as m_generate:
        m_generate.return_value = {
            "json": {
                "rankings": [
                    {"voice_id": "v1", "llm_rank": 1, "reason_summary": "贴近原声"},
                ]
            }
        }
        result = rank_voice_candidates(
            task_id="task-1",
            task={"target_lang": "en", "utterances": [{"text": "hello"}]},
            candidates=[{
                "voice_id": "v1",
                "similarity": 0.9,
                "preview_url": "https://cdn.test/v1.mp3",
                "local_preview_audio_path": str(local_preview),
            }],
            source_audio_path=source,
            task_dir=tmp_path,
            user_id=7,
            preview_downloader=fail_download,
            audio_trimmer=fake_trim,
        )

    assert result["status"] == "done"
    assert result["debug"]["request"]["visual"]["media"][1]["source"] == "local"
    assert result["candidates"][0]["voice_ai_preview_audio_relpath"].endswith("_sample.mp3")


def test_pick_breath_cut_seconds_prefers_silence_between_3_and_10_seconds():
    from appcore.voice_ai_ranking import _pick_breath_cut_seconds

    assert _pick_breath_cut_seconds([1.2, 4.1, 9.7], min_seconds=3, max_seconds=10) == 4.1
    assert _pick_breath_cut_seconds([1.2, 11.4], min_seconds=3, max_seconds=10) == 10


def test_prepare_audio_sample_reuses_destination_path_without_retrimming(tmp_path):
    from appcore.voice_ai_ranking import _prepare_audio_sample

    source = tmp_path / "voice_ai_ranking" / "source_sample.mp3"
    source.parent.mkdir()
    source.write_bytes(b"audio")

    def fail_trim(*args, **kwargs):
        raise AssertionError("same source/destination should not be trimmed")

    result = _prepare_audio_sample(
        source,
        source,
        min_seconds=3.0,
        max_seconds=10.0,
        audio_trimmer=fail_trim,
    )

    assert result == source
