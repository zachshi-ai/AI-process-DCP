import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class DrillSession:
    session_id: str
    ts: int
    max_depth: int
    event_id: int
    stack: List[Dict[str, Any]]


class DrillStore:
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
                CREATE TABLE IF NOT EXISTS drill_sessions (
                    session_id TEXT PRIMARY KEY,
                    ts INTEGER NOT NULL,
                    max_depth INTEGER NOT NULL,
                    event_id INTEGER NOT NULL,
                    stack TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_drill_sessions_ts ON drill_sessions(ts)")
            conn.commit()

    def create_session(self, max_depth: int, event_id: int) -> str:
        session_id = uuid.uuid4().hex
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO drill_sessions (session_id, ts, max_depth, event_id, stack) VALUES (?, ?, ?, ?, ?)",
                (session_id, now, max_depth, event_id, json.dumps([], ensure_ascii=False)),
            )
            conn.commit()
        return session_id

    def get_session(self, session_id: str) -> Optional[DrillSession]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT session_id, ts, max_depth, event_id, stack FROM drill_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        return DrillSession(
            session_id=str(row["session_id"]),
            ts=int(row["ts"]),
            max_depth=int(row["max_depth"]),
            event_id=int(row["event_id"]),
            stack=json.loads(row["stack"]) if row["stack"] else [],
        )

    def save_stack(self, session_id: str, stack: List[Dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE drill_sessions SET stack=? WHERE session_id=?",
                (json.dumps(stack, ensure_ascii=False), session_id),
            )
            conn.commit()
