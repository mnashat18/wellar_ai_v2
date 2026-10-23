"""Lazy worker-side media decoders and deterministic, non-diagnostic features."""
from __future__ import annotations

import math
import time
import wave
from pathlib import Path


class InvalidMediaError(ValueError): pass


def _face_mesh_factory(*,static_image_mode: bool):
    # Importing these packages at module import would slow and destabilize API startup.
    import mediapipe as mp
    return mp.solutions.face_mesh.FaceMesh(static_image_mode=static_image_mode,max_num_faces=1,refine_landmarks=True,min_detection_confidence=.5,min_tracking_confidence=.5)


_LEFT_EYE=(33,160,158,133,153,144)
_RIGHT_EYE=(362,385,387,263,373,380)
_MOUTH_VERTICAL=(13,14)
_MOUTH_HORIZONTAL=(78,308)
SHORT_EYE_FEATURE_VERSION="wellar-short-mobile-eye/1.0"
SHORT_EYE_MIN_OBSERVED_SECONDS=1.0
SHORT_EYE_MIN_VALID_SAMPLES=4
SHORT_EYE_MIN_TIME_COVERAGE=0.50
SHORT_EYE_MIN_SAMPLING_HZ=3.0


def _ear(landmarks, indices):
    import numpy as np
    try: points=np.asarray([(landmarks[i].x,landmarks[i].y) for i in indices],dtype=np.float64)
    except (IndexError,TypeError,AttributeError,ValueError): return None
    if points.shape!=(6,2) or not np.isfinite(points).all(): return None
    p0,p1,p2,p3,p4,p5=points
    horizontal=float(np.linalg.norm(p0-p3))
    if horizontal<=0: return None
    value=float((np.linalg.norm(p1-p5)+np.linalg.norm(p2-p4))/(2*horizontal))
    return value if math.isfinite(value) and 0<=value<=1 else None


def _mouth_aspect_ratio(landmarks):
    """Return a geometric mouth-opening observation, never a yawn diagnosis."""
    import numpy as np
    try:
        points=np.asarray([(landmarks[i].x,landmarks[i].y) for i in (*_MOUTH_VERTICAL,*_MOUTH_HORIZONTAL)],dtype=np.float64)
    except (IndexError,TypeError,AttributeError,ValueError): return None
    if points.shape!=(4,2) or not np.isfinite(points).all(): return None
    vertical=float(np.linalg.norm(points[0]-points[1])); horizontal=float(np.linalg.norm(points[2]-points[3]))
    if horizontal<=0: return None
    ratio=vertical/horizontal
    return ratio if math.isfinite(ratio) and 0<=ratio<=2 else None


def temporal_eye_features(observations: list[dict],duration: float,sampling_hz: float)->dict:
    """Sparse descriptive facial events; no PERCLOS, yawn, or fatigue diagnosis."""
    if not math.isfinite(duration) or duration <= 0:
        raise InvalidMediaError("invalid_video_duration")
    if not math.isfinite(sampling_hz) or sampling_hz <= 0:
        raise InvalidMediaError("invalid_video_sampling_rate")
    if not observations:
        return {"observation_count":0,"valid_face_count":0,"expected_sample_count":max(1,math.ceil(duration*sampling_hz)),"face_coverage":0.0,"sampling_coverage":0.0,"overall_coverage":0.0,"missingness":1.0,"closure_event_count":0,"blink_event_count":0,"mouth_open_event_count":0,"sampled_closed_fraction":None,"sampled_mouth_open_fraction":None,"short_eye_closure_fraction":None,"short_eye_closure_observed_seconds":0.0,"short_eye_valid_observation_count":0,"short_eye_time_coverage":0.0,"short_eye_feature_status":"face_not_detected","short_eye_feature_version":SHORT_EYE_FEATURE_VERSION,"perclos_estimate":None,"perclos_status":"not_computed_not_clinically_validated"}
    expected=max(1,math.ceil(duration*sampling_hz)); valid=sum(o["face_valid"] for o in observations)
    bins=set(); valid_bins=set()
    for o in observations:
        b=min(expected-1,max(0,int(math.floor(o["timestamp_seconds"]*sampling_hz+.5))))
        if abs(o["timestamp_seconds"]-b/sampling_hz)<=.5/sampling_hz:
            bins.add(b)
            if o["face_valid"]: valid_bins.add(b)
    event_durations=[]; closed_seconds=0.0; observed_eye_seconds=0.0; short_closed_seconds=0.0; short_gap=1.5/sampling_hz; run_start=None; mouth_seconds=0.0; mouth_start=None; mouth_durations=[]; max_gap=max(.25,1.5/sampling_hz)
    for current,following in zip(observations,observations[1:]):
        gap=following["timestamp_seconds"]-current["timestamp_seconds"]
        continuous=0<gap<=max_gap
        if continuous and current["face_valid"] and following["face_valid"]:
            if current["closed"]: closed_seconds+=gap
            if gap<=short_gap:
                observed_eye_seconds+=gap
                if current["closed"]: short_closed_seconds+=gap
            if current["closed"] and run_start is None: run_start=current["timestamp_seconds"]
            if run_start is not None and following["closed"] is False:
                d=following["timestamp_seconds"]-run_start
                if d>=.1: event_durations.append(d)
                run_start=None
            current_open=current.get("mouth_open") is True
            following_open=following.get("mouth_open") is True
            if current_open: mouth_seconds+=gap
            if current_open and mouth_start is None: mouth_start=current["timestamp_seconds"]
            if mouth_start is not None and not following_open:
                d=following["timestamp_seconds"]-mouth_start
                if d>=.2: mouth_durations.append(d)
                mouth_start=None
        else: run_start=None
        if not continuous or not current["face_valid"] or not following["face_valid"]: mouth_start=None
    mouth_events=sum(.2<=d<=3.0 for d in mouth_durations)
    short_coverage=observed_eye_seconds/duration
    short_status="available" if observed_eye_seconds>=SHORT_EYE_MIN_OBSERVED_SECONDS and valid>=SHORT_EYE_MIN_VALID_SAMPLES and short_coverage>=SHORT_EYE_MIN_TIME_COVERAGE and sampling_hz>=SHORT_EYE_MIN_SAMPLING_HZ else ("insufficient_observed_eye_time" if observed_eye_seconds<SHORT_EYE_MIN_OBSERVED_SECONDS else "insufficient_valid_eye_samples" if valid<SHORT_EYE_MIN_VALID_SAMPLES else "insufficient_eye_time_coverage" if short_coverage<SHORT_EYE_MIN_TIME_COVERAGE else "insufficient_sampling_rate")
    short_fraction=short_closed_seconds/observed_eye_seconds if short_status=="available" and observed_eye_seconds else None
    return {"observation_count":len(observations),"valid_face_count":valid,"expected_sample_count":expected,"face_coverage":valid/len(observations),"sampling_coverage":len(bins)/expected,"overall_coverage":len(valid_bins)/expected,"missingness":1-len(valid_bins)/expected,"observed_span_seconds":max(0,observations[-1]["timestamp_seconds"]-observations[0]["timestamp_seconds"]),"closure_event_count":len(event_durations),"blink_event_count":sum(.1<=d<=.5 for d in event_durations),"closure_duration_mean_seconds":sum(event_durations)/len(event_durations) if event_durations else None,"sampled_closed_fraction":min(1.0,closed_seconds/duration),"short_eye_closure_fraction":short_fraction,"short_eye_closure_observed_seconds":round(observed_eye_seconds,6),"short_eye_valid_observation_count":valid,"short_eye_time_coverage":round(short_coverage,6),"short_eye_feature_status":short_status,"short_eye_feature_version":SHORT_EYE_FEATURE_VERSION,"short_eye_feature_semantics":"observed_eye_closure_fraction_over_valid_adjacent_intervals_in_this_short_clip_not_PERCLOS_or_sleepiness_prediction","short_eye_minimum_observed_seconds":SHORT_EYE_MIN_OBSERVED_SECONDS,"short_eye_minimum_valid_samples":SHORT_EYE_MIN_VALID_SAMPLES,"short_eye_minimum_sampling_hz":SHORT_EYE_MIN_SAMPLING_HZ,"short_eye_exclusion_rule":"exclude_intervals_when_either_endpoint_lacks_face_or_both_eye_landmarks_or_gap_exceeds_1.5_actual_sample_periods; no_interpolation","mouth_open_event_count":mouth_events,"mouth_open_event_duration_mean_seconds":sum(mouth_durations)/len(mouth_durations) if mouth_durations else None,"sampled_mouth_open_fraction":min(1.0,mouth_seconds/duration),"mouth_event_semantics":"observed_mouth_opening_not_yawn_or_sleepiness_label","sampled_closed_semantics":"sparse_descriptive_occupancy_not_perclos","perclos_estimate":None,"perclos_status":"not_computed_not_clinically_validated"}


def extract_video(path: Path, *, max_seconds: int, max_frames: int, sample_hz: float, max_pixels: int, face_mesh=None, face_mesh_provider=None, include_sample_features: bool=False)->dict:
    import cv2
    started=time.perf_counter(); capture=cv2.VideoCapture(str(path)); mesh=face_mesh; owns_mesh=mesh is None and face_mesh_provider is None
    try:
        if not capture.isOpened(): raise InvalidMediaError("video_decode_failed")
        fps=float(capture.get(cv2.CAP_PROP_FPS)); count=float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not math.isfinite(fps) or fps<=0 or fps>120 or not math.isfinite(count) or count<=0 or not count.is_integer(): raise InvalidMediaError("invalid_video_metadata")
        frames=int(count); duration=frames/fps
        if duration>max_seconds or frames>max_frames: raise InvalidMediaError("video_resource_limit")
        width=int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)); height=int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if width<1 or height<1 or width*height>max_pixels: raise InvalidMediaError("video_frame_resource_limit")
        stride=max(1,round(fps/sample_hz)); actual_hz=fps/stride; observations=[]; frames_read=0; sampled=0; decode_started=time.perf_counter(); decode_ms=0
        light_values=[]
        for i in range(frames):
            tick=time.perf_counter(); ok,frame=capture.read(); decode_ms+=(time.perf_counter()-tick)*1000
            if not ok: raise InvalidMediaError("truncated_video")
            frames_read+=1
            if frame is None or frame.ndim!=3 or frame.size==0: raise InvalidMediaError("invalid_video_frame")
            h,w=frame.shape[:2]
            if h*w>max_pixels or w>4096 or h>4096: raise InvalidMediaError("video_frame_resource_limit")
            if i%stride: continue
            if mesh is None: mesh=face_mesh_provider() if face_mesh_provider else _face_mesh_factory(static_image_mode=False)
            gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY); light_values.append(float(gray.mean()))
            rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB); face_tick=time.perf_counter(); found=mesh.process(rgb); face_ms=(time.perf_counter()-face_tick)*1000
            sampled+=1; faces=getattr(found,"multi_face_landmarks",None) or []; lm=faces[0].landmark if faces else None
            left=_ear(lm,_LEFT_EYE) if lm is not None else None; right=_ear(lm,_RIGHT_EYE) if lm is not None else None
            mouth_ratio=_mouth_aspect_ratio(lm) if lm is not None else None
            valid=left is not None and right is not None; ear=(left+right)/2 if valid else None
            observations.append({"frame_index":i,"timestamp_seconds":i/fps,"face_valid":valid,"closed":ear is not None and ear<=.16 if valid else None,"eye_aspect_ratio":ear,"mouth_aspect_ratio":mouth_ratio,"mouth_open":mouth_ratio is not None and mouth_ratio>=.55})
        temporal=temporal_eye_features(observations,duration,actual_hz); temporal["video_duration_seconds"]=duration; temporal["actual_sample_hz"]=actual_hz; face_count=temporal["valid_face_count"]
        enough_temporal_samples=face_count>=2 and temporal["observed_span_seconds"]>=.1
        enough_coverage=temporal["overall_coverage"]>=.5
        low_light=bool(light_values) and sum(light_values)/len(light_values)<20.0
        available=enough_temporal_samples and enough_coverage and not low_light
        reason=None if available else ("face_not_detected" if face_count==0 else "insufficient_temporal_samples" if not enough_temporal_samples else "insufficient_temporal_coverage" if not enough_coverage else "low_light")
        temporal["mean_sampled_luminance"]=(sum(light_values)/len(light_values)) if light_values else None
        temporal["light_quality"]="low_light" if low_light else "sufficient_luminance_for_extraction"
        temporal["eye_state_semantics"]="research_only_EAR_threshold_not_sleepiness_prediction"
        result={"status":"available" if available else "insufficient_quality","quality":{"reason":reason,"low_light":low_light,"face_observation_coverage":temporal["face_coverage"],"sampling_coverage":temporal["sampling_coverage"],"overall_coverage":temporal["overall_coverage"],"missingness":temporal["missingness"]},"features":temporal,"timing_ms":{"decode":round(decode_ms,3),"total":round((time.perf_counter()-started)*1000,3),"frames_decoded":frames_read,"frames_sampled":sampled},"provenance":{"extractor":"opencv_decode_mediapipe_face_mesh_eye_mouth_geometry","version":"mediapipe-0.10.9-opencv-4.10.0","eye_state_rule":"research_only_EAR_threshold_0.16","short_eye_feature_version":SHORT_EYE_FEATURE_VERSION,"mouth_open_rule":"research_only_mouth_aspect_ratio_threshold_0.55","blink_event_rule":"0.10_to_0.50_seconds_observed_open_boundary","mouth_event_rule":"0.20_to_3.00_seconds_observed_open_boundary_not_yawn_classification"}}
        if include_sample_features:
            result["sample_observations"]=[{"timestamp_ms":int(round(o["timestamp_seconds"]*1000)),"eye_aspect_ratio":o["eye_aspect_ratio"],"mouth_aspect_ratio":o["mouth_aspect_ratio"],"face_valid":o["face_valid"]} for o in observations]
        return result
    finally:
        if owns_mesh and mesh is not None: mesh.close()
        capture.release()


def extract_image(path: Path, *, max_pixels: int, face_mesh=None, face_mesh_provider=None)->dict:
    import cv2
    import numpy as np
    import warnings
    from PIL import Image,UnidentifiedImageError
    started=time.perf_counter()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error",Image.DecompressionBombWarning)
            with Image.open(path) as decoded:
                w,h=decoded.size
                if w<80 or h<80 or w*h>max_pixels: raise InvalidMediaError("image_dimensions_out_of_range")
                decoded.load()
                image=cv2.cvtColor(np.asarray(decoded.convert("RGB")),cv2.COLOR_RGB2BGR)
    except (UnidentifiedImageError,Image.DecompressionBombError,Image.DecompressionBombWarning,OSError) as exc:
        raise InvalidMediaError("image_decode_failed") from exc
    if image is None or image.size==0: raise InvalidMediaError("image_decode_failed")
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY); blur=float(cv2.Laplacian(gray,cv2.CV_64F).var()); mean=float(gray.mean()); std=float(gray.std())
    mesh=face_mesh; owns_mesh=mesh is None and face_mesh_provider is None
    try:
        if mesh is None: mesh=face_mesh_provider() if face_mesh_provider else _face_mesh_factory(static_image_mode=True)
        result=mesh.process(cv2.cvtColor(image,cv2.COLOR_BGR2RGB)); faces=getattr(result,"multi_face_landmarks",None) or []; face_visible=bool(faces)
        landmarks=faces[0].landmark if faces else None
        left=_ear(landmarks,_LEFT_EYE) if landmarks is not None else None
        right=_ear(landmarks,_RIGHT_EYE) if landmarks is not None else None
        eye_aspect_ratio=(left+right)/2 if left is not None and right is not None else None
        mouth_aspect_ratio=_mouth_aspect_ratio(landmarks) if landmarks is not None else None
    finally:
        if owns_mesh and mesh is not None: mesh.close()
    return {"status":"available" if face_visible else "insufficient_quality","quality":{"reason":None if face_visible else "face_not_detected","face_visible":face_visible,"width":w,"height":h},"features":{"face_visible":face_visible,"width_px":w,"height_px":h,"gray_mean":mean,"gray_std":std,"laplacian_variance":blur,"clipped_pixel_fraction":float(((gray==0)|(gray==255)).mean()),"eye_aspect_ratio":eye_aspect_ratio,"mouth_aspect_ratio":mouth_aspect_ratio},"timing_ms":{"total":round((time.perf_counter()-started)*1000,3)},"provenance":{"extractor":"opencv_image_quality_mediapipe_face_mesh_visibility","version":"mediapipe-0.10.9-opencv-4.10.0","eye_aspect_ratio":"normalized_eye_landmark_geometry_for_research_annotation_only","mouth_aspect_ratio":"normalized_mouth_landmark_geometry_for_research_annotation_only"}}


def extract_audio(path: Path, *, max_seconds: int)->dict:
    import numpy as np
    started=time.perf_counter()
    try:
        with wave.open(str(path),"rb") as wav:
            channels=wav.getnchannels(); width=wav.getsampwidth(); rate=wav.getframerate(); frames=wav.getnframes(); duration=frames/rate if rate else 0
            if wav.getcomptype()!="NONE" or channels not in (1,2) or width not in (1,2,3,4) or not 8000<=rate<=96000 or not 0<duration<=max_seconds: raise InvalidMediaError("audio_format_or_duration_limit")
            raw=wav.readframes(frames)
    except (wave.Error,EOFError) as exc: raise InvalidMediaError("audio_decode_failed") from exc
    if len(raw)!=frames*channels*width: raise InvalidMediaError("truncated_audio")
    if width==1: samples=(np.frombuffer(raw,dtype=np.uint8).astype(np.float32)-128)/128
    elif width==2: samples=np.frombuffer(raw,dtype="<i2").astype(np.float32)/32768
    elif width==4: samples=np.frombuffer(raw,dtype="<i4").astype(np.float64)/2147483648
    else:
        bytes_=np.frombuffer(raw,dtype=np.uint8).reshape(-1,3); vals=bytes_[:,0].astype(np.int32)|(bytes_[:,1].astype(np.int32)<<8)|(bytes_[:,2].astype(np.int32)<<16); vals=(vals^(1<<23))-(1<<23); samples=vals.astype(np.float64)/(1<<23)
    if samples.size==0: raise InvalidMediaError("empty_audio")
    peak=float(np.max(np.abs(samples))); rms=float(np.sqrt(np.mean(samples*samples))); db=lambda x:20*math.log10(max(x,1e-12))
    # Window-level silence fraction is descriptive signal quality, not a fatigue cue.
    per_channel=samples.reshape(-1,channels)
    channel_means=per_channel.mean(axis=0)
    centered=per_channel-channel_means
    dc_offset=float(np.max(np.abs(channel_means)))
    ac_rms=float(np.sqrt(np.mean(centered*centered)))
    frame_windows=max(1,rate//10); silence=0; windows=0
    for start in range(0,len(per_channel),frame_windows):
        if float(np.sqrt(np.mean(centered[start:start+frame_windows]**2)))<.001: silence+=1
        windows+=1
    clipping=float(np.mean(np.abs(samples)>=.999)); status="available" if duration>=1 and ac_rms>=.001 and clipping<.05 else "insufficient_quality"
    return {"status":status,"quality":{"reason":None if status=="available" else "short_silent_dc_offset_or_clipped_recording","duration_seconds":duration},"features":{"duration_seconds":duration,"sample_rate_hz":rate,"channels":channels,"rms_dbfs":db(rms),"peak_dbfs":db(peak),"dc_offset_max":dc_offset,"ac_rms_dbfs":db(ac_rms),"silence_window_fraction":silence/max(1,windows),"clipped_sample_fraction":clipping},"timing_ms":{"decode_and_features":round((time.perf_counter()-started)*1000,3)},"provenance":{"extractor":"python_wave_pcm_acoustic_summary","version":"wellar-audio-contract-1.1","quality_scope":"signal_integrity_only_speech_noise_and_ood_not_assessed"}}
