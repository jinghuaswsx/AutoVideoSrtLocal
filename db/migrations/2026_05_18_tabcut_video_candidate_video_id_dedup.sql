-- Deduplicate Tabcut video candidates by video_id and enforce one candidate row per video.
-- Keeps the earliest inserted candidate row (smallest id) for each video_id.
-- Spec: docs/superpowers/specs/2026-05-12-tabcut-crawler-design.md

DELETE c
FROM tabcut_video_candidates c
JOIN (
  SELECT duplicate_rows.id
  FROM (
    SELECT dup.id
    FROM tabcut_video_candidates dup
    JOIN (
      SELECT video_id, MIN(id) AS keep_id
      FROM tabcut_video_candidates
      GROUP BY video_id
      HAVING COUNT(*) > 1
    ) keepers
      ON keepers.video_id = dup.video_id
    WHERE dup.id <> keepers.keep_id
  ) duplicate_rows
) duplicates
  ON duplicates.id = c.id;

ALTER TABLE tabcut_video_candidates
  ADD UNIQUE KEY uniq_tabcut_video_candidate_video_id (video_id);
