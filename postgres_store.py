from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from psycopg.errors import UniqueViolation

from app.core.errors import QueueFull
from app.core.ids import new_id
from app.core.logging import event
from app.observability.metrics import worker_metrics

logger=logging.getLogger("wellar.queue.postgres")


class PostgresQueueStore:
    """Shared PostgreSQL queue; delivery is at-least-once, not exactly-once."""
    def __init__(self, dsn: str, *, capacity: int=1000, max_attempts: int=3,
                 lease_seconds: int=30, retry_base_seconds: float=1.0,
                 min_pool_size: int=1, max_pool_size: int=10, pool_timeout: float=5.0,
                 connect_timeout: int=5, statement_timeout_ms: int=5000, migrate: bool=True,schema:str|None=None):
        if not 1 <= min_pool_size <= max_pool_size or max_pool_size > 64:
            raise ValueError("invalid bounded PostgreSQL pool size")
        if min(capacity,max_attempts,lease_seconds,connect_timeout,statement_timeout_ms) < 1 or pool_timeout <= 0 or retry_base_seconds <= 0:
            raise ValueError("PostgreSQL queue limits and timeouts must be positive")
        self.capacity,self.max_attempts=capacity,max_attempts
        self.lease_seconds,self.retry_base_seconds=lease_seconds,retry_base_seconds
        if schema is not None and (not schema.replace("_","").isalnum() or schema[0].isdigit()):raise ValueError("PostgreSQL schema name must be a simple identifier")
        self.schema=schema
        options=f"-c statement_timeout={statement_timeout_ms} -c lock_timeout={statement_timeout_ms}"
        if schema:options+=f" -c search_path={schema},public"
        self.pool=ConnectionPool(conninfo=dsn,min_size=min_pool_size,max_size=max_pool_size,timeout=pool_timeout,
            kwargs={"connect_timeout":connect_timeout,"options":options,"row_factory":dict_row},open=False)
        try:
            self.pool.open(wait=True,timeout=pool_timeout)
            if migrate:self.migrate()
        except Exception:
            self.pool.close()
            raise

    def migrate(self):
        with self.pool.connection() as conn,conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(728193441)")
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
            applied={row["version"] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}
            root=Path(__file__).resolve().parents[2]/"deployment"/"migrations"
            for version in (1,2):
                if version not in applied:
                    name="initial" if version==1 else "directus_sync"
                    conn.execute((root/f"{version:03d}_{name}.up.sql").read_text(encoding="utf-8"))
                    conn.execute("INSERT INTO schema_migrations(version) VALUES(%s)",(version,))

    def migrate_down(self):
        with self.pool.connection() as conn,conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(728193441)")
            if conn.execute("SELECT to_regclass('schema_migrations') AS table_name").fetchone()["table_name"] is None:return
            for version,name in ((2,"directus_sync"),(1,"initial")):
                if conn.execute("SELECT 1 FROM schema_migrations WHERE version=%s",(version,)).fetchone():
                    root=Path(__file__).resolve().parents[2]/"deployment"/"migrations"
                    conn.execute((root/f"{version:03d}_{name}.down.sql").read_text(encoding="utf-8"))
                    conn.execute("DELETE FROM schema_migrations WHERE version=%s",(version,))

    def submit(self, scan_id, principal, key, payload_hash, payload_json, request_id="-"):
        started=time.perf_counter()
        with self.pool.connection() as conn, conn.transaction():
            lock_key=json.dumps([principal.tenant_id,principal.subject_id,key],separators=(",",":"))
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(lock_key,))
            conn.execute("SELECT pg_advisory_xact_lock(728193442)")
            old=conn.execute("SELECT * FROM scans WHERE tenant_id=%s AND subject_id=%s AND idempotency_key=%s FOR UPDATE",(principal.tenant_id,principal.subject_id,key)).fetchone()
            if old:
                old["payload_json"]=json.dumps(old["payload_json"])
                return old,False
            pending=conn.execute("SELECT count(*) AS n FROM jobs WHERE status IN ('queued','running')").fetchone()["n"]
            if pending>=self.capacity: raise QueueFull()
            job_id=new_id()
            conn.execute("INSERT INTO scans(scan_id,tenant_id,subject_id,idempotency_key,payload_hash,payload_json,job_id,status,request_id) VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s,'queued',%s)",(scan_id,principal.tenant_id,principal.subject_id,key,payload_hash,payload_json,job_id,request_id))
            conn.execute("INSERT INTO jobs(job_id,scan_id,status) VALUES(%s,%s,'queued')",(job_id,scan_id))
            row=conn.execute("SELECT * FROM scans WHERE scan_id=%s",(scan_id,)).fetchone(); row["payload_json"]=json.dumps(row["payload_json"])
            event(logger,"queue.postgres.accepted",processing_stage="queue_accept",scan_id=scan_id,job_id=job_id,duration_ms=round((time.perf_counter()-started)*1000,3))
            worker_metrics.increment("queue","accepted")
            return row,True

    def claim(self, worker_id: str, now: float|None=None):
        started=time.perf_counter()
        instant=datetime.fromtimestamp(now,timezone.utc) if now is not None else datetime.now(timezone.utc)
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("UPDATE jobs SET status='failed',last_error='lease_expired_retry_exhausted',lease_until=NULL,worker_id=NULL WHERE status='running' AND lease_until < %s AND attempts >= %s",(instant,self.max_attempts))
            conn.execute("UPDATE scans SET status='failed',updated_at=%s WHERE scan_id IN (SELECT scan_id FROM jobs WHERE status='failed') AND status!='completed'",(instant,))
            conn.execute("UPDATE jobs SET status='queued',worker_id=NULL,lease_until=NULL,available_at=%s WHERE status='running' AND lease_until < %s AND attempts < %s",(instant,instant,self.max_attempts))
            conn.execute("UPDATE scans SET status='queued',updated_at=%s WHERE scan_id IN (SELECT scan_id FROM jobs WHERE status='queued') AND status='processing'",(instant,))
            row=conn.execute("SELECT * FROM jobs WHERE status='queued' AND available_at<=%s AND attempts<%s ORDER BY available_at LIMIT 1 FOR UPDATE SKIP LOCKED",(instant,self.max_attempts)).fetchone()
            if not row: return None
            lease=instant+timedelta(seconds=self.lease_seconds)
            updated=conn.execute("UPDATE jobs SET status='running',attempts=attempts+1,worker_id=%s,lease_until=%s,fencing_token=fencing_token+1 WHERE job_id=%s RETURNING *",(worker_id,lease,row["job_id"])).fetchone()
            conn.execute("UPDATE scans SET status='processing',updated_at=%s WHERE scan_id=%s",(instant,row["scan_id"]))
            scan=conn.execute("SELECT payload_json,request_id,created_at,tenant_id,subject_id FROM scans WHERE scan_id=%s",(row["scan_id"],)).fetchone()
            updated.update(scan); updated["payload_json"]=json.dumps(scan["payload_json"])
            updated["created_at"]=scan["created_at"].timestamp()
            event(logger,"queue.postgres.claimed",processing_stage="queue_claim",scan_id=updated["scan_id"],job_id=updated["job_id"],worker_id=worker_id,duration_ms=round((time.perf_counter()-started)*1000,3),attempt=updated["attempts"])
            worker_metrics.increment("queue","claimed")
            return updated

    def heartbeat(self,job):
        with self.pool.connection() as conn:
            row=conn.execute("UPDATE jobs SET lease_until=now()+(%s * interval '1 second') WHERE job_id=%s AND status='running' AND fencing_token=%s AND worker_id=%s AND lease_until>=now() RETURNING job_id",(self.lease_seconds,job["job_id"],job["fencing_token"],job["worker_id"])).fetchone()
            return row is not None

    def complete(self,job,result):
        started=time.perf_counter()
        with self.pool.connection() as conn,conn.transaction():
            row=conn.execute("SELECT * FROM jobs WHERE job_id=%s FOR UPDATE",(job["job_id"],)).fetchone()
            if not row or row["status"]!="running" or row["fencing_token"]!=job["fencing_token"] or row["worker_id"]!=job["worker_id"] or row["lease_until"]<datetime.now(timezone.utc): raise RuntimeError("stale worker lease")
            scan=conn.execute("SELECT tenant_id,subject_id FROM scans WHERE scan_id=%s",(job["scan_id"],)).fetchone()
            conn.execute("INSERT INTO results(scan_id,tenant_id,subject_id,result_json) VALUES(%s,%s,%s,%s::jsonb) ON CONFLICT(scan_id) DO NOTHING",(job["scan_id"],scan["tenant_id"],scan["subject_id"],json.dumps(result,sort_keys=True)))
            conn.execute("UPDATE jobs SET status='completed',lease_until=NULL WHERE job_id=%s",(job["job_id"],))
            conn.execute("UPDATE scans SET status='completed',updated_at=now() WHERE scan_id=%s",(job["scan_id"],))
            conn.execute("INSERT INTO outbox(event_id,scan_id,event_type,payload_json) VALUES(%s,%s,'scan.completed',%s::jsonb) ON CONFLICT(scan_id,event_type) DO NOTHING",(new_id(),job["scan_id"],json.dumps({"scan_id":job["scan_id"]})))
        event(logger,"queue.postgres.completed",processing_stage="queue_commit",scan_id=job["scan_id"],job_id=job["job_id"],worker_id=job["worker_id"],duration_ms=round((time.perf_counter()-started)*1000,3))
        worker_metrics.increment("queue","completed")

    def bind_directus_scan(self, evidence):
        from app.integrations.directus_sync import DirectusBindingEvidence
        if not isinstance(evidence,DirectusBindingEvidence): raise ValueError("verified Directus binding evidence is required")
        evidence.validate()
        scan_id,directus_scan_id,tenant_id,subject_id=evidence.scan_id,evidence.directus_scan_id,evidence.tenant_id,evidence.subject_id
        instant=datetime.fromtimestamp(evidence.verified_at,timezone.utc)
        try:
            with self.pool.connection() as conn,conn.transaction():
                owner=conn.execute("SELECT tenant_id,subject_id FROM scans WHERE scan_id=%s FOR UPDATE",(scan_id,)).fetchone()
                if not owner or owner["tenant_id"]!=tenant_id or owner["subject_id"]!=subject_id:
                    raise ValueError("scan ownership does not match verified Directus identity")
                old=conn.execute("SELECT directus_scan_id,tenant_id,subject_id FROM directus_scan_bindings WHERE scan_id=%s FOR UPDATE",(scan_id,)).fetchone()
                if old:
                    if tuple(old.values())==(directus_scan_id,tenant_id,subject_id): return False
                    raise ValueError("V2 scan already has a different Directus binding")
                conn.execute("INSERT INTO directus_scan_bindings(scan_id,directus_scan_id,tenant_id,subject_id,remote_owner_id,remote_tenant_id,remote_scan_status,membership_active,media_owner_verified,verification_version,verified_at) VALUES(%s,%s,%s,%s,%s,%s,%s,true,true,'directus-binding-v1',%s)",(scan_id,directus_scan_id,tenant_id,subject_id,evidence.remote_owner_id,evidence.remote_tenant_id,evidence.remote_status,instant))
                return True
        except UniqueViolation:
            raise ValueError("Directus scan is already bound to another V2 scan") from None

    def get_directus_binding(self, scan_id, tenant_id, subject_id):
        with self.pool.connection() as conn:
            return conn.execute("SELECT scan_id,directus_scan_id,tenant_id,subject_id,remote_owner_id,remote_tenant_id,remote_scan_status,membership_active,media_owner_verified,verification_version,verified_at FROM directus_scan_bindings WHERE scan_id=%s AND tenant_id=%s AND subject_id=%s",(scan_id,tenant_id,subject_id)).fetchone()

    def claim_directus_outbox(self, publisher_id, *, lease_seconds=30, max_attempts=8, now=None):
        if not publisher_id or not 0<lease_seconds<=600 or not 1<=max_attempts<=12: raise ValueError("publisher limits are invalid")
        instant=datetime.fromtimestamp(now,timezone.utc) if now is not None else datetime.now(timezone.utc)
        with self.pool.connection() as conn,conn.transaction():
            conn.execute("UPDATE outbox o SET delivery_status='dead',lease_until=NULL,publisher_id=NULL,last_error_code='retry_exhausted' WHERE event_type IN ('scan.completed','scan.failed') AND published_at IS NULL AND attempts >= %s AND (delivery_status='pending' OR (delivery_status='running' AND lease_until<=%s)) AND EXISTS(SELECT 1 FROM directus_scan_bindings b WHERE b.scan_id=o.scan_id)",(max_attempts,instant))
            row=conn.execute("""SELECT o.event_id,o.scan_id,o.event_type,o.payload_json,o.attempts,o.fencing_token,
                b.directus_scan_id,b.tenant_id,b.subject_id,r.result_json
                FROM outbox o JOIN directus_scan_bindings b ON b.scan_id=o.scan_id
                LEFT JOIN results r ON r.scan_id=o.scan_id
                WHERE o.event_type IN ('scan.completed','scan.failed') AND o.published_at IS NULL AND o.attempts < %s AND o.available_at<=%s
                  AND (o.delivery_status='pending' OR (o.delivery_status='running' AND o.lease_until<=%s))
                ORDER BY o.created_at,o.event_id LIMIT 1 FOR UPDATE OF o SKIP LOCKED""",(max_attempts,instant,instant)).fetchone()
            if not row:return None
            lease_until=instant+timedelta(seconds=lease_seconds)
            updated=conn.execute("UPDATE outbox SET delivery_status='running',attempts=attempts+1,lease_until=%s,fencing_token=fencing_token+1,publisher_id=%s WHERE event_id=%s RETURNING attempts,fencing_token",(lease_until,publisher_id,row["event_id"])).fetchone()
            event=dict(row);event.update(updated);event["publisher_id"]=publisher_id
            event["payload_json"]=event["payload_json"] if isinstance(event["payload_json"],dict) else json.loads(event["payload_json"])
            if event["result_json"] is not None and not isinstance(event["result_json"],dict): event["result_json"]=json.loads(event["result_json"])
            return event

    def ack_directus_outbox(self,event_id,publisher_id,fencing_token,directus_scan_id,remote_status,remote_result_scan_id,now=None):
        instant=datetime.fromtimestamp(now,timezone.utc) if now is not None else datetime.now(timezone.utc)
        with self.pool.connection() as conn,conn.transaction():
            row=conn.execute("""UPDATE outbox o SET delivery_status='delivered',published_at=%s,lease_until=NULL,publisher_id=NULL,last_error_code=NULL
                FROM directus_scan_bindings b WHERE o.event_id=%s AND b.scan_id=o.scan_id AND o.delivery_status='running'
                  AND o.publisher_id=%s AND o.fencing_token=%s AND o.lease_until>=%s
                AND b.directus_scan_id=%s
                AND ((o.event_type='scan.completed' AND %s=%s AND %s='completed') OR (o.event_type='scan.failed' AND %s IS NULL AND %s='failed'))
                RETURNING o.event_id""",
                (instant,event_id,publisher_id,fencing_token,instant,directus_scan_id,directus_scan_id,remote_result_scan_id,remote_status,remote_result_scan_id,remote_status)).fetchone()
            return row is not None

    def fail_directus_outbox(self,event_id,publisher_id,fencing_token,error_code,*,max_attempts=8,retry_base_seconds=1,now=None):
        allowed={"directus_unavailable","directus_timeout","directus_forbidden","directus_schema_mismatch","remote_state_mismatch","unsafe_result_contract","directus_sync_failed"}
        code=error_code if error_code in allowed else "directus_sync_failed"
        instant=datetime.fromtimestamp(now,timezone.utc) if now is not None else datetime.now(timezone.utc)
        with self.pool.connection() as conn,conn.transaction():
            row=conn.execute("SELECT attempts FROM outbox WHERE event_id=%s AND delivery_status='running' AND publisher_id=%s AND fencing_token=%s AND lease_until>=%s FOR UPDATE",(event_id,publisher_id,fencing_token,instant)).fetchone()
            if not row:return False
            dead=row["attempts"]>=max_attempts
            if not 0<retry_base_seconds<=60 or not 1<=max_attempts<=12: raise ValueError("publisher retry limits are invalid")
            delay=min(300,retry_base_seconds*(2**min(8,max(0,row["attempts"]-1))))
            conn.execute("UPDATE outbox SET delivery_status=%s,available_at=%s,lease_until=NULL,publisher_id=NULL,last_error_code=%s WHERE event_id=%s",("dead" if dead else "pending",instant+timedelta(seconds=delay),code,event_id))
            return True

    def fail(self,job,error_code,now=None):
        started=time.perf_counter()
        instant=datetime.fromtimestamp(now,timezone.utc) if now is not None else datetime.now(timezone.utc)
        with self.pool.connection() as conn,conn.transaction():
            row=conn.execute("SELECT * FROM jobs WHERE job_id=%s FOR UPDATE",(job["job_id"],)).fetchone()
            if not row or row["status"]!="running" or row["fencing_token"]!=job["fencing_token"] or row["worker_id"]!=job["worker_id"] or row["lease_until"]<instant: raise RuntimeError("stale worker lease")
            terminal=row["attempts"]>=self.max_attempts
            status="failed" if terminal else "queued"
            available=instant+timedelta(seconds=self.retry_base_seconds*(2**max(0,row["attempts"]-1)))
            conn.execute("UPDATE jobs SET status=%s,available_at=%s,lease_until=NULL,worker_id=NULL,last_error=%s WHERE job_id=%s",(status,available,error_code[:80],job["job_id"]))
            conn.execute("UPDATE scans SET status=%s,updated_at=%s WHERE scan_id=%s",(status,instant,job["scan_id"]))
            if terminal:
                conn.execute("INSERT INTO outbox(event_id,scan_id,event_type,payload_json) VALUES(%s,%s,'scan.failed',%s::jsonb) ON CONFLICT(scan_id,event_type) DO NOTHING",(new_id(),job["scan_id"],json.dumps({"scan_id":job["scan_id"]})))
        event(logger,"queue.postgres.retry_scheduled" if status=="queued" else "queue.postgres.terminal_failure",logging.WARNING if status=="queued" else logging.ERROR,processing_stage="queue_retry",scan_id=job["scan_id"],job_id=job["job_id"],worker_id=job["worker_id"],duration_ms=round((time.perf_counter()-started)*1000,3),error_code=error_code[:80])
        worker_metrics.increment("queue","retry_scheduled" if status=="queued" else "terminal_failure")

    def get_scan(self,scan_id,tenant_id,subject_id):
        with self.pool.connection() as conn: row=conn.execute("SELECT scan_id,status,created_at,updated_at FROM scans WHERE scan_id=%s AND tenant_id=%s AND subject_id=%s",(scan_id,tenant_id,subject_id)).fetchone()
        if not row:return None
        return {**row,"created_at":row["created_at"].isoformat(),"updated_at":row["updated_at"].isoformat()}

    def get_result(self,scan_id,tenant_id,subject_id):
        with self.pool.connection() as conn: row=conn.execute("SELECT result_json FROM results WHERE scan_id=%s AND tenant_id=%s AND subject_id=%s",(scan_id,tenant_id,subject_id)).fetchone()
        return row["result_json"] if row else None

    def register_media(self,*,media_id,tenant_id,subject_id,modality,content_type,size_bytes,sha256,relative_path,max_total_bytes,max_tenant_bytes):
        with self.pool.connection() as conn,conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(728193443)")
            usage=conn.execute("SELECT coalesce(sum(size_bytes),0) total,coalesce(sum(size_bytes) FILTER(WHERE tenant_id=%s),0) tenant FROM media_assets",(tenant_id,)).fetchone()
            if usage["total"]+size_bytes>max_total_bytes or usage["tenant"]+size_bytes>max_tenant_bytes: raise ValueError("media storage quota exceeded")
            conn.execute("INSERT INTO media_assets VALUES(%s,%s,%s,%s,%s,%s,%s,%s,now())",(media_id,tenant_id,subject_id,modality,content_type,size_bytes,sha256,relative_path))

    def get_media_by_ref(self,reference,tenant_id,subject_id):
        prefix="media://"+hashlib.sha256(tenant_id.encode()).hexdigest()[:24]+"/"
        if not isinstance(reference,str) or not reference.startswith(prefix):return None
        with self.pool.connection() as conn: return conn.execute("SELECT * FROM media_assets WHERE media_id=%s AND tenant_id=%s AND subject_id=%s",(reference[len(prefix):],tenant_id,subject_id)).fetchone()

    def delete_media_record(self,media_id,tenant_id,subject_id):
        with self.pool.connection() as conn,conn.transaction(): return conn.execute("DELETE FROM media_assets WHERE media_id=%s AND tenant_id=%s AND subject_id=%s RETURNING *",(media_id,tenant_id,subject_id)).fetchone()

    def list_expired_media(self,cutoff_epoch):
        cutoff=datetime.fromtimestamp(cutoff_epoch,timezone.utc)
        with self.pool.connection() as conn:
            rows=conn.execute("SELECT * FROM media_assets WHERE created_at<%s",(cutoff,)).fetchall()
            scans=conn.execute("SELECT tenant_id,payload_json FROM scans WHERE status IN ('queued','processing')").fetchall()
        referenced=set()
        for scan in scans:
            payload=scan["payload_json"] if isinstance(scan["payload_json"],dict) else json.loads(scan["payload_json"])
            referenced.update(payload.get("media_refs",[]))
        return [row for row in rows if f"media://{hashlib.sha256(row['tenant_id'].encode()).hexdigest()[:24]}/{row['media_id']}" not in referenced]

    def get_scan_owner(self,scan_id):
        with self.pool.connection() as conn:return conn.execute("SELECT tenant_id,subject_id FROM scans WHERE scan_id=%s",(scan_id,)).fetchone()

    def readiness(self):
        with self.pool.connection() as conn:
            return conn.execute("SELECT 1 FROM schema_migrations WHERE version=2").fetchone() is not None

    def diagnostics(self):
        """Return aggregate queue health without exposing job or tenant identifiers."""
        with self.pool.connection() as conn:
            row=conn.execute("""
                SELECT
                  count(*) FILTER (WHERE status='queued') AS queued,
                  count(*) FILTER (WHERE status='running') AS running,
                  count(*) FILTER (WHERE status='failed') AS terminal_failures,
                  count(*) FILTER (WHERE last_error IS NOT NULL) AS retries,
                  extract(epoch FROM (now()-min(available_at) FILTER (WHERE status='queued'))) AS oldest_queued_age_seconds
                FROM jobs
            """).fetchone()
        result=dict(row)
        if result["oldest_queued_age_seconds"] is not None:
            result["oldest_queued_age_seconds"]=round(max(0.0,float(result["oldest_queued_age_seconds"])),3)
        return result

    def close(self):self.pool.close()
