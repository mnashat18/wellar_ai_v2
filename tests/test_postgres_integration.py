"""Real PostgreSQL tests. Requires an explicitly disposable localhost test database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import multiprocessing
from pathlib import Path
from urllib.parse import urlparse
import os
import time
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from app.core.errors import QueueFull
from app.core.ids import new_id
from app.queue.postgres_store import PostgresQueueStore
from app.security.auth import Principal


def isolated_connect(dsn):
    """Suppress driver diagnostics that may include the synthetic test DSN."""
    try:
        return psycopg.connect(dsn)
    except Exception:
        pytest.fail("isolated PostgreSQL test connection failed; driver details suppressed", pytrace=False)


def isolated_store(dsn, **kwargs):
    """Avoid printing connection arguments if store initialization fails."""
    try:
        return PostgresQueueStore(dsn, **kwargs)
    except Exception:
        pytest.fail("isolated PostgreSQL store initialization failed; driver details suppressed", pytrace=False)


def claim_job_until_process_termination(dsn, schema, result_path, worker_id):
    child_store=isolated_store(
        dsn,capacity=100,max_attempts=2,lease_seconds=3,retry_base_seconds=.01,
        max_pool_size=1,schema=schema,migrate=False,
    )
    job=child_store.claim(worker_id)
    temporary=Path(str(result_path)+".partial")
    temporary.write_text(json.dumps(job,default=str),encoding="utf-8")
    os.replace(temporary,result_path)
    time.sleep(30)


@pytest.fixture(scope="module")
def postgres_store():
    dsn=os.getenv("WELLAR_TEST_POSTGRES_DSN")
    if not dsn:pytest.skip("WELLAR_TEST_POSTGRES_DSN not configured; real PostgreSQL integration not run")
    parsed=urlparse(dsn)
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.port != 55432
        or parsed.path.lstrip("/") != "wellar_v2_sprint08_test"
        or parsed.username != "wellar_sprint08"
    ):
        pytest.fail("refusing database integration unless DSN targets only the Sprint 08 loopback test database on port 55432")
    schema="wellar_test_"+uuid4().hex
    with isolated_connect(dsn) as conn:conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    store=isolated_store(dsn,capacity=100,max_attempts=2,lease_seconds=1,retry_base_seconds=.01,max_pool_size=12,schema=schema)
    try:yield store
    finally:
        store.close()
        with isolated_connect(dsn) as conn:conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def submit(store,key,payload=None,principal=None):
    import hashlib,json
    payload=payload or {"schema_version":"1.0","media_refs":[]}
    encoded=json.dumps(payload,sort_keys=True,separators=(",",":"))
    return store.submit(new_id(),principal or Principal("sub","tenant"),key,hashlib.sha256(encoded.encode()).hexdigest(),encoded,"test-request")


def test_postgres_concurrent_claim_fencing_idempotency_capacity_and_rollback(postgres_store):
    store=postgres_store
    first,_=submit(store,"same-key")
    replay,created=submit(store,"same-key")
    assert not created and replay["scan_id"]==first["scan_id"]
    first_job=store.claim("replay-worker");store.complete(first_job,{"status":"insufficient_evidence"})
    with ThreadPoolExecutor(max_workers=8) as pool:
        replays=list(pool.map(lambda _:submit(store,"concurrent-same-key"),range(8)))
    assert len({record["scan_id"] for record,_ in replays})==1
    assert sum(1 for _,created in replays if created)==1
    concurrent_replay=store.claim("concurrent-replay-worker")
    store.complete(concurrent_replay,{"status":"insufficient_evidence"})
    assert store.get_scan(first["scan_id"],"other-tenant","sub") is None
    assert store.get_result(first["scan_id"],"tenant","other-subject") is None
    tiny=isolated_store(os.environ["WELLAR_TEST_POSTGRES_DSN"],capacity=1,max_attempts=2,lease_seconds=1,retry_base_seconds=.01,max_pool_size=2,schema=store.schema)
    try:
        submit(tiny,"saturation-a")
        with pytest.raises(QueueFull):submit(tiny,"saturation-b")
        queued=tiny.claim("saturation-worker");tiny.complete(queued,{"status":"done"})
    finally:tiny.close()

    scans=[submit(store,"concurrent-"+str(i))[0] for i in range(8)]
    independent_pools=[isolated_store(
        os.environ["WELLAR_TEST_POSTGRES_DSN"],capacity=100,max_attempts=2,
        lease_seconds=1,retry_base_seconds=.01,max_pool_size=1,schema=store.schema,migrate=False
    ) for _ in range(8)]
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            jobs=list(pool.map(lambda pair:pair[1].claim("independent-worker-"+str(pair[0])),enumerate(independent_pools)))
    finally:
        for independent in independent_pools:independent.close()
    assert len({job["job_id"] for job in jobs if job})==8 and all(jobs)
    for job in jobs:store.complete(job,{"status":"insufficient_evidence"})

    # Outbox insert failure must roll back result and terminal transitions.
    scan=submit(store,"rollback")[0];job=store.claim("rollback-worker")
    trigger="fail_outbox_"+uuid4().hex
    with store.pool.connection() as conn,conn.transaction():
        conn.execute(sql.SQL("CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected'; END $$").format(sql.Identifier(trigger)))
        conn.execute(sql.SQL("CREATE TRIGGER {} BEFORE INSERT ON outbox FOR EACH ROW EXECUTE FUNCTION {}()").format(sql.Identifier(trigger),sql.Identifier(trigger)))
    with pytest.raises(psycopg.Error):store.complete(job,{"status":"insufficient_evidence"})
    with store.pool.connection() as conn,conn.transaction():
        assert conn.execute("SELECT count(*) n FROM results WHERE scan_id=%s",(scan["scan_id"],)).fetchone()["n"]==0
        assert conn.execute("SELECT status FROM jobs WHERE job_id=%s",(job["job_id"],)).fetchone()["status"]=="running"
        conn.execute(sql.SQL("DROP TRIGGER {} ON outbox").format(sql.Identifier(trigger)))
        conn.execute(sql.SQL("DROP FUNCTION {}()").format(sql.Identifier(trigger)))
    store.complete(job,{"status":"insufficient_evidence"})

    retry_scan=submit(store,"retry-backoff")[0];first_attempt=store.claim("retry-worker")
    store.fail(first_attempt,"temporary_failure")
    with store.pool.connection() as conn:due=conn.execute("SELECT available_at FROM jobs WHERE job_id=%s",(first_attempt["job_id"],)).fetchone()["available_at"].timestamp()
    assert store.claim("too-early",now=due-.001) is None
    second_attempt=store.claim("retry-worker",now=due+.001)
    assert second_attempt and second_attempt["scan_id"]==retry_scan["scan_id"] and second_attempt["attempts"]==2
    store.fail(second_attempt,"second_failure")
    assert store.get_scan(retry_scan["scan_id"],"tenant","sub")["status"]=="failed"


def test_postgres_worker_crash_lease_recovery_and_fenced_completion(postgres_store,tmp_path):
    store=postgres_store
    namespace=uuid4().hex
    tenant_id="synthetic-crash-tenant-"+namespace
    subject_id="synthetic-crash-subject-"+namespace
    worker_id="synthetic-crash-worker-"+namespace
    recovery_worker="synthetic-recovery-worker-"+namespace
    accepted,_=submit(store,"crash-recovery-"+namespace,principal=Principal(subject_id,tenant_id))
    context=multiprocessing.get_context("spawn")
    result_path=tmp_path/"claimed-job.json"
    process=context.Process(target=claim_job_until_process_termination,args=(
        os.environ["WELLAR_TEST_POSTGRES_DSN"],store.schema,str(result_path),worker_id,
    ))
    stale=None
    try:
        process.start()
        deadline=time.monotonic()+12
        while time.monotonic()<deadline and not result_path.exists() and process.is_alive():
            time.sleep(.05)
    finally:
        if process.is_alive():process.terminate()
        process.join(timeout=5)
        if process.is_alive():process.kill();process.join(timeout=3)
    if result_path.exists():
        stale=json.loads(result_path.read_text(encoding="utf-8"))
        if stale and stale.get("lease_until"):
            stale["lease_until"]=datetime.fromisoformat(stale["lease_until"])
    assert not process.is_alive() and process.exitcode is not None
    assert stale and stale["scan_id"]==accepted["scan_id"]
    assert stale["worker_id"]==worker_id and stale["attempts"]==1

    deadline=time.monotonic()+8
    recovered=None
    while time.monotonic()<deadline:
        recovered=store.claim(recovery_worker)
        if recovered:break
        time.sleep(.1)
    assert recovered and recovered["scan_id"]==accepted["scan_id"]
    assert recovered["worker_id"]==recovery_worker
    assert recovered["attempts"]==2
    assert recovered["fencing_token"]==stale["fencing_token"]+1
    with pytest.raises(RuntimeError):store.complete(stale,{"status":"stale"})
    assert store.get_result(accepted["scan_id"],tenant_id,subject_id) is None
    store.complete(recovered,{"status":"insufficient_evidence","synthetic_test":True})
    assert store.get_scan(accepted["scan_id"],tenant_id,subject_id)["status"]=="completed"
    assert store.get_result(accepted["scan_id"],tenant_id,subject_id)=={"status":"insufficient_evidence","synthetic_test":True}
