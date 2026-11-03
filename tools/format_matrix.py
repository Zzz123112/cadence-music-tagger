"""阶段 0：格式写入验证。

对每个样本做一次真实写入，证明三件事：
1. 标签写进去了
2. 音频参数没变
3. **音频数据区的字节哈希没变**（逐字节证据，不靠相信库的行为）

输出 CSV 与 Markdown 矩阵。
"""
from __future__ import annotations

import csv
import hashlib
import os
import shutil
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.probe import probe  # noqa: E402
from app.write import writer  # noqa: E402
from app.write.writer import WriteNotSupported  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "samples")
OUT = os.path.join(ROOT, "docs")

PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x01\x90\x00\x00\x01\x90\x08\x02\x00\x00\x00"
    b"\x00\x00\x00\x00"
)

TEST_FIELDS = {
    "title": "晴天",
    "artist": "周杰伦",
    "album": "叶惠美",
    "year": "2003",
    "track": "3",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def chunk_span(data: bytes, container: bytes, target: bytes,
               skip: int = 0) -> tuple[int, int] | None:
    """在 RIFF/FORM 类容器里找某个块的负载区间。"""
    if data[:4] != container:
        return None
    pos = 12
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little" if container == b"RIFF" else "big")
        start = pos + 8 + skip
        end = min(start + size - skip, len(data))
        if cid == target:
            return (start, end)
        pos += 8 + size + (size & 1)
    return None


def audio_span(path: str) -> tuple[int, int] | None:
    """返回音频数据区在文件中的 (start, end)。无法判断时返回 None。"""
    with open(path, "rb") as fh:
        data = fh.read()
    head = data[:4]

    if head == b"RIFF":
        return chunk_span(data, b"RIFF", b"data")
    if head == b"FORM":
        return chunk_span(data, b"FORM", b"SSND", skip=8)
    if head == b"fLaC":
        pos = 4
        while pos + 4 <= len(data):
            block_header = data[pos]
            is_last = block_header & 0x80
            block_type = block_header & 0x7F
            if block_type == 127:
                return None
            length = int.from_bytes(data[pos + 1:pos + 4], "big")
            pos += 4 + length
            if is_last:
                return (pos, len(data))
        return None
    if head[:3] == b"ID3":
        size_bytes = data[6:10]
        size = (
            ((size_bytes[0] & 0x7F) << 21)
            | ((size_bytes[1] & 0x7F) << 14)
            | ((size_bytes[2] & 0x7F) << 7)
            | (size_bytes[3] & 0x7F)
        )
        return (10 + size, len(data))
    if path.lower().endswith(".mp3"):
        # 没有 ID3 头的 MP3：整份都是音频
        return (0, len(data))
    if head == b"DSD ":
        pos = 0
        while pos + 12 <= len(data):
            cid = data[pos:pos + 4]
            size = int.from_bytes(data[pos + 4:pos + 12], "little")
            if cid == b"data":
                return (pos + 12, min(pos + size, len(data)))
            if size <= 0:
                break
            pos += size
        return None
    if head == b"OggS":
        return None  # 页交织，不做区间判定
    return None


def diff_ranges(before: bytes, after: bytes) -> tuple[int, int]:
    """返回 (首个不同字节, 末尾不同字节) 的偏移；完全相同返回 (-1, -1)。"""
    limit = min(len(before), len(after))
    first = -1
    for i in range(limit):
        if before[i] != after[i]:
            first = i
            break
    if first == -1 and len(before) == len(after):
        return (-1, -1)
    if first == -1:
        first = limit
    last = limit - 1
    while last >= 0 and last < len(before) and last < len(after) and before[last] == after[last]:
        last -= 1
    return (first, max(last, first))


def test_one(path: str) -> dict:
    fmt = probe(path).fmt
    row = {
        "file": os.path.basename(path),
        "format": fmt,
        "write_ok": False,
        "tags_ok": False,
        "params_ok": False,
        "audio_hash_ok": False,
        "cover_ok": False,
        "lyrics_ok": False,
        "span": "",
        "changed": "",
        "verdict": "",
        "note": "",
    }

    tmp_dir = tempfile.mkdtemp()
    target = os.path.join(tmp_dir, os.path.basename(path))
    try:
        shutil.copy2(path, target)
        before_probe = probe(target)
        with open(target, "rb") as fh:
            before_bytes = fh.read()

        span_before = audio_span(target)
        hash_before = sha(before_bytes[span_before[0]:span_before[1]]) if span_before else ""

        try:
            writer.write(target, TEST_FIELDS, cover=PNG_1PX,
                         lyrics="[00:01.00]窗外的麻雀")
            row["write_ok"] = True
        except WriteNotSupported as exc:
            row["verdict"] = "只读"
            row["note"] = str(exc)
            return row
        except Exception as exc:
            row["verdict"] = "写入失败"
            row["note"] = f"{type(exc).__name__}: {exc}"
            return row

        after_probe = probe(target)
        with open(target, "rb") as fh:
            after_bytes = fh.read()

        row["tags_ok"] = (
            after_probe.title == TEST_FIELDS["title"]
            and after_probe.artist == TEST_FIELDS["artist"]
        )
        row["params_ok"] = before_probe.audio_signature() == after_probe.audio_signature()
        row["cover_ok"] = after_probe.has_cover
        row["lyrics_ok"] = after_probe.has_lyrics

        span_after = audio_span(target)
        hash_after = sha(after_bytes[span_after[0]:span_after[1]]) if span_after else ""
        if span_before and span_after:
            row["audio_hash_ok"] = hash_before == hash_after
            row["span"] = f"{span_before[0]}-{span_before[1]}"

        first, last = diff_ranges(before_bytes, after_bytes)
        row["changed"] = f"{first}-{last}" if first >= 0 else "无"

        if not row["tags_ok"]:
            row["verdict"] = "标签未写入"
        elif not row["params_ok"]:
            row["verdict"] = "参数改变"
        elif span_before and span_after and not row["audio_hash_ok"]:
            row["verdict"] = "音频被改动"
        elif row["cover_ok"] and row["lyrics_ok"]:
            row["verdict"] = "可写（全通过）"
        else:
            row["verdict"] = "可写（部分字段）"
            missing = []
            if not row["cover_ok"]:
                missing.append("封面")
            if not row["lyrics_ok"]:
                missing.append("歌词")
            row["note"] = "未写入：" + "、".join(missing)
        return row
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> int:
    if not os.path.isdir(SAMPLES):
        print("没有 samples 目录，请先运行 tools/make_samples.py")
        return 1
    files = sorted(
        os.path.join(SAMPLES, n) for n in os.listdir(SAMPLES)
        if os.path.isfile(os.path.join(SAMPLES, n))
    )
    rows = [test_one(p) for p in files]

    os.makedirs(OUT, exist_ok=True)
    csv_path = os.path.join(OUT, "format-matrix.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    header = "| 文件 | 格式 | 标签 | 参数 | 音频哈希 | 封面 | 歌词 | 结论 |"
    sep = "|---|---|---|---|---|---|---|---|"
    lines = [header, sep]
    for r in rows:
        mark = lambda ok: "✅" if ok else "❌"  # noqa: E731
        lines.append(
            f"| {r['file']} | {r['format']} | {mark(r['tags_ok'])} | {mark(r['params_ok'])} "
            f"| {mark(r['audio_hash_ok']) if r['span'] else '—'} | {mark(r['cover_ok'])} "
            f"| {mark(r['lyrics_ok'])} | {r['verdict']}{('：' + r['note']) if r['note'] else ''} |"
        )
    md_path = os.path.join(OUT, "format-matrix.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print("\n".join(lines))
    print(f"\nCSV: {csv_path}")
    print(f"MD : {md_path}")

    failures = [r for r in rows if r["verdict"] not in ("可写（全通过）", "可写（部分字段）", "只读")]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
