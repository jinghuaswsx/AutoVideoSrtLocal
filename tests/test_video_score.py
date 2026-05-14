from appcore.llm_media_optimizer import OptimizedMedia


def test_score_video_optimizes_video_before_llm(tmp_path, monkeypatch):
    from pipeline import video_score as mod

    source = tmp_path / "score.mp4"
    optimized = tmp_path / "score.llm.mp4"
    source.write_bytes(b"video")
    optimized.write_bytes(b"small")
    captured = {}

    def fake_prepare(video_path, policy, output_dir=None):
        captured["policy"] = policy
        return OptimizedMedia(
            original_path=str(source),
            llm_path=str(optimized),
            optimized=True,
            cleanup_path=str(optimized),
            original_bytes=5,
            llm_bytes=5,
            command=["ffmpeg"],
            policy_name=policy.name,
        )

    def fake_invoke_generate(use_case, **kwargs):
        captured["use_case"] = use_case
        captured["kwargs"] = kwargs
        return {
            "json": {
                "summary": "ok",
                "dimensions": [{"key": "hook", "score": 10, "comment": "good"}],
                "suggestions": ["keep"],
            }
        }

    monkeypatch.setattr(mod, "prepare_video_for_llm", fake_prepare)
    monkeypatch.setattr("appcore.llm_client.invoke_generate", fake_invoke_generate)

    result = mod.score_video(source, user_id=3, project_id="proj-3")

    assert captured["policy"].name == "review_480p_audio"
    assert captured["kwargs"]["media"] == [str(optimized)]
    assert result["_video_optimization"]["original_video_path"] == str(source)
    assert result["_video_optimization"]["llm_video_path"] == str(optimized)
