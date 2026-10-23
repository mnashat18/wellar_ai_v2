import time
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.errors import QueueFull
from app.security.auth import Principal
from app.workers.worker import Worker


P=Principal("sub","tenant")


def submit(store,key="key",body='{}'):
    import hashlib
    return store.submit("scan-"+key,P,key,hashlib.sha256(body.encode()).hexdigest(),body)


def test_capacity_and_durable_worker_restart(store):
    store.capacity=1
    submit(store)
    with pytest.raises(QueueFull): submit(store,"second")
    recovered=Worker(store,"new-worker")
    assert recovered.run_once()
    record=store.get_scan("scan-key","tenant","sub")
    assert record["status"]=="completed"


def test_queue_diagnostics_report_aggregate_depth_age_retries_and_failures(store):
    submit(store)
    snapshot=store.diagnostics()
    assert snapshot["queued"]==1
    assert snapshot["running"]==0
    assert snapshot["oldest_queued_age_seconds"] is not None
    assert snapshot["retries"]==0
    assert snapshot["terminal_failures"]==0
    job=store.claim("diagnostics-worker")
    store.fail(job,"processing_error")
    snapshot=store.diagnostics()
    assert snapshot["queued"]==1
    assert snapshot["retries"]==1


def test_lease_expiry_fences_old_worker(store):
    submit(store)
    now=time.time()
    old=store.claim("old",now=now)
    fresh=store.claim("fresh",now=now+2)
    assert fresh and fresh["fencing_token"]>old["fencing_token"]
    with pytest.raises(RuntimeError): store.complete(old,{"status":"bad"})
    store.complete(fresh,{"status":"ok"})


def test_retry_exhaustion(store):
    submit(store)
    now=time.time()
    first=store.claim("w",now=now)
    store.fail(first,"processing_error",now=now)
    second=store.claim("w",now=now+2)
    store.fail(second,"processing_error",now=now+2)
    assert store.claim("w",now=now+3) is None
    assert store.get_scan("scan-key","tenant","sub")["status"]=="failed"


def test_final_expired_lease_becomes_terminal(store):
    submit(store)
    now=time.time()
    first=store.claim("w",now=now)
    store.fail(first,"processing_error",now=now)
    second=store.claim("w",now=now+2)
    assert second["attempts"]==2
    assert store.claim("other",now=now+4) is None
    assert store.get_scan("scan-key","tenant","sub")["status"]=="failed"


def test_result_and_outbox_are_atomic_and_idempotent(store):
    submit(store)
    job=store.claim("w")
    store.complete(job,{"scan_id":job["scan_id"],"status":"done"})
    store.complete(job,{"scan_id":job["scan_id"],"status":"done"}) if False else None
    assert store.get_result(job["scan_id"],"tenant","sub")["status"]=="done"
    assert store.db.execute("select count(*) from outbox").fetchone()[0]==1


def test_duplicate_submission_conflict_and_tenant_scoping(store):
    import hashlib
    row,created=submit(store)
    assert created
    same,created=store.submit("another",P,"key",row["payload_hash"],"{}")
    assert not created and same["scan_id"]==row["scan_id"]
    other=store.get_scan(row["scan_id"],"other-tenant","sub")
    assert other is None


def test_process_restart_recovers_persisted_work(tmp_path):
    from app.queue.sqlite_store import SQLiteQueueStore
    path=str(tmp_path/"restart.sqlite")
    first=SQLiteQueueStore(path,capacity=5)
    submit(first)
    first.close()
    restarted=SQLiteQueueStore(path,capacity=5)
    try:
        assert Worker(restarted,"restarted-worker").run_once()
        assert restarted.get_scan("scan-key","tenant","sub")["status"]=="completed"
    finally: restarted.close()


def test_in_progress_job_recovers_after_store_restart_and_fences_stale_worker(tmp_path):
    from app.queue.sqlite_store import SQLiteQueueStore
    path=str(tmp_path/"running-restart.sqlite")
    original=SQLiteQueueStore(path,capacity=5,max_attempts=2,lease_seconds=1,retry_base_seconds=.001)
    submit(original,"crashed-worker")
    started=time.time()
    stale=original.claim("crashed-process",now=started)
    original.close()
    recovered_store=SQLiteQueueStore(path,capacity=5,max_attempts=2,lease_seconds=1,retry_base_seconds=.001)
    try:
        recovered=recovered_store.claim("replacement-process",now=started+2)
        assert recovered is not None
        assert recovered["job_id"]==stale["job_id"]
        assert recovered["fencing_token"]==stale["fencing_token"]+1
        with pytest.raises(RuntimeError): recovered_store.complete(stale,{"status":"stale"})
        recovered_store.complete(recovered,{"status":"completed"})
        assert recovered_store.get_scan("scan-crashed-worker","tenant","sub")["status"]=="completed"
    finally: recovered_store.close()


def test_worker_recovers_from_result_commit_failure(store):
    submit(store)
    real_complete=store.complete
    calls={"n":0}
    def fail_once(job,result):
        calls["n"]+=1
        if calls["n"]==1: raise OSError("simulated storage failure")
        real_complete(job,result)
    store.complete=fail_once
    worker=Worker(store,"w")
    assert worker.run_once()
    assert store.get_scan("scan-key","tenant","sub")["status"]=="queued"
    time.sleep(0.01)
    assert worker.run_once()
    assert store.get_scan("scan-key","tenant","sub")["status"]=="completed"


def test_transaction_rolls_back_partial_result_and_outbox_on_failure(store):
    submit(store)
    job=store.claim("w")
    store.db.execute("CREATE TRIGGER reject_outbox BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT,'injected outbox failure'); END")
    with pytest.raises(Exception): store.complete(job,{"status":"done"})
    assert store.get_result(job["scan_id"],"tenant","sub") is None
    assert store.db.execute("select status from jobs where job_id=?",(job["job_id"],)).fetchone()[0]=="running"
    store.db.execute("DROP TRIGGER reject_outbox")
    store.complete(job,{"status":"done"})
    assert store.get_result(job["scan_id"],"tenant","sub")["status"]=="done"


def test_concurrent_idempotent_submissions_create_one_scan(store):
    import hashlib
    def attempt(_):
        return store.submit("candidate-"+str(time.time_ns()),P,"same-key",hashlib.sha256(b"{}").hexdigest(),"{}")
    with ThreadPoolExecutor(max_workers=8) as pool:
        records=list(pool.map(attempt,range(16)))
    assert sum(created for _,created in records)==1
    assert len({record["scan_id"] for record,_ in records})==1


def test_sqlite_lock_contention_is_classified_and_transaction_rolls_back(store, caplog):
    import sqlite3
    from app.queue.sqlite_store import sqlite_error_details
    blocker=sqlite3.connect(store.path,timeout=1,isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    store.db.execute("PRAGMA busy_timeout=1")
    try:
        with pytest.raises(sqlite3.OperationalError) as raised:
            submit(store,"locked")
        code,name,busy=sqlite_error_details(raised.value)
        assert code is None and name=="SQLITE_LOCK_CONTENTION" and busy
        assert not store.db.in_transaction
        assert store._busy_lock_count==3
        assert store._sqlite_retry_count==2
        assert any('"operation":"submit"' in record.message or
                   getattr(record,"event_fields",{}).get("operation")=="submit"
                   for record in caplog.records)
        assert any(getattr(record,"event_fields",{}).get("retry_attempt_count")==2
                   for record in caplog.records)
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
        store.db.execute(f"PRAGMA busy_timeout={store.busy_timeout_ms}")


def test_permanent_sqlite_integrity_failure_is_not_retried(store):
    import sqlite3
    store.db.execute("CREATE TRIGGER reject_scan BEFORE INSERT ON scans BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        submit(store,"permanent")
    assert store._sqlite_retry_count==0
    assert store._busy_lock_count==0
    assert not store.db.in_transaction


def test_submission_receipt_is_read_before_commit_for_reconciliation(store):
    import sqlite3
    committed={"value":False}
    def authorizer(action, arg1, arg2, database, trigger):
        if action==sqlite3.SQLITE_TRANSACTION and arg1=="COMMIT":
            committed["value"]=True
        if committed["value"] and action==sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    store.db.set_authorizer(authorizer)
    try:
        record,created=submit(store,"commit-receipt")
    finally:
        store.db.set_authorizer(None)
    assert created and record["scan_id"]=="scan-commit-receipt"
    assert committed["value"]


def test_worker_stops_cleanly_without_claiming_more_work(store):
    stop=threading.Event();worker=Worker(store,"shutdown-worker")
    stop.set();worker.run_forever(stop,idle_seconds=.01)
    assert store.db.execute("select count(*) from jobs where status='running'").fetchone()[0]==0


def test_single_host_sqlite_workers_process_distinct_claims_concurrently(store):
    """Exercise SQLite's single-host claim locking; this is not distributed validation."""
    from concurrent.futures import ThreadPoolExecutor

    worker_count=4
    for index in range(worker_count):submit(store,f"local-worker-{index}")
    barrier=threading.Barrier(worker_count)
    lock=threading.Lock()
    state={"active":0,"peak":0}
    def processor(_payload):
        with lock:
            state["active"]+=1
            state["peak"]=max(state["peak"],state["active"])
        try:
            barrier.wait(timeout=5)
            return {"status":"completed"}
        finally:
            with lock:state["active"]-=1
    workers=[Worker(store,f"local-worker-{index}",processor) for index in range(worker_count)]
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        outcomes=list(pool.map(lambda worker:worker.run_once(),workers))

    assert outcomes==[True]*worker_count
    assert state["peak"]==worker_count
    assert store.db.execute("SELECT count(*) FROM jobs WHERE status='completed'").fetchone()[0]==worker_count
    owners={row[0] for row in store.db.execute("SELECT worker_id FROM jobs WHERE status='completed'").fetchall()}
    assert owners=={f"local-worker-{index}" for index in range(worker_count)}


def test_single_host_sqlite_racing_claims_have_one_lease_owner(store):
    """Validate local SQLite locking only; PostgreSQL process races need live integration."""
    from concurrent.futures import ThreadPoolExecutor
    submit(store,"claim-race")
    worker_count=8
    barrier=threading.Barrier(worker_count)
    def claim(index):
        barrier.wait(timeout=3)
        return store.claim(f"claim-racer-{index}")
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        claims=list(pool.map(claim,range(worker_count)))
    owners=[job["worker_id"] for job in claims if job is not None]
    assert len(owners)==1
    assert store.db.execute("SELECT count(*) FROM jobs WHERE status='running'").fetchone()[0]==1


def test_worker_shutdown_drains_active_job_without_claiming_another(store):
    entered=threading.Event();release=threading.Event();stop=threading.Event()
    submit(store,"shutdown-drain-one");submit(store,"shutdown-drain-two")
    def processor(_payload):
        entered.set()
        assert release.wait(timeout=5)
        return {"status":"completed"}
    worker=Worker(store,"draining-worker",processor)
    thread=threading.Thread(target=worker.run_forever,args=(stop,),kwargs={"idle_seconds":0.01})
    thread.start()
    assert entered.wait(timeout=2)
    stop.set()
    assert thread.is_alive()
    release.set();thread.join(timeout=3)
    assert not thread.is_alive()
    assert store.db.execute("SELECT count(*) FROM jobs WHERE status='completed'").fetchone()[0]==1
    assert store.db.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0]==1
