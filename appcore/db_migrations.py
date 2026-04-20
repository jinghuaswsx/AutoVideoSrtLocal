"""启动时自动跑 db/migrations/*.sql 里还没 apply 过的脚本。

为什么存在这个模块：
  本项目没有 Alembic / Flyway 等迁移工具，历史上完全靠 "上线后手工
  mysql < xxx.sql" 来维护 schema。结果就是 2026-04-19 的
  `2026_04_19_medias_detail_image_translate_provenance.sql` 在生产
  漏跑，`list_detail_images` 的 SELECT 带着不存在的列 → 接口 500
  → 前端显示详情图 0 张，事故追查了一个上午。

设计要点：
  * 用一张 `schema_migrations` 表跟踪已应用脚本（文件名是主键）。
  * 首次引入本机制时，**旧部署**（`projects` 表已存在）会把当前
    `db/migrations/` 里的所有文件标记为 applied 作 baseline，不重跑 SQL；
    **新装部署**（空库）才真执行所有 SQL。
  * 之后每次启动只跑 baseline 之后新增的文件。
  * 用 MySQL session 级 `GET_LOCK` 防多 worker 并发。
  * 任何一条 migration 失败立即抛异常，阻止服务用旧 schema 继续启动。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from appcore.db import get_conn

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"
LOCK_NAME = "autovideosrt_schema_migrations"
LOCK_TIMEOUT_SECONDS = 30

# 简单语句切分：按分号 + 行尾。本项目历史 migration 全是纯 DDL / DML，
# 没有 DELIMITER 块、没有存储过程，够用。
_STATEMENT_SPLIT_RE = re.compile(r";\s*(?:\n|$)", re.MULTILINE)


def ensure_up_to_date() -> None:
    """扫 db/migrations/*.sql，应用所有尚未 apply 的脚本。启动时调用一次。"""
    if not MIGRATIONS_DIR.is_dir():
        log.warning("[migrations] dir not found: %s", MIGRATIONS_DIR)
        return

    files = sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        log.info("[migrations] no migration files found")
        return

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if not _acquire_lock(cur):
                raise RuntimeError(
                    f"schema migration 锁获取超时（{LOCK_TIMEOUT_SECONDS}s），"
                    "可能有另一个进程正在迁移"
                )
            try:
                bootstrap_empty = _ensure_tracking_table(cur)
                if bootstrap_empty is True:
                    # 新装（空库）：真执行全部 SQL
                    log.info("[migrations] bootstrap EMPTY DB: applying %d files", len(files))
                    for fname in files:
                        _apply_file(cur, fname)
                    return
                if bootstrap_empty is False:
                    # 老部署首次引入跟踪：baseline 标记已有文件，不重跑
                    for fname in files:
                        cur.execute(
                            "INSERT IGNORE INTO schema_migrations (filename, baseline) "
                            "VALUES (%s, 1)",
                            (fname,),
                        )
                    log.info(
                        "[migrations] bootstrap EXISTING DB: baselined %d files", len(files)
                    )
                    return

                # 正常路径：只跑新增的
                cur.execute("SELECT filename FROM schema_migrations")
                applied = {row["filename"] for row in cur.fetchall() or []}
                pending = [f for f in files if f not in applied]
                if not pending:
                    log.info("[migrations] up to date (%d files tracked)", len(applied))
                    return
                log.info("[migrations] %d pending: %s", len(pending), pending)
                for fname in pending:
                    _apply_file(cur, fname)
            finally:
                _release_lock(cur)
    finally:
        conn.close()


def _acquire_lock(cur) -> bool:
    cur.execute("SELECT GET_LOCK(%s, %s) AS ok", (LOCK_NAME, LOCK_TIMEOUT_SECONDS))
    row = cur.fetchone() or {}
    ok = row.get("ok") if isinstance(row, dict) else (row[0] if row else None)
    return ok == 1


def _release_lock(cur) -> None:
    try:
        cur.execute("SELECT RELEASE_LOCK(%s)", (LOCK_NAME,))
        cur.fetchone()
    except Exception:
        pass


def _ensure_tracking_table(cur):
    """建 schema_migrations 表。

    返回值语义：
      * None   → 表已存在，走正常 diff 路径
      * True   → 表刚刚建、projects 表也不存在，视为空库新装，调用方应真执行全部 SQL
      * False  → 表刚刚建、projects 表已存在，视为老部署首次引入，调用方应 baseline 标记
    """
    cur.execute("SHOW TABLES LIKE 'schema_migrations'")
    if cur.fetchone():
        return None
    cur.execute(
        "CREATE TABLE schema_migrations ("
        "  filename    VARCHAR(255) NOT NULL PRIMARY KEY,"
        "  baseline    TINYINT(1) NOT NULL DEFAULT 0,"
        "  applied_at  DATETIME   NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )
    cur.execute("SHOW TABLES LIKE 'projects'")
    projects_exists = cur.fetchone() is not None
    return not projects_exists  # True=empty DB, False=legacy deployment


def _apply_file(cur, filename: str) -> None:
    path = MIGRATIONS_DIR / filename
    body = path.read_text(encoding="utf-8")
    statements = _split_statements(body)
    if not statements:
        log.warning("[migrations] skip empty file %s", filename)
        cur.execute(
            "INSERT INTO schema_migrations (filename, baseline) VALUES (%s, 0)",
            (filename,),
        )
        return

    log.info("[migrations] applying %s (%d statements)", filename, len(statements))
    try:
        for stmt in statements:
            cur.execute(stmt)
    except Exception as exc:
        log.error("[migrations] FAILED %s: %s", filename, exc)
        raise
    cur.execute(
        "INSERT INTO schema_migrations (filename, baseline) VALUES (%s, 0)",
        (filename,),
    )
    log.info("[migrations] applied %s", filename)


def _split_statements(body: str) -> list[str]:
    # 去掉纯注释行后按分号切
    kept_lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        kept_lines.append(line)
    joined = "\n".join(kept_lines)
    raw_parts = _STATEMENT_SPLIT_RE.split(joined)
    out = []
    for part in raw_parts:
        stmt = part.strip()
        if stmt:
            out.append(stmt)
    return out
