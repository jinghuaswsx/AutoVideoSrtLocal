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
