"""Migrations légères (ALTER) pour colonnes/tables ajoutées sans Alembic."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def ensure_portal_schema(engine: Engine) -> None:
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if dialect == "sqlite":
            _sqlite_user_cols(conn)
            _sqlite_skill_schedule_cols(conn)
        elif dialect == "postgresql":
            _pg_user_cols(conn)
            _pg_skill_schedule_cols(conn)
        # Tables nouvelles créées via Base.metadata.create_all


def _sqlite_user_cols(conn) -> None:
    cols = {
        row[1] for row in conn.execute(text("PRAGMA table_info(users)")).fetchall()
    }
    if not cols:
        return
    alters = [
        ("can_access_apps", "BOOLEAN NOT NULL DEFAULT 1"),
        ("can_access_skills", "BOOLEAN NOT NULL DEFAULT 0"),
        ("display_name", "VARCHAR(255) NOT NULL DEFAULT ''"),
        ("uccx_team", "VARCHAR(128) NOT NULL DEFAULT ''"),
        ("uccx_roles", "VARCHAR(255) NOT NULL DEFAULT ''"),
        ("auth_source", "VARCHAR(32) NOT NULL DEFAULT 'local'"),
        ("updated_at", "DATETIME"),
    ]
    for name, typedef in alters:
        if name not in cols:
            conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {typedef}"))
            logger.info("users: colonne %s ajoutée (sqlite)", name)
    # password_hash nullable : SQLite ignore souvent NOT NULL alter — OK en pratique


def _pg_user_cols(conn) -> None:
    stmts = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS can_access_apps BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS can_access_skills BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name VARCHAR(255) NOT NULL DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS uccx_team VARCHAR(128) NOT NULL DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS uccx_roles VARCHAR(255) NOT NULL DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_source VARCHAR(32) NOT NULL DEFAULT 'local'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP",
        "ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL",
    ]
    for sql in stmts:
        try:
            conn.execute(text(sql))
        except Exception as exc:  # noqa: BLE001
            logger.debug("migrate skip: %s (%s)", sql[:60], exc)


def _sqlite_skill_schedule_cols(conn) -> None:
    try:
        cols = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(skill_schedules)")).fetchall()
        }
    except Exception:  # noqa: BLE001
        return
    if not cols:
        return
    alters = [
        ("kind", "VARCHAR(16) NOT NULL DEFAULT 'once'"),
        ("weekdays", "VARCHAR(32)"),
        ("time_start", "VARCHAR(5)"),
        ("time_end", "VARCHAR(5)"),
    ]
    for name, typedef in alters:
        if name not in cols:
            conn.execute(text(f"ALTER TABLE skill_schedules ADD COLUMN {name} {typedef}"))
            logger.info("skill_schedules: colonne %s ajoutée (sqlite)", name)


def _pg_skill_schedule_cols(conn) -> None:
    stmts = [
        "ALTER TABLE skill_schedules ADD COLUMN IF NOT EXISTS kind VARCHAR(16) NOT NULL DEFAULT 'once'",
        "ALTER TABLE skill_schedules ADD COLUMN IF NOT EXISTS weekdays VARCHAR(32)",
        "ALTER TABLE skill_schedules ADD COLUMN IF NOT EXISTS time_start VARCHAR(5)",
        "ALTER TABLE skill_schedules ADD COLUMN IF NOT EXISTS time_end VARCHAR(5)",
        "ALTER TABLE skill_schedules ALTER COLUMN end_at DROP NOT NULL",
    ]
    for sql in stmts:
        try:
            conn.execute(text(sql))
        except Exception as exc:  # noqa: BLE001
            logger.debug("migrate skip: %s (%s)", sql[:60], exc)
