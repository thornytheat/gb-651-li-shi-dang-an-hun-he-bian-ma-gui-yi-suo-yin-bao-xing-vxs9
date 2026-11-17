from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .errors import DomainError

LEGACY_VERSION = "2026.11"

_BATCH_COLUMNS = [
    "batch_no",
    "template_version",
    "template_revision",
    "template_snapshot",
    "content_digest",
    "content",
    "filename",
    "status",
    "header",
    "next_seq",
    "expected_count",
    "input_count",
    "empty_count",
    "valid_count",
    "quarantine_count",
    "receipt",
    "created_at",
    "updated_at",
]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Store:
    """SQLite persistence for reproducible runs, template releases and receipts.

    The store is deliberately small, but the pieces that matter for this
    project are transactional:

    * publishing a template uses a compare-and-swap on the active revision;
    * opening a batch resolves and snapshots the active template in the same
      write transaction;
    * received rows are committed in idempotent chunks, so an interrupted
      receive can resume without creating a second record.
    """

    def __init__(self, path: str, default_template_version: str = LEGACY_VERSION):
        self.path = path
        self.default_template_version = default_template_version
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.transaction(immediate=True) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (digest TEXT PRIMARY KEY, run_id TEXT NOT NULL, result TEXT NOT NULL)"
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS templates (
                       version TEXT PRIMARY KEY,
                       revision INTEGER NOT NULL UNIQUE,
                       definition TEXT NOT NULL,
                       published_at TEXT NOT NULL
                   )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS template_state (
                       id INTEGER PRIMARY KEY CHECK (id = 1),
                       active_version TEXT NOT NULL,
                       revision INTEGER NOT NULL
                   )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS batches (
                       batch_no TEXT PRIMARY KEY,
                       template_version TEXT NOT NULL,
                       template_revision INTEGER NOT NULL,
                       template_snapshot TEXT NOT NULL,
                       content_digest TEXT NOT NULL,
                       content BLOB NOT NULL,
                       filename TEXT,
                       status TEXT NOT NULL,
                       header TEXT NOT NULL,
                       next_seq INTEGER NOT NULL DEFAULT 1,
                       expected_count INTEGER NOT NULL DEFAULT 0,
                       input_count INTEGER NOT NULL DEFAULT 0,
                       empty_count INTEGER NOT NULL DEFAULT 0,
                       valid_count INTEGER NOT NULL DEFAULT 0,
                       quarantine_count INTEGER NOT NULL DEFAULT 0,
                       receipt TEXT,
                       created_at TEXT NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS batch_records (
                       batch_no TEXT NOT NULL,
                       seq INTEGER NOT NULL,
                       source_line INTEGER NOT NULL,
                       state TEXT NOT NULL,
                       reason_code TEXT,
                       record TEXT NOT NULL,
                       PRIMARY KEY (batch_no, seq)
                   )"""
            )
            db.execute("CREATE INDEX IF NOT EXISTS batch_records_batch ON batch_records(batch_no, seq)")
            self._migrate(db, "batches", "expected_count", "INTEGER NOT NULL DEFAULT 0")
        self._seed_templates()

    @staticmethod
    def _migrate(db, table: str, column: str, decl: str) -> None:
        """Additive migration for databases created by an earlier revision."""
        existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    # ------------------------------------------------------------------
    # connections / transactions
    # ------------------------------------------------------------------
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.execute("PRAGMA busy_timeout=15000")
        try:
            db.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        return db

    @contextmanager
    def transaction(self, immediate: bool = False):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield db
            db.execute("COMMIT")
        except Exception:
            try:
                db.execute("ROLLBACK")
            except sqlite3.DatabaseError:
                pass
            raise
        finally:
            db.close()

    # ------------------------------------------------------------------
    # legacy run store (M6)
    # ------------------------------------------------------------------
    def record(self, digest: str, run_id: str, result: dict) -> dict:
        with self.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO runs VALUES (?, ?, ?)",
                (digest, run_id, json.dumps(result, ensure_ascii=False, sort_keys=True)),
            )
            row = db.execute("SELECT run_id, result FROM runs WHERE digest=?", (digest,)).fetchone()
        return {"run_id": row[0], "result": json.loads(row[1])}

    def count(self) -> int:
        with self.transaction() as db:
            return db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]

    def all(self) -> list[dict]:
        with self.transaction() as db:
            rows = db.execute("SELECT run_id, digest, result FROM runs ORDER BY run_id").fetchall()
        return [{"run_id": r[0], "digest": r[1], "result": json.loads(r[2])} for r in rows]

    # ------------------------------------------------------------------
    # template registry
    # ------------------------------------------------------------------
    def _seed_templates(self) -> None:
        # Imported lazily so that the persistence layer does not pull the
        # template parser into low-level module import time.
        from .modules.m3_fields.templates import legacy_definition

        with self.transaction(immediate=True) as db:
            state = db.execute("SELECT active_version, revision FROM template_state WHERE id=1").fetchone()
            if state is not None:
                if self._template_row(db, state[0]) is None:
                    raise DomainError("INVALID_VALUE", f"活动模板 {state[0]} 缺失")
                return
            existing = db.execute("SELECT version, definition, revision FROM templates ORDER BY revision LIMIT 1").fetchone()
            if existing is not None:
                definition = json.loads(existing[1])
                revision = int(existing[2])
                definition["revision"] = revision
                db.execute(
                    "INSERT OR IGNORE INTO template_state (id, active_version, revision) VALUES (1, ?, ?)",
                    (existing[0], revision),
                )
                return
            definition = legacy_definition(self.default_template_version, revision=1)
            db.execute(
                "INSERT INTO templates (version, revision, definition, published_at) VALUES (?, ?, ?, ?)",
                (
                    definition["version"],
                    int(definition["revision"]),
                    json.dumps(definition, ensure_ascii=False, sort_keys=True),
                    _utcnow(),
                ),
            )
            db.execute(
                "INSERT INTO template_state (id, active_version, revision) VALUES (1, ?, ?)",
                (definition["version"], int(definition["revision"])),
            )

    @staticmethod
    def _template_from_row(row) -> dict | None:
        if row is None:
            return None
        definition = json.loads(row[1])
        definition["version"] = row[0]
        definition["revision"] = int(row[2] if row[2] is not None else definition.get("revision", 0))
        return definition

    def _template_row(self, db, version: str):
        return db.execute(
            "SELECT version, definition, revision FROM templates WHERE version=?", (version,)
        ).fetchone()

    def active_state(self) -> dict:
        with self.transaction() as db:
            state = db.execute("SELECT active_version, revision FROM template_state WHERE id=1").fetchone()
        if state is None:
            raise DomainError("INVALID_VALUE", "模板注册表尚未初始化")
        return {"active_version": state[0], "revision": int(state[1])}

    def get_template(self, version: str) -> dict | None:
        with self.transaction() as db:
            row = self._template_row(db, version)
        return self._template_from_row(row)

    def list_templates(self) -> list[dict]:
        with self.transaction() as db:
            state = db.execute("SELECT active_version, revision FROM template_state WHERE id=1").fetchone()
            rows = db.execute(
                "SELECT version, definition, revision, published_at FROM templates ORDER BY revision"
            ).fetchall()
        active_version = state[0] if state else None
        out = []
        for version, raw, revision, published_at in rows:
            definition = json.loads(raw)
            definition["version"] = version
            definition["revision"] = int(revision)
            definition["published_at"] = published_at
            definition["active"] = version == active_version
            out.append(definition)
        return out

    def publish_template(
        self,
        definition: dict,
        expected_revision: int | None = None,
        expected_version: str | None = None,
        requested_revision: int | None = None,
    ) -> dict:
        """Publish an immutable template definition and make it active.

        The revision check happens under ``BEGIN IMMEDIATE``.  Two publishers
        that started from the same revision therefore cannot both win: the
        loser observes a changed revision and receives ``STALE_VERSION``
        (HTTP 409).
        """
        version = str(definition.get("version") or "").strip()
        if not version:
            raise DomainError("INVALID_VALUE", "模板版本不能为空")
        with self.transaction(immediate=True) as db:
            state = db.execute("SELECT active_version, revision FROM template_state WHERE id=1").fetchone()
            if state is None:
                raise DomainError("INVALID_VALUE", "模板注册表尚未初始化")
            current_revision = int(state[1])
            if expected_revision is not None and int(expected_revision) != current_revision:
                raise DomainError(
                    "STALE_VERSION",
                    f"提交基于修订 {expected_revision}，当前已是修订 {current_revision}",
                    status_code=409,
                )
            if expected_version is not None and str(expected_version) != str(state[0]):
                raise DomainError(
                    "STALE_VERSION",
                    f"提交基于模板 {expected_version}，当前模板为 {state[0]}",
                    status_code=409,
                )
            if requested_revision is not None:
                requested = int(requested_revision)
                if requested <= current_revision:
                    raise DomainError(
                        "STALE_VERSION",
                        f"模板修订 {requested} 已过期，当前为 {current_revision}",
                        status_code=409,
                    )
                if requested != current_revision + 1:
                    raise DomainError(
                        "INVALID_VALUE",
                        f"模板修订必须为 {current_revision + 1}，收到 {requested}",
                    )
            if db.execute("SELECT 1 FROM templates WHERE version=?", (version,)).fetchone() is not None:
                raise DomainError("DUPLICATE_CONFLICT", f"模板版本已存在：{version}", status_code=409)

            new_revision = current_revision + 1
            published = dict(definition)
            published["version"] = version
            published["revision"] = new_revision
            published["published_at"] = _utcnow()
            try:
                db.execute(
                    "INSERT INTO templates (version, revision, definition, published_at) VALUES (?, ?, ?, ?)",
                    (
                        version,
                        new_revision,
                        json.dumps(published, ensure_ascii=False, sort_keys=True),
                        published["published_at"],
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DomainError("STALE_VERSION", "模板修订已被其他发布占用", status_code=409) from exc
            changed = db.execute(
                "UPDATE template_state SET active_version=?, revision=? WHERE id=1 AND revision=?",
                (version, new_revision, current_revision),
            )
            if changed.rowcount != 1:
                raise DomainError("STALE_VERSION", "活动模板已被其他发布切换", status_code=409)
        return published

    # ------------------------------------------------------------------
    # batch receiving
    # ------------------------------------------------------------------
    @staticmethod
    def _batch_from_row(row) -> dict | None:
        if row is None:
            return None
        data = dict(zip(_BATCH_COLUMNS, row))
        data["template"] = json.loads(data.pop("template_snapshot"))
        data["header"] = json.loads(data["header"])
        data["receipt"] = json.loads(data["receipt"]) if data["receipt"] else None
        return data

    def get_batch(self, batch_no: str) -> dict | None:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM batches WHERE batch_no=?", (batch_no,)).fetchone()
        return self._batch_from_row(row)

    def begin_batch(
        self,
        *,
        batch_no: str,
        digest: str,
        content: bytes,
        filename: str | None,
        header: list[str],
        template_version: str | None = None,
        validate_header: Callable[[dict, list[str]], None] | None = None,
    ) -> tuple[dict, bool]:
        """Return ``(batch, created)`` after atomically binding a template.

        An existing batch is never reinterpreted: same batch number + same
        content resumes/returns the stored receipt; same batch number +
        different content is rejected as a conflict.
        """
        with self.transaction(immediate=True) as db:
            row = db.execute("SELECT * FROM batches WHERE batch_no=?", (batch_no,)).fetchone()
            if row is not None:
                existing = self._batch_from_row(row)
                if existing["content_digest"] != digest:
                    raise DomainError(
                        "DUPLICATE_CONFLICT",
                        f"批号 {batch_no} 已接收不同内容，拒绝覆盖",
                        status_code=409,
                    )
                return existing, False

            state = db.execute("SELECT active_version, revision FROM template_state WHERE id=1").fetchone()
            if state is None:
                raise DomainError("INVALID_VALUE", "模板注册表尚未初始化")
            version = template_version or state[0]
            tpl_row = self._template_row(db, version)
            if tpl_row is None:
                raise DomainError("INVALID_VALUE", f"模板版本不存在：{version}")
            template = json.loads(tpl_row[1])
            template["version"] = version
            # ``revision`` comes from the registry row, not from the payload,
            # so a concurrent publication cannot slip in between the read of
            # the active state and the write of this batch.
            template["revision"] = int(tpl_row[2])

            if validate_header is not None:
                validate_header(template, header)

            now = _utcnow()
            db.execute(
                """INSERT INTO batches
                   (batch_no, template_version, template_revision, template_snapshot,
                    content_digest, content, filename, status, header, next_seq,
                    expected_count, input_count, empty_count, valid_count,
                    quarantine_count, receipt, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'RECEIVING', ?, 1, 0, 0, 0, 0, 0, NULL, ?, ?)""",
                (
                    batch_no,
                    template["version"],
                    int(template["revision"]),
                    json.dumps(template, ensure_ascii=False, sort_keys=True),
                    digest,
                    sqlite3.Binary(content),
                    filename,
                    json.dumps(header, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            row = db.execute("SELECT * FROM batches WHERE batch_no=?", (batch_no,)).fetchone()
            return self._batch_from_row(row), True

    def get_batch_content(self, batch_no: str) -> bytes | None:
        with self.transaction() as db:
            row = db.execute("SELECT content FROM batches WHERE batch_no=?", (batch_no,)).fetchone()
        return bytes(row[0]) if row and row[0] is not None else None

    def append_progress(self, batch_no: str, records: list[dict], expected_count: int | None = None) -> dict | None:
        """Idempotently persist a contiguous chunk of parsed records.

        Counts are recomputed from the stored rows instead of being blindly
        incremented, so a retried or duplicated chunk can never double count.
        Returns the fresh batch or ``None`` when it is no longer receiving.
        """
        with self.transaction(immediate=True) as db:
            row = db.execute("SELECT status FROM batches WHERE batch_no=?", (batch_no,)).fetchone()
            if row is None:
                raise DomainError("INVALID_VALUE", "批次不存在")
            if row[0] != "RECEIVING":
                return None
            for record in records:
                db.execute(
                    """INSERT OR IGNORE INTO batch_records
                       (batch_no, seq, source_line, state, reason_code, record)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        batch_no,
                        int(record["seq"]),
                        int(record["source_line"]),
                        record["state"],
                        record.get("reason_code"),
                        json.dumps(record, ensure_ascii=False, sort_keys=True),
                    ),
                )
            counts = {
                state: int(total)
                for state, total in db.execute(
                    "SELECT state, COUNT(*) FROM batch_records WHERE batch_no=? GROUP BY state",
                    (batch_no,),
                ).fetchall()
            }
            total = sum(counts.values())
            max_seq = db.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM batch_records WHERE batch_no=?", (batch_no,)
            ).fetchone()[0]
            db.execute(
                """UPDATE batches SET
                       next_seq=?, expected_count=COALESCE(?, expected_count),
                       input_count=?, empty_count=?, valid_count=?,
                       quarantine_count=?, updated_at=?
                   WHERE batch_no=? AND status='RECEIVING'""",
                (
                    int(max_seq) + 1,
                    int(expected_count) if expected_count is not None else None,
                    total,
                    counts.get("empty", 0),
                    counts.get("valid", 0),
                    counts.get("quarantine", 0),
                    _utcnow(),
                    batch_no,
                ),
            )
            row = db.execute("SELECT * FROM batches WHERE batch_no=?", (batch_no,)).fetchone()
            return self._batch_from_row(row)

    def batch_records(self, batch_no: str) -> list[dict]:
        with self.transaction() as db:
            rows = db.execute(
                "SELECT seq, record FROM batch_records WHERE batch_no=? ORDER BY seq",
                (batch_no,),
            ).fetchall()
        out = []
        for seq, raw in rows:
            record = json.loads(raw)
            record["seq"] = int(seq)
            out.append(record)
        return out

    def finalize_batch(self, batch_no: str, receipt_builder: Callable[[dict, list[dict]], dict]) -> dict:
        """Build and persist a receipt exactly once, under a write lock."""
        with self.transaction(immediate=True) as db:
            row = db.execute("SELECT * FROM batches WHERE batch_no=?", (batch_no,)).fetchone()
            if row is None:
                raise DomainError("INVALID_VALUE", "批次不存在")
            batch = self._batch_from_row(row)
            if batch["status"] == "RECEIVED" and batch["receipt"] is not None:
                return batch["receipt"]
            rows = db.execute(
                "SELECT seq, record FROM batch_records WHERE batch_no=? ORDER BY seq",
                (batch_no,),
            ).fetchall()
            records = []
            for seq, raw in rows:
                record = json.loads(raw)
                record["seq"] = int(seq)
                records.append(record)
            receipt = receipt_builder(batch, records)
            db.execute(
                "UPDATE batches SET status='RECEIVED', receipt=?, updated_at=? WHERE batch_no=?",
                (json.dumps(receipt, ensure_ascii=False, sort_keys=True), _utcnow(), batch_no),
            )
            fresh = db.execute("SELECT receipt FROM batches WHERE batch_no=?", (batch_no,)).fetchone()
        return json.loads(fresh[0]) if fresh and fresh[0] else receipt

    def batch_summary(self, batch: dict) -> dict:
        valid = int(batch["valid_count"])
        quarantine = int(batch["quarantine_count"])
        empty = int(batch["empty_count"])
        total = int(batch["input_count"])
        summary = {
            "batch_no": batch["batch_no"],
            "template_version": batch["template_version"],
            "template_revision": int(batch["template_revision"]),
            "status": batch["status"],
            "digest": batch["content_digest"],
            "filename": batch["filename"],
            "header": list(batch["header"]),
            "input_count": total,
            "valid_count": valid,
            "quarantine_count": quarantine,
            "empty_count": empty,
            "valid_plus_quarantine": valid + quarantine,
            "accounted_count": valid + quarantine + empty,
            "balanced": valid + quarantine + empty == total,
            "created_at": batch["created_at"],
            "updated_at": batch["updated_at"],
        }
        expected = int(batch.get("expected_count") or 0)
        summary["expected_count"] = expected
        summary["pending_count"] = max(expected - total, 0)
        summary["complete"] = batch["status"] == "RECEIVED"
        if batch["status"] == "RECEIVING":
            summary["resumable"] = True
            summary["next_seq"] = int(batch["next_seq"])
        return summary

    def list_batches(self) -> list[dict]:
        with self.transaction() as db:
            rows = db.execute("SELECT * FROM batches ORDER BY rowid").fetchall()
        return [self.batch_summary(self._batch_from_row(row)) for row in rows]

    def count_batches(self) -> int:
        with self.transaction() as db:
            return db.execute("SELECT COUNT(*) FROM batches").fetchone()[0]
