from types import SimpleNamespace

from appcore import gemini


def test_build_config_adds_google_search_tool():
    cfg = gemini._build_config(
        system=None,
        temperature=1.0,
        response_schema=None,
        max_output_tokens=1024,
        enable_google_search=True,
    )

    payload = cfg.model_dump(exclude_none=True)
    assert payload["tools"] == [{"google_search": {}}]


def test_build_config_omits_schema_when_google_search_is_enabled():
    cfg = gemini._build_config(
        system=None,
        temperature=None,
        response_schema={"type": "object"},
        max_output_tokens=None,
        enable_google_search=True,
    )

    payload = cfg.model_dump(exclude_none=True)
    assert payload["tools"] == [{"google_search": {}}]
    assert "response_mime_type" not in payload
    assert "response_schema" not in payload


def test_extract_grounding_metadata_from_response_candidate():
    metadata = SimpleNamespace(
        web_search_queries=["query one"],
        grounding_chunks=[SimpleNamespace(web=SimpleNamespace(uri="https://example.com"))],
    )
    resp = SimpleNamespace(candidates=[SimpleNamespace(grounding_metadata=metadata)])

    assert gemini._extract_grounding_metadata(resp) == {
        "web_search_queries": ["query one"],
        "grounding_chunks": [{"web": {"uri": "https://example.com"}}],
    }


def test_to_part_inlines_small_video_without_files_api(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"small-video")
    client = SimpleNamespace(
        files=SimpleNamespace(
            upload=lambda **kwargs: (_ for _ in ()).throw(AssertionError("no upload")),
        )
    )

    part = gemini._to_part(client, video)
    payload = part.model_dump(exclude_none=True)

    assert payload["inline_data"]["mime_type"] == "video/mp4"
    assert payload["inline_data"]["data"] == b"small-video"
