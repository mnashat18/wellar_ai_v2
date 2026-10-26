from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
import time
import logging
import random
from functools import wraps
from pathlib import Path
from typing import Any

from app.core.errors import QueueFull
from app.core.ids import new_id
from app.core.logging import event

logger = logging.getLogger("wellar.queue.sqlite")


def sqlite_error_details(exc: sqlite3.Error) -> tuple[int | None, str | None, bool]:
    """Classify lock errors without logging SQLite's potentially sensitive message."""
    code = getattr(exc, "sqlite_errorcode", None)
    name = getattr(exc, "sqlite_errorname", None)
    if code is not None:
        return code, name, (code & 0xFF) in (5, 6)  # SQLITE_BUSY / SQLITE_LOCKED
    # Python builds before sqlite_errorcode attributes: recognize only SQLite's
    # fixed lock messages; arbitrary OperationalErrors remain permanent/unknown.
    message = str(exc).strip().lower()
    if isinstance(exc, sqlite3.OperationalError) and message in {
        "database is locked", "database table is locked", "database schema is locked",
    }:
        return None, "SQLITE_LOCK_CONTENTION", True
    return None, None, False


def _db_operation(name: str, *, transaction: bool = False):
    """Serialize use of this store's one SQLite connection and emit safe diagnostics."""
    def decorate(method):
        @wraps(method)
        def wrapped(self, *args, **kwargs):
            started = time.perf_counter()
            with self._lock:
                retries = 0
                while True:
                    try:
                        result = method(self, *args, **kwargs)
                        break
                    except sqlite3.Error as exc:
                        code, name_code, busy = sqlite_error_details(exc)
                        if busy:
                            self._busy_lock_count += 1
                        if busy and transaction and retries < self.max_lock_retries:
                            retries += 1
                            self._sqlite_retry_count += 1
                            delay = random.uniform(0.001, 0.01 * (2 ** retries))
                            event(logger, "sqlite.operation.retry", logging.WARNING,
                                  processing_stage="queue_storage", operation=name,
                                  exception_type=type(exc).__name__, sqlite_error_code=code,
                                  sqlite_error_name=name_code, retry_attempt_count=retries,
                                  attempt_count=retries+1, busy_lock_count=self._busy_lock_count,
                                  transaction_duration_ms=round((time.perf_counter()-started)*1000, 3))
                            time.sleep(delay)
                            continue
                        event(logger, "sqlite.operation.failed", logging.WARNING,
                              processing_stage="queue_storage", operation=name,
                              exception_type=type(exc).__name__, sqlite_error_code=code,
                              sqlite_error_name=name_code, retry_attempt_count=retries,
                              attempt_count=retries+1,
                              transaction_duration_ms=round((time.perf_counter()-started)*1000, 3),
                              busy_lock_count=self._busy_lock_count)
                        raise
                if transaction:
                    event(logger, "sqlite.transaction.completed", logging.INFO,
                          processing_stage="queue_storage", operation=name,
                          retry_attempt_count=retries, attempt_count=retries+1,
                          transaction_duration_ms=round((time.perf_counter()-started)*1000, 3),
                          busy_lock_count=self._busy_lock_count)
                return result
        return wrapped
    return decorate


class SQLiteQueueStore:
    """Single-host local development queue. Never use for multi-host production."""
    shared_monotonic_clock = True

    def __init__(self, path: str, capacity: int = 1000, max_attempts: int = 3, lease_seconds: int = 30, retry_base_seconds: float = 1.0):
        self.path, self.capacity, self.max_attempts = path, capacity, max_attempts
        self.lease_seconds, self.retry_base_seconds = lease_seconds, retry_base_seconds
        self._lock = threading.RLock()
        self._busy_lock_count = 0
        self._sqlite_retry_count = 0
        self.max_lock_retries = 2
        self.busy_timeout_ms = 2000
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=self.busy_timeout_ms/1000)
        self.db.row_factory = sqlite3.Row
        self.db.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS scans(
          scan_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, subject_id TEXT NOT NULL,
          idempotency_key TEXT NOT NULL, payload_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
          job_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
          request_id TEXT NOT NULL DEFAULT '-',
          UNIQUE(tenant_id, subject_id, idempotency_key));
        CREATE TABLE IF NOT EXISTS media_reconciliation_state(
          singleton INTEGER PRIMARY KEY CHECK(singleton=1),
          cycle_started_at REAL,
          after_scan_id TEXT,
          after_reference TEXT,
          lease_owner TEXT,
          lease_until REAL);
        INSERT OR IGNORE INTO media_reconciliation_state(singleton) VALUES(1);
        CREATE TABLE IF NOT EXISTS jobs(
          job_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL UNIQUE REFERENCES scans(scan_id),
          status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, available_at REAL NOT NULL,
          lease_until REAL, fencing_token INTEGER NOT NULL DEFAULT 0, worker_id TEXT, last_error TEXT);
        CREATE TABLE IF NOT EXISTS results(
          scan_id TEXT PRIMARY KEY REFERENCES scans(scan_id), tenant_id TEXT NOT NULL,
          subject_id TEXT NOT NULL, result_json TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox(
          event_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL, event_type TEXT NOT NULL,
          payload_json TEXT NOT NULL, created_at REAL NOT NULL, published_at REAL,
          UNIQUE(scan_id,event_type));
        CREATE TABLE IF NOT EXISTS media_assets(
          media_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, subject_id TEXT NOT NULL,
          modality TEXT NOT NULL, content_type TEXT NOT NULL, size_bytes INTEGER NOT NULL,
          sha256 TEXT NOT NULL, relative_path TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS training_labels(
          scan_id TEXT PRIMARY KEY REFERENCES scans(scan_id) ON DELETE CASCADE, tenant_id TEXT NOT NULL, subject_id TEXT NOT NULL,
          kss INTEGER NOT NULL CHECK(kss BETWEEN 1 AND 9), features_json TEXT NOT NULL,
          created_at REAL NOT NULL, fatigue INTEGER, focus INTEGER, shift TEXT, time_of_day TEXT,
          expires_at REAL, consented_at REAL NOT NULL DEFAULT 0,
          consent_text_version TEXT NOT NULL DEFAULT 'wellar-v2-training-consent-v1');
        """)
        reconciliation_columns={row[1] for row in self.db.execute("PRAGMA table_info(media_reconciliation_state)")}
        if "after_reference" not in reconciliation_columns:
            self.db.execute("ALTER TABLE media_reconciliation_state ADD COLUMN after_reference TEXT")
        label_columns={row[1] for row in self.db.execute("PRAGMA table_info(training_labels)")}
        label_additions={"fatigue":"INTEGER","focus":"INTEGER","shift":"TEXT","time_of_day":"TEXT","expires_at":"REAL","consented_at":"REAL NOT NULL DEFAULT 0","consent_text_version":"TEXT NOT NULL DEFAULT 'wellar-v2-training-consent-v1'"}
        for name, declaration in label_additions.items():
            if name not in label_columns:
                self.db.execute(f"ALTER TABLE training_labels ADD COLUMN {name} {declaration}")
        self.db.execute("CREATE INDEX IF NOT EXISTS training_labels_expiry_idx ON training_labels(expires_at)")
        self.db.executescript("""
        CREATE TRIGGER IF NOT EXISTS training_labels_owner_insert BEFORE INSERT ON training_labels
        WHEN NOT EXISTS(SELECT 1 FROM scans WHERE scan_id=NEW.scan_id AND tenant_id=NEW.tenant_id AND subject_id=NEW.subject_id AND status='completed')
        BEGIN SELECT RAISE(ABORT,'completed owned scan required'); END;
        CREATE TRIGGER IF NOT EXISTS training_labels_owner_update BEFORE UPDATE ON training_labels
        WHEN NOT EXISTS(SELECT 1 FROM scans WHERE scan_id=NEW.scan_id AND tenant_id=NEW.tenant_id AND subject_id=NEW.subject_id AND status='completed')
        BEGIN SELECT RAISE(ABORT,'completed owned scan required'); END;
        CREATE TRIGGER IF NOT EXISTS training_labels_scan_cascade AFTER DELETE ON scans
        BEGIN DELETE FROM training_labels WHERE scan_id=OLD.scan_id; END;
        """)
        outbox_columns={row[1] for row in self.db.execute("PRAGMA table_info(outbox)")}
        additions={
            "delivery_status": "TEXT NOT NULL DEFAULT 'pending'",
            "attempts": "INTEGER NOT NULL DEFAULT 0",
            "available_at": "REAL NOT NULL DEFAULT 0",
            "lease_until": "REAL",
            "fencing_token": "INTEGER NOT NULL DEFAULT 0",
            "publisher_id": "TEXT",
            "last_error_code": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in outbox_columns:
                self.db.execute(f"ALTER TABLE outbox ADD COLUMN {name} {declaration}")
        self.db.execute("CREATE INDEX IF NOT EXISTS outbox_delivery_idx ON outbox(delivery_status,available_at,lease_until)")
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS scans_identity_unique ON scans(scan_id,tenant_id,subject_id)")
        self.db.execute("""CREATE TABLE IF NOT EXISTS directus_scan_bindings(
          scan_id TEXT PRIMARY KEY, directus_scan_id TEXT NOT NULL UNIQUE,
          tenant_id TEXT NOT NULL, subject_id TEXT NOT NULL, remote_owner_id TEXT NOT NULL,
          remote_tenant_id TEXT NOT NULL, remote_scan_status TEXT NOT NULL,
          membership_active INTEGER NOT NULL CHECK(membership_active=1),
          media_owner_verified INTEGER NOT NULL CHECK(media_owner_verified IN (0,1)),
          verification_version TEXT NOT NULL, verified_at REAL NOT NULL,
          FOREIGN KEY(scan_id,tenant_id,subject_id) REFERENCES scans(scan_id,tenant_id,subject_id))""")
        binding_sql=self.db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='directus_scan_bindings'").fetchone()[0]
        if "check(media_owner_verified=1)" in binding_sql.lower().replace(" ",""):
            self.db.execute("ALTER TABLE directus_scan_bindings RENAME TO directus_scan_bindings_legacy")
            self.db.execute("""CREATE TABLE directus_scan_bindings(
              scan_id TEXT PRIMARY KEY, directus_scan_id TEXT NOT NULL UNIQUE,
              tenant_id TEXT NOT NULL, subject_id TEXT NOT NULL, remote_owner_id TEXT NOT NULL,
              remote_tenant_id TEXT NOT NULL, remote_scan_status TEXT NOT NULL,
              membership_active INTEGER NOT NULL CHECK(membership_active=1),
              media_owner_verified INTEGER NOT NULL CHECK(media_owner_verified IN (0,1)),
              verification_version TEXT NOT NULL, verified_at REAL NOT NULL,
              FOREIGN KEY(scan_id,tenant_id,subject_id) REFERENCES scans(scan_id,tenant_id,subject_id))""")
            self.db.execute("INSERT INTO directus_scan_bindings SELECT * FROM directus_scan_bindings_legacy")
            self.db.execute("DROP TABLE directus_scan_bindings_legacy")

    @_db_operation("submit", transaction=True)
    def submit(self, scan_id: str, principal, key: str, payload_hash: str, payload_json: str, request_id: str = "-"):
        now = time.time()
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                existing = self.db.execute("SELECT * FROM scans WHERE tenant_id=? AND subject_id=? AND idempotency_key=?", (principal.tenant_id, principal.subject_id, key)).fetchone()
                if existing:
                    self.db.execute("COMMIT")
                    return dict(existing), False
                pending = self.db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
                if pending >= self.capacity:
                    raise QueueFull()
                job_id = new_id()
                self.db.execute("INSERT INTO scans(scan_id,tenant_id,subject_id,idempotency_key,payload_hash,payload_json,job_id,status,created_at,updated_at,request_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (scan_id, principal.tenant_id, principal.subject_id, key, payload_hash, payload_json, job_id, "queued", now, now, request_id))
                self.db.execute("INSERT INTO jobs(job_id,scan_id,status,available_at) VALUES(?,?,?,?)", (job_id, scan_id, "queued", now))
                row = self.db.execute("SELECT * FROM scans WHERE scan_id=?", (scan_id,)).fetchone()
                self.db.execute("COMMIT")
                return dict(row), True
            except Exception:
                self._rollback_safely()
                raise

    def _rollback_safely(self) -> None:
        """Best-effort rollback which never masks the original transaction error."""
        if self.db.in_transaction:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.Error:
                pass

    @_db_operation("claim", transaction=True)
    def claim(self, worker_id: str, now: float | None = None):
        supplied_now=now
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                now=supplied_now if supplied_now is not None else time.time()
                terminal_at=time.time()
                # A worker that died on its final attempt is terminal once its lease expires.
                self.db.execute("UPDATE jobs SET status='failed',last_error='lease_expired_retry_exhausted',lease_until=NULL,worker_id=NULL WHERE status='running' AND lease_until<? AND attempts>=?", (now,self.max_attempts))
                self.db.execute("UPDATE scans SET status='failed',updated_at=? WHERE scan_id IN (SELECT scan_id FROM jobs WHERE status='failed') AND status IN ('queued','processing')", (terminal_at,))
                self.db.execute("UPDATE jobs SET status='queued',worker_id=NULL,lease_until=NULL,available_at=? WHERE status='running' AND lease_until<? AND attempts<?", (now, now, self.max_attempts))
                self.db.execute("UPDATE scans SET status='queued',updated_at=? WHERE scan_id IN (SELECT scan_id FROM jobs WHERE status='queued') AND status='processing'", (now,))
                row = self.db.execute("SELECT * FROM jobs WHERE status='queued' AND available_at<=? AND attempts<? ORDER BY available_at,rowid LIMIT 1", (now,self.max_attempts)).fetchone()
                if not row:
                    self.db.execute("UPDATE jobs SET status='failed',last_error='retry_exhausted' WHERE status='queued' AND attempts>=?", (self.max_attempts,))
                    self.db.execute("UPDATE scans SET status='failed',updated_at=? WHERE scan_id IN (SELECT scan_id FROM jobs WHERE status='failed') AND status IN ('queued','processing')", (terminal_at,))
                    self.db.execute("COMMIT")
                    return None
                fence = row["fencing_token"] + 1
                self.db.execute("UPDATE jobs SET status='running',attempts=attempts+1,worker_id=?,lease_until=?,fencing_token=? WHERE job_id=?", (worker_id,now+self.lease_seconds,fence,row["job_id"]))
                self.db.execute("UPDATE scans SET status='processing',updated_at=? WHERE scan_id=?", (now,row["scan_id"]))
                job = dict(self.db.execute("SELECT * FROM jobs WHERE job_id=?",(row["job_id"],)).fetchone())
                job["payload_json"] = self.db.execute("SELECT payload_json FROM scans WHERE scan_id=?",(row["scan_id"],)).fetchone()[0]
                scan_meta=self.db.execute("SELECT request_id,created_at FROM scans WHERE scan_id=?",(row["scan_id"],)).fetchone()
                job["request_id"] = scan_meta["request_id"]
                job["created_at"] = scan_meta["created_at"]
                owner=self.db.execute("SELECT tenant_id,subject_id FROM scans WHERE scan_id=?",(row["scan_id"],)).fetchone()
                job["tenant_id"],job["subject_id"]=owner["tenant_id"],owner["subject_id"]
                self.db.execute("COMMIT")
                return job
            except Exception:
                self._rollback_safely()
                raise

    @_db_operation("register_media", transaction=True)
    def register_media(self, *, media_id: str, tenant_id: str, subject_id: str, modality: str,
                       content_type: str, size_bytes: int, sha256: str, relative_path: str,
                       max_total_bytes: int, max_tenant_bytes: int) -> None:
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                total=self.db.execute("SELECT COALESCE(SUM(size_bytes),0) FROM media_assets").fetchone()[0]
                tenant=self.db.execute("SELECT COALESCE(SUM(size_bytes),0) FROM media_assets WHERE tenant_id=?",(tenant_id,)).fetchone()[0]
                if total+size_bytes>max_total_bytes or tenant+size_bytes>max_tenant_bytes: raise ValueError("media storage quota exceeded")
                self.db.execute("INSERT INTO media_assets VALUES(?,?,?,?,?,?,?,?,?)",(media_id,tenant_id,subject_id,modality,content_type,size_bytes,sha256,relative_path,time.time()))
                self.db.execute("COMMIT")
            except Exception:
                self._rollback_safely()
                raise

    @_db_operation("get_media_by_ref")
    def get_media_by_ref(self, reference: str, tenant_id: str, subject_id: str) -> dict[str,Any] | None:
        import hashlib
        prefix="media://"+hashlib.sha256(tenant_id.encode()).hexdigest()[:24]+"/"
        if not isinstance(reference,str) or not reference.startswith(prefix): return None
        media_id=reference[len(prefix):]
        row=self.db.execute("SELECT * FROM media_assets WHERE media_id=? AND tenant_id=? AND subject_id=?",(media_id,tenant_id,subject_id)).fetchone()
        return dict(row) if row else None

    @_db_operation("delete_media_record")
    def delete_media_record(self, media_id: str, tenant_id: str, subject_id: str):
        with self._lock:
            row=self.db.execute("SELECT * FROM media_assets WHERE media_id=? AND tenant_id=? AND subject_id=?",(media_id,tenant_id,subject_id)).fetchone()
            if not row:return None
            self.db.execute("DELETE FROM media_assets WHERE media_id=? AND tenant_id=? AND subject_id=?",(media_id,tenant_id,subject_id))
            return dict(row)

    @_db_operation("list_expired_media")
    def list_expired_media(self, cutoff_epoch: float):
        rows=self.db.execute("SELECT * FROM media_assets WHERE created_at<?",(cutoff_epoch,)).fetchall()
        active=self.db.execute("SELECT tenant_id,payload_json FROM scans WHERE status IN ('queued','processing')").fetchall()
        referenced=set()
        for scan in active:
            try: referenced.update(json.loads(scan["payload_json"]).get("media_refs",[]))
            except (ValueError,TypeError): continue
        return [dict(row) for row in rows if f"media://{hashlib.sha256(row['tenant_id'].encode()).hexdigest()[:24]}/{row['media_id']}" not in referenced]

    @_db_operation("list_terminal_scan_media_refs")
    def list_terminal_scan_media_refs(self, limit: int = 500, after_scan_id: str | None = None,
                                      after_reference: str | None = None, terminal_before: float | None = None):
        rows=self.db.execute("""
            SELECT s.scan_id,s.tenant_id,s.subject_id,media_ref.value AS reference
            FROM scans AS s
            JOIN json_each(
                CASE WHEN json_valid(s.payload_json) THEN s.payload_json ELSE '{}' END,
                '$.media_refs'
            ) AS media_ref
            WHERE s.status IN ('completed','failed','cancelled')
              AND media_ref.type='text' AND substr(media_ref.value,1,8)='media://'
              -- The cycle cutoff is exclusive: equal timestamps belong to the
              -- next cycle because wall clocks cannot order events within one tick.
              AND (? IS NULL OR s.updated_at < ?)
              AND (? IS NULL OR s.scan_id > ? OR (s.scan_id=? AND media_ref.value>?))
            ORDER BY s.scan_id,media_ref.value
            LIMIT ?
        """,(terminal_before,terminal_before,after_scan_id,after_scan_id,after_scan_id,after_reference or "",max(1,min(int(limit),5000)))).fetchall()
        return [{"scan_id":row["scan_id"],"tenant_id":row["tenant_id"],"subject_id":row["subject_id"],"reference":row["reference"]} for row in rows]

    @_db_operation("begin_media_reconciliation", transaction=True)
    def begin_media_reconciliation(self, owner: str, lease_seconds: int=300):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            now=time.time()
            state=self.db.execute("SELECT cycle_started_at,after_scan_id,after_reference,lease_owner,lease_until FROM media_reconciliation_state WHERE singleton=1").fetchone()
            if state["lease_owner"] and state["lease_until"] is not None and state["lease_until"]>now:
                self.db.execute("COMMIT")
                return None
            cycle_started_at=state["cycle_started_at"] if state["cycle_started_at"] is not None else now
            self.db.execute("UPDATE media_reconciliation_state SET cycle_started_at=?,lease_owner=?,lease_until=? WHERE singleton=1",(cycle_started_at,owner,now+max(1,int(lease_seconds))))
            self.db.execute("COMMIT")
            return {"cycle_started_at":cycle_started_at,"after_scan_id":state["after_scan_id"],"after_reference":state["after_reference"]}
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    @_db_operation("advance_media_reconciliation", transaction=True)
    def advance_media_reconciliation(self, owner: str, after_scan_id: str, after_reference: str, lease_seconds: int=300) -> bool:
        cursor=self.db.execute("UPDATE media_reconciliation_state SET after_scan_id=?,after_reference=?,lease_until=(julianday('now')-2440587.5)*86400.0+? WHERE singleton=1 AND lease_owner=? AND lease_until>(julianday('now')-2440587.5)*86400.0",(after_scan_id,after_reference,max(1,int(lease_seconds)),owner))
        return cursor.rowcount==1

    @_db_operation("renew_media_reconciliation", transaction=True)
    def renew_media_reconciliation(self, owner: str, lease_seconds: int=300) -> bool:
        cursor=self.db.execute("UPDATE media_reconciliation_state SET lease_until=(julianday('now')-2440587.5)*86400.0+? WHERE singleton=1 AND lease_owner=? AND lease_until>(julianday('now')-2440587.5)*86400.0",(max(1,int(lease_seconds)),owner))
        return cursor.rowcount==1

    @_db_operation("finish_media_reconciliation_cycle", transaction=True)
    def finish_media_reconciliation_cycle(self, owner: str) -> bool:
        cursor=self.db.execute("UPDATE media_reconciliation_state SET cycle_started_at=NULL,after_scan_id=NULL,after_reference=NULL,lease_owner=NULL,lease_until=NULL WHERE singleton=1 AND lease_owner=? AND lease_until>(julianday('now')-2440587.5)*86400.0",(owner,))
        return cursor.rowcount==1

    @_db_operation("release_media_reconciliation", transaction=True)
    def release_media_reconciliation(self, owner: str) -> bool:
        cursor=self.db.execute("UPDATE media_reconciliation_state SET lease_owner=NULL,lease_until=NULL WHERE singleton=1 AND lease_owner=?",(owner,))
        return cursor.rowcount==1

    @_db_operation("remove_terminal_scan_media_refs", transaction=True)
    def remove_terminal_scan_media_refs(self, owner: str, scan_id: str, references: list[str]) -> int:
        if not references:
            return 0
        self.db.execute("BEGIN IMMEDIATE")
        try:
            lease=self.db.execute("SELECT lease_owner,lease_until FROM media_reconciliation_state WHERE singleton=1").fetchone()
            if lease["lease_owner"]!=owner or lease["lease_until"] is None or lease["lease_until"]<=time.time():
                raise RuntimeError("media reconciliation lease is no longer valid")
            row=self.db.execute("SELECT payload_json,tenant_id,subject_id FROM scans WHERE scan_id=? AND status IN ('completed','failed','cancelled')",(scan_id,)).fetchone()
            if not row:
                self.db.execute("COMMIT")
                return 0
            try:
                payload=json.loads(row["payload_json"])
            except (ValueError,TypeError):
                self.db.execute("COMMIT")
                return 0
            media_refs=payload.get("media_refs") if isinstance(payload,dict) else None
            if not isinstance(media_refs,list):
                self.db.execute("COMMIT")
                return 0
            removed=set(references)
            remaining=[ref for ref in media_refs if ref not in removed]
            count=len(media_refs)-len(remaining)
            if count:
                payload["media_refs"]=remaining
                self.db.execute("UPDATE scans SET payload_json=? WHERE scan_id=? AND status IN ('completed','failed','cancelled')",(json.dumps(payload,separators=(",",":")),scan_id))
                # Media metadata is deleted under the same live-lease transaction as
                # the payload reference; a stale storage operation cannot write it.
                for reference in removed:
                    if isinstance(reference,str) and reference.startswith("media://"):
                        media_id=reference.rsplit("/",1)[-1]
                        self.db.execute("DELETE FROM media_assets WHERE media_id=? AND tenant_id=? AND subject_id=?",
                                        (media_id,row["tenant_id"],row["subject_id"]))
            self.db.execute("COMMIT")
            return count
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    @_db_operation("get_scan_owner")
    def get_scan_owner(self, scan_id: str) -> dict[str,str] | None:
        row=self.db.execute("SELECT tenant_id,subject_id FROM scans WHERE scan_id=?",(scan_id,)).fetchone()
        return dict(row) if row else None

    @_db_operation("complete", transaction=True)
    def complete(self, job: dict, result: dict):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                now=time.time()
                current=self.db.execute("SELECT * FROM jobs WHERE job_id=?",(job["job_id"],)).fetchone()
                if not current or current["status"]!="running" or current["fencing_token"]!=job["fencing_token"] or current["worker_id"]!=job["worker_id"] or current["lease_until"] < now:
                    raise RuntimeError("stale worker lease")
                scan=self.db.execute("SELECT * FROM scans WHERE scan_id=?",(job["scan_id"],)).fetchone()
                self.db.execute("INSERT OR IGNORE INTO results VALUES(?,?,?,?,?)",(job["scan_id"],scan["tenant_id"],scan["subject_id"],json.dumps(result,sort_keys=True),now))
                self.db.execute("UPDATE jobs SET status='completed',lease_until=NULL WHERE job_id=?",(job["job_id"],))
                self.db.execute("UPDATE scans SET status='completed',updated_at=? WHERE scan_id=?",(now,job["scan_id"]))
                self.db.execute("INSERT OR IGNORE INTO outbox(event_id,scan_id,event_type,payload_json,created_at,published_at,available_at) VALUES(?,?,?,?,?,NULL,?)",(new_id(),job["scan_id"],"scan.completed",json.dumps({"scan_id":job["scan_id"]}),now,now))
                self.db.execute("COMMIT")
            except Exception:
                self._rollback_safely()
                raise

    @_db_operation("bind_directus_scan", transaction=True)
    def bind_directus_scan(self, evidence) -> bool:
        from app.integrations.directus_sync import DirectusBindingEvidence
        if not isinstance(evidence,DirectusBindingEvidence): raise ValueError("verified Directus binding evidence is required")
        evidence.validate()
        scan_id,directus_scan_id,tenant_id,subject_id=evidence.scan_id,evidence.directus_scan_id,evidence.tenant_id,evidence.subject_id
        now=evidence.verified_at
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                owner=self.db.execute("SELECT tenant_id,subject_id FROM scans WHERE scan_id=?",(scan_id,)).fetchone()
                if not owner or owner["tenant_id"]!=tenant_id or owner["subject_id"]!=subject_id:
                    raise ValueError("scan ownership does not match verified Directus identity")
                old=self.db.execute("SELECT directus_scan_id,tenant_id,subject_id FROM directus_scan_bindings WHERE scan_id=?",(scan_id,)).fetchone()
                if old:
                    if tuple(old)==(directus_scan_id,tenant_id,subject_id):
                        self.db.execute("COMMIT")
                        return False
                    raise ValueError("V2 scan already has a different Directus binding")
                self.db.execute("INSERT INTO directus_scan_bindings(scan_id,directus_scan_id,tenant_id,subject_id,remote_owner_id,remote_tenant_id,remote_scan_status,membership_active,media_owner_verified,verification_version,verified_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(scan_id,directus_scan_id,tenant_id,subject_id,evidence.remote_owner_id,evidence.remote_tenant_id,evidence.remote_status,int(evidence.membership_active),int(evidence.media_owner_verified),"directus-binding-v1",now))
                self.db.execute("COMMIT")
                return True
            except sqlite3.IntegrityError:
                self._rollback_safely()
                raise ValueError("Directus scan is already bound to another V2 scan") from None
            except Exception:
                self._rollback_safely()
                raise

    @_db_operation("get_directus_binding")
    def get_directus_binding(self, scan_id: str, tenant_id: str, subject_id: str):
        row=self.db.execute("SELECT scan_id,directus_scan_id,tenant_id,subject_id,remote_owner_id,remote_tenant_id,remote_scan_status,membership_active,media_owner_verified,verification_version,verified_at FROM directus_scan_bindings WHERE scan_id=? AND tenant_id=? AND subject_id=?",(scan_id,tenant_id,subject_id)).fetchone()
        return dict(row) if row else None

    @_db_operation("set_directus_deletion_state")
    def set_directus_deletion_state(self, scan_id: str, state: str) -> bool:
        if state not in {"pending","proven"}:raise ValueError("invalid Directus deletion state")
        row=self.db.execute("SELECT payload_json FROM scans WHERE scan_id=?",(scan_id,)).fetchone()
        if not row:return False
        payload=json.loads(row["payload_json"])
        if not isinstance(payload,dict):return False
        payload["_directus_deletion_state"]=state
        cursor=self.db.execute("UPDATE scans SET payload_json=?,updated_at=? WHERE scan_id=?",(json.dumps(payload,sort_keys=True,separators=(",",":")),time.time(),scan_id))
        return cursor.rowcount==1

    @_db_operation("list_pending_directus_deletions")
    def list_pending_directus_deletions(self, limit: int=100):
        rows=self.db.execute("SELECT scan_id,payload_json FROM scans WHERE status='failed' ORDER BY updated_at LIMIT ?",(max(1,min(int(limit),500)),)).fetchall()
        pending=[]
        for row in rows:
            try:payload=json.loads(row["payload_json"])
            except (TypeError,ValueError):continue
            if isinstance(payload,dict) and payload.get("_directus_deletion_state")=="pending" and isinstance(payload.get("_directus_pull"),dict):
                pending.append({"scan_id":row["scan_id"],"directus_pull":payload["_directus_pull"]})
        return pending

    @_db_operation("claim_directus_outbox", transaction=True)
    def claim_directus_outbox(self, publisher_id: str, *, lease_seconds: float=30, max_attempts: int=8, now: float | None=None):
        if not publisher_id or not 0<lease_seconds<=600 or not 1<=max_attempts<=12: raise ValueError("publisher limits are invalid")
        instant=time.time() if now is None else now
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute("UPDATE outbox SET delivery_status='dead',lease_until=NULL,publisher_id=NULL,last_error_code='retry_exhausted' WHERE event_type IN ('scan.completed','scan.failed') AND published_at IS NULL AND attempts>=? AND (delivery_status='pending' OR (delivery_status='running' AND lease_until<=?)) AND event_id IN (SELECT o.event_id FROM outbox o JOIN directus_scan_bindings b ON b.scan_id=o.scan_id)",(max_attempts,instant))
                row=self.db.execute("""SELECT o.event_id,o.scan_id,o.event_type,o.payload_json,o.attempts,o.fencing_token,
                    b.directus_scan_id,b.tenant_id,b.subject_id,r.result_json
                    FROM outbox o JOIN directus_scan_bindings b ON b.scan_id=o.scan_id
                    LEFT JOIN results r ON r.scan_id=o.scan_id
                    WHERE o.event_type IN ('scan.completed','scan.failed') AND o.published_at IS NULL AND o.attempts<? AND o.available_at<=?
                      AND (o.delivery_status='pending' OR (o.delivery_status='running' AND o.lease_until<=?))
                    ORDER BY o.created_at,o.event_id LIMIT 1""",(max_attempts,instant,instant)).fetchone()
                if not row:
                    self.db.execute("COMMIT")
                    return None
                fence=row["fencing_token"]+1
                self.db.execute("UPDATE outbox SET delivery_status='running',attempts=attempts+1,lease_until=?,fencing_token=?,publisher_id=? WHERE event_id=?",(instant+lease_seconds,fence,publisher_id,row["event_id"]))
                claimed=dict(row)
                claimed.update({"attempts":row["attempts"]+1,"fencing_token":fence,"publisher_id":publisher_id})
                claimed["payload_json"]=json.loads(claimed["payload_json"])
                if claimed["result_json"] is not None: claimed["result_json"]=json.loads(claimed["result_json"])
                self.db.execute("COMMIT")
                return claimed
            except Exception:
                self._rollback_safely()
                raise

    @_db_operation("ack_directus_outbox", transaction=True)
    def ack_directus_outbox(self, event_id: str, publisher_id: str, fencing_token: int,
                            directus_scan_id: str, remote_status: str, remote_result_scan_id: str | None,
                            now: float | None=None) -> bool:
        instant=time.time() if now is None else now
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row=self.db.execute("SELECT o.*,b.directus_scan_id expected_id FROM outbox o JOIN directus_scan_bindings b ON b.scan_id=o.scan_id WHERE o.event_id=?",(event_id,)).fetchone()
                if (not row or row["delivery_status"]!="running" or row["publisher_id"]!=publisher_id
                    or row["fencing_token"]!=fencing_token or row["lease_until"] is None
                    or row["lease_until"]<instant or row["expected_id"]!=directus_scan_id
                    or (row["event_type"]=="scan.completed" and (directus_scan_id!=remote_result_scan_id or remote_status!="completed"))
                    or (row["event_type"]=="scan.failed" and (remote_result_scan_id is not None or remote_status!="failed"))):
                    self.db.execute("COMMIT")
                    return False
                self.db.execute("UPDATE outbox SET delivery_status='delivered',published_at=?,lease_until=NULL,publisher_id=NULL,last_error_code=NULL WHERE event_id=?",(instant,event_id))
                self.db.execute("COMMIT")
                return True
            except Exception:
                self._rollback_safely()
                raise

    @_db_operation("fail_directus_outbox", transaction=True)
    def fail_directus_outbox(self, event_id: str, publisher_id: str, fencing_token: int, error_code: str,
                             *, max_attempts: int=8, retry_base_seconds: float=1, now: float | None=None, permanent: bool=False) -> bool:
        allowed={"directus_unavailable","directus_timeout","directus_forbidden","directus_schema_mismatch","remote_state_mismatch","unsafe_result_contract","directus_sync_failed","directus_invalid_payload"}
        code=error_code if error_code in allowed else "directus_sync_failed"
        instant=time.time() if now is None else now
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row=self.db.execute("SELECT attempts,delivery_status,publisher_id,fencing_token,lease_until FROM outbox WHERE event_id=?",(event_id,)).fetchone()
                valid=(row and row["delivery_status"]=="running" and row["publisher_id"]==publisher_id
                       and row["fencing_token"]==fencing_token and row["lease_until"] is not None and row["lease_until"]>=instant)
                if not valid:
                    self.db.execute("COMMIT")
                    return False
                dead=permanent or row["attempts"]>=max_attempts
                if not 0<retry_base_seconds<=60 or not 1<=max_attempts<=12: raise ValueError("publisher retry limits are invalid")
                delay=min(300,retry_base_seconds*(2**min(8,max(0,row["attempts"]-1))))
                self.db.execute("UPDATE outbox SET delivery_status=?,available_at=?,lease_until=NULL,publisher_id=NULL,last_error_code=? WHERE event_id=?",("dead" if dead else "pending",instant+delay,code,event_id))
                self.db.execute("COMMIT")
                return True
            except Exception:
                self._rollback_safely()
                raise

    @_db_operation("heartbeat")
    def heartbeat(self, job: dict) -> bool:
        now=time.time()
        with self._lock:
            cursor=self.db.execute("UPDATE jobs SET lease_until=? WHERE job_id=? AND status='running' AND fencing_token=? AND worker_id=? AND lease_until>=?",(now+self.lease_seconds,job["job_id"],job["fencing_token"],job["worker_id"],now))
            return cursor.rowcount==1

    @_db_operation("fail", transaction=True)
    def fail(self, job: dict, error_code: str, now: float | None = None, *, force_terminal: bool=False):
        supplied_now=now
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                now=supplied_now if supplied_now is not None else time.time()
                row=self.db.execute("SELECT * FROM jobs WHERE job_id=?",(job["job_id"],)).fetchone()
                if not row or row["status"]!="running" or row["fencing_token"]!=job["fencing_token"] or row["worker_id"]!=job["worker_id"] or row["lease_until"] < now:
                    raise RuntimeError("stale worker lease")
                if force_terminal or row["attempts"]>=self.max_attempts:
                    status="failed"; available=now
                else:
                    status="queued"; available=now+self.retry_base_seconds*(2**(row["attempts"]-1))
                self.db.execute("UPDATE jobs SET status=?,available_at=?,lease_until=NULL,worker_id=NULL,last_error=? WHERE job_id=?",(status,available,error_code[:80],job["job_id"]))
                updated_at=time.time() if status=="failed" else now
                self.db.execute("UPDATE scans SET status=?,updated_at=? WHERE scan_id=?",(status,updated_at,job["scan_id"]))
                if status=="failed":
                    self.db.execute("INSERT OR IGNORE INTO outbox(event_id,scan_id,event_type,payload_json,created_at,published_at,available_at) VALUES(?,?,?,?,?,NULL,?)",(new_id(),job["scan_id"],"scan.failed",json.dumps({"scan_id":job["scan_id"],"error_code":error_code[:80]}),now,now))
                self.db.execute("COMMIT")
            except Exception:
                self._rollback_safely()
                raise

    @_db_operation("get_scan")
    def get_scan(self, scan_id: str, tenant_id: str, subject_id: str):
        row=self.db.execute("SELECT * FROM scans WHERE scan_id=? AND tenant_id=? AND subject_id=?",(scan_id,tenant_id,subject_id)).fetchone()
        if not row: return None
        from datetime import datetime, timezone
        return {"scan_id":row["scan_id"],"status":row["status"],"created_at":datetime.fromtimestamp(row["created_at"],timezone.utc).isoformat(),"updated_at":datetime.fromtimestamp(row["updated_at"],timezone.utc).isoformat()}

    @_db_operation("get_result")
    def get_result(self, scan_id: str, tenant_id: str, subject_id: str):
        row=self.db.execute("SELECT result_json FROM results WHERE scan_id=? AND tenant_id=? AND subject_id=?",(scan_id,tenant_id,subject_id)).fetchone()
        return json.loads(row[0]) if row else None

    @_db_operation("store_training_label", transaction=True)
    def store_training_label(self, scan_id: str, tenant_id: str, subject_id: str, kss: int, features: dict, *, fatigue=None, focus=None, shift=None, time_of_day=None, expires_at=None, consent_text_version="wellar-v2-training-consent-v1") -> None:
        # The feature-only table has no media reference, path, or media payload column.
        owner=self.db.execute("SELECT 1 FROM scans WHERE scan_id=? AND tenant_id=? AND subject_id=? AND status='completed'",(scan_id,tenant_id,subject_id)).fetchone()
        if not owner: raise ValueError("completed owned scan is required")
        self.db.execute("INSERT INTO training_labels(scan_id,tenant_id,subject_id,kss,features_json,created_at,fatigue,focus,shift,time_of_day,expires_at,consented_at,consent_text_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(scan_id) DO UPDATE SET kss=excluded.kss,features_json=excluded.features_json,created_at=excluded.created_at,fatigue=excluded.fatigue,focus=excluded.focus,shift=excluded.shift,time_of_day=excluded.time_of_day,expires_at=excluded.expires_at,consented_at=excluded.consented_at,consent_text_version=excluded.consent_text_version WHERE training_labels.tenant_id=excluded.tenant_id AND training_labels.subject_id=excluded.subject_id",
                        (scan_id,tenant_id,subject_id,kss,json.dumps(features,separators=(",",":"),allow_nan=False),time.time(),fatigue,focus,shift,time_of_day,expires_at,time.time(),consent_text_version))

    @_db_operation("delete_training_label", transaction=True)
    def delete_training_label(self,scan_id,tenant_id,subject_id):
        cursor=self.db.execute("DELETE FROM training_labels WHERE scan_id=? AND tenant_id=? AND subject_id=?",(scan_id,tenant_id,subject_id))
        return cursor.rowcount > 0

    @_db_operation("delete_training_labels", transaction=True)
    def delete_training_labels(self,tenant_id,subject_id):
        cursor=self.db.execute("DELETE FROM training_labels WHERE tenant_id=? AND subject_id=?",(tenant_id,subject_id))
        return cursor.rowcount

    @_db_operation("purge_expired_training_labels", transaction=True)
    def purge_expired_training_labels(self,now=None):
        cursor=self.db.execute("DELETE FROM training_labels WHERE expires_at IS NOT NULL AND expires_at<=?",(time.time() if now is None else now,))
        return cursor.rowcount

    @_db_operation("get_indicator_history")
    def get_indicator_history(self, tenant_id: str, subject_id: str, limit: int = 100) -> list[dict]:
        rows=self.db.execute("SELECT r.result_json,r.created_at FROM results r JOIN scans s USING(scan_id) WHERE r.tenant_id=? AND r.subject_id=? AND s.status='completed' ORDER BY r.created_at DESC LIMIT ?",(tenant_id,subject_id,min(max(limit,1),100))).fetchall()
        history=[]
        for row in rows:
            try:
                result=json.loads(row["result_json"]); indicator=result.get("evidence",{}).get("drowsiness_indicator",{})
                if (indicator.get("engine_type")=="rule_based_indicators"
                        and indicator.get("analysis_status")=="prediction_available"):
                    history.append({"measurements":indicator.get("raw_measurements",{}),
                        "created_at":row["created_at"],"status":indicator.get("analysis_status"),
                        "engine_type":indicator.get("engine_type"),"engine_version":indicator.get("model_version")})
            except (TypeError,ValueError,AttributeError):
                continue
        return history

    @_db_operation("readiness")
    def readiness(self) -> bool:
        self.db.execute("SELECT 1").fetchone()
        return True

    @_db_operation("diagnostics")
    def diagnostics(self) -> dict[str, int | float | None]:
        """Return aggregate queue health without exposing job or tenant identifiers."""
        now = time.time()
        with self._lock:
            row = self.db.execute("""
                SELECT
                  SUM(CASE WHEN status='queued' THEN 1 ELSE 0 END) AS queued,
                  SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) AS running,
                  SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS terminal_failures,
                  SUM(CASE WHEN last_error IS NOT NULL THEN 1 ELSE 0 END) AS retries,
                  MIN(CASE WHEN status='queued' THEN available_at END) AS oldest_queued_at
                FROM jobs
            """).fetchone()
        oldest = row["oldest_queued_at"]
        return {
            "queued": row["queued"] or 0,
            "running": row["running"] or 0,
            "terminal_failures": row["terminal_failures"] or 0,
            "retries": row["retries"] or 0,
            "oldest_queued_age_seconds": round(max(0.0, now-oldest), 3) if oldest is not None else None,
        }

    @_db_operation("close")
    def close(self) -> None:
        with self._lock: self.db.close()
