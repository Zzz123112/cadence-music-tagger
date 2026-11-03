"""图片尺寸解析：不依赖 Pillow，直接读文件头。"""
from __future__ import annotations

PNG_SIG = b"\x89PNG\r\n\x1a\n"
GIF_SIG = (b"GIF87a", b"GIF89a")
JPEG_SIG = b"\xff\xd8"


def image_size(data: bytes) -> tuple[int, int]:
    """返回 (宽, 高)。无法识别时返回 (0, 0)。"""
    if not data:
        return (0, 0)
    if data.startswith(PNG_SIG) and len(data) >= 24:
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        return (width, height)
    if data.startswith(JPEG_SIG):
        return _jpeg_size(data)
    for sig in GIF_SIG:
        if data.startswith(sig) and len(data) >= 10:
            width = int.from_bytes(data[6:8], "little")
            height = int.from_bytes(data[8:10], "little")
            return (width, height)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp_size(data)
    return (0, 0)


def _jpeg_size(data: bytes) -> tuple[int, int]:
    index = 2
    length = len(data)
    while index + 9 < length:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        # SOF0..SOF15，排除 DHT(0xC4)/JPG(0xC8)/DAC(0xCC)
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height = int.from_bytes(data[index + 5:index + 7], "big")
            width = int.from_bytes(data[index + 7:index + 9], "big")
            return (width, height)
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        segment_length = int.from_bytes(data[index + 2:index + 4], "big")
        if segment_length <= 0:
            break
        index += 2 + segment_length
    return (0, 0)


def _webp_size(data: bytes) -> tuple[int, int]:
    if len(data) >= 30 and data[12:16] == b"VP8X":
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return (width, height)
    if len(data) >= 30 and data[12:16] == b"VP8 ":
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        return (width, height)
    return (0, 0)


def is_small(data: bytes, threshold: int = 500) -> bool:
    """分辨率低于阈值的封面在界面上标「偏小」。"""
    width, height = image_size(data)
    if width == 0 or height == 0:
        return False
    return min(width, height) < threshold
