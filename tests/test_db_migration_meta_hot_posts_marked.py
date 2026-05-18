from pathlib import Path


def test_meta_hot_posts_marked_migration_adds_local_annotation_fields():
    body = Path("db/migrations/2026_05_14_meta_hot_posts_marked.sql").read_text(
        encoding="utf-8"
    )

    assert "ALTER TABLE meta_hot_posts" in body
    assert "ADD COLUMN is_marked TINYINT(1) NOT NULL DEFAULT 0" in body
    assert "ADD COLUMN marked_at DATETIME DEFAULT NULL" in body
    assert "ADD COLUMN marked_by INT DEFAULT NULL" in body
    assert "ADD KEY idx_meta_hot_posts_is_marked" in body


def test_meta_hot_posts_mark_status_migration_adds_two_choice_field():
    body = Path("db/migrations/2026_05_14_meta_hot_posts_mark_status.sql").read_text(
        encoding="utf-8"
    )

    assert "ALTER TABLE meta_hot_posts" in body
    assert "ADD COLUMN mark_status VARCHAR(16) NULL" in body
    assert "ADD KEY idx_meta_hot_posts_mark_status" in body
    assert "UPDATE meta_hot_posts" in body
    assert "mark_status = 'bad'" in body
    assert "is_marked = 1" in body


def test_meta_hot_posts_message_translation_migration_adds_cached_chinese_fields():
    body = Path("db/migrations/2026_05_14_meta_hot_posts_message_translation.sql").read_text(
        encoding="utf-8"
    )

    assert "ALTER TABLE meta_hot_posts" in body
    assert "message_zh_html" in body
    assert "message_zh_status" in body
    assert "message_zh_attempts" in body
    assert "message_zh_translated_at" in body
    assert "idx_meta_hot_posts_message_zh_status" in body


def test_meta_hot_posts_local_video_migration_adds_cache_fields():
    body = Path("db/migrations/2026_05_14_meta_hot_posts_local_video.sql").read_text(
        encoding="utf-8"
    )

    assert "ALTER TABLE meta_hot_posts" in body
    assert "local_video_path" in body
    assert "local_video_status" in body
    assert "local_video_error" in body
    assert "local_video_downloaded_at" in body
    assert "local_video_attempts" in body
    assert "idx_meta_hot_posts_local_video_status" in body
    assert "docs/superpowers/specs/2026-05-14-meta-hot-posts-video-localization-design.md" in body


def test_meta_hot_posts_local_video_metadata_migration_adds_duration_and_cover_fields():
    body = Path("db/migrations/2026_05_18_meta_hot_posts_local_video_metadata.sql").read_text(
        encoding="utf-8"
    )

    assert "ALTER TABLE meta_hot_posts" in body
    assert "local_video_duration_seconds" in body
    assert "local_video_cover_path" in body
    assert "information_schema.COLUMNS" in body
    assert "docs/superpowers/specs/2026-05-14-meta-hot-posts-video-localization-design.md" in body


def test_meta_hot_posts_europe_fit_migration_creates_assessment_table():
    body = Path("db/migrations/2026_05_14_meta_hot_posts_europe_fit.sql").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE IF NOT EXISTS meta_hot_post_europe_assessments" in body
    assert "post_id BIGINT UNSIGNED NOT NULL" in body
    assert "suitability_score" in body
    assert "recommendation" in body
    assert "direct_reuse" in body
    assert "video_optimization_json" in body
    assert "uniq_meta_hot_post_europe_assessments_post" in body
    assert "idx_meta_hot_post_europe_assessments_rank" in body
    assert "docs/superpowers/specs/2026-05-14-meta-hot-posts-europe-fit-design.md" in body


def test_meta_hot_posts_video_copyability_migration_adds_result_table():
    body = Path("db/migrations/2026_05_14_meta_hot_posts_video_copyability.sql").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE IF NOT EXISTS meta_hot_post_video_copyability_analyses" in body
    assert "hot_post_id BIGINT UNSIGNED NOT NULL" in body
    assert "compressed_video_path VARCHAR(2048)" in body
    assert "overall_score DECIMAL(5, 2)" in body
    assert "copyability_score DECIMAL(5, 2)" in body
    assert "meta_us_ad_fit_score DECIMAL(5, 2)" in body
    assert "analysis_json JSON" in body
    assert "UNIQUE KEY uniq_meta_hot_post_video_copyability_hot_post" in body
    assert "idx_meta_hot_post_video_copyability_score" in body
    assert "docs/superpowers/specs/2026-05-14-meta-hot-posts-video-copyability-analysis-design.md" in body


def test_meta_hot_posts_video_retry_backoff_migration_marks_exhausted_failures():
    body = Path("db/migrations/2026_05_15_meta_hot_posts_video_retry_backoff.sql").read_text(
        encoding="utf-8"
    )

    assert "UPDATE meta_hot_posts" in body
    assert "local_video_status = 'unavailable'" in body
    assert "local_video_status = 'failed'" in body
    assert "local_video_attempts >= 5" in body
    assert "unavailable after max retry attempts" in body


def test_meta_hot_posts_sync_period_likes_migration_allows_negative_changes():
    body = Path("db/migrations/2026_05_15_meta_hot_posts_sync_period_signed.sql").read_text(
        encoding="utf-8"
    )

    assert "ALTER TABLE meta_hot_posts" in body
    assert "MODIFY COLUMN sync_period_likes BIGINT NULL" in body
    assert "UNSIGNED" not in body.upper()


def test_meta_hot_posts_vertex_adc_pro_binding_migration_pins_queue_use_cases():
    body = Path("db/migrations/2026_05_15_meta_hot_posts_vertex_adc_pro_binding.sql").read_text(
        encoding="utf-8"
    )

    assert "'meta_hot_posts.europe_fit'" in body
    assert "'meta_hot_posts.video_copyability'" in body
    assert "'gemini_vertex_adc'" in body
    assert "'gemini-3.1-pro-preview'" in body
    assert "ON DUPLICATE KEY UPDATE" in body


def test_meta_hot_posts_vertex_adc_pro_to_flash_migration_updates_queue_use_cases():
    body = Path("db/migrations/2026_05_15_meta_hot_posts_vertex_adc_pro_to_flash.sql").read_text(
        encoding="utf-8"
    )

    assert "'meta_hot_posts.europe_fit'" in body
    assert "'meta_hot_posts.video_copyability'" in body
    assert "'gemini_vertex_adc'" in body
    assert "'gemini-3-flash-preview'" in body
    assert "ON DUPLICATE KEY UPDATE" in body
