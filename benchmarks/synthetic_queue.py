from __future__ import annotations

import argparse
import concurrent.futures
import statistics
import tempfile
import time
from pathlib import Path

from app.queue.sqlite_store import SQLiteQueueStore
from app.security.auth import Principal
from app.workers.worker import Worker


def percentile(values, p):
    values=sorted(values)
    return values[min(len(values)-1,max(0,int((len(values)-1)*p)))]


def run(scans: int, concurrency: int) -> dict:
    with tempfile.TemporaryDirectory() as temp:
        store=SQLiteQueueStore(str(Path(temp)/"bench.sqlite"),capacity=scans+1)
        principal=Principal("synthetic-user","synthetic-tenant")
        cpu_start=time.process_time()
        accepted=[]
        def submit(i):
            started=time.perf_counter()
            store.submit(f"scan-{i}",principal,f"key-{i}",str(i),"{}")
            return (time.perf_counter()-started)*1000
        start=time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            accepted=list(pool.map(submit,range(scans)))
        accepted_elapsed=time.perf_counter()-start
        worker=Worker(store,"bench-worker")
        completed=0
        start=time.perf_counter()
        while completed<scans:
            if worker.run_once(): completed+=1
            else: time.sleep(0.001)
        elapsed=time.perf_counter()-start
        completion_ms=[max(0,row["result_at"]-row["enqueued_at"])*1000 for row in store.db.execute("SELECT results.created_at AS result_at,scans.created_at AS enqueued_at FROM results JOIN scans USING(scan_id)").fetchall()]
        store.close()
        cpu_seconds=time.process_time()-cpu_start
        return {"benchmark":"synthetic_queue_only","scans":scans,"accepted_per_second":round(scans/accepted_elapsed,2),"completed_per_second":round(completed/elapsed,2),"acceptance_ms":{"p50":round(statistics.median(accepted),3),"p95":round(percentile(accepted,.95),3),"p99":round(percentile(accepted,.99),3)},"completion_latency_ms":{"p50":round(statistics.median(completion_ms),3),"p95":round(percentile(completion_ms,.95),3),"p99":round(percentile(completion_ms,.99),3)},"completion_elapsed_seconds":round(elapsed,3),"process_cpu_seconds":round(cpu_seconds,4),"errors":0,"note":"Synthetic queue/database only; excludes media extraction, analysis, network services and client polling."}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--scans",type=int,default=100); parser.add_argument("--concurrency",type=int,default=4)
    args=parser.parse_args()
    if not 1<=args.scans<=10000 or not 1<=args.concurrency<=32: parser.error("scans must be 1..10000 and concurrency 1..32")
    print(run(args.scans,args.concurrency))


if __name__=="__main__": main()
