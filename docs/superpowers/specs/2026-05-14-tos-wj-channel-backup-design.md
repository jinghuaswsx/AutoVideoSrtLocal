# TOS WJ Channel Backup Design

Last updated: 2026-05-14

## Goal

Add a second Volcano TOS channel named `495828376@qq.com WJ`, keep the existing
channel as `3482299@qq.com CJH`, and let superadmin switch the active main TOS
channel from `/settings?tab=infrastructure`.

Also provide a one-time backup/migration path that copies every file covered by
the existing TOS backup reference collector into the WJ channel, plus the latest
MySQL dump backup into the WJ `mysqldump/` prefix.

## Constraints

- Do not commit TOS secrets. The untracked local `TOS` file is only an operator
  input for testing or manual entry.
- Do not connect to Windows local MySQL. Full reference collection and dump
  discovery must run on the server environment.
- Keep existing business code reading `config.TOS_*`; channel selection is
  applied by `infra_credentials.sync_to_runtime()`.
- Do not change the daily `tos_backup` scheduled job. The WJ copy is a manual
  one-time migration command.

## Design

`infra_credentials` gets a new credential code:

- `tos_main`: `3482299@qq.com CJH`
- `tos_wj`: `495828376@qq.com WJ`

Both use the same TOS field schema. A system setting
`infra_credentials.tos_active_channel` stores the selected channel and defaults
to `tos_main`. During runtime sync, only the selected channel writes
`config.TOS_ACCESS_KEY`, `config.TOS_BUCKET`, endpoints, and related `TOS_*`
fields. `tos_backup` remains the dedicated disaster-recovery source channel.

The manual WJ backup command reuses:

- `tos_backup_references.collect_protected_file_refs()` for videos, covers,
  product images, raw source videos/covers, and project source videos.
- `tos_backup_storage.backup_object_key_for_local_path()` for `FILES/<env>/...`
  target keys.
- `tos_backup_restore.latest_db_dump_key()` for the latest dump in the current
  backup channel.

File copy behavior:

1. Collect protected refs from the server DB.
2. Ensure the local file exists, letting current backup storage materialize it
   from the existing backup channel when needed.
3. Upload the local file to WJ using the same `FILES/<env>/...` object key.
4. Skip existing WJ objects unless `--overwrite` is passed.

Dump copy behavior:

1. Find the latest source dump key, e.g.
   `DB/test/2026-05-13/appdb_2026-05-13_020000.sql.gz`.
2. Download it from the current backup channel to a temporary file.
3. Upload it to WJ as
   `mysqldump/test/2026-05-13/appdb_2026-05-13_020000.sql.gz`.

## Verification

- Unit tests for active TOS channel sync.
- Unit tests for WJ file copy, skipped existing objects, and latest dump key
  mapping under `mysqldump/`.
- CLI source test for the manual script flags.
- Connectivity smoke against the WJ bucket with `head_bucket`.
