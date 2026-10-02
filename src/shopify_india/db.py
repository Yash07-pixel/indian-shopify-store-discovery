from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .models import StoreRecord, utc_now


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY,
    normalized_url TEXT NOT NULL UNIQUE,
    registrable_domain TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    discovered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS candidate_sources (
    candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    source_name TEXT NOT NULL,
    source_value TEXT NOT NULL,
    UNIQUE(candidate_id, source_name, source_value)
);
CREATE TABLE IF NOT EXISTS stores (
    candidate_id INTEGER PRIMARY KEY REFERENCES candidates(id) ON DELETE CASCADE,
    record_json TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    shopify_score INTEGER NOT NULL,
    india_score INTEGER NOT NULL,
    checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fetches (
    id INTEGER PRIMARY KEY,
    candidate_id INTEGER REFERENCES candidates(id) ON DELETE CASCADE,
    requested_url TEXT NOT NULL,
    final_url TEXT,
    status_code INTEGER,
    fetched_at TEXT NOT NULL,
    content_hash TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidates(status);
CREATE INDEX IF NOT EXISTS idx_stores_accepted ON stores(accepted);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def add_candidate(self, url: str, domain: str, source: str, source_value: str) -> bool:
        return self.add_candidates([(url, domain, source, source_value)])[0] == 1

    def add_candidates(self, rows: list[tuple[str, str, str, str]]) -> tuple[int, int]:
        """Insert a batch in one transaction; returns (new candidates, valid rows)."""
        if not rows:
            return 0, 0
        now = utc_now()
        added = 0
        with self.connect() as conn:
            for url, domain, source, source_value in rows:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO candidates(normalized_url, registrable_domain, discovered_at, updated_at) VALUES(?,?,?,?)",
                    (url, domain, now, now),
                )
                added += int(cursor.rowcount > 0)
                row = conn.execute("SELECT id FROM candidates WHERE normalized_url=?", (url,)).fetchone()
                conn.execute(
                    "INSERT OR IGNORE INTO candidate_sources(candidate_id, source_name, source_value) VALUES(?,?,?)",
                    (row["id"], source, source_value[:1000]),
                )
        return added, len(rows)

    def pending_candidates(self, limit: int | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM candidates WHERE status IN ('pending','retry') ORDER BY id"
        params: tuple[object, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        with self.connect() as conn:
            return list(conn.execute(sql, params))

    def candidates_with_status(self, status: str) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute("SELECT * FROM candidates WHERE status=? ORDER BY id", (status,)))

    def sources_for(self, candidate_id: int) -> list[str]:
        with self.connect() as conn:
            return [row[0] for row in conn.execute(
                "SELECT DISTINCT source_name FROM candidate_sources WHERE candidate_id=? ORDER BY source_name",
                (candidate_id,),
            )]

    def mark_running(self, candidate_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE candidates SET status='running', attempts=attempts+1, updated_at=? WHERE id=?",
                (utc_now(), candidate_id),
            )

    def save_store(self, candidate_id: int, record: StoreRecord) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO stores(candidate_id,record_json,accepted,shopify_score,india_score,checked_at)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET
                   record_json=excluded.record_json, accepted=excluded.accepted,
                   shopify_score=excluded.shopify_score, india_score=excluded.india_score,
                   checked_at=excluded.checked_at""",
                (candidate_id, record.model_dump_json(), int(record.accepted), record.shopify_score, record.india_score, record.checked_at),
            )
            status = "accepted" if record.accepted else "rejected"
            conn.execute("UPDATE candidates SET status=?, updated_at=?, last_error=NULL WHERE id=?", (status, utc_now(), candidate_id))

    def mark_error(self, candidate_id: int, error: str, retry: bool) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE candidates SET status=?, updated_at=?, last_error=? WHERE id=?",
                ("retry" if retry else "failed", utc_now(), error[:1000], candidate_id),
            )

    def accepted_records(self) -> list[StoreRecord]:
        with self.connect() as conn:
            rows = conn.execute("SELECT record_json FROM stores WHERE accepted=1 ORDER BY shopify_score+india_score DESC").fetchall()
        return [StoreRecord.model_validate_json(row[0]) for row in rows]

    def all_records(self) -> list[StoreRecord]:
        with self.connect() as conn:
            rows = conn.execute("SELECT record_json FROM stores ORDER BY candidate_id").fetchall()
        return [StoreRecord.model_validate_json(row[0]) for row in rows]

    def counts(self) -> dict[str, int]:
        with self.connect() as conn:
            return {row[0]: row[1] for row in conn.execute("SELECT status, COUNT(*) FROM candidates GROUP BY status")}

