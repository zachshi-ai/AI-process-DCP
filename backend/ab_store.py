import json
import os
import time
import uuid
from threading import Lock
from typing import Any, Dict, List, Optional


_LOCK = Lock()


def _now_ts() -> int:
    return int(time.time())


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class ABAuditStore:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def _load(self) -> Dict[str, Any]:
        if not os.path.exists(self.path):
            return {"version": 1, "updated_ts": _now_ts(), "records": []}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                obj = json.load(f)
            if not isinstance(obj, dict):
                return {"version": 1, "updated_ts": _now_ts(), "records": []}
            if "records" not in obj or not isinstance(obj.get("records"), list):
                obj["records"] = []
            if "version" not in obj:
                obj["version"] = 1
            if "updated_ts" not in obj:
                obj["updated_ts"] = _now_ts()
            return obj
        except Exception:
            return {"version": 1, "updated_ts": _now_ts(), "records": []}

    def _save(self, obj: Dict[str, Any]) -> None:
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def list_records(self, *, limit: int = 200, offset: int = 0) -> Dict[str, Any]:
        with _LOCK:
            obj = self._load()
            records = obj.get("records") or []
            try:
                records_sorted = sorted(records, key=lambda r: str(r.get("created_at") or ""), reverse=True)
            except Exception:
                records_sorted = records
            sliced = records_sorted[offset:offset + limit]
            return {"version": obj.get("version", 1), "updated_ts": obj.get("updated_ts"), "records": sliced, "total": len(records_sorted)}

    def get_record(self, record_id: str) -> Optional[Dict[str, Any]]:
        rid = str(record_id or "").strip()
        if not rid:
            return None
        with _LOCK:
            obj = self._load()
            for r in obj.get("records") or []:
                if str(r.get("id") or "") == rid:
                    return r
        return None

    def create_record(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with _LOCK:
            obj = self._load()
            records: List[Dict[str, Any]] = obj.get("records") or []
            rid = str(payload.get("id") or "").strip() or str(uuid.uuid4())
            rec = {
                "id": rid,
                "created_at": payload.get("created_at") or _now_iso(),
                "updated_at": _now_iso(),
                "title": str(payload.get("title") or "").strip(),
                "skill_name": str(payload.get("skill_name") or "").strip(),
                "criteria": str(payload.get("criteria") or "").strip(),
                "input": payload.get("input"),
                "custom_skills": payload.get("custom_skills") if isinstance(payload.get("custom_skills"), dict) else None,
                "drill_config": payload.get("drill_config") if isinstance(payload.get("drill_config"), dict) else None,
                "runs": {"A": None, "B": None, "J": None},
                "note": "",
                "conclusion": "",
            }
            records.append(rec)
            obj["records"] = records
            obj["updated_ts"] = _now_ts()
            self._save(obj)
            return rec

    def update_record_fields(self, record_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        rid = str(record_id or "").strip()
        if not rid:
            return None
        with _LOCK:
            obj = self._load()
            records: List[Dict[str, Any]] = obj.get("records") or []
            for idx, r in enumerate(records):
                if str(r.get("id") or "") != rid:
                    continue
                for k in ["title", "criteria", "note", "conclusion"]:
                    if k in updates:
                        r[k] = str(updates.get(k) or "")
                if "custom_skills" in updates:
                    r["custom_skills"] = updates.get("custom_skills") if isinstance(updates.get("custom_skills"), dict) else None
                if "drill_config" in updates:
                    r["drill_config"] = updates.get("drill_config") if isinstance(updates.get("drill_config"), dict) else None
                if "runs" in updates and isinstance(updates.get("runs"), dict):
                    cur_runs = r.get("runs") if isinstance(r.get("runs"), dict) else {}
                    next_runs = dict(cur_runs)
                    for rk in ["A", "B", "J"]:
                        if rk in updates["runs"]:
                            next_runs[rk] = updates["runs"][rk]
                    r["runs"] = next_runs
                r["updated_at"] = _now_iso()
                records[idx] = r
                obj["records"] = records
                obj["updated_ts"] = _now_ts()
                self._save(obj)
                return r
        return None
