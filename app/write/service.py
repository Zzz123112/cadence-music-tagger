"""批量写回编排：备份 → 写入 → 校验 → 失败回滚 → 留痕。"""
from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field

from ..core import db, fileio
from ..core.models import Track
from ..core.probe import probe
from . import verify, writer
from .writer import WriteNotSupported, WriteOptions


@dataclass
class PendingWrite:
    track: Track
    fields: dict[str, str] = field(default_factory=dict)
    cover: bytes | None = None
    lyrics: str | None = None


def _looks_permission_error(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if getattr(exc, "errno", None) == 13:
        return True
    text = str(exc)
    return (
        "Permission denied" in text
        or "拒绝访问" in text
        or "Access is denied" in text
        or "Errno 13" in text
        or "WinError 5" in text
    )


def _write_with_fallback(path: str, item: "PendingWrite", opts: WriteOptions,
                         allow_elevation: bool = True) -> str:
    """先直接写原文件；被拦住就改在临时目录写，再借系统程序拷回去。

    注意：mutagen 会把底层的 PermissionError 包装成它自己的 MutagenError，
    那个类不继承 OSError，所以这里必须捕获所有异常再判断。
    """
    try:
        writer.write(path, item.fields, item.cover, item.lyrics, opts)
        return "direct"
    except Exception as exc:
        if not _looks_permission_error(exc):
            raise
        tmp_dir = tempfile.mkdtemp(prefix="mt-write-")
        tmp = os.path.join(tmp_dir, os.path.basename(path))
        try:
            shutil.copy2(path, tmp)
            writer.write(tmp, item.fields, item.cover, item.lyrics, opts)
            ok, detail = fileio.copy_file(tmp, path, allow_elevation=allow_elevation)
            if not ok:
                raise PermissionError(f"借道写入失败：{detail}") from exc
            return "helper"
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@dataclass
class ItemOutcome:
    track_id: int
    path: str
    status: str
    error: str = ""
    backup_path: str = ""
    cover_file: str = ""


def _cover_extension(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def _write_cover_file(audio_path: str, data: bytes) -> str:
    """把封面存成和歌曲同目录的 cover.jpg / cover.png。

    先写临时文件再走 fileio.copy_file：权限不足时会尝试系统程序代写。
    """
    stem = os.path.splitext(audio_path)[0]
    target = stem + _cover_extension(data)
    handle, tmp = tempfile.mkstemp(prefix="cadence-cover-", suffix=".bin")
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(data)
        ok, error = fileio.copy_file(tmp, target, allow_elevation=True)
        if not ok:
            raise OSError(error or "复制失败")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return target


def _library_root_for(track: Track, fallback: str) -> str:
    return fallback or os.path.dirname(os.path.abspath(track.path))


def write_batch(
    conn,
    pending: list[PendingWrite],
    library_root: str,
    opts: WriteOptions | None = None,
    on_item=None,
    prune_keep: int = 0,          # 备份功能已移除，保留参数只为兼容旧调用
    batch_dir: str | None = None,
    fallback_dir: str = '',
    allow_elevation: bool = True,
) -> dict:
    """执行一次批量写入。

    返回汇总；单条失败只影响自己，其余继续。
    """
    opts = opts or WriteOptions()
    summary = {
        "batch_id": 0,
        "ok": 0,
        "failed": 0,
        "skipped": 0,
        "via_helper": 0,
        "error": "",
        "needed": 0,
        "items": [],
    }
    if not pending:
        return summary

    batch_id = db.create_batch(conn, len(pending))
    summary["batch_id"] = batch_id

    for item in pending:
        track = item.track
        path = track.path
        record = dict(item.fields)
        item_id = db.add_write_item(conn, batch_id, track.id or 0, path, record)
        outcome = ItemOutcome(track_id=track.id or 0, path=path, status="pending")

        fmt_supported = writer.supports(path)
        if not item.fields and not item.cover and not item.lyrics:
            outcome.status = "skipped"
            summary["skipped"] += 1
        elif not fmt_supported:
            outcome.status = "failed"
            outcome.error = "该格式暂不支持写入"
            summary["failed"] += 1
        else:
            before = probe(path)
            try:
                db.update_write_item(conn, item_id, "writing")

                method = _write_with_fallback(path, item, opts, allow_elevation)
                if method == "helper":
                    summary["via_helper"] = summary.get("via_helper", 0) + 1

                db.update_write_item(conn, item_id, "verifying")
                ok, reason = verify.verify(before, path)
                if ok:
                    outcome.status = "done"
                    summary["ok"] += 1
                    db.update_tags(conn, track.id or 0, item.fields)
                    if item.cover and opts.write_cover_file:
                        try:
                            target = _write_cover_file(path, item.cover)
                            summary["cover_files"] = summary.get("cover_files", 0) + 1
                            outcome.cover_file = target
                        except OSError as exc:
                            outcome.error = f"封面文件写入失败：{exc}"
                    if item.cover:
                        conn.execute(
                            "UPDATE tracks SET has_cover = 1 WHERE id = ?", (track.id,)
                        )
                    if item.lyrics:
                        conn.execute(
                            "UPDATE tracks SET has_lyrics = 1 WHERE id = ?", (track.id,)
                        )
                else:
                    outcome.status = "failed"
                    outcome.error = reason
                    summary["failed"] += 1
            except WriteNotSupported as exc:
                outcome.status = "failed"
                outcome.error = str(exc)
                summary["failed"] += 1
            except Exception as exc:  # 文件被占用、权限不足等
                outcome.status = "failed"
                outcome.error = f"{type(exc).__name__}: {exc}"
                summary["failed"] += 1

        db.update_write_item(conn, item_id, outcome.status, outcome.error)
        summary["items"].append(outcome)
        if on_item:
            on_item(outcome)

    db.finish_batch(conn, batch_id, summary["ok"], summary["failed"], summary["skipped"])
    conn.commit()
    return summary
