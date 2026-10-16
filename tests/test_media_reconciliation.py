from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

from app.media.reconciliation import MediaReconciler,delete_with_retry
from app.media.storage import LocalMediaStore
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
