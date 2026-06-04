import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class HistoryEvent:
    id: int
    ts: int
    kind: str
    title: str
    url: str
    path: Optional[str]
    status: str
    updated_ts: int
    started_ts: Optional[int]
    finished_ts: Optional[int]
    total: int
    processed: int
    success: int
    failed: int
    meta: Dict[str, Any]


@dataclass
class HistoryItem:
    id: int
    event_id: int
    ts: int
    url: str
    skill: str
    evidence: str
    result: str
    status: str
    meta: Dict[str, Any]


class HistoryStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(os.path.dirname(self.db_path)).mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS history_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    path TEXT,
                    status TEXT NOT NULL DEFAULT 'queued',
                    updated_ts INTEGER NOT NULL DEFAULT 0,
                    started_ts INTEGER,
                    finished_ts INTEGER,
                    total INTEGER NOT NULL DEFAULT 0,
                    processed INTEGER NOT NULL DEFAULT 0,
                    success INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0,
                    meta TEXT NOT NULL
                )
                """
            )
            event_cols = [r["name"] for r in conn.execute("PRAGMA table_info(history_events)").fetchall()]
            if "status" not in event_cols:
                conn.execute("ALTER TABLE history_events ADD COLUMN status TEXT NOT NULL DEFAULT 'queued'")
            if "updated_ts" not in event_cols:
                conn.execute("ALTER TABLE history_events ADD COLUMN updated_ts INTEGER NOT NULL DEFAULT 0")
            if "started_ts" not in event_cols:
                conn.execute("ALTER TABLE history_events ADD COLUMN started_ts INTEGER")
            if "finished_ts" not in event_cols:
                conn.execute("ALTER TABLE history_events ADD COLUMN finished_ts INTEGER")
            if "total" not in event_cols:
                conn.execute("ALTER TABLE history_events ADD COLUMN total INTEGER NOT NULL DEFAULT 0")
            if "processed" not in event_cols:
                conn.execute("ALTER TABLE history_events ADD COLUMN processed INTEGER NOT NULL DEFAULT 0")
            if "success" not in event_cols:
                conn.execute("ALTER TABLE history_events ADD COLUMN success INTEGER NOT NULL DEFAULT 0")
            if "failed" not in event_cols:
                conn.execute("ALTER TABLE history_events ADD COLUMN failed INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS history_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER NOT NULL,
                    ts INTEGER NOT NULL,
                    url TEXT NOT NULL,
                    skill TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    result TEXT NOT NULL,
                    status TEXT NOT NULL,
                    meta TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(event_id) REFERENCES history_events(id)
                )
                """
            )
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(history_items)").fetchall()]
            if "meta" not in cols:
                conn.execute("ALTER TABLE history_items ADD COLUMN meta TEXT NOT NULL DEFAULT '{}'")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_events_ts ON history_events(ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_events_kind ON history_events(kind)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_events_url ON history_events(url)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_events_status ON history_events(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_items_event_id ON history_items(event_id)")
            conn.commit()

    def create_event(
        self,
        kind: str,
        title: str,
        url: str,
        path: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> int:
        now = int(time.time())
        meta_json = json.dumps(meta or {}, ensure_ascii=False)
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO history_events (ts, kind, title, url, path, status, updated_ts, meta) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (now, kind, title, url, path, "queued", now, meta_json),
            )
            conn.commit()
            return int(cur.lastrowid)

    def update_event(
        self,
        event_id: int,
        *,
        title: Optional[str] = None,
        path: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
        status: Optional[str] = None,
        updated_ts: Optional[int] = None,
        started_ts: Optional[int] = None,
        finished_ts: Optional[int] = None,
        total: Optional[int] = None,
        processed: Optional[int] = None,
        success: Optional[int] = None,
        failed: Optional[int] = None,
    ) -> None:
        updates: List[Tuple[str, Any]] = []
        if title is not None:
            updates.append(("title", title))
        if path is not None:
            updates.append(("path", path))
        if meta is not None:
            updates.append(("meta", json.dumps(meta, ensure_ascii=False)))
        if status is not None:
            updates.append(("status", status))
        if updated_ts is not None:
            updates.append(("updated_ts", updated_ts))
        if started_ts is not None:
            updates.append(("started_ts", started_ts))
        if finished_ts is not None:
            updates.append(("finished_ts", finished_ts))
        if total is not None:
            updates.append(("total", total))
        if processed is not None:
            updates.append(("processed", processed))
        if success is not None:
            updates.append(("success", success))
        if failed is not None:
            updates.append(("failed", failed))
        if not updates:
            return
        set_clause = ", ".join([f"{k}=?" for k, _ in updates])
        values = [v for _, v in updates] + [event_id]
        with self._connect() as conn:
            conn.execute(f"UPDATE history_events SET {set_clause} WHERE id=?", values)
            conn.commit()

    def merge_event_meta(self, event_id: int, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        以“浅合并”的方式更新 history_events.meta，并返回更新后的 meta。

        - new_meta = {**old_meta, **patch}
        - 不会删除旧字段；如需覆盖，直接在 patch 中传新值即可
        """
        if not patch:
            ev = self.get_event(event_id)
            return (ev.meta if ev else None) if isinstance(getattr(ev, "meta", None), dict) else None
        with self._connect() as conn:
            row = conn.execute("SELECT meta FROM history_events WHERE id=?", (event_id,)).fetchone()
            if not row:
                return None
            old_meta = json.loads(row["meta"]) if row["meta"] else {}
            if not isinstance(old_meta, dict):
                old_meta = {}
            new_meta = {**old_meta, **patch}
            conn.execute(
                "UPDATE history_events SET meta=?, updated_ts=? WHERE id=?",
                (json.dumps(new_meta, ensure_ascii=False), int(time.time()), event_id),
            )
            conn.commit()
            return new_meta

    def increment_event_progress(
        self,
        event_id: int,
        *,
        processed_delta: int = 0,
        success_delta: int = 0,
        failed_delta: int = 0,
        status: Optional[str] = None,
    ) -> None:
        now = int(time.time())
        with self._connect() as conn:
            if status is None:
                conn.execute(
                    """
                    UPDATE history_events
                    SET processed = processed + ?,
                        success = success + ?,
                        failed = failed + ?,
                        updated_ts = ?
                    WHERE id = ?
                    """,
                    (processed_delta, success_delta, failed_delta, now, event_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE history_events
                    SET processed = processed + ?,
                        success = success + ?,
                        failed = failed + ?,
                        updated_ts = ?,
                        status = ?
                    WHERE id = ?
                    """,
                    (processed_delta, success_delta, failed_delta, now, status, event_id),
                )
            conn.commit()

    def add_item(
        self,
        event_id: int,
        url: str,
        skill: str,
        evidence: str,
        result: str,
        status: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> int:
        now = int(time.time())
        meta_json = json.dumps(meta or {}, ensure_ascii=False)
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO history_items (event_id, ts, url, skill, evidence, result, status, meta) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, now, url, skill, evidence, result, status, meta_json),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_events(self, *, q: Optional[str] = None, kind: Optional[str] = None, limit: int = 50,
                    offset: int = 0) -> List[HistoryEvent]:
        where: List[str] = []
        params: List[Any] = []
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if q:
            where.append("(title LIKE ? OR url LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like])
        where_clause = f"WHERE {' AND '.join(where)}" if where else ""
        params.extend([limit, offset])
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT id, ts, kind, title, url, path, status, updated_ts, started_ts, finished_ts, total, processed, success, failed, meta FROM history_events {where_clause} ORDER BY ts DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
            ids = [int(r["id"]) for r in rows]
            stats = {}
            if ids:
                placeholders = ",".join(["?"] * len(ids))
                agg = conn.execute(
                    f"""
                    SELECT event_id,
                           SUM(CASE WHEN status IN ('success','unmatched','exception','failed','cancelled') THEN 1 ELSE 0 END) AS processed,
                           SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success,
                           SUM(CASE WHEN status IN ('unmatched','exception','failed','cancelled') THEN 1 ELSE 0 END) AS failed
                    FROM history_items
                    WHERE event_id IN ({placeholders})
                    GROUP BY event_id
                    """,
                    ids,
                ).fetchall()
                for a in agg:
                    stats[int(a["event_id"])] = {
                        "processed": int(a["processed"] or 0),
                        "success": int(a["success"] or 0),
                        "failed": int(a["failed"] or 0),
                    }
            now = int(time.time())
            for r in rows:
                eid = int(r["id"])
                if str(r["kind"]) != "batch":
                    continue
                cur_status = str(r["status"] or "queued")
                if cur_status not in ["queued", "running"]:
                    continue
                meta = json.loads(r["meta"]) if r["meta"] else {}
                inferred = stats.get(eid, {"processed": 0, "success": 0, "failed": 0})
                inferred_total = int(r["total"] or 0)
                meta_count = int(meta.get("count") or 0) if isinstance(meta, dict) else 0
                inferred_total = max(inferred_total, meta_count, inferred["processed"])
                inferred_status = cur_status
                if inferred_total > 0 and inferred["processed"] >= inferred_total:
                    inferred_status = "completed" if inferred["success"] > 0 else "failed"
                elif inferred["processed"] > 0:
                    inferred_status = "running"
                if inferred_status != cur_status or int(r["total"] or 0) == 0:
                    conn.execute(
                        """
                        UPDATE history_events
                        SET status=?,
                            updated_ts=?,
                            total=?,
                            processed=?,
                            success=?,
                            failed=?
                        WHERE id=?
                        """,
                        (
                            inferred_status,
                            now,
                            inferred_total,
                            inferred["processed"],
                            inferred["success"],
                            inferred["failed"],
                            eid,
                        ),
                    )
            conn.commit()
            if ids:
                placeholders = ",".join(["?"] * len(ids))
                rows = conn.execute(
                    f"SELECT id, ts, kind, title, url, path, status, updated_ts, started_ts, finished_ts, total, processed, success, failed, meta FROM history_events WHERE id IN ({placeholders}) ORDER BY ts DESC",
                    ids,
                ).fetchall()
        events: List[HistoryEvent] = []
        for r in rows:
            events.append(
                HistoryEvent(
                    id=int(r["id"]),
                    ts=int(r["ts"]),
                    kind=str(r["kind"]),
                    title=str(r["title"]),
                    url=str(r["url"]),
                    path=str(r["path"]) if r["path"] is not None else None,
                    status=str(r["status"] or "queued"),
                    updated_ts=int(r["updated_ts"] or 0),
                    started_ts=int(r["started_ts"]) if r["started_ts"] is not None else None,
                    finished_ts=int(r["finished_ts"]) if r["finished_ts"] is not None else None,
                    total=int(r["total"] or 0),
                    processed=int(r["processed"] or 0),
                    success=int(r["success"] or 0),
                    failed=int(r["failed"] or 0),
                    meta=json.loads(r["meta"]) if r["meta"] else {},
                )
            )
        return events

    def get_event(self, event_id: int) -> Optional[HistoryEvent]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, ts, kind, title, url, path, status, updated_ts, started_ts, finished_ts, total, processed, success, failed, meta FROM history_events WHERE id=?",
                (event_id,),
            ).fetchone()
            if not row:
                return None

            meta = json.loads(row["meta"]) if row["meta"] else {}
            if str(row["kind"]) == "batch" and str(row["status"] or "queued") in ["queued", "running"]:
                agg = conn.execute(
                    """
                    SELECT SUM(CASE WHEN status IN ('success','unmatched','exception','failed','cancelled') THEN 1 ELSE 0 END) AS processed,
                           SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success,
                           SUM(CASE WHEN status IN ('unmatched','exception','failed','cancelled') THEN 1 ELSE 0 END) AS failed
                    FROM history_items
                    WHERE event_id=?
                    """,
                    (event_id,),
                ).fetchone()
                inferred_processed = int(agg["processed"] or 0) if agg else 0
                inferred_success = int(agg["success"] or 0) if agg else 0
                inferred_failed = int(agg["failed"] or 0) if agg else 0
                inferred_total = max(
                    int(row["total"] or 0),
                    int(meta.get("count") or 0) if isinstance(meta, dict) else 0,
                    inferred_processed,
                )
                inferred_status = str(row["status"] or "queued")
                if inferred_total > 0 and inferred_processed >= inferred_total:
                    inferred_status = "completed" if inferred_success > 0 else "failed"
                elif inferred_processed > 0:
                    inferred_status = "running"
                if inferred_status != str(row["status"] or "queued") or int(row["total"] or 0) == 0:
                    now = int(time.time())
                    conn.execute(
                        """
                        UPDATE history_events
                        SET status=?,
                            updated_ts=?,
                            total=?,
                            processed=?,
                            success=?,
                            failed=?
                        WHERE id=?
                        """,
                        (
                            inferred_status,
                            now,
                            inferred_total,
                            inferred_processed,
                            inferred_success,
                            inferred_failed,
                            event_id,
                        ),
                    )
                    conn.commit()
                    row = conn.execute(
                        "SELECT id, ts, kind, title, url, path, status, updated_ts, started_ts, finished_ts, total, processed, success, failed, meta FROM history_events WHERE id=?",
                        (event_id,),
                    ).fetchone()
                    meta = json.loads(row["meta"]) if row and row["meta"] else meta

            return HistoryEvent(
                id=int(row["id"]),
                ts=int(row["ts"]),
                kind=str(row["kind"]),
                title=str(row["title"]),
                url=str(row["url"]),
                path=str(row["path"]) if row["path"] is not None else None,
                status=str(row["status"] or "queued"),
                updated_ts=int(row["updated_ts"] or 0),
                started_ts=int(row["started_ts"]) if row["started_ts"] is not None else None,
                finished_ts=int(row["finished_ts"]) if row["finished_ts"] is not None else None,
                total=int(row["total"] or 0),
                processed=int(row["processed"] or 0),
                success=int(row["success"] or 0),
                failed=int(row["failed"] or 0),
                meta=meta if isinstance(meta, dict) else {},
            )

    def list_items(self, event_id: int) -> List[HistoryItem]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, event_id, ts, url, skill, evidence, result, status, meta FROM history_items WHERE event_id=? ORDER BY ts ASC",
                (event_id,),
            ).fetchall()
        items: List[HistoryItem] = []
        for r in rows:
            items.append(
                HistoryItem(
                    id=int(r["id"]),
                    event_id=int(r["event_id"]),
                    ts=int(r["ts"]),
                    url=str(r["url"]),
                    skill=str(r["skill"]),
                    evidence=str(r["evidence"]),
                    result=str(r["result"]),
                    status=str(r["status"]),
                    meta=json.loads(r["meta"]) if r["meta"] else {},
                )
            )
        return items

    def delete_event(self, event_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM history_items WHERE event_id=?", (event_id,))
            conn.execute("DELETE FROM history_events WHERE id=?", (event_id,))
            conn.commit()

    def update_item(
        self,
        item_id: int,
        *,
        skill: Optional[str] = None,
        evidence: Optional[str] = None,
        result: Optional[str] = None,
        status: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        更新指定 history_items 的字段。
        """
        updates: List[Tuple[str, Any]] = []
        if skill is not None:
            updates.append(("skill", str(skill)))
        if evidence is not None:
            updates.append(("evidence", str(evidence)))
        if result is not None:
            updates.append(("result", str(result)))
        if status is not None:
            updates.append(("status", str(status)))
        if meta is not None:
            updates.append(("meta", json.dumps(meta, ensure_ascii=False)))
        if not updates:
            return
        set_clause = ", ".join([f"{k}=?" for k, _ in updates])
        values = [v for _, v in updates] + [item_id]
        with self._connect() as conn:
            conn.execute(f"UPDATE history_items SET {set_clause} WHERE id=?", values)
            conn.commit()

    def update_item_meta(self, item_id: int, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        更新指定 history_items 的 meta（JSON 字段），并返回更新后的 meta。

        - 采用“浅合并”：new_meta = {**old_meta, **patch}
        - 不会删除旧字段；如需清空某字段，传入空字符串即可
        """
        if not patch:
            return self.get_item_meta(item_id)
        with self._connect() as conn:
            row = conn.execute("SELECT meta FROM history_items WHERE id=?", (item_id,)).fetchone()
            if not row:
                return None
            old_meta = json.loads(row["meta"]) if row["meta"] else {}
            if not isinstance(old_meta, dict):
                old_meta = {}
            new_meta = {**old_meta, **patch}
            conn.execute(
                "UPDATE history_items SET meta=? WHERE id=?",
                (json.dumps(new_meta, ensure_ascii=False), item_id),
            )
            conn.commit()
            return new_meta

    def get_item_meta(self, item_id: int) -> Optional[Dict[str, Any]]:
        """
        获取指定 history_items 的 meta（JSON 字段）。
        """
        with self._connect() as conn:
            row = conn.execute("SELECT meta FROM history_items WHERE id=?", (item_id,)).fetchone()
            if not row:
                return None
            meta = json.loads(row["meta"]) if row["meta"] else {}
            return meta if isinstance(meta, dict) else {}
