"""Procedural synthetic fixtures; contain no human or licensed research media."""
from __future__ import annotations

import io
import wave

import numpy as np
from PIL import Image


def image_bytes(width=240,height=180,kind="black"):
    pixels=np.zeros((height,width,3),dtype=np.uint8)
    if kind=="texture":
        rng=np.random.default_rng(7); pixels[:]=rng.integers(0,255,pixels.shape,dtype=np.uint8)
    buff=io.BytesIO(); Image.fromarray(pixels,"RGB").save(buff,format="JPEG",quality=85)
    return buff.getvalue()


def synthetic_face_image_bytes(*,brightness=1.0):
    """Original geometric cartoon; generated from code, no source photo or person.

    The fixture is a project-authored test drawing made from PIL primitives.
    It has no external asset, identity, biometric source, or third-party media
    license dependency. Brightness scales pixels only; geometry and dimensions
    remain constant across variants.
    """
    from PIL import ImageDraw,ImageEnhance
    scale=3
    image=Image.new("RGB",(512*scale,512*scale),(210,218,225))
    draw=ImageDraw.Draw(image)
    def ellipse(box,fill,outline=None,width=1):
        draw.ellipse(tuple(int(value*scale) for value in box),fill=fill,outline=outline,width=width*scale if outline else 1)
    def line(points,fill,width=1):
        draw.line([(int(x*scale),int(y*scale)) for x,y in points],fill=fill,width=width*scale,joint="curve")
    def polygon(points,fill):
        draw.polygon([(int(x*scale),int(y*scale)) for x,y in points],fill=fill)

    ellipse((105,35,407,462),(43,38,35))
    ellipse((128,70,384,440),(227,175,133),(105,65,48),3)
    ellipse((105,200,146,278),(218,153,116)); ellipse((366,200,407,278),(218,153,116))
    polygon([(128,170),(135,105),(180,55),(255,38),(335,58),(382,120),(384,178),(345,126),(300,104),(232,112),(177,135)],(44,38,35))
    line([(167,190),(194,177),(222,185)],(64,43,34),8); line([(287,185),(316,177),(343,191)],(64,43,34),8)
    ellipse((166,197,224,226),(246,244,231),(82,59,46),3); ellipse((287,197,345,226),(246,244,231),(82,59,46),3)
    ellipse((187,197,207,222),(70,118,126)); ellipse((308,197,328,222),(70,118,126))
    ellipse((193,202,202,220),(30,34,34)); ellipse((314,202,323,220),(30,34,34))
    ellipse((196,203,201,208),(255,255,255)); ellipse((317,203,322,208),(255,255,255))
    line([(256,205),(245,254),(233,278),(253,287),(273,278)],(145,91,69),5)
    ellipse((231,277,247,284),(112,68,54)); ellipse((265,277,281,284),(112,68,54))
    ellipse((158,253,207,273),(222,132,119)); ellipse((305,253,354,273),(222,132,119))
    line([(205,322),(229,309),(255,315),(281,309),(306,322),(282,339),(255,345),(228,339),(205,322)],(115,42,49),7)
    line([(218,320),(255,326),(293,320)],(255,222,204),4)
    image=image.resize((512,512),Image.Resampling.LANCZOS)
    if brightness!=1.0:
        image=ImageEnhance.Brightness(image).enhance(brightness)
    output=io.BytesIO(); image.save(output,format="PNG")
    return output.getvalue()


def wav_bytes(*,seconds=1.5,silence=False,rate=16000):
    """Generate PCM silence or a synthetic speech-like, explicitly non-speech signal."""
    count=int(rate*seconds)
    if silence:
        values=np.zeros(count,dtype=np.int16)
    else:
        t=np.arange(count,dtype=np.float64)/rate
        f0=115+28*np.sin(2*np.pi*.8*t)+12*np.sin(2*np.pi*2.1*t)
        phase=2*np.pi*np.cumsum(f0)/rate
        envelope=.18+.82*(.5+.5*np.sin(2*np.pi*2.4*t))**1.4
        signal=np.zeros(count,dtype=np.float64)
        for harmonic in range(1,25):
            frequency=harmonic*f0
            formant=(.75*np.exp(-.5*((frequency-650)/330)**2)+
                     .55*np.exp(-.5*((frequency-1450)/500)**2)+
                     .28*np.exp(-.5*((frequency-2450)/650)**2)+.015)
            signal+=formant/harmonic*np.sin(harmonic*phase)
        signal=signal*envelope
        signal=signal/max(float(np.max(np.abs(signal))),1e-9)*8000
        values=np.clip(signal,-32768,32767).astype(np.int16)
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


def mp4_video_file(path, *, seconds=2, fps=4):
    """Write a synthetic no-face MP4 using OpenCV's MPEG-4 Part 2 codec."""
    import cv2
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*"mp4v"),fps,(160,120))
    if not writer.isOpened():
        writer.release()
        raise RuntimeError("synthetic mp4v encoder unavailable")
    try:
        for index in range(seconds*fps):
            writer.write(np.full((120,160,3),32+(index%8)*8,dtype=np.uint8))
    finally:
        writer.release()
    data=path.read_bytes()
    if len(data)<12 or data[4:8]!=b"ftyp":
        raise RuntimeError("synthetic MP4 writer produced no MP4 container")
    return data


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
