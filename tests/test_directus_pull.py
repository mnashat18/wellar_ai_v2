import time
from uuid import UUID
import hashlib
import json

import httpx
import pytest

from app.compat.directus_facade import DirectusHTTPClient, DirectusProcessRequest, DirectusPullProcessor, DirectusUnavailable
from app.security.auth import Principal
from tests.media_fixtures import image_bytes, wav_bytes
from app.workers.worker import Worker


SCAN="11111111-1111-4111-8111-111111111111"
USER="22222222-2222-4222-8222-222222222222"
VIDEO="33333333-3333-4333-8333-333333333333"
AUDIO="44444444-4444-4444-8444-444444444444"
IMAGE="55555555-5555-4555-8555-555555555555"


class FakeDirectus:
    def __init__(self):
        self.fail_delete=False
        self.fail_fetch=False
        self.scans={"id":SCAN,"user":USER,"business_profile":"tenant-a","status":"media_ready"}
        self.relations={"scan_id":SCAN,"video_file":VIDEO,"audio_file":AUDIO,"image_file":IMAGE}
        self.blobs={VIDEO:(b"0000ftypisomsynthetic", "video/mp4"), AUDIO:(wav_bytes(seconds=.1),"audio/wav"), IMAGE:(image_bytes(),"image/jpeg")}
        self.deleted=[]
        self.writes=[]
        self.statuses=[]
        self.result=None

    def get_scan(self,scan_id): return dict(self.scans)
    def get_scan_media(self,scan_id): return dict(self.relations)
    def fetch_file(self,file_id,maximum_bytes):
        if self.fail_fetch: raise DirectusUnavailable("directus_unavailable")
        blob,mime=self.blobs[file_id]
        return blob,mime
    def existing_result(self,scan_id): return self.result
    def write_result(self,scan_id,projection,deletion_proven):
        self.writes.append(bool(deletion_proven))
        self.result={"scan_id":scan_id,**projection,"status":projection.get("status","completed"),"deletion_proven":deletion_proven}
    def set_scan_status(self,scan_id,status): self.statuses.append(status)
    def delete_file(self,file_id):
        self.deleted.append(file_id)
        return not self.fail_delete


def request_payload():
    return DirectusProcessRequest(scan_id=SCAN,user_id=USER,video_file_id=VIDEO,audio_file_id=AUDIO,image_file_id=IMAGE).model_dump(mode="json")


def test_process_endpoint_is_fast_and_idempotent(client,auth):
    client.app.state.directus_pull_enabled=True
    client.app.state.authenticator.authenticate=lambda token: Principal(subject_id=USER,tenant_id="tenant-a")
    body=request_payload()
    started=time.perf_counter()
    first=client.post("/process",headers=auth,json=body)
    elapsed=time.perf_counter()-started
    second=client.post("/process",headers=auth,json=body)
    assert first.status_code==202 and second.status_code==202
    assert elapsed<.2
    assert first.json()["created"] is True and second.json()["created"] is False
    assert first.json()["scan_id"]==SCAN
    assert client.app.state.store.db.execute("SELECT COUNT(*) FROM scans").fetchone()[0]==1


def test_process_rejects_wrong_authenticated_owner(client,auth):
    client.app.state.directus_pull_enabled=True
    response=client.post("/process",headers=auth,json=request_payload())
    assert response.status_code==403


def test_pull_happy_path_projects_null_scores_and_deletes_both_stores(client):
    fake=FakeDirectus()
    calls=[]
    def analyze(payload):
        calls.append(payload)
        return {"scan_id":"internal","status":"insufficient_evidence","drowsiness_indicator":{"status":"available","state":"Stable","config_version":"1.0","personal_baseline":{"status":"available"}}}
    processor=DirectusPullProcessor(fake,client.app.state.media_store,analyze)
    result=processor({"_directus_pull":request_payload(),"_worker_context":{"tenant_id":"tenant-a","subject_id":USER}})
    assert result["deletion_proven"] is True and result["source"]=="directus_pull"
    assert result["legacy_projection"]["risk_level"]=="Stable"
    assert all(result["legacy_projection"][field] is None for field in ("readiness_score","fatigue_score","focus_score","risk_score","confidence_score"))
    assert set(fake.deleted)=={VIDEO,AUDIO,IMAGE}
    assert fake.writes==[False,True]
    assert fake.statuses==["completed"]
    assert len(calls)==1
    assert not list(client.app.state.media_store.root.rglob("*.jpg"))


@pytest.mark.parametrize("failure",["wrong_owner","wrong_tenant","foreign_file"])
def test_pull_rejects_wrong_owner_or_unattached_file_before_fetch(client,failure):
    fake=FakeDirectus()
    if failure=="wrong_owner": fake.scans["user"]="99999999-9999-4999-8999-999999999999"
    if failure=="wrong_tenant": fake.scans["business_profile"]="tenant-b"
    else: fake.relations["video_file"]="99999999-9999-4999-8999-999999999999"
    processor=DirectusPullProcessor(fake,client.app.state.media_store,lambda payload: pytest.fail("should reject before analysis"))
    with pytest.raises(ValueError):
        processor({"_directus_pull":request_payload(),"_worker_context":{"tenant_id":"tenant-a","subject_id":USER}})
    assert fake.deleted==[]


def test_delete_failure_retries_then_retry_uses_idempotent_result(client):
    fake=FakeDirectus(); calls=[]
    def analyze(payload):
        calls.append(1)
        return {"status":"insufficient_evidence","drowsiness_indicator":{"status":"insufficient_quality","config_version":"1.0"}}
    processor=DirectusPullProcessor(fake,client.app.state.media_store,analyze)
    fake.fail_delete=True
    with pytest.raises(DirectusUnavailable):
        processor({"_directus_pull":request_payload(),"_worker_context":{"tenant_id":"tenant-a","subject_id":USER}})
    assert calls==[1] and fake.writes==[False]
    fake.fail_delete=False
    result=processor({"_directus_pull":request_payload(),"_worker_context":{"tenant_id":"tenant-a","subject_id":USER}})
    assert result["deletion_proven"] is True and calls==[1]
    assert fake.writes[-1] is True


def test_directus_download_failure_is_retryable_without_deleting_remote_files(client):
    fake=FakeDirectus(); fake.fail_fetch=True
    processor=DirectusPullProcessor(fake,client.app.state.media_store,lambda payload: {})
    with pytest.raises(DirectusUnavailable):
        processor({"_directus_pull":request_payload(),"_worker_context":{"tenant_id":"tenant-a","subject_id":USER}})
    assert fake.deleted==[]
    fake.fail_fetch=False
    result=processor({"_directus_pull":request_payload(),"_worker_context":{"tenant_id":"tenant-a","subject_id":USER}})
    assert result["deletion_proven"] is True


def test_directus_down_releases_lease_and_worker_retry_completes(client,store):
    fake=FakeDirectus(); fake.fail_fetch=True
    processor=DirectusPullProcessor(fake,client.app.state.media_store,lambda payload:{"status":"insufficient_evidence","drowsiness_indicator":{"status":"insufficient_quality","config_version":"1.0"}})
    principal=Principal(subject_id=USER,tenant_id="tenant-a")
    payload={"media_refs":[],"_directus_pull":request_payload()}
    raw=json.dumps(payload,sort_keys=True,separators=(",",":"))
    internal_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    store.submit(internal_id,principal,SCAN,hashlib.sha256(raw.encode()).hexdigest(),raw,"request-test")
    worker=Worker(store,"directus-retry-test",processor)
    assert worker.run_once() is True
    assert store.get_scan(internal_id,"tenant-a",USER)["status"]=="queued"
    fake.fail_fetch=False
    time.sleep(.02)
    assert worker.run_once() is True
    assert store.get_scan(internal_id,"tenant-a",USER)["status"]=="completed"
    assert store.get_result(internal_id,"tenant-a",USER)["deletion_proven"] is True
    assert set(fake.deleted)=={VIDEO,AUDIO,IMAGE}


def test_file_size_mime_and_corrupt_content_are_rejected(client):
    fake=FakeDirectus(); fake.blobs[IMAGE]=(b"x"*(5*1024*1024+1),"image/jpeg")
    processor=DirectusPullProcessor(fake,client.app.state.media_store,lambda payload: {})
    with pytest.raises(ValueError,match="too_large"):
        processor({"_directus_pull":request_payload(),"_worker_context":{"tenant_id":"tenant-a","subject_id":USER}})
    fake=FakeDirectus(); fake.blobs[IMAGE]=(b"not-jpeg","image/jpeg")
    processor=DirectusPullProcessor(fake,client.app.state.media_store,lambda payload: {})
    with pytest.raises(Exception):
        processor({"_directus_pull":request_payload(),"_worker_context":{"tenant_id":"tenant-a","subject_id":USER}})


def test_http_client_rejects_redirect_private_origin_and_oversize_file():
    def redirect(request): return httpx.Response(302,headers={"Location":"http://127.0.0.1/secret"})
    client=DirectusHTTPClient("https://directus.example.test","test-token",transport=httpx.MockTransport(redirect))
    with pytest.raises(DirectusUnavailable,match="redirect"):
        client.get_scan(SCAN)
    with pytest.raises(DirectusUnavailable,match="private_address"):
        DirectusHTTPClient("https://127.0.0.1","test-token").get_scan(SCAN)
    def oversized(request): return httpx.Response(200,headers={"Content-Type":"image/jpeg","Content-Length":"999"},content=b"123")
    client=DirectusHTTPClient("https://directus.example.test","test-token",transport=httpx.MockTransport(oversized))
    with pytest.raises(ValueError,match="too_large"):
        client.fetch_file(IMAGE,10)


def test_http_mock_server_happy_path_uses_only_ids_and_idempotent_result_write(client):
    remote={"scan":{"id":SCAN,"user":USER,"business_profile":"tenant-a","status":"media_ready"},
            "relation":{"scan_id":SCAN,"video_file":VIDEO,"audio_file":AUDIO,"image_file":IMAGE},
            "files":{VIDEO:(b"0000ftypisomsynthetic","video/mp4"),AUDIO:(wav_bytes(seconds=.1),"audio/wav"),IMAGE:(image_bytes(),"image/jpeg")},
            "results":[],"deleted":set(),"status_writes":[]}
    def handler(request):
        assert request.headers["authorization"]=="Bearer local-fake-token"
        path=request.url.path
        if request.method=="GET" and path==f"/items/wellness_scans/{SCAN}":
            return httpx.Response(200,json={"data":remote["scan"]})
        if request.method=="GET" and path=="/items/scan_media":
            return httpx.Response(200,json={"data":[remote["relation"]]})
        if request.method=="GET" and path.startswith("/assets/"):
            file_id=path.rsplit("/",1)[-1]
            data,mime=remote["files"][file_id]
            return httpx.Response(200,headers={"content-type":mime,"content-length":str(len(data))},content=data)
        if request.method=="GET" and path=="/items/scan_results":
            return httpx.Response(200,json={"data":remote["results"]})
        if request.method=="POST" and path=="/items/scan_results":
            remote["results"].append({"id":"result-1",**__import__("json").loads(request.content)})
            return httpx.Response(201,json={"data":remote["results"][-1]})
        if request.method=="PATCH" and path=="/items/scan_results/result-1":
            remote["results"][0].update(__import__("json").loads(request.content))
            return httpx.Response(200,json={"data":remote["results"][0]})
        if request.method=="PATCH" and path==f"/items/wellness_scans/{SCAN}":
            remote["scan"].update(__import__("json").loads(request.content)); remote["status_writes"].append(remote["scan"]["status"])
            return httpx.Response(200,json={"data":remote["scan"]})
        if request.method=="DELETE" and path.startswith("/files/"):
            remote["deleted"].add(path.rsplit("/",1)[-1])
            return httpx.Response(204)
        return httpx.Response(404)
    directus=DirectusHTTPClient("https://directus.test","local-fake-token",transport=httpx.MockTransport(handler))
    calls=[]
    processor=DirectusPullProcessor(directus,client.app.state.media_store,lambda payload:(calls.append(payload) or {"status":"insufficient_evidence","drowsiness_indicator":{"status":"available","state":"Elevated Fatigue","config_version":"2.0"}}))
    result=processor({"_directus_pull":request_payload(),"_worker_context":{"tenant_id":"tenant-a","subject_id":USER}})
    assert result["deletion_proven"] is True
    assert remote["deleted"]=={VIDEO,AUDIO,IMAGE}
    assert remote["status_writes"]==["completed"]
    assert remote["results"][0]["risk_level"]=="Elevated Fatigue"
    assert remote["results"][0]["confidence"] is None
    projection=remote["results"][0]["internal_analysis"]["projection"]
    assert all(projection[field] is None for field in ("readiness_score","fatigue_score","focus_score","risk_score","confidence_score"))
    assert remote["results"][0]["internal_analysis"]["deletion_proven"] is True
    assert len(calls)==1
