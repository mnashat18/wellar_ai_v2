"""Procedural synthetic fixtures; contain no human or licensed research media."""
from __future__ import annotations

import io
import math
import wave

import numpy as np
from PIL import Image


def image_bytes(width=240,height=180,kind="black"):
    pixels=np.zeros((height,width,3),dtype=np.uint8)
    if kind=="texture":
        rng=np.random.default_rng(7); pixels[:]=rng.integers(0,255,pixels.shape,dtype=np.uint8)
    buff=io.BytesIO(); Image.fromarray(pixels,"RGB").save(buff,format="JPEG",quality=85)
    return buff.getvalue()


def wav_bytes(*,seconds=1.5,silence=False):
    rate=16000; count=int(rate*seconds)
    values=np.zeros(count,dtype=np.int16) if silence else (np.sin(np.arange(count)*2*math.pi*440/rate)*5000).astype(np.int16)
    buff=io.BytesIO()
    with wave.open(buff,"wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate); out.writeframes(values.astype("<i2").tobytes())
    return buff.getvalue()


def video_file(path, *, seconds=2, fps=4):
    import cv2
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*"MJPG"),fps,(160,120))
    if not writer.isOpened(): raise RuntimeError("synthetic MJPG encoder unavailable")
    for _ in range(seconds*fps): writer.write(np.zeros((120,160,3),dtype=np.uint8))
    writer.release()
    return path.read_bytes()


def audio_file(path, *, seconds=31, rate=16000):
    path.write_bytes(wav_bytes(seconds=seconds))
    return path


def png_dimension_bomb():
    import struct,zlib
    buff=io.BytesIO(); Image.new("RGB",(20,20),(1,2,3)).save(buff,format="PNG")
    data=bytearray(buff.getvalue()); width,height=3000,2000
    data[16:24]=struct.pack(">II",width,height)
    data[29:33]=struct.pack(">I",zlib.crc32(data[12:29])&0xffffffff)
    return bytes(data)
