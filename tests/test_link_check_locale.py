def test_detect_target_language_from_plain_locale_segment():
    from appcore.link_check_locale import detect_target_language_from_url

    assert detect_target_language_from_url(
        "https://newjoyloo.com/fr/products/demo?variant=1",
        {"de", "fr", "ja"},
    ) == "fr"


def test_detect_target_language_falls_back_to_primary_subtag():
    from appcore.link_check_locale import detect_target_language_from_url

    assert detect_target_language_from_url(
        "https://newjoyloo.com/fr-fr/products/demo",
        {"de", "fr", "ja"},
    ) == "fr"


def test_detect_target_language_returns_empty_when_segment_not_enabled():
    from appcore.link_check_locale import detect_target_language_from_url

    assert detect_target_language_from_url(
        "https://newjoyloo.com/es/products/demo",
        {"de", "fr", "ja"},
    ) == ""


def test_build_display_name_prefers_product_handle_and_language():
    from appcore.link_check_locale import build_link_check_display_name

    assert build_link_check_display_name(
        "https://newjoyloo.com/fr/products/baseball-cap-organizer?variant=1",
        "fr",
    ) == "baseball-cap-organizer · FR"
