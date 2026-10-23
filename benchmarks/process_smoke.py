"""Bounded subprocess smoke: real Uvicorn HTTP plus the independent worker CLI."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def request(url: str, method="GET", body=None, headers=None):
    data=json.dumps(body).encode() if body is not None else None
    req=urllib.request.Request(url,data=data,headers=headers or {},method=method)
    if data is not None: req.add_header("Content-Type","application/json")
    with urllib.request.urlopen(req,timeout=1) as response:
        return response.status,json.loads(response.read())


def main():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        env=os.environ.copy()
        env.update({"WELLAR_ENV":"test","WELLAR_DB_PATH":str(Path(directory)/"smoke.sqlite"),"WELLAR_DEV_AUTH_TOKENS_JSON":json.dumps({"smoke-token":{"sub":"synthetic-user","tenant_id":"synthetic-tenant"}}),"WELLAR_QUEUE_CAPACITY":"10"})
        api=subprocess.Popen([sys.executable,"-m","uvicorn","app.main:app","--host","127.0.0.1","--port","18765","--log-level","error"],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        worker=None
        try:
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                if api.poll() is not None: raise RuntimeError("API process exited before health became ready")
                try:
                    status,_=request("http://127.0.0.1:18765/ready")
                    if status==200: break
                except (OSError,urllib.error.URLError): time.sleep(.1)
            else: raise TimeoutError("API did not become ready within 10 seconds")
            worker=subprocess.Popen([sys.executable,"-m","app.workers.entrypoint"],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            status,accepted=request("http://127.0.0.1:18765/api/v1/scans","POST",{"schema_version":"1.0","media_refs":[],"reaction_task":None},{"Authorization":"Bearer smoke-token","Idempotency-Key":"smoke-001"})
            assert status==202
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                try:
                    status,result=request(f"http://127.0.0.1:18765/api/v1/scans/{accepted['scan_id']}/result",headers={"Authorization":"Bearer smoke-token"})
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code!=404: raise
                    time.sleep(.1)
            else: raise TimeoutError("worker did not publish result within 8 seconds")
            assert result["status"]=="insufficient_evidence"
            print(json.dumps({"api":"ready","worker":"processed","http_status":status,"result_status":result["status"],"synthetic_only":True}))
        finally:
            if worker is not None:
                worker.terminate()
                try: worker.wait(timeout=3)
                except subprocess.TimeoutExpired: worker.kill(); worker.wait(timeout=2)
            api.terminate()
            try: api.wait(timeout=3)
            except subprocess.TimeoutExpired: api.kill(); api.wait(timeout=2)
            time.sleep(.2)


if __name__=="__main__": main()
