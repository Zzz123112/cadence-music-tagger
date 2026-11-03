"""生成测试用样本音频文件。

只生成真实可解析的文件：能用 libsndfile 编码的格式直接编码，
DSF 按规范手工构造（静音 DSD），不做伪造文件头。
"""
from __future__ import annotations

import os
import struct

import numpy as np
import soundfile as sf

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")


def tone(seconds: float = 1.0, rate: int = 44100, channels: int = 2) -> np.ndarray:
    t = np.linspace(0, seconds, int(seconds * rate), endpoint=False)
    sig = (0.08 * np.sin(2 * np.pi * 440 * t)).astype("float32")
    return np.stack([sig] * channels, axis=1)


def make_with_soundfile(name: str, fmt: str, subtype: str | None = None,
                        rate: int = 44100, seconds: float = 1.0) -> str:
    path = os.path.join(ROOT, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = tone(seconds=seconds, rate=rate, channels=2)
    kwargs = {"format": fmt}
    if subtype:
        kwargs["subtype"] = subtype
    sf.write(path, data, rate, **kwargs)
    return path


def make_dsf(name: str, seconds: float = 0.5, rate: int = 2822400,
             channels: int = 2) -> str:
    """按 DSF 规范构造静音 DSD 文件（mutagen 可读写其 ID3）。"""
    path = os.path.join(ROOT, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    sample_count = int(seconds * rate)
    bytes_per_channel = (sample_count + 7) // 8
    block_size = 4096
    # 每声道按 block 打包，静音即全 0
    data_bytes = bytes_per_channel * channels

    dsd_chunk_size = 28
    fmt_chunk_size = 52
    data_chunk_size = 12 + data_bytes
    total_size = dsd_chunk_size + fmt_chunk_size + data_chunk_size

    out = bytearray()
    # "DSD " chunk
    out += b"DSD "
    out += struct.pack("<Q", dsd_chunk_size)
    out += struct.pack("<Q", total_size)
    out += struct.pack("<Q", 0)  # 无 metadata 块
    # "fmt " chunk
    out += b"fmt "
    out += struct.pack("<Q", fmt_chunk_size)
    out += struct.pack("<I", 1)          # format version
    out += struct.pack("<I", 0)          # format id: DSD raw
    out += struct.pack("<I", 2)          # channel type: stereo
    out += struct.pack("<I", channels)
    out += struct.pack("<I", rate)
    out += struct.pack("<I", 1)          # bits per sample
    out += struct.pack("<Q", sample_count)
    out += struct.pack("<I", block_size)
    out += struct.pack("<I", 0)          # reserved
    # "data" chunk
    out += b"data"
    out += struct.pack("<Q", data_chunk_size)
    out += bytes(data_bytes)

    with open(path, "wb") as fh:
        fh.write(out)
    return path


def build_all() -> dict[str, str]:
    made: dict[str, str] = {}
    attempts = [
        ("sample.wav", lambda: make_with_soundfile("sample.wav", "WAV", "PCM_16")),
        ("sample_hires.wav", lambda: make_with_soundfile("sample_hires.wav", "WAV", "PCM_24", rate=96000)),
        ("sample.aiff", lambda: make_with_soundfile("sample.aiff", "AIFF", "PCM_16")),
        ("sample.flac", lambda: make_with_soundfile("sample.flac", "FLAC", "PCM_16")),
        ("sample_hires.flac", lambda: make_with_soundfile("sample_hires.flac", "FLAC", "PCM_24", rate=96000)),
        ("sample.ogg", lambda: make_with_soundfile("sample.ogg", "OGG", "VORBIS")),
        ("sample.mp3", lambda: make_with_soundfile("sample.mp3", "MP3")),
        ("sample.dsf", lambda: make_dsf("sample.dsf")),
    ]
    for name, fn in attempts:
        try:
            made[name] = fn()
        except Exception as exc:  # 编码器缺失时如实记录
            made[name] = f"ERROR: {type(exc).__name__}: {exc}"
    return made


if __name__ == "__main__":
    os.makedirs(ROOT, exist_ok=True)
    for name, result in build_all().items():
        status = "ok" if os.path.isfile(result) else result
        print(f"{name:20s} {status}")
