import asyncio
import hashlib
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest

from app.media.s3_storage import S3ObjectMediaStore
from app.media.deletion_process import (StorageDeleteTimeout,run_bounded_storage_action,
    StorageDeleteCapacityTimeout,MAX_CONCURRENT_STORAGE_CHILDREN)
from tests.media_fixtures import wav_bytes


def _run_synthetic_deletes_in_process(endpoint):
    from concurrent.futures import ThreadPoolExecutor
    from app.media.deletion_process import run_bounded_storage_action
    request={"action":"s3_delete","bucket":"synthetic-only","key":"synthetic/object",
        "region":"us-east-1","endpoint_url":endpoint,"connect_timeout":1,"read_timeout":5,
        "credentials":{"access_key":"synthetic-access","secret_key":"synthetic-secret","session_token":None}}
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            values=list(executor.map(lambda _:run_bounded_storage_action(request,10),range(4)))
    except BaseException as exc:
        raise


class FakeS3:
    def __init__(self):self.objects={}
    def upload_file(self,path,bucket,key,ExtraArgs):
        self.objects[(bucket,key)]=open(path,"rb").read()
    def head_object(self,*,Bucket,Key):
        data=self.objects[(Bucket,Key)]
        return {"ContentLength":len(data)}
    def download_file(self,bucket,key,path):open(path,"wb").write(self.objects[(bucket,key)])
    def delete_object(self,*,Bucket,Key):self.objects.pop((Bucket,Key),None)


class BrokenS3(FakeS3):
    def upload_file(self,path,bucket,key,ExtraArgs):raise RuntimeError("access_key=SECRET-AWS-TOKEN")


class SlowS3(FakeS3):
    def __init__(self, delay_seconds=0.25):
        super().__init__(); self.delay_seconds=delay_seconds; self.active=0; self.peak=0; self.lock=threading.Lock()
    def upload_file(self,path,bucket,key,ExtraArgs):
        with self.lock:self.active+=1;self.peak=max(self.peak,self.active)
        try:
            time.sleep(self.delay_seconds)
            super().upload_file(path,bucket,key,ExtraArgs)
        finally:
            with self.lock:self.active-=1


class BlockingS3(SlowS3):
    def __init__(self): super().__init__(0); self.started=threading.Event(); self.release=threading.Event()
    def upload_file(self,path,bucket,key,ExtraArgs):
        self.started.set();self.release.wait(timeout=5)
        super().upload_file(path,bucket,key,ExtraArgs)


class PartialUploadS3(FakeS3):
    def upload_file(self,path,bucket,key,ExtraArgs):
        super().upload_file(path,bucket,key,ExtraArgs)
        raise RuntimeError("synthetic response lost after object write")


class BrokenDeleteS3(FakeS3):
    def delete_object(self,*,Bucket,Key):raise RuntimeError("secret=PRIVATE-OBJECT-CREDENTIAL")


class RecoveringDeleteS3(FakeS3):
    def __init__(self): super().__init__(); self.fail_deletes=True
    def delete_object(self,*,Bucket,Key):
        if self.fail_deletes: raise RuntimeError("synthetic delete outage")
        super().delete_object(Bucket=Bucket,Key=Key)


class QuotaStore:
    def register_media(self,**kwargs):raise ValueError("media storage quota exceeded")


def test_s3_storage_tenant_scope_integrity_and_delete(store,tmp_path):
    client=FakeS3(); storage=S3ObjectMediaStore(str(tmp_path),store,{"audio":100000,"video":100000,"image":100000},bucket="private-test",client=client)
    data=wav_bytes()
    async def chunks():yield data[:20];yield data[20:]
    response=asyncio.run(storage.save_stream(modality="audio",content_type="audio/wav",content_length=str(len(data)),chunks=chunks(),tenant_id="tenant-a",subject_id="user-a"))
    ref=response["media_ref"]
    assert response["size_bytes"]==len(data)
    assert storage.resolve(ref,"tenant-b","user-a",verify_integrity=True) is None
    resolved=storage.resolve(ref,"tenant-a","user-a",verify_integrity=True)
    assert resolved["integrity_ok"] and resolved["path"].read_bytes()==data
    storage.release(resolved)
    key=next(iter(client.objects));client.objects[key]=b"tampered"
    corrupted=storage.resolve(ref,"tenant-a","user-a",verify_integrity=True)
    assert not corrupted["integrity_ok"]
    storage.release(corrupted)
    assert not storage.delete(ref,"tenant-b","user-a")
    assert storage.delete(ref,"tenant-a","user-a") and not client.objects


def test_s3_upload_does_not_block_event_loop(store,tmp_path):
    """Baseline reproduction: the synchronous SDK call stalls this loop today."""
    storage=S3ObjectMediaStore(str(tmp_path),store,{"audio":100000},bucket="private-test",client=SlowS3())
    data=wav_bytes()
    async def chunks(): yield data
    async def scenario():
        loop=asyncio.get_running_loop(); target=loop.time()+0.02
        upload=asyncio.create_task(storage.save_stream(
            modality="audio",content_type="audio/wav",content_length=str(len(data)),chunks=chunks(),
            tenant_id="tenant-a",subject_id="user-a"))
        await asyncio.sleep(max(0,target-loop.time()))
        heartbeat_lag=loop.time()-target
        response=await upload
        return heartbeat_lag,response
    try:
        lag,response=asyncio.run(scenario())
        assert lag<0.10, f"event loop stalled for {lag:.3f}s during S3 upload"
        assert response["size_bytes"]==len(data)
    finally:
        storage.close()


def test_s3_upload_admission_is_bounded_and_overload_is_immediate(store,tmp_path):
    from app.media.storage import MediaInputError
    client=SlowS3(.15)
    storage=S3ObjectMediaStore(str(tmp_path),store,{"audio":100000},bucket="private-test",client=client,max_concurrent_uploads=2)
    data=wav_bytes()
    async def chunks():yield data
    async def scenario():
        async def upload(index):
            return await storage.save_stream(modality="audio",content_type="audio/wav",content_length=str(len(data)),
                chunks=chunks(),tenant_id=f"tenant-{index}",subject_id=f"subject-{index}")
        results=await asyncio.gather(*(upload(i) for i in range(3)),return_exceptions=True)
        return results
    try:
        results=asyncio.run(scenario())
        assert sum(isinstance(x,dict) for x in results)==2
        rejected=[x for x in results if isinstance(x,MediaInputError)]
        assert len(rejected)==1 and rejected[0].code=="upload_capacity_exceeded"
        assert client.peak==2
    finally:storage.close()


def test_s3_cancelled_active_upload_is_compensated_and_holds_admission_until_done(store,tmp_path):
    client=BlockingS3()
    storage=S3ObjectMediaStore(str(tmp_path),store,{"audio":100000},bucket="private-test",client=client,max_concurrent_uploads=1)
    data=wav_bytes()
    async def chunks():yield data
    async def scenario():
        task=asyncio.create_task(storage.save_stream(modality="audio",content_type="audio/wav",content_length=str(len(data)),
            chunks=chunks(),tenant_id="tenant-cancel",subject_id="subject-cancel"))
        assert await asyncio.to_thread(client.started.wait,2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert not storage._slots.acquire(blocking=False),"cancel released capacity while SDK upload was still running"
        client.release.set()
        await storage.aclose()
    try:
        asyncio.run(scenario())
        assert client.objects=={}
        assert store.db.execute("SELECT count(*) FROM media_assets WHERE tenant_id=?",("tenant-cancel",)).fetchone()[0]==0
        assert all(not thread.is_alive() for thread in storage._upload_executor._threads)
    finally:
        client.release.set();storage.close()


def test_s3_cancel_before_admission_does_not_consume_slot(store,tmp_path):
    storage=S3ObjectMediaStore(str(tmp_path),store,{"audio":100000},bucket="private-test",client=FakeS3(),max_concurrent_uploads=1)
    async def chunks():yield wav_bytes()
    async def scenario():
        task=asyncio.create_task(storage.save_stream(modality="audio",content_type="audio/wav",content_length=None,
            chunks=chunks(),tenant_id="tenant",subject_id="subject"))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    try:
        asyncio.run(scenario())
        assert storage._slots.acquire(blocking=False)
        storage._slots.release()
    finally:storage.close()


def test_s3_client_disconnect_during_body_does_not_upload_or_register(store,tmp_path):
    from starlette.requests import ClientDisconnect
    client=FakeS3()
    storage=S3ObjectMediaStore(str(tmp_path),store,{"audio":100000},bucket="private-test",client=client)
    async def chunks():
        yield wav_bytes()[:24]
        raise ClientDisconnect()
    try:
        with pytest.raises(ClientDisconnect):
            asyncio.run(storage.save_stream(modality="audio",content_type="audio/wav",content_length=None,
                chunks=chunks(),tenant_id="tenant-disconnected",subject_id="subject-disconnected"))
        assert client.objects=={}
        assert store.db.execute("SELECT count(*) FROM media_assets WHERE tenant_id=?",
            ("tenant-disconnected",)).fetchone()[0]==0
        assert storage._slots.acquire(blocking=False)
        storage._slots.release()
    finally:storage.close()


def test_s3_ambiguous_upload_failure_is_compensated(store,tmp_path):
    from app.core.errors import WellarError
    client=PartialUploadS3()
    storage=S3ObjectMediaStore(str(tmp_path),store,{"audio":100000},bucket="private-test",client=client)
    data=wav_bytes()
    async def chunks():yield data
    try:
        with pytest.raises(WellarError) as caught:
            asyncio.run(storage.save_stream(modality="audio",content_type="audio/wav",content_length=str(len(data)),
                chunks=chunks(),tenant_id="tenant",subject_id="subject"))
        assert caught.value.code=="object_storage_unavailable"
        assert client.objects=={}
        assert store.db.execute("SELECT count(*) FROM media_assets WHERE tenant_id=?",("tenant",)).fetchone()[0]==0
    finally:storage.close()


def test_s3_graceful_async_close_waits_for_active_upload(store,tmp_path):
    client=BlockingS3()
    storage=S3ObjectMediaStore(str(tmp_path),store,{"audio":100000},bucket="private-test",client=client)
    data=wav_bytes()
    async def chunks():yield data
    async def scenario():
        upload=asyncio.create_task(storage.save_stream(modality="audio",content_type="audio/wav",content_length=str(len(data)),
            chunks=chunks(),tenant_id="tenant-shutdown",subject_id="subject-shutdown"))
        assert await asyncio.to_thread(client.started.wait,2)
        close=asyncio.create_task(storage.aclose())
        await asyncio.sleep(.02)
        assert not close.done()
        client.release.set()
        result=await upload
        await close
        return result
    try:
        result=asyncio.run(scenario())
        assert result["size_bytes"]==len(data)
        assert all(not thread.is_alive() for thread in storage._upload_executor._threads)
    finally:
        client.release.set();storage.close()


def test_s3_storage_rejects_mime_signature_mismatch(store,tmp_path):
    storage=S3ObjectMediaStore(str(tmp_path),store,{"audio":1000,"video":1000,"image":1000},bucket="private",client=FakeS3())
    async def chunks():yield b"not-a-wave"
    try:asyncio.run(storage.save_stream(modality="audio",content_type="audio/wav",content_length="10",chunks=chunks(),tenant_id="t",subject_id="s"))
    except Exception as exc:assert getattr(exc,"code",None)=="invalid_media_signature"
    else:assert False,"invalid audio signature accepted"


def test_s3_error_contract_and_logs_do_not_expose_credentials(store,tmp_path,caplog):
    from app.core.errors import WellarError
    storage=S3ObjectMediaStore(str(tmp_path),store,{"audio":100000,"video":100000,"image":100000},bucket="private-test",client=BrokenS3())
    data=wav_bytes()
    async def chunks():yield data
    with pytest.raises(WellarError) as caught:
        asyncio.run(storage.save_stream(modality="audio",content_type="audio/wav",content_length=str(len(data)),chunks=chunks(),tenant_id="tenant",subject_id="subject"))
    assert caught.value.code=="object_storage_unavailable"
    leaked_marker_present = "SECRET-AWS-TOKEN" in caplog.text
    assert leaked_marker_present is False, "sensitive content was present in logs"
    storage.close()


def test_s3_quota_compensation_failure_is_observable_and_sanitized(tmp_path,caplog):
    import io
    import logging
    from app.core.logging import JsonFormatter
    from app.media.storage import MediaInputError
    stream=io.StringIO();handler=logging.StreamHandler(stream);handler.setFormatter(JsonFormatter());root=logging.getLogger();root.addHandler(handler)
    storage=S3ObjectMediaStore(str(tmp_path),QuotaStore(),{"audio":100000,"video":100000,"image":100000},bucket="private-test",client=BrokenDeleteS3())
    data=wav_bytes()
    async def chunks():yield data
    try:
        with pytest.raises(MediaInputError) as caught:
            asyncio.run(storage.save_stream(modality="audio",content_type="audio/wav",content_length=str(len(data)),chunks=chunks(),tenant_id="tenant-synthetic",subject_id="subject-synthetic"))
    finally:
        root.removeHandler(handler)
    assert caught.value.status_code==429
    assert "media.object.compensation_failed" in stream.getvalue()
    assert "orphan_object_possible" in stream.getvalue()
    assert "PRIVATE-OBJECT-CREDENTIAL" not in stream.getvalue()
    assert "tenant-synthetic" not in stream.getvalue()
    storage.close()


def test_s3_delete_failure_preserves_metadata_for_retry(store,tmp_path):
    from app.core.errors import WellarError
    client=RecoveringDeleteS3()
    storage=S3ObjectMediaStore(str(tmp_path),store,{"audio":100000,"video":100000,"image":100000},bucket="private-test",client=client)
    data=wav_bytes()
    async def chunks(): yield data
    response=asyncio.run(storage.save_stream(modality="audio",content_type="audio/wav",content_length=str(len(data)),chunks=chunks(),tenant_id="tenant-a",subject_id="user-a"))
    ref=response["media_ref"]
    with pytest.raises(WellarError) as failed:
        storage.delete(ref,"tenant-a","user-a")
    assert failed.value.code=="object_storage_unavailable"
    assert storage.resolve(ref,"tenant-a","user-a") is not None
    client.fail_deletes=False
    assert storage.delete(ref,"tenant-a","user-a")
    assert storage.resolve(ref,"tenant-a","user-a") is None
    storage.close()


def test_s3_blocked_delete_hits_total_deadline_and_retry_is_safe(monkeypatch):
    entered=threading.Event();release=threading.Event();lock=threading.Lock();calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_DELETE(self):
            with lock:
                calls.append(1);call_number=len(calls)
            entered.set()
            if call_number==1:release.wait(timeout=10)
            try:
                self.send_response(204);self.send_header("Content-Length","0");self.end_headers()
            except OSError:
                pass
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    server.daemon_threads=True
    server_thread=threading.Thread(target=server.serve_forever,daemon=True);server_thread.start()
    request={"action":"s3_delete","bucket":"synthetic-only","key":"synthetic/object",
        "region":"us-east-1","endpoint_url":f"http://127.0.0.1:{server.server_port}",
        "connect_timeout":1,"read_timeout":10,
        "credentials":{"access_key":"synthetic-key","secret_key":"synthetic-secret","session_token":None}}
    import app.media.deletion_process as deletion_module
    real_popen=deletion_module.subprocess.Popen;children=[]
    def record_child(*args,**kwargs):
        child=real_popen(*args,**kwargs);children.append(child);return child
    monkeypatch.setattr(deletion_module.subprocess,"Popen",record_child)
    try:
        with pytest.raises(StorageDeleteTimeout):run_bounded_storage_action(request,2.5)
        assert children[0].poll() is not None,"timed-out child was not reaped before returning"
        assert entered.wait(timeout=1)
        release.set()
        assert run_bounded_storage_action(request,5)["removed"]==1
        assert children[1].poll() is not None
        assert len(calls)==2
    finally:
        release.set();server.shutdown();server.server_close();server_thread.join(timeout=2)
    assert not server_thread.is_alive()


def test_storage_delete_subprocesses_are_limited_per_python_process(monkeypatch):
    import json
    import time
    from concurrent.futures import ThreadPoolExecutor
    import app.media.deletion_process as module

    lock=threading.Lock();release=threading.Event();at_limit=threading.Event()
    active=0;peak=0
    class SyntheticChild:
        returncode=0
        def __init__(self,*args,**kwargs):pass
        def communicate(self,**kwargs):
            nonlocal active,peak
            with lock:
                active+=1;peak=max(peak,active)
                if active==MAX_CONCURRENT_STORAGE_CHILDREN:at_limit.set()
            assert release.wait(2)
            with lock:active-=1
            return json.dumps({"removed":1}),""
        def poll(self):return self.returncode
        def kill(self):self.returncode=-9
    monkeypatch.setattr(module.subprocess,"Popen",SyntheticChild)
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures=[executor.submit(run_bounded_storage_action,{"action":"synthetic"},2) for _ in range(8)]
        assert at_limit.wait(1),"concurrent requests did not fill the configured child slots"
        time.sleep(.03)
        with lock:assert active==MAX_CONCURRENT_STORAGE_CHILDREN
        release.set()
        assert [future.result(timeout=2) for future in futures]==[{"removed":1}]*8
    assert peak==MAX_CONCURRENT_STORAGE_CHILDREN
    assert active==0


def test_storage_delete_capacity_wait_is_bounded(monkeypatch):
    import json
    from concurrent.futures import ThreadPoolExecutor
    import app.media.deletion_process as module
    release=threading.Event();at_limit=threading.Event();lock=threading.Lock();active=0
    class SyntheticChild:
        returncode=0
        def __init__(self,*args,**kwargs):pass
        def communicate(self,**kwargs):
            nonlocal active
            with lock:
                active+=1
                if active==MAX_CONCURRENT_STORAGE_CHILDREN:at_limit.set()
            assert release.wait(2)
            with lock:active-=1
            return json.dumps({"removed":1}),""
        def poll(self):return 0
        def kill(self):pytest.fail("completed synthetic child should not be killed")
    monkeypatch.setattr(module.subprocess,"Popen",SyntheticChild)
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_STORAGE_CHILDREN+1) as executor:
        active_calls=[executor.submit(run_bounded_storage_action,{"action":"synthetic"},1)
            for _ in range(MAX_CONCURRENT_STORAGE_CHILDREN)]
        assert at_limit.wait(1)
        saturated=executor.submit(run_bounded_storage_action,{"action":"synthetic"},.05)
        with pytest.raises(StorageDeleteCapacityTimeout):saturated.result(timeout=1)
        release.set()
        assert [future.result(timeout=1) for future in active_calls]==[{"removed":1}]*MAX_CONCURRENT_STORAGE_CHILDREN


@pytest.mark.parametrize("parent_error",[KeyboardInterrupt,SystemExit])
def test_storage_child_is_killed_and_reaped_on_parent_cancellation(monkeypatch,parent_error):
    import app.media.deletion_process as module
    calls=[]
    class SyntheticChild:
        returncode=None
        def __init__(self,*args,**kwargs):pass
        def communicate(self,**kwargs):
            calls.append("communicate")
            if len(calls)==1:raise parent_error()
            self.returncode=-9
            return "",""
        def poll(self):return self.returncode
        def kill(self):calls.append("kill");self.returncode=-9
    monkeypatch.setattr(module.subprocess,"Popen",SyntheticChild)
    with pytest.raises(parent_error):run_bounded_storage_action({"action":"synthetic"},1)
    assert calls==["communicate","kill","communicate"]
    # The slot was returned; a fresh action can start and finish.
    class FinishedChild:
        returncode=0
        def __init__(self,*args,**kwargs):pass
        def communicate(self,**kwargs):return '{"removed":1}',""
        def poll(self):return 0
        def kill(self):pytest.fail("finished child must not be killed")
    monkeypatch.setattr(module.subprocess,"Popen",FinishedChild)
    assert run_bounded_storage_action({"action":"synthetic"},1)=={"removed":1}


def test_synthetic_s3_delete_concurrency_resource_experiment(monkeypatch):
    """Small local-only process/RSS experiment; the server stores no objects."""
    import time
    import psutil
    from concurrent.futures import ThreadPoolExecutor
    from app.media.deletion_process import run_bounded_storage_action

    requests=0;http_peak=0;lock=threading.Lock();parent=psutil.Process()
    import app.media.deletion_process as deletion_module
    real_popen=deletion_module.subprocess.Popen;argv_records=[]
    def record_argv(argv,**kwargs):
        argv_records.append(list(argv))
        assert kwargs.get("stdin") is not None
        return real_popen(argv,**kwargs)
    monkeypatch.setattr(deletion_module.subprocess,"Popen",record_argv)
    class Handler(BaseHTTPRequestHandler):
        def do_DELETE(self):
            nonlocal requests,http_peak
            with lock:
                requests+=1;http_peak=max(http_peak,requests)
            time.sleep(.25)
            with lock:requests-=1
            self.send_response(204);self.send_header("Content-Length","0");self.end_headers()
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler);server.daemon_threads=True
    server_thread=threading.Thread(target=server.serve_forever,daemon=True);server_thread.start()
    observed={};cpu_observed={};sampled_child_peak=0;sampled_aggregate_peak=0;stop=threading.Event()
    def sample():
        nonlocal sampled_child_peak,sampled_aggregate_peak
        while not stop.is_set():
            try:
                current_count=0;current_rss=0
                for child in parent.children(recursive=False):
                    if child.is_running() and any(arg.endswith("deletion_process.py") for arg in child.cmdline()):
                        pid=child.pid
                        rss=child.memory_info().rss;times=child.cpu_times()
                        observed[pid]=max(observed.get(pid,0),rss)
                        cpu_observed[pid]=max(cpu_observed.get(pid,0),times.user+times.system)
                        current_count+=1;current_rss+=rss
                sampled_child_peak=max(sampled_child_peak,current_count)
                sampled_aggregate_peak=max(sampled_aggregate_peak,current_rss)
            except psutil.Error:pass
            stop.wait(.005)
    sampler=threading.Thread(target=sample,daemon=True);sampler.start()
    request={"action":"s3_delete","bucket":"synthetic-only","key":"synthetic/object",
        "region":"us-east-1","endpoint_url":f"http://127.0.0.1:{server.server_port}",
        "connect_timeout":1,"read_timeout":2,
        "credentials":{"access_key":"synthetic-access","secret_key":"synthetic-secret","session_token":None}}
    started=time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=6) as executor:
            results=list(executor.map(lambda _:run_bounded_storage_action(request,5),range(6)))
    finally:
        elapsed=time.perf_counter()-started;stop.set();sampler.join(timeout=2)
        server.shutdown();server.server_close();server_thread.join(timeout=2)
    assert results==[{"removed":1}]*6
    assert not sampler.is_alive() and not server_thread.is_alive()
    assert http_peak<=MAX_CONCURRENT_STORAGE_CHILDREN
    assert len(argv_records)==6 and all(len(argv)==2 for argv in argv_records)
    assert all("synthetic" not in " ".join(argv) and "secret" not in " ".join(argv) for argv in argv_records)
    print(f"synthetic_delete_experiment requests=6 concurrency_cap={MAX_CONCURRENT_STORAGE_CHILDREN} observed_http_peak={http_peak} elapsed_s={elapsed:.3f} sampled_process_peak={sampled_child_peak} sampled_single_child_peak_rss_mib={max(observed.values(),default=0)/1024/1024:.1f} sampled_aggregate_peak_rss_mib={sampled_aggregate_peak/1024/1024:.1f} sampled_child_cpu_s={sum(cpu_observed.values()):.3f}")


def test_delete_limit_scope_is_one_python_process_not_hostwide():
    """Three parents each admit two children: six host-visible deletes total."""
    processes=3;per_process=MAX_CONCURRENT_STORAGE_CHILDREN
    release=threading.Event();reached=threading.Event();lock=threading.Lock()
    active=0;peak=0
    class Handler(BaseHTTPRequestHandler):
        def do_DELETE(self):
            nonlocal active,peak
            with lock:
                active+=1;peak=max(peak,active)
                if active>=processes*per_process:reached.set()
            release.wait(timeout=12)
            with lock:active-=1
            self.send_response(204);self.send_header("Content-Length","0");self.end_headers()
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler);server.daemon_threads=True
    server_thread=threading.Thread(target=server.serve_forever,daemon=True);server_thread.start()
    endpoint=f"http://127.0.0.1:{server.server_port}"
    runner_code=("from tests.test_s3_storage import _run_synthetic_deletes_in_process; "
        "import sys; _run_synthetic_deletes_in_process(sys.argv[1])")
    workers=[subprocess.Popen([sys.executable,"-c",runner_code,endpoint],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,close_fds=True)
        for _ in range(processes)]
    try:
        assert reached.wait(timeout=25),"three processes did not reach their independent local limits"
        assert peak==processes*per_process
        release.set()
        for process in workers:
            stdout,stderr=process.communicate(timeout=20)
            assert process.returncode==0,(stdout,stderr)
    finally:
        release.set()
        for process in workers:
            if process.poll() is None:process.kill();process.wait(timeout=3)
        server.shutdown();server.server_close();server_thread.join(timeout=3)
    assert not server_thread.is_alive()
    print(f"multiprocess_delete_experiment parent_processes={processes} per_process_limit={per_process} observed_aggregate_peak={peak}; no host-wide cap is claimed")
