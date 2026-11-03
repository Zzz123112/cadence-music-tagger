"""按容器格式写标签。只改标签区，不碰音频帧（PRD P1）。"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass

import mutagen
from mutagen.aiff import AIFF
from mutagen.apev2 import BINARY, APEValue
from mutagen.asf import ASF
from mutagen.dsf import DSF
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, TALB, TDRC, TIT2, TPE1, TPE2, TRCK, USLT
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE

from ..core.probe import READ_ONLY_FORMATS, format_of


class WriteNotSupported(Exception):
    """该格式不支持写入，调用方应把它标成只读。"""


@dataclass
class WriteOptions:
    write_cover: bool = True
    write_lyrics: bool = True
    replace_cover: bool = True
    # 额外把封面存成同目录的 cover.jpg / cover.png
    write_cover_file: bool = False


ID3_FIELDS = {
    "title": TIT2,
    "artist": TPE1,
    "album": TALB,
    "year": TDRC,
    "track": TRCK,
    "albumartist": TPE2,
}

VORBIS_FIELDS = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "year": "date",
    "track": "tracknumber",
    "albumartist": "albumartist",
}

APE_FIELDS = {
    "title": "Title",
    "artist": "Artist",
    "album": "Album",
    "year": "Year",
    "track": "Track",
    "albumartist": "Album Artist",
}

MP4_FIELDS = {
    "title": "\xa9nam",
    "artist": "\xa9ART",
    "album": "\xa9alb",
    "year": "\xa9day",
    "albumartist": "aART",
}


def mime_of(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return "image/jpeg"


# ------------------------------------------------------------------ ID3


def _apply_id3(tags, fields: dict, cover: bytes | None, lyrics: str | None,
               opts: WriteOptions) -> None:
    for key, frame_cls in ID3_FIELDS.items():
        if key in fields and fields[key]:
            tags.delall(frame_cls.__name__)
            tags.add(frame_cls(encoding=3, text=fields[key]))
    if lyrics and opts.write_lyrics:
        tags.delall("USLT")
        tags.add(USLT(encoding=3, lang="chi", desc="", text=lyrics))
    if cover and opts.write_cover:
        if opts.replace_cover:
            tags.delall("APIC")
        tags.add(APIC(encoding=3, mime=mime_of(cover), type=3, desc="Cover", data=cover))


def _write_mp3(path: str, fields, cover, lyrics, opts) -> None:
    audio = MP3(path)
    if audio.tags is None:
        audio.add_tags()
    _apply_id3(audio.tags, fields, cover, lyrics, opts)
    audio.save()


def _write_wav(path: str, fields, cover, lyrics, opts) -> None:
    audio = WAVE(path)
    if audio.tags is None:
        audio.add_tags()
    _apply_id3(audio.tags, fields, cover, lyrics, opts)
    audio.save()


def _write_aiff(path: str, fields, cover, lyrics, opts) -> None:
    audio = AIFF(path)
    if audio.tags is None:
        audio.add_tags()
    _apply_id3(audio.tags, fields, cover, lyrics, opts)
    audio.save()


def _write_dsf(path: str, fields, cover, lyrics, opts) -> None:
    audio = DSF(path)
    if audio.tags is None:
        audio.add_tags()
    _apply_id3(audio.tags, fields, cover, lyrics, opts)
    audio.save()


# ----------------------------------------------------------------- FLAC


def _write_flac(path: str, fields, cover, lyrics, opts) -> None:
    audio = FLAC(path)
    for key, tag in VORBIS_FIELDS.items():
        if key in fields and fields[key]:
            audio[tag] = fields[key]
    if lyrics and opts.write_lyrics:
        audio["lyrics"] = lyrics
    if cover and opts.write_cover:
        if opts.replace_cover:
            audio.clear_pictures()
        picture = Picture()
        picture.type = 3
        picture.mime = mime_of(cover)
        picture.desc = "Cover"
        picture.data = cover
        audio.add_picture(picture)
    audio.save()


# --------------------------------------------------------------- Vorbis


def _vorbis_cover(audio, cover: bytes, replace: bool) -> None:
    if replace:
        audio.pop("metadata_block_picture", None)
    picture = Picture()
    picture.type = 3
    picture.mime = mime_of(cover)
    picture.desc = "Cover"
    picture.data = cover
    audio["metadata_block_picture"] = [base64.b64encode(picture.write()).decode("ascii")]


def _write_ogg(path: str, fields, cover, lyrics, opts) -> None:
    audio = OggVorbis(path)
    if audio.tags is None:
        audio.add_tags()
    for key, tag in VORBIS_FIELDS.items():
        if key in fields and fields[key]:
            audio[tag] = fields[key]
    if lyrics and opts.write_lyrics:
        audio["lyrics"] = lyrics
    if cover and opts.write_cover:
        _vorbis_cover(audio, cover, opts.replace_cover)
    audio.save()


def _write_opus(path: str, fields, cover, lyrics, opts) -> None:
    audio = OggOpus(path)
    if audio.tags is None:
        audio.add_tags()
    for key, tag in VORBIS_FIELDS.items():
        if key in fields and fields[key]:
            audio[tag] = fields[key]
    if lyrics and opts.write_lyrics:
        audio["lyrics"] = lyrics
    if cover and opts.write_cover:
        _vorbis_cover(audio, cover, opts.replace_cover)
    audio.save()


# ------------------------------------------------------------------ MP4


def _write_mp4(path: str, fields, cover, lyrics, opts) -> None:
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    for key, tag in MP4_FIELDS.items():
        if key in fields and fields[key]:
            audio[tag] = [fields[key]]
    if "track" in fields and fields["track"]:
        digits = "".join(ch for ch in str(fields["track"]).split("/")[0] if ch.isdigit())
        if digits:
            audio["trkn"] = [(int(digits), 0)]
    if lyrics and opts.write_lyrics:
        audio["\xa9lyr"] = [lyrics]
    if cover and opts.write_cover:
        fmt = MP4Cover.FORMAT_PNG if mime_of(cover) == "image/png" else MP4Cover.FORMAT_JPEG
        audio["covr"] = [MP4Cover(cover, imageformat=fmt)]
    audio.save()


# ---------------------------------------------------------------- APEv2


def _write_apev2(path: str, fields, cover, lyrics, opts) -> None:
    audio = mutagen.File(path)
    if audio is None:
        raise WriteNotSupported(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    for key, tag in APE_FIELDS.items():
        if key in fields and fields[key]:
            tags[tag] = str(fields[key])
    if lyrics and opts.write_lyrics:
        tags["Lyrics"] = lyrics
    if cover and opts.write_cover:
        filename = b"cover.png" if mime_of(cover) == "image/png" else b"cover.jpg"
        tags["Cover Art (Front)"] = APEValue(filename + b"\x00" + cover, BINARY)
    audio.save()


# ------------------------------------------------------------------ WMA


def _write_asf(path: str, fields, cover, lyrics, opts) -> None:
    audio = ASF(path)
    mapping = {
        "title": "Title",
        "artist": "Author",
        "album": "WM/AlbumTitle",
        "year": "WM/Year",
        "albumartist": "WM/AlbumArtist",
    }
    for key, tag in mapping.items():
        if key in fields and fields[key]:
            audio[tag] = fields[key]
    if "track" in fields and fields["track"]:
        digits = "".join(ch for ch in str(fields["track"]).split("/")[0] if ch.isdigit())
        if digits:
            audio["WM/TrackNumber"] = int(digits)
    if lyrics and opts.write_lyrics:
        audio["WM/Lyrics"] = lyrics
    audio.save()


HANDLERS = {
    "MP3": _write_mp3,
    "FLAC": _write_flac,
    "M4A": _write_mp4,
    "OGG": _write_ogg,
    "OPUS": _write_opus,
    "WAV": _write_wav,
    "AIFF": _write_aiff,
    "APE": _write_apev2,
    "WV": _write_apev2,
    "TTA": _write_apev2,
    "WMA": _write_asf,
    "DSF": _write_dsf,
}


def supports(path: str) -> bool:
    return format_of(path) in HANDLERS


def write(
    path: str,
    fields: dict[str, str],
    cover: bytes | None = None,
    lyrics: str | None = None,
    opts: WriteOptions | None = None,
) -> None:
    """写入标签。不支持的格式抛 WriteNotSupported。"""
    opts = opts or WriteOptions()
    fmt = format_of(path)
    if fmt in READ_ONLY_FORMATS:
        raise WriteNotSupported(f"{fmt} 为只读格式，不写入")
    handler = HANDLERS.get(fmt)
    if handler is None:
        raise WriteNotSupported(f"暂不支持写入 {fmt or '未知'} 格式")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    handler(path, fields, cover, lyrics, opts)
