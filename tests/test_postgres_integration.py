"""Real PostgreSQL tests. Requires an explicitly disposable localhost test database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
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


def submit(store,key,payload=None,principal=None,scan_id=None):
    import hashlib,json
    payload=payload or {"schema_version":"1.0","media_refs":[]}
    encoded=json.dumps(payload,sort_keys=True,separators=(",",":"))
    return store.submit(scan_id or new_id(),principal or Principal("sub","tenant"),key,hashlib.sha256(encoded.encode()).hexdigest(),encoded,"test-request")


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


def test_postgres_heartbeat_renews_only_current_unexpired_lease(postgres_store):
    store=postgres_store
    accepted,_=submit(store,"heartbeat-fencing-"+uuid4().hex)
    stale=store.claim("heartbeat-owner")
    assert stale and stale["scan_id"]==accepted["scan_id"]
    with store.pool.connection() as conn:
        before=conn.execute("SELECT lease_until FROM jobs WHERE job_id=%s",(stale["job_id"],)).fetchone()["lease_until"]
    assert store.heartbeat(stale) is True
    with store.pool.connection() as conn:
        after=conn.execute("SELECT lease_until FROM jobs WHERE job_id=%s",(stale["job_id"],)).fetchone()["lease_until"]
        conn.execute("UPDATE jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE job_id=%s",(stale["job_id"],))
    assert after>before
    assert store.heartbeat(stale) is False

    recovered=store.claim("heartbeat-recovery")
    assert recovered and recovered["fencing_token"]==stale["fencing_token"]+1
    with pytest.raises(RuntimeError):store.complete(stale,{"status":"stale"})
    assert store.get_result(accepted["scan_id"],"tenant","sub") is None
    store.complete(recovered,{"status":"completed"})
    assert store.get_result(accepted["scan_id"],"tenant","sub")["status"]=="completed"


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


def test_postgres_media_reconciliation_pages_past_500_and_retries(postgres_store):
    store=postgres_store
    count=505
    references=[]
    principal=Principal("reconcile-subject","reconcile-tenant")
    failed_scan_id=None
    failed_scan_payload=None
    for index in range(count):
        reference=f"media://synthetic/{index:04d}"
        references.append(reference)
        payload={"schema_version":"1.0","media_refs":[reference],"unrelated":{"keep":True}}
        if index==0:
            payload.update({"media_refs":[reference,"media://synthetic/companion"],"provenance":{"fixture":"synthetic"}})
            failed_scan_payload=dict(payload)
        row,_=submit(store,"reconcile-"+str(index),payload,principal)
        if index==0:failed_scan_id=row["scan_id"]
        job=store.claim("postgres-reconciliation-test")
        assert job and job["scan_id"]==row["scan_id"]
        store.complete(job,{"scan_id":row["scan_id"],"status":"insufficient_evidence"})
    active_reference="media://synthetic/active"

    class SyntheticMedia:
        def __init__(self):self.calls=[];self.outage_remaining=3
        def delete(self,reference,tenant_id,subject_id):
            self.calls.append((reference,tenant_id,subject_id))
            if reference==references[0] and self.outage_remaining:
                self.outage_remaining-=1
                raise OSError("synthetic temporary storage outage")
            return reference not in {references[1],"media://synthetic/companion"}  # already absent is idempotent success

    from app.media.reconciliation import MediaReconciler
    media=SyntheticMedia();reconciler=MediaReconciler(media,store,page_size=100,max_pages_per_run=10,max_references_per_run=count+10)
    first=reconciler.run_once()
    assert first["terminal_media_deleted"]==count and first["deletion_failed"]==1
    assert len(media.calls)==count+3
    assert all(tenant_id==principal.tenant_id and subject_id==principal.subject_id for _,tenant_id,subject_id in media.calls)
    assert all(reference!=active_reference for reference,_,_ in media.calls)
    assert store.list_terminal_scan_media_refs()==[{"scan_id":failed_scan_id,"tenant_id":principal.tenant_id,"subject_id":principal.subject_id,"reference":references[0]}]
    with store.pool.connection() as conn:
        payload=conn.execute("SELECT payload_json FROM scans WHERE scan_id=%s",(failed_scan_id,)).fetchone()["payload_json"]
    assert payload=={"schema_version":"1.0","media_refs":[references[0]],"unrelated":{"keep":True},"provenance":{"fixture":"synthetic"}}
    rollback_owner="rollback-owner-"+uuid4().hex
    assert store.begin_media_reconciliation(rollback_owner) is not None

    function="reconciliation_rollback_"+uuid4().hex
    trigger="reconciliation_rollback_trigger_"+uuid4().hex
    with store.pool.connection() as conn,conn.transaction():
        conn.execute(sql.SQL("CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic rollback'; END $$").format(sql.Identifier(function)))
        conn.execute(sql.SQL("CREATE TRIGGER {} BEFORE UPDATE OF payload_json ON scans FOR EACH ROW EXECUTE FUNCTION {}()").format(sql.Identifier(trigger),sql.Identifier(function)))
    try:
        with pytest.raises(psycopg.Error):store.remove_terminal_scan_media_refs(rollback_owner,failed_scan_id,[references[0]])
        with store.pool.connection() as conn:
            payload_after_rollback=conn.execute("SELECT payload_json FROM scans WHERE scan_id=%s",(failed_scan_id,)).fetchone()["payload_json"]
        assert payload_after_rollback=={**failed_scan_payload,"media_refs":[references[0]]}
    finally:
        with store.pool.connection() as conn,conn.transaction():
            conn.execute(sql.SQL("DROP TRIGGER {} ON scans").format(sql.Identifier(trigger)))
            conn.execute(sql.SQL("DROP FUNCTION {}()").format(sql.Identifier(function)))
        store.release_media_reconciliation(rollback_owner)

    second=reconciler.run_once()
    assert second["terminal_media_deleted"]==1 and second["deletion_failed"]==0
    assert reconciler.run_once()["terminal_media_deleted"]==0
    assert store.list_terminal_scan_media_refs()==[]

    concurrent_reference="media://synthetic/concurrent"
    concurrent_scan,_=submit(store,"reconcile-concurrent",{"schema_version":"1.0","media_refs":[concurrent_reference]},principal)
    store.complete(store.claim("postgres-reconciliation-concurrent"),{"scan_id":concurrent_scan["scan_id"],"status":"insufficient_evidence"})
    entered=__import__("threading").Event();release=__import__("threading").Event()

    class ConcurrentQueueView:
        def __getattr__(self,name):return getattr(store,name)

    class ConcurrentSyntheticMedia:
        def __init__(self):self.calls=[]
        def delete(self,reference,tenant_id,subject_id):
            self.calls.append((reference,tenant_id,subject_id))
            entered.set()
            assert release.wait(timeout=5)
            return True

    concurrent_media=ConcurrentSyntheticMedia()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_worker=pool.submit(MediaReconciler(concurrent_media,ConcurrentQueueView()).run_once)
        assert entered.wait(timeout=5)
        second_worker=MediaReconciler(concurrent_media,ConcurrentQueueView()).run_once()
        assert second_worker["lease_busy"]==1 and second_worker["references_processed"]==0
        release.set()
        concurrent_result=first_worker.result(timeout=10)
    assert concurrent_result["terminal_media_deleted"]==1
    assert concurrent_media.calls==[(concurrent_reference,principal.tenant_id,principal.subject_id)]
    assert store.list_terminal_scan_media_refs()==[]
    active_scan,_=submit(store,"reconcile-active",{"schema_version":"1.0","media_refs":[active_reference]},principal)
    assert reconciler.run_once()["terminal_media_deleted"]==0
    assert all(reference!=active_reference for reference,_,_ in media.calls)
    active_job=store.claim("active-fixture-cleanup")
    assert active_job and active_job["scan_id"]==active_scan["scan_id"]
    store.complete(active_job,{"scan_id":active_scan["scan_id"],"status":"synthetic-fixture-finished"})
    active_owner="active-fixture-cleaner-"+uuid4().hex
    assert store.begin_media_reconciliation(active_owner) is not None
    assert store.remove_terminal_scan_media_refs(active_owner,active_scan["scan_id"],[active_reference])==1
    assert store.finish_media_reconciliation_cycle(active_owner) is True
    store.release_media_reconciliation(active_owner)


def test_postgres_reconciliation_budget_resumes_and_defers_late_terminal_scans(postgres_store):
    from app.media.reconciliation import MediaReconciler
    store=postgres_store
    namespace=uuid4().hex
    principal=Principal("budget-subject-"+namespace,"budget-tenant-"+namespace)
    original_refs=[f"media://synthetic/{namespace}/{index:02d}" for index in range(9)]
    for index,reference in enumerate(original_refs):
        row,_=submit(store,"budget-"+namespace+"-"+str(index),{"schema_version":"1.0","media_refs":[reference]},principal)
        job=store.claim("budget-fixture-worker")
        assert job and job["scan_id"]==row["scan_id"]
        store.complete(job,{"scan_id":row["scan_id"],"status":"insufficient_evidence"})

    late_reference=f"media://synthetic/{namespace}/late"
    class ArrivingSyntheticMedia:
        def __init__(self):self.calls=[];self.arrived=False
        def delete(self,reference,tenant_id,subject_id):
            self.calls.append(reference)
            if not self.arrived:
                self.arrived=True
                row,_=submit(store,"late-"+namespace,{"schema_version":"1.0","media_refs":[late_reference]},principal,scan_id="zzzz-late-"+namespace)
                job=store.claim("late-fixture-worker")
                assert job and job["scan_id"]==row["scan_id"]
                store.complete(job,{"scan_id":row["scan_id"],"status":"insufficient_evidence"})
            return True

    media=ArrivingSyntheticMedia()
    settings={"page_size":2,"max_pages_per_run":2,"max_references_per_run":8}
    first=MediaReconciler(media,store,**settings).run_once()
    assert first["pages_processed"]==2 and first["references_processed"]==4 and first["budget_exhausted"]==1
    with store.pool.connection() as conn:
        checkpoint=conn.execute("SELECT after_scan_id,after_reference,cycle_started_at FROM media_reconciliation_state WHERE singleton=TRUE").fetchone()
    assert checkpoint["after_scan_id"] is not None and checkpoint["after_reference"] is not None and checkpoint["cycle_started_at"] is not None
    assert late_reference not in media.calls

    # Simulate a process crash with the cycle cursor persisted and its lease expiring.
    crashed_owner="crashed-budget-reconciler-"+namespace
    assert store.begin_media_reconciliation(crashed_owner,lease_seconds=1) is not None
    with store.pool.connection() as conn,conn.transaction():
        conn.execute("UPDATE media_reconciliation_state SET lease_until=clock_timestamp()-interval '1 second' WHERE singleton=TRUE")

    cycle_complete=False
    for _ in range(5):
        result=MediaReconciler(media,store,**settings).run_once()
        assert result["pages_processed"]<=2 and result["references_processed"]<=8
        if result["cycle_complete"]:
            cycle_complete=True
            break
    assert cycle_complete
    assert sorted(reference for reference in media.calls if reference in original_refs)==sorted(original_refs)
    assert late_reference not in media.calls
    final=MediaReconciler(media,store,**settings).run_once()
    assert final["terminal_media_deleted"]==1 and late_reference in media.calls


def test_postgres_reconciliation_storage_delete_keeps_stale_metadata_write_fenced(postgres_store,tmp_path):
    import hashlib
    from app.media.reconciliation import MediaReconciler
    from app.media.storage import LocalMediaStore
    store=postgres_store
    namespace=uuid4().hex
    principal=Principal("fenced-subject-"+namespace,"fenced-tenant-"+namespace)
    media_id=uuid4().hex
    tenant_hash=hashlib.sha256(principal.tenant_id.encode()).hexdigest()[:24]
    media_root=tmp_path/"synthetic-media"
    relative=f"{tenant_hash}/{media_id}.wav"
    file_path=media_root/relative
    file_path.parent.mkdir(parents=True);file_path.write_bytes(b"synthetic")
    reference=f"media://{tenant_hash}/{media_id}"
    payload={"schema_version":"1.0","media_refs":[reference],"provenance":{"synthetic":True}}
    row,_=submit(store,"fenced-storage-"+namespace,payload,principal)
    store.register_media(media_id=media_id,tenant_id=principal.tenant_id,subject_id=principal.subject_id,
        modality="audio",content_type="audio/wav",size_bytes=9,sha256=hashlib.sha256(b"synthetic").hexdigest(),
        relative_path=relative,max_total_bytes=1000,max_tenant_bytes=1000)
    job=store.claim("fenced-storage-worker")
    assert job and job["scan_id"]==row["scan_id"]
    store.complete(job,{"scan_id":row["scan_id"],"status":"insufficient_evidence"})
    media=LocalMediaStore(str(media_root),store,{"video":1000,"image":1000,"audio":1000})

    class ExpiringQueue:
        def __getattr__(self,name):return getattr(store,name)
        def remove_terminal_scan_media_refs(self,owner,scan_id,references):
            with store.pool.connection() as conn,conn.transaction():
                conn.execute("UPDATE media_reconciliation_state SET lease_until=clock_timestamp()-interval '1 second' WHERE singleton=TRUE")
            return store.remove_terminal_scan_media_refs(owner,scan_id,references)

    with pytest.raises(RuntimeError,match="lease"):
        MediaReconciler(media,ExpiringQueue(),page_size=10,max_pages_per_run=2,max_references_per_run=10).run_once()
    assert not file_path.exists()
    assert store.get_media_by_ref(reference,principal.tenant_id,principal.subject_id) is not None
    assert store.list_terminal_scan_media_refs()==[{"scan_id":row["scan_id"],"tenant_id":principal.tenant_id,
        "subject_id":principal.subject_id,"reference":reference}]

    recovered=MediaReconciler(media,store,page_size=10,max_pages_per_run=2,max_references_per_run=10).run_once()
    assert recovered["terminal_media_deleted"]==1 and recovered["cycle_complete"]==1
    assert store.get_media_by_ref(reference,principal.tenant_id,principal.subject_id) is None
    assert store.list_terminal_scan_media_refs()==[]
    with store.pool.connection() as conn:
        cleaned=conn.execute("SELECT payload_json FROM scans WHERE scan_id=%s",(row["scan_id"],)).fetchone()["payload_json"]
    assert cleaned=={"schema_version":"1.0","media_refs":[],"provenance":{"synthetic":True}}


def test_postgres_terminal_transition_waits_for_cycle_snapshot(postgres_store):
    from app.media.reconciliation import MediaReconciler
    store=postgres_store
    namespace=uuid4().hex
    principal=Principal("snapshot-subject-"+namespace,"snapshot-tenant-"+namespace)
    row,_=submit(store,"snapshot-"+namespace,{"schema_version":"1.0","media_refs":["media://synthetic/snapshot"]},principal)
    job=store.claim("snapshot-worker")
    assert job and job["scan_id"]==row["scan_id"]
    entered=__import__("threading").Event()

    # Hold the same exclusive checkpoint lock used when a real cycle fixes its cutoff.
    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.pool.connection() as conn,conn.transaction():
            cutoff=conn.execute("SELECT clock_timestamp() AS cutoff").fetchone()["cutoff"]
            conn.execute("UPDATE media_reconciliation_state SET cycle_started_at=%s WHERE singleton=TRUE",(cutoff,))
            conn.execute("SELECT singleton FROM media_reconciliation_state WHERE singleton=TRUE FOR UPDATE")
            def complete_after_boundary():
                entered.set()
                store.complete(job,{"scan_id":row["scan_id"],"status":"insufficient_evidence"})
            completion=pool.submit(complete_after_boundary)
            assert entered.wait(timeout=5)
            time.sleep(.05)
            assert not completion.done()
    completion.result(timeout=10)
    assert store.list_terminal_scan_media_refs(terminal_before=cutoff)==[]
    assert len(store.list_terminal_scan_media_refs())==1
    with store.pool.connection() as conn,conn.transaction():
        conn.execute("UPDATE media_reconciliation_state SET cycle_started_at=NULL,after_scan_id=NULL,after_reference=NULL,lease_owner=NULL,lease_until=NULL WHERE singleton=TRUE")
    class CleanupMedia:
        def delete(self,reference,tenant_id,subject_id):return True
    assert MediaReconciler(CleanupMedia(),store).run_once()["terminal_media_deleted"]==1


def test_postgres_reconciliation_cutoff_is_exclusive_at_exact_timestamp(postgres_store):
    store=postgres_store
    namespace=uuid4().hex
    principal=Principal("boundary-subject-"+namespace,"boundary-tenant-"+namespace)
    references={
        "before":f"media://synthetic/{namespace}/before",
        "equal":f"media://synthetic/{namespace}/equal",
        "after":f"media://synthetic/{namespace}/after",
    }
    scan_ids={}
    for name,reference in references.items():
        row,_=submit(store,"boundary-"+name+"-"+namespace,
            {"schema_version":"1.0","media_refs":[reference]},principal)
        job=store.claim("boundary-worker-"+name)
        assert job and job["scan_id"]==row["scan_id"]
        store.complete(job,{"status":"insufficient_evidence"})
        scan_ids[name]=row["scan_id"]

    cutoff=datetime(2026,10,20,12,0,0,tzinfo=timezone.utc)
    with store.pool.connection() as conn,conn.transaction():
        for name,instant in (("before",cutoff-timedelta(microseconds=1)),
                             ("equal",cutoff),
                             ("after",cutoff+timedelta(microseconds=1))):
            conn.execute("UPDATE scans SET updated_at=%s WHERE scan_id=%s",(instant,scan_ids[name]))

    current_cycle=store.list_terminal_scan_media_refs(terminal_before=cutoff)
    next_cycle=store.list_terminal_scan_media_refs(terminal_before=cutoff+timedelta(microseconds=2))
    assert {item["reference"] for item in current_cycle}=={references["before"]}
    assert {item["reference"] for item in next_cycle}==set(references.values())
    # This fixture shares one temporary schema for the module. Remove only
    # these synthetic references so later lease/failure tests start clean.
    with store.pool.connection() as conn,conn.transaction():
        conn.execute("UPDATE scans SET payload_json=jsonb_set(payload_json,'{media_refs}','[]'::jsonb) WHERE scan_id=ANY(%s)",(list(scan_ids.values()),))


@pytest.mark.parametrize("failure_point",["before_delete","after_delete_before_remove","checkpoint_advance"])
def test_postgres_database_failures_resume_without_losing_references(postgres_store,failure_point):
    from app.media.reconciliation import MediaReconciler
    store=postgres_store
    namespace=uuid4().hex
    principal=Principal("fault-subject-"+namespace,"fault-tenant-"+namespace)
    reference=f"media://synthetic/{namespace}/fault"
    row,_=submit(store,"fault-"+namespace,{"schema_version":"1.0","media_refs":[reference]},principal)
    store.complete(store.claim("fault-worker"),{"scan_id":row["scan_id"],"status":"insufficient_evidence"})

    class FaultingQueueView:
        def __init__(self):self.failed=False
        def __getattr__(self,name):return getattr(store,name)
        def list_terminal_scan_media_refs(self,*args,**kwargs):
            if failure_point=="before_delete" and not self.failed:
                self.failed=True
                raise psycopg.OperationalError("synthetic isolated database disconnect")
            return store.list_terminal_scan_media_refs(*args,**kwargs)
        def remove_terminal_scan_media_refs(self,owner,scan_id,references):
            if failure_point=="after_delete_before_remove" and not self.failed:
                self.failed=True
                raise psycopg.OperationalError("synthetic isolated database disconnect after storage deletion")
            return store.remove_terminal_scan_media_refs(owner,scan_id,references)
        def advance_media_reconciliation(self,owner,scan_id,ref,lease_seconds):
            if failure_point=="checkpoint_advance" and not self.failed:
                self.failed=True
                raise psycopg.OperationalError("synthetic checkpoint persistence disconnect")
            return store.advance_media_reconciliation(owner,scan_id,ref,lease_seconds)

    class SyntheticMedia:
        def __init__(self):self.present=True;self.calls=0
        def delete(self,ref,tenant_id,subject_id):
            self.calls+=1
            was_present=self.present
            self.present=False
            return was_present

    media=SyntheticMedia();queue=FaultingQueueView()
    with pytest.raises(psycopg.OperationalError):
        MediaReconciler(media,queue,page_size=1,max_pages_per_run=2,max_references_per_run=2).run_once()
    refs=store.list_terminal_scan_media_refs()
    assert refs==([] if failure_point=="checkpoint_advance" else [{"scan_id":row["scan_id"],"tenant_id":principal.tenant_id,"subject_id":principal.subject_id,"reference":reference}])
    with store.pool.connection() as conn:
        checkpoint=conn.execute("SELECT after_scan_id,lease_owner FROM media_reconciliation_state WHERE singleton=TRUE").fetchone()
    assert checkpoint["lease_owner"] is None
    if failure_point=="checkpoint_advance":assert checkpoint["after_scan_id"] is None
    result=MediaReconciler(media,queue,page_size=1,max_pages_per_run=2,max_references_per_run=2).run_once()
    assert result["cycle_complete"]==1 and store.list_terminal_scan_media_refs()==[]
    assert media.present is False
    assert media.calls==(1 if failure_point in {"before_delete","checkpoint_advance"} else 2)


def test_postgres_expired_lease_fences_all_stale_mutations_after_slow_delete(postgres_store):
    from app.media.reconciliation import MediaReconciler
    store=postgres_store
    namespace=uuid4().hex
    principal=Principal("slow-subject-"+namespace,"slow-tenant-"+namespace)
    reference=f"media://synthetic/{namespace}/slow"
    row,_=submit(store,"slow-"+namespace,{"schema_version":"1.0","media_refs":[reference]},principal)
    store.complete(store.claim("slow-fixture-worker"),{"scan_id":row["scan_id"],"status":"insufficient_evidence"})
    owners=[]

    class ObservedQueue:
        def __getattr__(self,name):return getattr(store,name)
        def begin_media_reconciliation(self,owner,lease_seconds):
            state=store.begin_media_reconciliation(owner,lease_seconds)
            if state is not None:owners.append(owner)
            return state

    class SlowSyntheticMedia:
        def __init__(self):self.calls=0;self.lock=__import__("threading").Lock();self.first_enter=__import__("threading").Event();self.second_enter=__import__("threading").Event();self.release_first=__import__("threading").Event();self.release_second=__import__("threading").Event();self.present=True
        def delete(self,ref,tenant_id,subject_id):
            with self.lock:
                self.calls+=1
                call=self.calls
            entered,release=(self.first_enter,self.release_first) if call==1 else (self.second_enter,self.release_second)
            entered.set()
            assert release.wait(timeout=5)
            with self.lock:
                was_present=self.present
                self.present=False
            return was_present

    media=SlowSyntheticMedia();queue=ObservedQueue()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(MediaReconciler(media,queue,lease_seconds=1,page_size=1,max_pages_per_run=1,max_references_per_run=1).run_once)
        assert media.first_enter.wait(timeout=5)
        time.sleep(1.05)
        old_owner=owners[0]
        assert store.renew_media_reconciliation(old_owner,1) is False
        assert store.advance_media_reconciliation(old_owner,row["scan_id"],reference,1) is False
        assert store.finish_media_reconciliation_cycle(old_owner) is False
        with pytest.raises(RuntimeError):store.remove_terminal_scan_media_refs(old_owner,row["scan_id"],[reference])
        second=pool.submit(MediaReconciler(media,queue,lease_seconds=1,page_size=1,max_pages_per_run=1,max_references_per_run=1).run_once)
        assert media.second_enter.wait(timeout=5)
        assert len(owners)==2 and owners[0]!=owners[1]
        with store.pool.connection() as conn:
            checkpoint=conn.execute("SELECT lease_owner,after_scan_id FROM media_reconciliation_state WHERE singleton=TRUE").fetchone()
        assert checkpoint["lease_owner"]==owners[1] and checkpoint["after_scan_id"] is None
        assert store.renew_media_reconciliation(old_owner,1) is False
        assert store.advance_media_reconciliation(old_owner,row["scan_id"],reference,1) is False
        assert store.finish_media_reconciliation_cycle(old_owner) is False
        assert store.release_media_reconciliation(old_owner) is False
        with pytest.raises(RuntimeError):store.remove_terminal_scan_media_refs(old_owner,row["scan_id"],[reference])
        media.release_first.set()
        with pytest.raises(RuntimeError):first.result(timeout=5)
        assert store.list_terminal_scan_media_refs()[0]["reference"]==reference
        with store.pool.connection() as conn:
            checkpoint=conn.execute("SELECT lease_owner,after_scan_id FROM media_reconciliation_state WHERE singleton=TRUE").fetchone()
        assert checkpoint["lease_owner"]==owners[1] and checkpoint["after_scan_id"] is None
        media.release_second.set()
        assert second.result(timeout=10)["terminal_media_deleted"]==1
    assert store.list_terminal_scan_media_refs()==[]
    assert media.calls==2 and media.present is False
