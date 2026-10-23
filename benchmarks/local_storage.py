from __future__ import annotations

import argparse
import asyncio
import io
import json
import statistics
import tempfile
import time
import wave
from pathlib import Path

from app.media.storage import LocalMediaStore
from app.queue.sqlite_store import SQLiteQueueStore


def percentile(values,p):
    ordered=sorted(values);return ordered[min(len(ordered)-1,int((len(ordered)-1)*p))]


async def chunks(data):yield data


async def exercise(objects,blob,repeats,upload,resolve,deleted):
    for _ in range(repeats):
        start=time.perf_counter();record=await objects.save_stream(modality="audio",content_type="audio/wav",content_length=str(len(blob)),chunks=chunks(blob),tenant_id="synthetic-tenant",subject_id="synthetic-user");upload.append((time.perf_counter()-start)*1000)
        start=time.perf_counter();objects.resolve(record["media_ref"],"synthetic-tenant","synthetic-user",verify_integrity=True);resolve.append((time.perf_counter()-start)*1000)
        start=time.perf_counter();objects.delete(record["media_ref"],"synthetic-tenant","synthetic-user");deleted.append((time.perf_counter()-start)*1000)


def run(repeats=100):
    data=io.BytesIO()
    with wave.open(data,"wb") as output:output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(b"\x01\x00"*1600)
    blob=data.getvalue()
    with tempfile.TemporaryDirectory() as root:
        store=SQLiteQueueStore(str(Path(root)/"queue.sqlite"),capacity=10)
        objects=LocalMediaStore(str(Path(root)/"objects"),store,{"audio":100000,"video":100000,"image":100000},max_total_bytes=10**8,max_tenant_bytes=10**8)
        deleted=[]; upload=[]; resolve=[]; cpu=time.process_time();started=time.perf_counter()
        asyncio.run(exercise(objects,blob,repeats,upload,resolve,deleted))
        elapsed=time.perf_counter()-started
        result={"benchmark":"synthetic_local_storage_only","repeats":repeats,"bytes_per_object":len(blob),"cycles_per_second":round(repeats/elapsed,2),"elapsed_seconds":round(elapsed,4),"connection_count":1,"cpu_seconds":round(time.process_time()-cpu,4),"upload_ms":{"p50":statistics.median(upload),"p95":percentile(upload,.95),"p99":percentile(upload,.99)},"resolve_and_sha256_ms":{"p50":statistics.median(resolve),"p95":percentile(resolve,.95),"p99":percentile(resolve,.99)},"delete_ms":{"p50":statistics.median(deleted),"p95":percentile(deleted,.95),"p99":percentile(deleted,.99)},"failures":0,"retries":0,"limitation":"Synthetic 3.2KB PCM WAV objects, local Windows filesystem and SQLite metadata only; not PostgreSQL/S3 or network object storage."}
        store.close();return result


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--repeats",type=int,default=100);args=parser.parse_args()
    if not 1<=args.repeats<=10000:parser.error("repeats must be 1..10000")
    print(json.dumps(run(args.repeats),sort_keys=True))
