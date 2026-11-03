"""SQLite 存储层。只做数据存取，不含业务判断。"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Iterable, Optional

from .models import Candidate, Draft, Track

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS tracks (
  id            INTEGER PRIMARY KEY,
  path          TEXT UNIQUE NOT NULL,
  name          TEXT,
  size          INTEGER,
  mtime         INTEGER,
  fmt           TEXT,
  duration      REAL,
  bitrate       INTEGER,
  bit_depth     INTEGER,
  sample_rate   INTEGER,
  channels      INTEGER,
  tag_title     TEXT,
  tag_artist    TEXT,
  tag_album     TEXT,
  tag_year      TEXT,
  tag_track     TEXT,
  tag_albumartist TEXT,
  has_cover     INTEGER DEFAULT 0,
  has_lyrics    INTEGER DEFAULT 0,
  status        TEXT,
  parsed_artist TEXT,
  parsed_title  TEXT,
  parsed_version TEXT,
  parsed_conf   REAL,
  last_scan     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tracks_status ON tracks(status);
CREATE INDEX IF NOT EXISTS idx_tracks_mtime  ON tracks(mtime);

CREATE TABLE IF NOT EXISTS candidates (
  id           INTEGER PRIMARY KEY,
  track_id     INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  source       TEXT NOT NULL,
  payload      TEXT NOT NULL,
  evidence     TEXT,
  score        REAL,
  fetched_at   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cand_track ON candidates(track_id);

CREATE TABLE IF NOT EXISTS drafts (
  track_id   INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
  data       TEXT NOT NULL,
  updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS corrections (
  id        INTEGER PRIMARY KEY,
  keyword   TEXT UNIQUE NOT NULL,
  payload   TEXT NOT NULL,
  hits      INTEGER DEFAULT 0,
  updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS write_batches (
  id          INTEGER PRIMARY KEY,
  started_at  INTEGER,
  finished_at INTEGER,
  total       INTEGER DEFAULT 0,
  ok          INTEGER DEFAULT 0,
  failed      INTEGER DEFAULT 0,
  skipped     INTEGER DEFAULT 0,
  state       TEXT
);

CREATE TABLE IF NOT EXISTS write_items (
  id          INTEGER PRIMARY KEY,
  batch_id    INTEGER REFERENCES write_batches(id),
  track_id    INTEGER,
  path        TEXT,
  fields      TEXT,
  status      TEXT,
  error       TEXT,
  backup_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_batch ON write_items(batch_id);

CREATE TABLE IF NOT EXISTS app_meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);
"""

TRACK_COLUMNS = (
    "path name size mtime fmt duration bitrate bit_depth sample_rate channels "
    "tag_title tag_artist tag_album tag_year tag_track tag_albumartist "
    "has_cover has_lyrics status parsed_artist parsed_title parsed_version "
    "parsed_conf last_scan"
).split()


def connect(path: str) -> sqlite3.Connection:
    """打开（并按需创建）数据库。目录不存在时自动建立。"""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _row_to_track(row: sqlite3.Row) -> Track:
    data = dict(row)
    data["has_cover"] = bool(data.get("has_cover"))
    data["has_lyrics"] = bool(data.get("has_lyrics"))
    known = set(Track.__dataclass_fields__)
    return Track(**{k: v for k, v in data.items() if k in known})


# ---------------------------------------------------------------- tracks


def upsert_track(conn: sqlite3.Connection, track: Track) -> int:
    row = track.to_row()
    placeholders = ", ".join(f":{c}" for c in TRACK_COLUMNS)
    updates = ", ".join(f"{c}=excluded.{c}" for c in TRACK_COLUMNS if c != "path")
    sql = (
        f"INSERT INTO tracks ({', '.join(TRACK_COLUMNS)}) VALUES ({placeholders}) "
        f"ON CONFLICT(path) DO UPDATE SET {updates}"
    )
    conn.execute(sql, row)
    cur = conn.execute("SELECT id FROM tracks WHERE path = ?", (track.path,))
    track.id = cur.fetchone()[0]
    return track.id


def get_track(conn: sqlite3.Connection, track_id: int) -> Optional[Track]:
    cur = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,))
    row = cur.fetchone()
    return _row_to_track(row) if row else None


def get_track_by_path(conn: sqlite3.Connection, path: str) -> Optional[Track]:
    cur = conn.execute("SELECT * FROM tracks WHERE path = ?", (os.path.abspath(path),))
    row = cur.fetchone()
    return _row_to_track(row) if row else None


def list_tracks(
    conn: sqlite3.Connection,
    statuses: Optional[Iterable[str]] = None,
    fmt: Optional[str] = None,
    keyword: str = "",
    include_ignored: bool = False,
    order: str = "path",
) -> list[Track]:
    where, params = [], []
    if statuses:
        statuses = list(statuses)
        where.append(f"status IN ({','.join('?' * len(statuses))})")
        params.extend(statuses)
    elif not include_ignored:
        where.append("status != 'ignored'")
    if fmt:
        where.append("fmt = ?")
        params.append(fmt)
    if keyword:
        like = f"%{keyword}%"
        where.append("(name LIKE ? OR tag_title LIKE ? OR tag_artist LIKE ? OR tag_album LIKE ? OR parsed_title LIKE ?)")
        params.extend([like] * 5)
    sql = "SELECT * FROM tracks"
    if where:
        sql += " WHERE " + " AND ".join(where)
    allowed_orders = {"path", "name", "fmt", "duration", "bit_depth", "sample_rate",
                      "tag_artist", "tag_album", "status", "size", "mtime"}
    sql += f" ORDER BY {order if order in allowed_orders else 'path'}"
    return [_row_to_track(r) for r in conn.execute(sql, params)]


def count_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {"all": 0}
    for row in conn.execute("SELECT status, COUNT(*) AS n FROM tracks GROUP BY status"):
        counts[row["status"]] = row["n"]
        counts["all"] += row["n"]
    return counts


def folder_tracks(conn: sqlite3.Connection, folder: str) -> list[Track]:
    folder = os.path.abspath(folder)
    rows = conn.execute("SELECT * FROM tracks WHERE path LIKE ?", (folder + os.sep + "%",))
    return [_row_to_track(r) for r in rows]


def known_paths(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT path FROM tracks")}


def delete_tracks(conn: sqlite3.Connection, paths: Iterable[str]) -> int:
    n = 0
    for p in paths:
        n += conn.execute("DELETE FROM tracks WHERE path = ?", (p,)).rowcount
    return n


def set_status(conn: sqlite3.Connection, track_id: int, status: str) -> None:
    conn.execute("UPDATE tracks SET status = ? WHERE id = ?", (status, track_id))


def update_tags(conn: sqlite3.Connection, track_id: int, fields: dict[str, str]) -> None:
    """写入成功之后同步数据库里的标签值。"""
    mapping = {
        "title": "tag_title",
        "artist": "tag_artist",
        "album": "tag_album",
        "year": "tag_year",
        "track": "tag_track",
        "albumartist": "tag_albumartist",
    }
    sets, params = [], []
    for key, value in fields.items():
        col = mapping.get(key)
        if col:
            sets.append(f"{col} = ?")
            params.append(value)
    if not sets:
        return
    params.append(track_id)
    conn.execute(f"UPDATE tracks SET {', '.join(sets)} WHERE id = ?", params)


# ------------------------------------------------------------ candidates


def replace_candidates(conn: sqlite3.Connection, track_id: int, cands: list[Candidate]) -> None:
    conn.execute("DELETE FROM candidates WHERE track_id = ?", (track_id,))
    now = int(time.time())
    for c in cands:
        conn.execute(
            "INSERT INTO candidates (track_id, source, payload, evidence, score, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                track_id,
                c.source,
                json.dumps(c.to_json(), ensure_ascii=False),
                json.dumps(c.evidence, ensure_ascii=False),
                c.score,
                now,
            ),
        )


def load_candidates(conn: sqlite3.Connection, track_id: int, max_age_sec: int = 7 * 86400) -> list[Candidate]:
    cutoff = int(time.time()) - max_age_sec
    rows = conn.execute(
        "SELECT payload FROM candidates WHERE track_id = ? AND fetched_at >= ? ORDER BY score DESC",
        (track_id, cutoff),
    )
    out = []
    for r in rows:
        c = Candidate.from_json(json.loads(r["payload"]))
        out.append(c)
    return out


def has_candidates(conn: sqlite3.Connection, track_id: int) -> bool:
    row = conn.execute("SELECT 1 FROM candidates WHERE track_id = ? LIMIT 1", (track_id,)).fetchone()
    return row is not None


# ----------------------------------------------------------------- drafts


def save_draft(conn: sqlite3.Connection, draft: Draft) -> None:
    if draft.is_empty():
        conn.execute("DELETE FROM drafts WHERE track_id = ?", (draft.track_id,))
        return
    conn.execute(
        "INSERT INTO drafts (track_id, data, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(track_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
        (draft.track_id, json.dumps(draft.fields, ensure_ascii=False), int(time.time())),
    )


def load_draft(conn: sqlite3.Connection, track_id: int) -> Draft:
    row = conn.execute("SELECT data FROM drafts WHERE track_id = ?", (track_id,)).fetchone()
    if not row:
        return Draft(track_id=track_id)
    return Draft(track_id=track_id, fields=json.loads(row["data"]))


# ------------------------------------------------------------ corrections


def remember_correction(conn: sqlite3.Connection, keyword: str, cand: Candidate) -> None:
    key = keyword.strip().lower()
    if not key:
        return
    conn.execute(
        "INSERT INTO corrections (keyword, payload, hits, updated_at) VALUES (?, ?, 1, ?) "
        "ON CONFLICT(keyword) DO UPDATE SET payload=excluded.payload, "
        "hits=hits+1, updated_at=excluded.updated_at",
        (key, json.dumps(cand.to_json(), ensure_ascii=False), int(time.time())),
    )


def lookup_correction(conn: sqlite3.Connection, keyword: str) -> Optional[Candidate]:
    row = conn.execute(
        "SELECT payload FROM corrections WHERE keyword = ?", (keyword.strip().lower(),)
    ).fetchone()
    return Candidate.from_json(json.loads(row["payload"])) if row else None


def list_corrections(conn: sqlite3.Connection) -> list[tuple[str, Candidate, int]]:
    out = []
    for r in conn.execute("SELECT keyword, payload, hits FROM corrections ORDER BY hits DESC"):
        out.append((r["keyword"], Candidate.from_json(json.loads(r["payload"])), r["hits"]))
    return out


# -------------------------------------------------------------- meta


def get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


# ---------------------------------------------------------- write batches


def create_batch(conn: sqlite3.Connection, total: int) -> int:
    cur = conn.execute(
        "INSERT INTO write_batches (started_at, total, state) VALUES (?, ?, 'running')",
        (int(time.time()), total),
    )
    conn.commit()
    return int(cur.lastrowid)


def add_write_item(
    conn: sqlite3.Connection,
    batch_id: int,
    track_id: int,
    path: str,
    fields: dict,
    status: str = "pending",
) -> int:
    cur = conn.execute(
        "INSERT INTO write_items (batch_id, track_id, path, fields, status) "
        "VALUES (?, ?, ?, ?, ?)",
        (batch_id, track_id, path, json.dumps(fields, ensure_ascii=False), status),
    )
    return int(cur.lastrowid)


def update_write_item(
    conn: sqlite3.Connection,
    item_id: int,
    status: str,
    error: str = "",
    backup_path: str = "",
) -> None:
    conn.execute(
        "UPDATE write_items SET status = ?, error = ?, backup_path = ? WHERE id = ?",
        (status, error, backup_path, item_id),
    )


def finish_batch(conn: sqlite3.Connection, batch_id: int, ok: int, failed: int,
                 skipped: int, state: str = "done") -> None:
    conn.execute(
        "UPDATE write_batches SET finished_at = ?, ok = ?, failed = ?, skipped = ?, state = ? "
        "WHERE id = ?",
        (int(time.time()), ok, failed, skipped, state, batch_id),
    )
    conn.commit()


def list_batches(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM write_batches ORDER BY started_at DESC LIMIT ?", (limit,)
    )
    return [dict(r) for r in rows]


def batch_items(conn: sqlite3.Connection, batch_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM write_items WHERE batch_id = ?", (batch_id,))
    out = []
    for r in rows:
        item = dict(r)
        try:
            item["fields"] = json.loads(item.get("fields") or "{}")
        except (TypeError, ValueError):
            item["fields"] = {}
        out.append(item)
    return out


def unfinished_batches(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM write_batches WHERE state = 'running'")
    return [dict(r) for r in rows]
