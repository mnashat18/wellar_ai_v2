"""Deterministic PCM VAD checks; synthetic tones are not real speech validation."""
from __future__ import annotations

import struct
import wave

import numpy as np
import pytest

from app.media.extractors import InvalidMediaError, extract_audio
from tests.media_fixtures import wav_bytes


def _write_pcm(path, values: np.ndarray, rate: int=16000):
    values=np.asarray(values,dtype=np.int16)
    with wave.open(str(path),"wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(values.astype("<i2").tobytes())
    return path


def test_synthetic_speech_like_signal_is_a_conservative_candidate(tmp_path):
    path=tmp_path/"speech-like-not-speech.wav"
    path.write_bytes(wav_bytes(seconds=1.5))
    result=extract_audio(path,max_seconds=30)
    assert result["status"]=="available"
    assert result["features"]["voice_activity_status"]=="speech_like_activity_heuristic"
    assert result["features"]["voice_activity_candidate_window_count"]>=5
    assert result["provenance"]["voice_activity_scope"]=="speech_like_activity_candidate_only_not_speech_recognition_or_validated_vad"


def test_silence_and_pure_tone_are_not_reported_as_speech(tmp_path):
    silence=tmp_path/"silence.wav"
    silence.write_bytes(wav_bytes(seconds=1.5,silence=True))
    silence_result=extract_audio(silence,max_seconds=30)
    assert silence_result["status"]=="insufficient_quality"
    assert silence_result["features"]["voice_activity_status"]=="silence"
    assert silence_result["quality"]["reason"]=="silence_detected"

    rate=16000
    tone=(np.sin(2*np.pi*440*np.arange(rate*2)/rate)*8000).astype(np.int16)
    tone_path=_write_pcm(tmp_path/"tone.wav",tone,rate)
    tone_result=extract_audio(tone_path,max_seconds=30)
    assert tone_result["features"]["voice_activity_status"]=="uncertain_noisy_or_non_speech"
    assert tone_result["status"]=="insufficient_quality"


@pytest.mark.parametrize("sample_width",[1,2,3,4])
def test_supported_pcm_integer_sample_widths_decode(tmp_path,sample_width):
    path=tmp_path/f"pcm-{sample_width * 8}-bit.wav"
    payload=bytes([128])*16000 if sample_width==1 else bytes(16000*sample_width)
    with wave.open(str(path),"wb") as output:
        output.setnchannels(1); output.setsampwidth(sample_width); output.setframerate(16000); output.writeframes(payload)
    result=extract_audio(path,max_seconds=30)
    assert result["features"]["sample_rate_hz"]==16000
    assert result["features"]["voice_activity_status"]=="silence"


def test_stationary_noise_is_uncertain_not_speech(tmp_path):
    rng=np.random.default_rng(20261020)
    noise=np.clip(rng.normal(0,.12,16000*2),-.9,.9)
    path=_write_pcm(tmp_path/"noise.wav",(noise*32767).astype(np.int16))
    result=extract_audio(path,max_seconds=30)
    assert result["status"]=="insufficient_quality"
    assert result["features"]["voice_activity_status"]=="uncertain_noisy_or_non_speech"
    assert result["quality"]["reason"]=="speech_activity_uncertain"


def test_clipping_invalid_wav_and_unsupported_codec_are_rejected(tmp_path):
    clipped=np.tile(np.array([32767,-32768,32767,-32768,0,0,0,0],dtype=np.int16),4000)
    clip_path=_write_pcm(tmp_path/"clipped.wav",clipped)
    clipped_result=extract_audio(clip_path,max_seconds=30)
    assert clipped_result["status"]=="insufficient_quality"
    assert clipped_result["features"]["voice_activity_status"]=="uncertain_clipped_signal"

    invalid=tmp_path/"invalid.wav"
    invalid.write_bytes(b"RIFF-no-valid-wave")
    with pytest.raises(InvalidMediaError,match="audio_decode_failed"):
        extract_audio(invalid,max_seconds=30)

    # A-law WAV container (format tag 6) is RIFF/WAVE but not supported PCM.
    fmt=struct.pack("<HHIIHH",6,1,8000,8000,1,8)
    payload=b"\xd5"*8000
    def chunk(tag,data):
        return tag+struct.pack("<I",len(data))+data+(b"\0" if len(data)%2 else b"")
    body=b"WAVE"+chunk(b"fmt ",fmt)+chunk(b"data",payload)
    unsupported=tmp_path/"unsupported-alaw.wav"
    unsupported.write_bytes(b"RIFF"+struct.pack("<I",len(body))+body)
    with pytest.raises(InvalidMediaError,match="audio_decode_failed"):
        extract_audio(unsupported,max_seconds=30)


def test_media_intake_rejects_non_wav_audio_codec(client,auth):
    response=client.post("/api/v1/media/audio",headers={**auth,"Content-Type":"audio/mp4"},content=b"synthetic")
    assert response.status_code==415
    assert response.json()["error"]["code"]=="unsupported_media_type"
