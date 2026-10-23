from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.media.reconciliation import MediaReconciler,delete_with_retry
from app.media.storage import LocalMediaStore
from app.media.deletion_process import StorageDeleteTimeout
from app.security.auth import Principal
from app.workers.worker import Worker


def _register_asset(store,media_root,payload,*,terminal=False):
    principal=Principal("subject-a","tenant-a")
    scan_id="scan-cleanup"
    body=__import__("json").dumps(payload)
    row,_=store.submit(scan_id,principal,"idem-cleanup",hashlib.sha256(body.encode()).hexdigest(),body)
    media_id="media-cleanup"
    tenant_hash=hashlib.sha256(principal.tenant_id.encode()).hexdigest()[:24]
    path=media_root/tenant_hash/(media_id+".wav")
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b"test")
    store.register_media(media_id=media_id,tenant_id=principal.tenant_id,subject_id=principal.subject_id,
        modality="audio",content_type="audio/wav",size_bytes=4,sha256=hashlib.sha256(b"test").hexdigest(),
        relative_path=path.relative_to(media_root).as_posix(),max_total_bytes=1000,max_tenant_bytes=1000)
    if terminal:
        job=store.claim("complete-cleanup")
        store.complete(job,{"scan_id":scan_id,"status":"insufficient_evidence"})
        # Fixtures named as pre-existing terminal media must precede the next
        # cutoff even on hosts whose wall clock returns the same tick twice.
        store.db.execute("UPDATE scans SET updated_at=updated_at-0.001 WHERE scan_id=?",(scan_id,))
    return row,path,f"media://{tenant_hash}/{media_id}"


class FailingProcessor:
    def __init__(self,media_store):self.media_store=media_store
    def __call__(self,payload):raise ValueError("synthetic malformed payload")


def test_worker_poison_payload_best_effort_deletes_referenced_media(store,tmp_path):
    import json
    media=LocalMediaStore(str(tmp_path/"media"),store,{"video":1000,"image":1000,"audio":1000})
    principal=Principal("subject-a","tenant-a")
    ref=f"media://{hashlib.sha256(principal.tenant_id.encode()).hexdigest()[:24]}/poison"
    body=json.dumps({"media_refs":[ref],"invalid":"processor rejects this"})
    row,_=store.submit("scan-poison",principal,"idem-poison",hashlib.sha256(body.encode()).hexdigest(),body)
    poison=tmp_path/"media"/hashlib.sha256(principal.tenant_id.encode()).hexdigest()[:24]/"poison.wav"
    poison.parent.mkdir(parents=True);poison.write_bytes(b"poison")
    store.register_media(media_id="poison",tenant_id=principal.tenant_id,subject_id=principal.subject_id,
        modality="audio",content_type="audio/wav",size_bytes=6,sha256=hashlib.sha256(b"poison").hexdigest(),
        relative_path=poison.relative_to(media.root).as_posix(),max_total_bytes=1000,max_tenant_bytes=1000)
    assert Worker(store,"poison-worker",FailingProcessor(media)).run_once()
    assert not poison.exists()
    assert store.get_media_by_ref(ref,principal.tenant_id,principal.subject_id) is None
    assert row["scan_id"]=="scan-poison"
    media.close()


def test_startup_sweep_deletes_media_for_terminal_scan_and_stale_partial(store,tmp_path):
    payload={"media_refs":[]}
    _,path,ref=_register_asset(store,tmp_path/"media",payload,terminal=True)
    # Attach the reference to the terminal scan's retained payload.
    store.db.execute("UPDATE scans SET payload_json=? WHERE scan_id=?",(__import__("json").dumps({"media_refs":[ref]}),"scan-cleanup"))
    scratch=tmp_path/"media"/"tenant"/"old.partial";scratch.parent.mkdir(parents=True);scratch.write_bytes(b"partial")
    os.utime(scratch,(time.time()-7200,time.time()-7200))
    assert store.list_terminal_scan_media_refs()==[{"scan_id":"scan-cleanup","tenant_id":"tenant-a","subject_id":"subject-a","reference":ref}]
    media=LocalMediaStore(str(tmp_path/"media"),store,{"video":1000,"image":1000,"audio":1000})
    Worker(store,"startup-sweeper",FailingProcessor(media))
    assert not path.exists() and not scratch.exists()
    media.close()


def test_worker_crash_then_restart_sweep_removes_media(store,tmp_path):
    store.max_attempts=1
    payload={"media_refs":[]}
    _,path,ref=_register_asset(store,tmp_path/"media",payload)
    store.db.execute("UPDATE scans SET payload_json=? WHERE scan_id=?",(__import__("json").dumps({"media_refs":[ref]}),"scan-cleanup"))
    script=("import os,sys; from app.queue.sqlite_store import SQLiteQueueStore; "
            "s=SQLiteQueueStore(sys.argv[1],max_attempts=1,lease_seconds=1); s.claim('crashed-worker'); os._exit(0)")
    env=dict(os.environ);env["PYTHONPATH"]=str(Path(__file__).resolve().parents[1])
    child=subprocess.run([sys.executable,"-c",script,store.path],env=env,timeout=10)
    assert child.returncode==0
    assert store.claim("restart-reconciler",now=time.time()+3) is None
    assert store.get_scan("scan-cleanup","tenant-a","subject-a")["status"]=="failed"
    # The synthetic lease-recovery clock above is advanced; normalize the fixture's terminal timestamp.
    store.db.execute("UPDATE scans SET updated_at=? WHERE scan_id=?",(time.time()-0.001,"scan-cleanup"))
    media=LocalMediaStore(str(tmp_path/"media"),store,{"video":1000,"image":1000,"audio":1000})
    assert MediaReconciler(media,store).run_once()["terminal_media_deleted"]==1
    assert not path.exists()
    media.close()


def test_s3_adapter_delete_retries_successfully_after_injected_failure():
    calls=[]
    class Store:
        def delete(self,*args):
            calls.append(args)
            if len(calls)<3:raise OSError("temporary synthetic outage")
            return True
    assert delete_with_retry(Store(),"media://opaque/ref","tenant-a","subject-a",attempts=3,base_delay=0)
    assert len(calls)==3


def test_s3_adapter_delete_exhaustion_is_reported():
    class Store:
        def delete(self,*args):raise OSError("synthetic object-store failure")
    assert not delete_with_retry(Store(),"media://opaque/ref","tenant-a","subject-a",attempts=2,base_delay=0)


def test_local_delete_timeout_is_explicit_and_safe_retry_succeeds(store,tmp_path,monkeypatch):
    _,path,reference=_register_asset(store,tmp_path/"bounded-media",{"media_refs":[]})
    media=LocalMediaStore(str(tmp_path/"bounded-media"),store,{"video":1000,"image":1000,"audio":1000})
    import app.media.deletion_process as deletion_process
    original=deletion_process.subprocess.Popen
    calls=[]
    def timeout_once(*args,**kwargs):
        calls.append(1)
        if len(calls)==1:
            class TimedOut:
                returncode=None
                calls=0
                def communicate(self,**kwargs):
                    self.calls+=1
                    if self.calls==1:raise subprocess.TimeoutExpired(args[0],kwargs.get("timeout"))
                    self.returncode=-9
                    return "",""
                def poll(self):return None
                def kill(self):self.returncode=-9
            return TimedOut()
        return original(*args,**kwargs)
    monkeypatch.setattr(deletion_process.subprocess,"Popen",timeout_once)
    threads_before=threading.active_count()
    try:
        with __import__("pytest").raises(StorageDeleteTimeout):
            media.delete_for_reconciliation(reference,"tenant-a","subject-a")
        assert path.exists()
        assert store.get_media_by_ref(reference,"tenant-a","subject-a") is not None
        assert media.delete_for_reconciliation(reference,"tenant-a","subject-a") is True
        assert not path.exists()
        assert store.get_media_by_ref(reference,"tenant-a","subject-a") is not None
        assert threading.active_count()==threads_before
    finally:media.close()


def test_expired_reconciler_cannot_write_media_metadata_after_storage_delete(store,tmp_path):
    _,path,reference=_register_asset(store,tmp_path/"fenced-media",{"media_refs":[]},terminal=True)
    store.db.execute("UPDATE scans SET payload_json=? WHERE scan_id=?",
        (__import__("json").dumps({"media_refs":[reference],"keep":"synthetic"}),"scan-cleanup"))
    media=LocalMediaStore(str(tmp_path/"fenced-media"),store,{"video":1000,"image":1000,"audio":1000})
    class ExpiringQueue:
        def __getattr__(self,name):return getattr(store,name)
        def remove_terminal_scan_media_refs(self,owner,scan_id,references):
            store.db.execute("UPDATE media_reconciliation_state SET lease_until=0 WHERE singleton=1")
            return store.remove_terminal_scan_media_refs(owner,scan_id,references)
    try:
        with __import__("pytest").raises(RuntimeError,match="lease"):
            MediaReconciler(media,ExpiringQueue(),page_size=1,max_pages_per_run=1,max_references_per_run=1).run_once()
        assert not path.exists()
        assert store.get_media_by_ref(reference,"tenant-a","subject-a") is not None
        assert store.list_terminal_scan_media_refs()[0]["reference"]==reference
        result=MediaReconciler(media,store,page_size=1,max_pages_per_run=1,max_references_per_run=1).run_once()
        assert result["terminal_media_deleted"]==1
        assert store.get_media_by_ref(reference,"tenant-a","subject-a") is None
        assert store.list_terminal_scan_media_refs()==[]
        assert MediaReconciler(media,store,page_size=1,max_pages_per_run=1,max_references_per_run=1).run_once()["cycle_complete"]==1
    finally:media.close()


def test_worker_reconciliation_failure_backoff_is_bounded_and_resets(store,monkeypatch):
    import app.workers.worker as worker_module
    monkeypatch.setattr(worker_module.time,"monotonic",lambda:100.0)
    monkeypatch.setattr(worker_module.random,"uniform",lambda low,high:high)
    worker=Worker(store,"backoff-worker",reconciliation_retry_initial_seconds=1,
        reconciliation_retry_multiplier=2,reconciliation_retry_max_seconds=4,reconciliation_retry_jitter=.2)
    class Flaky:
        def __init__(self):self.fail=True;self.partial=False
        def run_once(self):
            if self.fail:raise OSError("synthetic database outage")
            if self.partial:return {"run_status":"partial","deletion_failed":1}
            return {"run_status":"complete"}
    worker.reconciler=Flaky()
    assert worker._reconcile_media()["run_status"]=="failed"
    assert worker._next_reconciliation_at==101.2
    assert worker._reconcile_media()["retry_delay_seconds"]==2.4
    assert worker._next_reconciliation_at==102.4
    worker.reconciler.fail=False
    assert worker._reconcile_media()["run_status"]=="complete"
    assert worker._reconciliation_failures==0
    worker.reconciler.partial=True
    assert worker._reconcile_media()["run_status"]=="partial"
    assert worker._next_reconciliation_at==101.2
    assert worker._reconcile_media()["run_status"]=="partial"
    assert worker._next_reconciliation_at==102.4
    worker.reconciler.partial=False
    assert worker._reconcile_media()["run_status"]=="complete"
    assert worker._reconciliation_failures==0
    assert worker._next_reconciliation_at==400.0


def test_worker_shutdown_wait_is_interruptible_during_reconciliation_backoff(store,monkeypatch):
    import app.workers.worker as worker_module
    monkeypatch.setattr(worker_module.time,"monotonic",lambda:100.0)
    worker=Worker(store,"shutdown-backoff-worker")
    worker._next_reconciliation_at=1000
    stop=threading.Event()
    waits=[]
    original_wait=stop.wait
    def stop_on_wait(timeout=None):
        waits.append(timeout)
        stop.set()
        return original_wait(0)
    stop.wait=stop_on_wait
    worker.run_forever(stop,idle_seconds=.05)
    assert waits==[.05]


def test_reconciliation_shutdown_checkpoints_completed_deletion_and_defers_rest(store):
    first="media://tenant-ref/shutdown-a"
    second="media://tenant-ref/shutdown-b"
    principal=Principal("subject-a","tenant-a")
    payload={"schema_version":"1.0","media_refs":[first,second]}
    body=__import__("json").dumps(payload)
    row,_=store.submit("scan-shutdown-cleanup",principal,"idem-shutdown-cleanup",hashlib.sha256(body.encode()).hexdigest(),body)
    job=store.claim("shutdown-fixture-worker")
    store.complete(job,{"scan_id":row["scan_id"],"status":"insufficient_evidence"})
    # The reconciler intentionally excludes timestamp ties at its cycle cutoff.
    # Make this terminal record unambiguously older so this test only exercises
    # shutdown checkpointing, not host clock precision.
    store.db.execute("UPDATE scans SET updated_at=updated_at-0.001 WHERE scan_id=?",(row["scan_id"],))
    stop=threading.Event()
    class StopAfterOneDelete:
        def __init__(self):self.calls=[]
        def delete(self,reference,tenant_id,subject_id):
            self.calls.append(reference);stop.set();return True
    media=StopAfterOneDelete()
    first_run=MediaReconciler(media,store,page_size=10,max_pages_per_run=2,max_references_per_run=10).run_once(stop_event=stop)
    assert first_run["shutdown_requested"]==1 and first_run["references_processed"]==1
    assert len(media.calls)==1
    assert store.list_terminal_scan_media_refs()==[{"scan_id":row["scan_id"],"tenant_id":"tenant-a","subject_id":"subject-a","reference":second}]
    resumed=MediaReconciler(media,store,page_size=10,max_pages_per_run=2,max_references_per_run=10).run_once()
    assert resumed["terminal_media_deleted"]==1 and resumed["cycle_complete"]==1
    assert media.calls==[first,second]


def _add_terminal_scan_with_reference(store,scan_id,reference):
    import json
    principal=Principal("subject-a","tenant-a")
    body=json.dumps({"schema_version":"1.0","media_refs":[reference]})
    row,_=store.submit(scan_id,principal,"idem-"+scan_id,hashlib.sha256(body.encode()).hexdigest(),body)
    job=store.claim("reconciliation-test-worker")
    store.complete(job,{"scan_id":scan_id,"status":"insufficient_evidence"})
    store.db.execute("UPDATE scans SET updated_at=updated_at-0.001 WHERE scan_id=?",(scan_id,))
    return row


def test_reconciliation_pages_past_500_and_retries_partial_failures(store):
    count=507
    refs=[f"media://tenant-ref/media-{index:04d}" for index in range(count)]
    for index,ref in enumerate(refs):
        _add_terminal_scan_with_reference(store,f"scan-{index:04d}",ref)
    active_ref="media://tenant-ref/active-must-remain"
    active=Principal("subject-a","tenant-a")
    body=__import__("json").dumps({"schema_version":"1.0","media_refs":[active_ref]})
    store.submit("scan-zz-active",active,"idem-scan-zz-active",hashlib.sha256(body.encode()).hexdigest(),body)

    class SyntheticMedia:
        def __init__(self):
            self.calls=[]
            self.outages={refs[0]:3}
            self.already_missing={refs[1]}
        def delete(self,reference,tenant_id,subject_id):
            self.calls.append((reference,tenant_id,subject_id))
            if self.outages.get(reference,0):
                self.outages[reference]-=1
                raise OSError("synthetic temporary storage outage")
            return reference not in self.already_missing

    media=SyntheticMedia()
    reconciler=MediaReconciler(media,store,page_size=100,max_pages_per_run=10,max_references_per_run=count+10)
    first=reconciler.run_once()
    assert first["terminal_media_deleted"]==count-1
    assert first["deletion_failed"]==1
    assert len(media.calls)==count+2  # three attempts for one outage; one attempt for each other reference
    assert all(tenant=="tenant-a" and subject=="subject-a" for _,tenant,subject in media.calls)
    assert all(reference!=active_ref for reference,_,_ in media.calls)
    remaining=store.list_terminal_scan_media_refs()
    assert remaining==[{"scan_id":"scan-0000","tenant_id":"tenant-a","subject_id":"subject-a","reference":refs[0]}]

    second=reconciler.run_once()
    assert second["terminal_media_deleted"]==1
    assert second["deletion_failed"]==0
    assert reconciler.run_once()["terminal_media_deleted"]==0
    assert store.list_terminal_scan_media_refs()==[]


def test_sqlite_ref_update_is_atomic_and_preserves_unrelated_payload(store):
    import json
    scan_id="scan-payload-preservation"
    principal=Principal("subject-a","tenant-a")
    first="media://tenant-ref/first"
    second="media://tenant-ref/second"
    payload={"schema_version":"1.0","media_refs":[first,second],"provenance":{"fixture":"synthetic"},"unrelated":{"keep":True}}
    body=json.dumps(payload)
    store.submit(scan_id,principal,"idem-"+scan_id,hashlib.sha256(body.encode()).hexdigest(),body)
    store.complete(store.claim("payload-test-worker"),{"scan_id":scan_id,"status":"insufficient_evidence"})
    owner="payload-cleanup-owner"
    assert store.begin_media_reconciliation(owner) is not None
    trigger="fail_reconciliation_payload_update"
    store.db.execute(f"CREATE TRIGGER {trigger} BEFORE UPDATE OF payload_json ON scans WHEN OLD.scan_id='{scan_id}' BEGIN SELECT RAISE(ABORT,'synthetic rollback'); END")
    with __import__("pytest").raises(sqlite3.IntegrityError):
        store.remove_terminal_scan_media_refs(owner,scan_id,[first])
    assert not store.db.in_transaction
    unchanged=json.loads(store.db.execute("SELECT payload_json FROM scans WHERE scan_id=?",(scan_id,)).fetchone()[0])
    assert unchanged==payload
    store.db.execute(f"DROP TRIGGER {trigger}")

    assert store.remove_terminal_scan_media_refs(owner,scan_id,[first])==1
    updated=json.loads(store.db.execute("SELECT payload_json FROM scans WHERE scan_id=?",(scan_id,)).fetchone()[0])
    assert updated=={**payload,"media_refs":[second]}
    assert store.remove_terminal_scan_media_refs(owner,scan_id,[first])==0
    store.release_media_reconciliation(owner)


def test_concurrent_sqlite_reconcilers_are_idempotent_and_owned(store):
    scan_id="scan-concurrent-reconcile"
    reference="media://tenant-ref/concurrent"
    _add_terminal_scan_with_reference(store,scan_id,reference)
    entered=threading.Event();release=threading.Event()

    class ConcurrentSyntheticMedia:
        def __init__(self):self.calls=[]
        def delete(self,ref,tenant_id,subject_id):
            self.calls.append((ref,tenant_id,subject_id))
            entered.set()
            assert release.wait(timeout=5)
            return True

    media=ConcurrentSyntheticMedia()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(MediaReconciler(media,store).run_once)
        assert entered.wait(timeout=5)
        second=MediaReconciler(media,store).run_once()
        assert second["lease_busy"]==1 and second["references_processed"]==0
        release.set()
        result=first.result(timeout=10)
    assert result["terminal_media_deleted"]==1
    assert media.calls==[(reference,"tenant-a","subject-a")]
    assert store.list_terminal_scan_media_refs()==[]


def test_reconciliation_budget_resumes_after_new_reconciler_and_expired_lease(store):
    refs=[f"media://tenant-ref/budget-{index:02d}" for index in range(11)]
    for index,ref in enumerate(refs):
        _add_terminal_scan_with_reference(store,f"scan-budget-{index:02d}",ref)
    class SyntheticMedia:
        def __init__(self):self.calls=[]
        def delete(self,ref,tenant_id,subject_id):self.calls.append(ref);return True
    media=SyntheticMedia()
    first_reconciler=MediaReconciler(media,store,page_size=2,max_pages_per_run=2,max_references_per_run=8)
    first=first_reconciler.run_once()
    assert first["pages_processed"]==2 and first["references_processed"]==4 and first["budget_exhausted"]==1 and first["cycle_complete"]==0
    state=store.db.execute("SELECT after_scan_id,after_reference,cycle_started_at FROM media_reconciliation_state WHERE singleton=1").fetchone()
    first_cursor=state["after_scan_id"]
    assert first_cursor is not None and state["after_reference"] is not None and state["cycle_started_at"] is not None

    # Simulate a process death while it owns a checkpoint; expiry permits a new owner to resume.
    owner="synthetic-crashed-reconciler"
    assert store.begin_media_reconciliation(owner,lease_seconds=1) is not None
    store.db.execute("UPDATE media_reconciliation_state SET lease_until=? WHERE singleton=1",(time.time()-1,))
    resumed=MediaReconciler(media,store,page_size=2,max_pages_per_run=2,max_references_per_run=8).run_once()
    assert resumed["references_processed"]==4 and resumed["budget_exhausted"]==1
    next_run=MediaReconciler(media,store,page_size=2,max_pages_per_run=2,max_references_per_run=8).run_once()
    assert next_run["references_processed"]==3 and next_run["budget_exhausted"]==1
    final=MediaReconciler(media,store,page_size=2,max_pages_per_run=2,max_references_per_run=8).run_once()
    assert final["references_processed"]==0 and final["cycle_complete"]==1
    assert len(media.calls)==len(refs)
    assert store.list_terminal_scan_media_refs()==[]


def test_reconciliation_reference_cursor_bounds_work_inside_one_scan(store):
    import json
    principal=Principal("subject-a","tenant-a")
    references=[f"media://tenant-ref/same-scan-{index}" for index in range(3)]
    body=json.dumps({"schema_version":"1.0","media_refs":references})
    row,_=store.submit("scan-many-references",principal,"idem-many-references",hashlib.sha256(body.encode()).hexdigest(),body)
    store.complete(store.claim("many-reference-worker"),{"scan_id":row["scan_id"],"status":"insufficient_evidence"})
    store.db.execute("UPDATE scans SET updated_at=updated_at-0.001 WHERE scan_id=?",(row["scan_id"],))

    class SyntheticMedia:
        def __init__(self):self.calls=[]
        def delete(self,reference,tenant_id,subject_id):self.calls.append(reference);return True

    media=SyntheticMedia()
    settings={"page_size":1,"max_pages_per_run":1,"max_references_per_run":1}
    results=[MediaReconciler(media,store,**settings).run_once() for _ in range(4)]
    assert [result["references_processed"] for result in results]==[1,1,1,0]
    assert results[-1]["cycle_complete"]==1
    assert sorted(media.calls)==sorted(references)
    assert store.list_terminal_scan_media_refs()==[]


def test_sqlite_terminal_transition_after_snapshot_is_deferred(store):
    from app.queue.sqlite_store import SQLiteQueueStore
    principal=Principal("subject-a","tenant-a")
    reference="media://tenant-ref/sqlite-snapshot"
    body=__import__("json").dumps({"schema_version":"1.0","media_refs":[reference]})
    row,_=store.submit("scan-sqlite-snapshot",principal,"idem-sqlite-snapshot",hashlib.sha256(body.encode()).hexdigest(),body)
    job=store.claim("sqlite-snapshot-worker")
    assert job and job["scan_id"]==row["scan_id"]
    database_path=store.db.execute("PRAGMA database_list").fetchone()["file"]
    completing_store=SQLiteQueueStore(database_path,capacity=20,max_attempts=2,lease_seconds=1,retry_base_seconds=.001)
    entered=threading.Event()
    try:
        store.db.execute("BEGIN IMMEDIATE")
        cutoff=time.time()
        store.db.execute("UPDATE media_reconciliation_state SET cycle_started_at=? WHERE singleton=1",(cutoff,))
        with ThreadPoolExecutor(max_workers=1) as pool:
            def complete_after_boundary():
                entered.set()
                completing_store.complete(job,{"scan_id":row["scan_id"],"status":"insufficient_evidence"})
            completion=pool.submit(complete_after_boundary)
            assert entered.wait(timeout=5)
            time.sleep(.05)
            assert not completion.done()
            store.db.execute("COMMIT")
            completion.result(timeout=10)
        assert store.list_terminal_scan_media_refs(terminal_before=cutoff)==[]
        assert len(store.list_terminal_scan_media_refs())==1
    finally:
        if store.db.in_transaction:store.db.execute("ROLLBACK")
        completing_store.close()


@__import__("pytest").mark.parametrize("failure_point",["before_delete","after_delete_before_remove","checkpoint_advance"])
def test_sqlite_database_failures_resume_without_losing_references(store,failure_point):
    import pytest
    scan_id="scan-db-fault-"+failure_point
    reference="media://tenant-ref/db-fault-"+failure_point
    _add_terminal_scan_with_reference(store,scan_id,reference)

    class FaultingQueueView:
        def __init__(self):self.failed=False
        def __getattr__(self,name):return getattr(store,name)
        def list_terminal_scan_media_refs(self,*args,**kwargs):
            if failure_point=="before_delete" and not self.failed:
                self.failed=True
                raise sqlite3.OperationalError("synthetic database disconnect before deletion")
            return store.list_terminal_scan_media_refs(*args,**kwargs)
        def remove_terminal_scan_media_refs(self,owner,scan_id,references):
            if failure_point=="after_delete_before_remove" and not self.failed:
                self.failed=True
                raise sqlite3.OperationalError("synthetic database disconnect after storage deletion")
            return store.remove_terminal_scan_media_refs(owner,scan_id,references)
        def advance_media_reconciliation(self,owner,scan_id,ref,lease_seconds):
            if failure_point=="checkpoint_advance" and not self.failed:
                self.failed=True
                raise sqlite3.OperationalError("synthetic checkpoint persistence failure")
            return store.advance_media_reconciliation(owner,scan_id,ref,lease_seconds)

    class SyntheticMedia:
        def __init__(self):self.present=True;self.calls=0
        def delete(self,ref,tenant_id,subject_id):
            self.calls+=1
            was_present=self.present
            self.present=False
            return was_present

    media=SyntheticMedia();queue=FaultingQueueView()
    reconciler=MediaReconciler(media,queue,page_size=1,max_pages_per_run=2,max_references_per_run=2)
    with pytest.raises(sqlite3.OperationalError):reconciler.run_once()
    refs=store.list_terminal_scan_media_refs()
    assert refs==([] if failure_point=="checkpoint_advance" else [{"scan_id":scan_id,"tenant_id":"tenant-a","subject_id":"subject-a","reference":reference}])
    state=store.db.execute("SELECT after_scan_id,lease_owner FROM media_reconciliation_state WHERE singleton=1").fetchone()
    assert state["lease_owner"] is None
    if failure_point=="checkpoint_advance":assert state["after_scan_id"] is None

    result=MediaReconciler(media,queue,page_size=1,max_pages_per_run=2,max_references_per_run=2).run_once()
    assert result["cycle_complete"]==1
    assert store.list_terminal_scan_media_refs()==[]
    assert media.present is False
    assert media.calls==(1 if failure_point in {"before_delete","checkpoint_advance"} else 2)


def test_sqlite_expired_lease_fences_all_stale_mutations_after_slow_delete(store):
    scan_id="scan-slow-lease"
    reference="media://tenant-ref/slow-lease"
    _add_terminal_scan_with_reference(store,scan_id,reference)
    owners=[]

    class ObservedQueue:
        def __getattr__(self,name):return getattr(store,name)
        def begin_media_reconciliation(self,owner,lease_seconds):
            state=store.begin_media_reconciliation(owner,lease_seconds)
            if state is not None:owners.append(owner)
            return state

    class SlowSyntheticMedia:
        def __init__(self):self.calls=0;self.lock=threading.Lock();self.first_enter=threading.Event();self.second_enter=threading.Event();self.release_first=threading.Event();self.release_second=threading.Event();self.present=True
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
        assert store.renew_media_reconciliation(owners[0],1) is False
        assert store.advance_media_reconciliation(owners[0],scan_id,reference,1) is False
        assert store.finish_media_reconciliation_cycle(owners[0]) is False
        with __import__("pytest").raises(RuntimeError):store.remove_terminal_scan_media_refs(owners[0],scan_id,[reference])
        assert store.list_terminal_scan_media_refs()[0]["reference"]==reference
        second=pool.submit(MediaReconciler(media,queue,lease_seconds=1,page_size=1,max_pages_per_run=1,max_references_per_run=1).run_once)
        assert media.second_enter.wait(timeout=5)
        assert len(owners)==2 and owners[0]!=owners[1]
        state=store.db.execute("SELECT lease_owner,after_scan_id FROM media_reconciliation_state WHERE singleton=1").fetchone()
        assert state["lease_owner"]==owners[1] and state["after_scan_id"] is None
        assert store.renew_media_reconciliation(owners[0],1) is False
        assert store.advance_media_reconciliation(owners[0],scan_id,reference,1) is False
        assert store.finish_media_reconciliation_cycle(owners[0]) is False
        assert store.release_media_reconciliation(owners[0]) is False
        with __import__("pytest").raises(RuntimeError):store.remove_terminal_scan_media_refs(owners[0],scan_id,[reference])
        media.release_first.set()
        with __import__("pytest").raises(RuntimeError):first.result(timeout=5)
        assert store.list_terminal_scan_media_refs()[0]["reference"]==reference
        state=store.db.execute("SELECT lease_owner,after_scan_id FROM media_reconciliation_state WHERE singleton=1").fetchone()
        assert state["lease_owner"]==owners[1] and state["after_scan_id"] is None
        media.release_second.set()
        assert second.result(timeout=10)["terminal_media_deleted"]==1
    assert store.list_terminal_scan_media_refs()==[]
    assert media.calls==2 and media.present is False


def test_sqlite_reconciliation_cutoff_is_exclusive_at_exact_timestamp(store):
    before="media://tenant-ref/before-cutoff"
    equal="media://tenant-ref/exact-cutoff"
    after="media://tenant-ref/after-cutoff"
    _add_terminal_scan_with_reference(store,"scan-before-cutoff",before)
    _add_terminal_scan_with_reference(store,"scan-exact-cutoff",equal)
    _add_terminal_scan_with_reference(store,"scan-after-cutoff",after)
    cutoff=10_000.0
    store.db.execute("UPDATE scans SET updated_at=? WHERE scan_id=?",(cutoff-0.000001,"scan-before-cutoff"))
    store.db.execute("UPDATE scans SET updated_at=? WHERE scan_id=?",(cutoff,"scan-exact-cutoff"))
    store.db.execute("UPDATE scans SET updated_at=? WHERE scan_id=?",(cutoff+0.000001,"scan-after-cutoff"))

    refs=store.list_terminal_scan_media_refs(terminal_before=cutoff)

    assert [item["reference"] for item in refs]==[before]


def test_terminal_arrival_after_cycle_cutoff_is_deferred_to_next_cycle(store):
    first_ref="media://tenant-ref/before-cutoff"
    _add_terminal_scan_with_reference(store,"scan-before-cutoff",first_ref)
    class ObservedQueue:
        cutoff=None
        def __getattr__(self,name):return getattr(store,name)
        def begin_media_reconciliation(self,owner,lease_seconds):
            state=store.begin_media_reconciliation(owner,lease_seconds)
            if state is not None:self.cutoff=state["cycle_started_at"]
            return state

    class ArrivingMedia:
        def __init__(self,queue):self.calls=[];self.arrived=False;self.queue=queue
        def delete(self,ref,tenant_id,subject_id):
            self.calls.append(ref)
            if not self.arrived:
                self.arrived=True
                _add_terminal_scan_with_reference(store,"scan-z-arrived-during-cycle","media://tenant-ref/after-cutoff")
                # Force an exact timestamp tie without sleeping or depending on
                # filesystem/host clock precision. Program order is after begin.
                store.db.execute("UPDATE scans SET updated_at=? WHERE scan_id=?",
                    (self.queue.cutoff,"scan-z-arrived-during-cycle"))
            return True
    queue=ObservedQueue();media=ArrivingMedia(queue)
    first=MediaReconciler(media,queue,page_size=5,max_pages_per_run=5,max_references_per_run=10).run_once()
    assert first["terminal_media_deleted"]==1 and first["cycle_complete"]==1
    assert media.calls==[first_ref]
    assert store.list_terminal_scan_media_refs()==[{"scan_id":"scan-z-arrived-during-cycle","tenant_id":"tenant-a","subject_id":"subject-a","reference":"media://tenant-ref/after-cutoff"}]
    second=MediaReconciler(media,store,page_size=5,max_pages_per_run=5,max_references_per_run=10).run_once()
    assert second["terminal_media_deleted"]==1 and second["cycle_complete"]==1


def test_sqlite_terminal_arrivals_before_and_after_cutoff_have_defined_cycle_ownership(store):
    before="media://tenant-ref/before"
    after="media://tenant-ref/after"
    _add_terminal_scan_with_reference(store,"scan-before-boundary",before)
    _add_terminal_scan_with_reference(store,"scan-after-boundary",after)
    cutoff=20_000.0
    store.db.execute("UPDATE scans SET updated_at=? WHERE scan_id=?",(cutoff-0.000001,"scan-before-boundary"))
    store.db.execute("UPDATE scans SET updated_at=? WHERE scan_id=?",(cutoff+0.000001,"scan-after-boundary"))
    assert [item["reference"] for item in store.list_terminal_scan_media_refs(terminal_before=cutoff)]==[before]
    assert {item["reference"] for item in store.list_terminal_scan_media_refs(terminal_before=cutoff+0.000002)}=={before,after}


def test_post_commit_delete_failure_keeps_result_and_reconciliation_reference(store):
    reference="media://tenant-ref/post-commit-delete-failure"
    principal=Principal("subject-a","tenant-a")
    payload={"schema_version":"1.0","media_refs":[reference]}
    body=__import__("json").dumps(payload)
    row,_=store.submit("scan-post-commit-cleanup",principal,"idem-post-commit-cleanup",
        hashlib.sha256(body.encode()).hexdigest(),body)

    class FlakyMedia:
        def __init__(self):self.fail_deletes=True;self.calls=[]
        def delete(self,ref,tenant_id,subject_id):
            self.calls.append((ref,tenant_id,subject_id))
            if self.fail_deletes:raise OSError("synthetic post-commit object-store outage")
            return True
    media=FlakyMedia()
    class Processor:
        media_store=media
        def __call__(self,_payload):return {"status":"insufficient_evidence","marker":"durable-result"}

    assert Worker(store,"post-commit-cleanup-worker",Processor()).run_once()
    result=store.get_result(row["scan_id"],"tenant-a","subject-a")
    assert result["marker"]=="durable-result"
    assert store.get_scan(row["scan_id"],"tenant-a","subject-a")["status"]=="completed"
    assert len(media.calls)==3
    assert store.list_terminal_scan_media_refs()==[{
        "scan_id":row["scan_id"],"tenant_id":"tenant-a","subject_id":"subject-a","reference":reference}]

    media.fail_deletes=False
    reconciled=MediaReconciler(media,store).run_once()
    assert reconciled["terminal_media_deleted"]==1
    assert store.list_terminal_scan_media_refs()==[]
    assert store.get_result(row["scan_id"],"tenant-a","subject-a")["marker"]=="durable-result"
    assert store.get_scan(row["scan_id"],"tenant-a","subject-a")["status"]=="completed"


def test_retry_keeps_media_until_result_commit_then_cleanup_is_reconcilable(store):
    reference="media://tenant-ref/retry-retention"
    principal=Principal("subject-a","tenant-a")
    payload={"schema_version":"1.0","media_refs":[reference]}
    body=__import__("json").dumps(payload)
    row,_=store.submit("scan-retry-retention",principal,"idem-retry-retention",
        hashlib.sha256(body.encode()).hexdigest(),body)

    class Media:
        def __init__(self):self.delete_allowed=False;self.calls=[]
        def delete(self,ref,tenant_id,subject_id):
            self.calls.append((ref,tenant_id,subject_id))
            if not self.delete_allowed:raise OSError("synthetic cleanup outage")
            return True
    media=Media()
    class RetryOnceProcessor:
        media_store=media
        def __init__(self):self.calls=0
        def __call__(self,_payload):
            self.calls+=1
            if self.calls==1:raise OSError("synthetic retryable resolve failure")
            return {"status":"insufficient_evidence","marker":"committed-once"}
    processor=RetryOnceProcessor();worker=Worker(store,"retry-retention-worker",processor)

    assert worker.run_once()
    assert store.get_scan(row["scan_id"],"tenant-a","subject-a")["status"]=="queued"
    assert media.calls==[]
    store.db.execute("UPDATE jobs SET available_at=0 WHERE scan_id=?",(row["scan_id"],))

    assert worker.run_once()
    assert store.get_scan(row["scan_id"],"tenant-a","subject-a")["status"]=="completed"
    assert store.get_result(row["scan_id"],"tenant-a","subject-a")["marker"]=="committed-once"
    assert len(media.calls)==3
    assert store.list_terminal_scan_media_refs()==[{
        "scan_id":row["scan_id"],"tenant_id":"tenant-a","subject_id":"subject-a","reference":reference}]

    media.delete_allowed=True
    assert MediaReconciler(media,store).run_once()["terminal_media_deleted"]==1
    assert store.list_terminal_scan_media_refs()==[]
    assert store.get_scan(row["scan_id"],"tenant-a","subject-a")["status"]=="completed"
    assert store.get_result(row["scan_id"],"tenant-a","subject-a")["marker"]=="committed-once"


def test_reconciliation_deletes_each_reference_only_with_its_tenant_and_subject(store):
    owners=[("tenant-a","subject-a"),("tenant-b","subject-b")]
    references=[]
    for index,(tenant_id,subject_id) in enumerate(owners):
        principal=Principal(subject_id,tenant_id)
        reference=f"media://{tenant_id}/owned-{index:02d}"
        payload={"schema_version":"1.0","media_refs":[reference]}
        body=__import__("json").dumps(payload)
        row,_=store.submit(f"scan-owned-{index}",principal,f"idem-owned-{index}",
            hashlib.sha256(body.encode()).hexdigest(),body)
        store.complete(store.claim(f"owned-worker-{index}"),{"status":"insufficient_evidence"})
        store.db.execute("UPDATE scans SET updated_at=updated_at-0.001 WHERE scan_id=?",(row["scan_id"],))
        references.append((row["scan_id"],reference,tenant_id,subject_id))

    class OwnerCheckingMedia:
        def __init__(self):self.calls=[]
        def delete(self,reference,tenant_id,subject_id):
            self.calls.append((reference,tenant_id,subject_id))
            assert (reference,tenant_id,subject_id) in {
                (ref,tenant,subject) for _,ref,tenant,subject in references}
            return True

    media=OwnerCheckingMedia()
    result=MediaReconciler(media,store).run_once()
    assert result["terminal_media_deleted"]==2
    assert set(media.calls)=={(ref,tenant,subject) for _,ref,tenant,subject in references}
    assert store.list_terminal_scan_media_refs()==[]
