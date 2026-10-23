"""Bounded cold/warm measurements on local synthetic, non-human media."""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
import sys
from threading import RLock

import cv2
import mediapipe
import numpy

from app.media.extractors import extract_audio,extract_image,extract_video,_face_mesh_factory


class MeshCache:
    def __init__(self): self.items={}; self.lock=RLock()
    def get(self,static):
        with self.lock:
            key="image" if static else "video"
            if key not in self.items: self.items[key]=_face_mesh_factory(static_image_mode=static)
            return self.items[key]
    def close(self):
        for mesh in self.items.values(): mesh.close()


def percentile(values,p):
    ordered=sorted(values); return ordered[min(len(ordered)-1,max(0,int((len(ordered)-1)*p)))]


def run(repeats:int=8):
    import cv2,mediapipe,numpy
    from tests.media_fixtures import image_bytes,video_file,wav_bytes
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory); image=root/"synthetic.jpg"; image.write_bytes(image_bytes()); video=root/"synthetic.avi"; video.write_bytes(video_file(root/"generated.avi")); audio=root/"synthetic.wav"; audio.write_bytes(wav_bytes())
        cache=MeshCache(); results={}; cpu_start=time.process_time()
        callables={"image":lambda:extract_image(image,max_pixels=4_000_000,face_mesh_provider=lambda:cache.get(True)),"video":lambda:extract_video(video,max_seconds=30,max_frames=1800,sample_hz=4,max_pixels=4_000_000,face_mesh_provider=lambda:cache.get(False)),"audio":lambda:extract_audio(audio,max_seconds=30)}
        try:
            for modality,fn in callables.items():
                proc_started=time.perf_counter()
                proc=subprocess.run([sys.executable,"-m","benchmarks.media_latency","--probe",modality,str({"image":image,"video":video,"audio":audio}[modality])],capture_output=True,text=True,timeout=30,check=False)
                if proc.returncode: raise RuntimeError(f"cold probe failed for {modality}: {proc.stderr[-2000:]}")
                cold_process_ms=(time.perf_counter()-proc_started)*1000
                probe=json.loads(proc.stdout.strip().splitlines()[-1])
                cold_start=time.perf_counter(); cold=fn(); cold_wall=(time.perf_counter()-cold_start)*1000
                warm=[]; internal=[]
                for _ in range(repeats):
                    start=time.perf_counter(); result=fn(); warm.append((time.perf_counter()-start)*1000)
                    key="decode_and_features" if modality=="audio" else "total"
                    internal.append(result["timing_ms"].get(key,warm[-1]))
                results[modality]={"fixture_status":cold["status"],"cold_fresh_process_ms":round(cold_process_ms,3),"cold_process_first_extractor_ms":probe["extractor_ms"],"cold_detector_first_call_ms":round(cold_wall,3),"warm_wall_ms":{"p50":round(statistics.median(warm),3),"p95":round(percentile(warm,.95),3),"p99":round(percentile(warm,.99),3)},"warm_extractor_ms":{"p50":round(statistics.median(internal),3),"p95":round(percentile(internal,.95),3),"p99":round(percentile(internal,.99),3)}}
        finally: cache.close()
        return {"benchmark":"synthetic_media_extraction_only","platform":platform.platform(),"python":platform.python_version(),"cpu":platform.processor() or platform.machine(),"logical_cpu_count":__import__("os").cpu_count(),"opencv":cv2.__version__,"mediapipe":mediapipe.__version__,"numpy":numpy.__version__,"repeats":repeats,"process_cpu_seconds":round(time.process_time()-cpu_start,4),"modalities":results,"limitation":"Generated synthetic fixtures only; no human media, no end-to-end queue/network timings, no validated fatigue inference, no Linux container verification."}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--repeats",type=int,default=8); parser.add_argument("--probe",choices=("image","video","audio")); parser.add_argument("fixture_path",nargs="?"); args=parser.parse_args()
    if args.probe:
        path=Path(args.fixture_path); cache=MeshCache()
        try:
            started=time.perf_counter()
            if args.probe=="image": result=extract_image(path,max_pixels=4_000_000,face_mesh_provider=lambda:cache.get(True))
            elif args.probe=="video": result=extract_video(path,max_seconds=30,max_frames=1800,sample_hz=4,max_pixels=4_000_000,face_mesh_provider=lambda:cache.get(False))
            else: result=extract_audio(path,max_seconds=30)
            print(json.dumps({"status":result["status"],"extractor_ms":round((time.perf_counter()-started)*1000,3)}))
        finally: cache.close()
        return
    if not 3<=args.repeats<=30: parser.error("repeats must be between 3 and 30")
    print(json.dumps(run(args.repeats),sort_keys=True))


if __name__=="__main__": main()
