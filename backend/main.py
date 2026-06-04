from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict
from crawler import WebCrawler
from config_manager import ConfigManager
from secret_store import SecretStore
from llm_processor_v2 import LLMProcessor
from plugin_manager import PluginManager
from batch_processor import BatchProcessor
from logger_setup import global_logger as logger
import os
import json
import io
import time
import asyncio
import shutil
from cryptography.fernet import Fernet
from fastapi.middleware.cors import CORSMiddleware
from urllib.parse import urlparse
from history_store import HistoryStore
from drill_store import DrillStore
from path_opener import open_in_file_manager, PathOpenError
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import hashlib
from ab_store import ABAuditStore

app = FastAPI(title="AI-DCP Backend")

# 允许跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 基础目录及配置管理实例
BASE_DIR = os.environ.get("AI_DCP_BASE_DIR") or os.path.dirname(__file__)
USER_DATA_DIR = os.environ.get("AI_DCP_USER_DATA_DIR") or os.path.join(BASE_DIR, "data")

CONFIG_DIR = os.path.join(BASE_DIR, "config")
PLUGIN_DIR = os.path.join(BASE_DIR, "plugins")
DATA_DIR = os.path.join(USER_DATA_DIR, "data")
OUTPUT_DIR = os.path.join(USER_DATA_DIR, "output")
LOG_DIR = os.path.join(USER_DATA_DIR, "logs")

os.makedirs(os.path.join(CONFIG_DIR, "skill"), exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

_BG_WORKERS = int(os.environ.get("AI_DCP_BG_WORKERS") or "2")
_BG_EXECUTOR = ThreadPoolExecutor(max_workers=max(1, _BG_WORKERS))

config_manager = ConfigManager(CONFIG_DIR)
secret_store = SecretStore(
    key_path=os.path.join(USER_DATA_DIR, "secret.key"),
    store_path=os.path.join(USER_DATA_DIR, "llm_config.enc"),
)


def _maybe_migrate_legacy_llm_config():
    legacy_key_path = os.path.join(BASE_DIR, "secret.key")
    legacy_store_path = os.path.join(BASE_DIR, "llm_config.enc")
    target_key_path = secret_store.key_path
    target_store_path = secret_store.store_path

    if not os.path.exists(legacy_store_path) or not os.path.exists(legacy_key_path):
        return

    loaded = secret_store.load_config()
    if loaded:
        return

    try:
        with open(legacy_store_path, "rb") as f:
            legacy_store_bytes = f.read()
    except Exception:
        return

    try:
        secret_store.cipher.decrypt(legacy_store_bytes)
        os.makedirs(os.path.dirname(target_store_path), exist_ok=True)
        if os.path.exists(target_store_path):
            os.replace(target_store_path, f"{target_store_path}.bak.{int(time.time())}")
        with open(target_store_path, "wb") as f:
            f.write(legacy_store_bytes)
        return
    except Exception:
        pass

    try:
        with open(legacy_key_path, "rb") as f:
            legacy_key_bytes = f.read()
        legacy_cipher = Fernet(legacy_key_bytes)
        legacy_cipher.decrypt(legacy_store_bytes)

        os.makedirs(os.path.dirname(target_key_path), exist_ok=True)
        if os.path.exists(target_key_path):
            os.replace(target_key_path, f"{target_key_path}.bak.{int(time.time())}")
        with open(target_key_path, "wb") as f:
            f.write(legacy_key_bytes)

        secret_store.key = legacy_key_bytes
        secret_store.cipher = legacy_cipher

        os.makedirs(os.path.dirname(target_store_path), exist_ok=True)
        if os.path.exists(target_store_path):
            os.replace(target_store_path, f"{target_store_path}.bak.{int(time.time())}")
        with open(target_store_path, "wb") as f:
            f.write(legacy_store_bytes)
    except Exception:
        return


_maybe_migrate_legacy_llm_config()


def _count_rows(db_path: str, table: str) -> Optional[int]:
    if not os.path.exists(db_path):
        return None
    try:
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        cur.execute(f"select count(*) from {table}")
        n = cur.fetchone()[0]
        con.close()
        return int(n)
    except Exception:
        return None


def _maybe_migrate_legacy_history_and_drill_db():
    legacy_history = os.path.join(USER_DATA_DIR, "history.db")
    legacy_drill = os.path.join(USER_DATA_DIR, "drill.db")
    target_history = os.path.join(DATA_DIR, "history.db")
    target_drill = os.path.join(DATA_DIR, "drill.db")

    def copy_if_needed(legacy_path: str, target_path: str, table: str):
        if not os.path.exists(legacy_path):
            return

        legacy_count = _count_rows(legacy_path, table)
        if legacy_count is None or legacy_count <= 0:
            return

        target_count = _count_rows(target_path, table)
        if target_count is not None and target_count >= legacy_count:
            return

        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        if os.path.exists(target_path):
            os.replace(target_path, f"{target_path}.bak.{int(time.time())}")
        os.replace(legacy_path, target_path)

    copy_if_needed(legacy_history, target_history, "history_events")
    copy_if_needed(legacy_drill, target_drill, "drill_sessions")


_maybe_migrate_legacy_history_and_drill_db()
plugin_manager = PluginManager(PLUGIN_DIR)

history_store = HistoryStore(os.path.join(DATA_DIR, "history.db"))
drill_store = DrillStore(os.path.join(DATA_DIR, "drill.db"))
ab_audit_store = ABAuditStore(os.path.join(DATA_DIR, "ab_audit.json"))


def _run_coro_in_new_loop(coro_fn, *args, **kwargs):
    import asyncio
    asyncio.run(coro_fn(*args, **kwargs))


def _submit_background(coro_fn, *args, **kwargs) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        loop.run_in_executor(_BG_EXECUTOR, _run_coro_in_new_loop, coro_fn, *args, **kwargs)
        return

    _BG_EXECUTOR.submit(_run_coro_in_new_loop, coro_fn, *args, **kwargs)

# 初始化一些示例配置
agent_md_path = os.path.join(CONFIG_DIR, "Agent.md")
if not os.path.exists(agent_md_path):
    with open(agent_md_path, "w", encoding="utf-8") as f:
        f.write("# Agent Role\n你是一个强大的 AI 知识提取与分析助手。请根据抓取的内容进行总结分析。")


class LLMConfig(BaseModel):
    api_token: str
    base_url: str
    model: str
    timeout: int = 60
    retry: int = 3


class CrawlRequest(BaseModel):
    url: str
    max_depth: int = 3
    headless: bool = True
    skill_name: Optional[str] = "summary"
    custom_skill_content: Optional[str] = None


class BatchRequest(BaseModel):
    urls: List[str]
    custom_skills: Optional[Dict[str, str]] = None
    drill_config: Optional[Dict[str, object]] = None
    skill_name: Optional[str] = None


class JsonInputItem(BaseModel):
    url: Optional[str] = None
    payload: object
    meta: Optional[Dict[str, object]] = None


class BatchIngestRequest(BaseModel):
    urls: Optional[List[str]] = None
    json_inputs: Optional[List[JsonInputItem]] = None
    custom_skills: Optional[Dict[str, str]] = None
    drill_config: Optional[Dict[str, object]] = None
    skill_name: Optional[str] = None
    title: Optional[str] = None
    llm_profile: Optional[str] = None
    json_concurrency: Optional[int] = None


class ABCreateRequest(BaseModel):
    title: Optional[str] = None
    urls: Optional[List[str]] = None
    json_inputs: Optional[List[JsonInputItem]] = None
    custom_skills: Optional[Dict[str, str]] = None
    drill_config: Optional[Dict[str, object]] = None
    skill_name: Optional[str] = None
    criteria: Optional[str] = None


class ABUpdateRequest(BaseModel):
    title: Optional[str] = None
    criteria: Optional[str] = None
    note: Optional[str] = None
    conclusion: Optional[str] = None


class ABRunRequest(BaseModel):
    which: str


class HistoryOpenRequest(BaseModel):
    event_id: int


class MBNotesUpdateRequest(BaseModel):
    problem_core: Optional[str] = None


class RetryFailedRequest(BaseModel):
    only_failed: bool = True
    adjustment_measures: Optional[str] = None
    actual_result: Optional[str] = None


class MBExtraField(BaseModel):
    source: str
    path: str


class MBTableCollectRequest(BaseModel):
    event_ids: List[int]
    extra_fields: Optional[List[MBExtraField]] = None
    visible_keys: Optional[List[str]] = None


class MBTableFieldsRequest(BaseModel):
    event_ids: List[int]


class DrillStartRequest(BaseModel):
    url: str
    max_depth: int = 5


class DrillNavigateRequest(BaseModel):
    session_id: str
    url: str


class DrillBackRequest(BaseModel):
    session_id: str


class AuthStartRequest(BaseModel):
    force: bool = False


def _looks_like_login_page(title: str, content: str, final_url: str) -> bool:
    t = (title or "").lower()
    u = (final_url or "").lower()
    c = (content or "").lower()
    if any(x in u for x in ["login", "sso", "cas", "passport"]):
        return True
    if any(x in t for x in ["登录", "login", "sign in", "统一身份", "单点"]):
        return True
    if any(x in c for x in ["用户名", "密码", "验证码", "扫码登录", "单点登录", "统一身份认证", "login"]):
        return True
    return False


def _parse_json_from_text(text: str):
    """
    从任意 LLM 文本中尽力解析出 JSON。
    适配常见情况：
    1) 直接返回 JSON；
    2) Markdown 代码块 ``` ... ```（或 ```json ... ```）内包含 JSON；
    3) 文本前后有解释，但中间夹着一段 JSON。
    返回 (parsed_obj, extracted_json_text)；若无法解析则返回 (None, "")。
    """
    import json as _json
    import re as _re

    s = str(text or "").strip()
    if not s:
        return None, ""

    def _try_load(candidate: str):
        c = str(candidate or "").strip()
        if not c:
            return None, ""
        try:
            return _json.loads(c), c
        except Exception:
            return None, ""

    obj, used = _try_load(s)
    if obj is not None:
        return obj, used

    for m in _re.finditer(r"```(?:json|JSON)?\s*([\s\S]*?)```", s, flags=_re.MULTILINE):
        obj, used = _try_load(m.group(1))
        if obj is not None:
            return obj, used

    first = s.find("{")
    last = s.rfind("}")
    if first != -1 and last != -1 and last > first:
        obj, used = _try_load(s[first:last + 1])
        if obj is not None:
            return obj, used

    first = s.find("[")
    last = s.rfind("]")
    if first != -1 and last != -1 and last > first:
        obj, used = _try_load(s[first:last + 1])
        if obj is not None:
            return obj, used

    return None, ""


@app.get("/")
def read_root():
    return {"status": "ok", "message": "AI-DCP Backend is running!"}


@app.post("/api/llm/config")
def save_llm_config(config: LLMConfig):
    """
    保存 LLM 连接配置信息到本地加密存储
    """
    incoming = dict(config.dict())
    secret_store.save_llm_profile("default", incoming)
    return {"status": "success", "message": "配置保存成功"}


@app.get("/api/llm/config")
def get_llm_config():
    """
    获取当前解密后的 LLM 连接配置信息（不包含真实 token）
    """
    return secret_store.get_llm_profile("default", mask_token=True)


@app.get("/api/llm/profiles")
def list_llm_profiles():
    names = secret_store.list_llm_profile_names()
    profiles = {n: secret_store.get_llm_profile(n, mask_token=True) for n in names}
    return {"names": names, "profiles": profiles}


@app.get("/api/llm/profiles/{profile}")
def get_llm_profile(profile: str):
    cfg = secret_store.get_llm_profile(profile, mask_token=True)
    return {"profile": profile, "config": cfg}


@app.post("/api/llm/profiles/{profile}")
def save_llm_profile(profile: str, config: LLMConfig):
    incoming = dict(config.dict())
    secret_store.save_llm_profile(profile, incoming)
    return {"status": "success", "message": "配置保存成功", "profile": profile}


@app.get("/api/llm/diagnose")
def diagnose_llm(profile: Optional[str] = None):
    p = str(profile or "").strip() or "default"
    llm_config = secret_store.get_llm_profile(p, mask_token=False) or {}
    llm_processor = LLMProcessor(
        api_token=llm_config.get("api_token", ""),
        base_url=llm_config.get("base_url", ""),
        model=llm_config.get("model", ""),
        timeout=llm_config.get("timeout", 60),
        retry=llm_config.get("retry", 3),
    )

    raw_token = llm_config.get("api_token", "") or ""
    normalized_token = llm_processor._normalize_api_token(raw_token)
    token_len = len(normalized_token)
    token_fp = ""
    if normalized_token:
        token_fp = hashlib.sha256(normalized_token.encode("utf-8")).hexdigest()[:12]

    final_url = ""
    try:
        final_url = llm_processor._build_candidate_urls()[0] if llm_processor._build_candidate_urls() else ""
    except Exception:
        final_url = ""

    try:
        text = llm_processor.generate("ping")
        return {
            "status": "success",
            "profile": p,
            "final_url": final_url,
            "base_url": llm_config.get("base_url", ""),
            "model": llm_config.get("model", ""),
            "token_fingerprint": token_fp,
            "token_len": token_len,
            "result_preview": (text or "")[:200],
        }
    except Exception as e:
        return {
            "status": "error",
            "profile": p,
            "final_url": final_url,
            "base_url": llm_config.get("base_url", ""),
            "model": llm_config.get("model", ""),
            "token_fingerprint": token_fp,
            "token_len": token_len,
            "error": str(e),
        }


def _build_llm_processor_by_profile(profile: Optional[str]) -> LLMProcessor:
    p = str(profile or "").strip() or "default"
    llm_config = secret_store.get_llm_profile(p, mask_token=False) or {}
    if not llm_config:
        raise HTTPException(status_code=400, detail=f"LLM 配置未初始化（profile={p}）")
    return LLMProcessor(
        api_token=llm_config.get("api_token", ""),
        base_url=llm_config.get("base_url", ""),
        model=llm_config.get("model", ""),
        timeout=llm_config.get("timeout", 60),
        retry=llm_config.get("retry", 3),
    )


def _submit_batch_job(
    *,
    urls: List[str],
    inline_payloads: Dict[str, Dict[str, object]],
    custom_skills: Optional[Dict[str, str]],
    drill_config: Optional[Dict[str, object]],
    skill_name: Optional[str],
    title: str,
    llm_profile: Optional[str],
    json_concurrency: Optional[int] = None,
) -> int:
    llm_processor = _build_llm_processor_by_profile(llm_profile)

    total_count = len(urls)
    json_count = len(inline_payloads or {})
    url_count = total_count - json_count

    os.makedirs(os.path.join(OUTPUT_DIR, "job_inputs"), exist_ok=True)

    event_id = history_store.create_event(
        kind="batch",
        title=(title or "").strip() or "批量审批",
        url=urls[0] if urls else "",
        path=None,
        meta={
            "count": total_count,
            "sources": {"urls": url_count, "json": json_count},
            "llm_profile": (llm_profile or "default"),
            "skill_name": (skill_name or "").strip() if skill_name is not None else None,
            "drill_config": drill_config,
            "json_concurrency": json_concurrency,
        },
    )
    history_store.update_event(
        event_id,
        status="queued",
        total=total_count,
        processed=0,
        success=0,
        failed=0,
        updated_ts=int(time.time()),
    )

    processor = BatchProcessor(
        config_manager,
        llm_processor,
        output_dir=OUTPUT_DIR,
        history_store=history_store,
        event_id=event_id,
        drill_config=drill_config,
        skill_name=skill_name,
        json_concurrency=json_concurrency,
    )

    input_snapshot_path = os.path.join(OUTPUT_DIR, "job_inputs", f"event_{event_id}_inputs.json")
    try:
        with open(input_snapshot_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "event_id": event_id,
                    "title": (title or "").strip(),
                    "llm_profile": (llm_profile or "default"),
                    "skill_name": (skill_name or "").strip() if skill_name is not None else None,
                    "drill_config": drill_config,
                    "json_concurrency": json_concurrency,
                    "urls": urls,
                    "inline_payloads": inline_payloads or {},
                    "custom_skills": custom_skills or {},
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        history_store.merge_event_meta(event_id, {"input_snapshot_path": input_snapshot_path})
    except Exception:
        pass

    _submit_background(processor.process_batch, urls, custom_skills, inline_payloads)
    return event_id


@app.get("/api/skills")
def get_skills():
    """
    获取所有可用的技能模板列表
    """
    skills = config_manager.list_skills()
    return {"skills": skills}


@app.post("/api/auth/start")
async def start_auth(req: Optional[AuthStartRequest] = None):
    """
    首次运行，启动可见浏览器让用户登录
    """
    force = bool(req and req.force)
    if secret_store.get_auth_status() and not force:
        return {"status": "success", "message": "已经授权过，无需再次授权"}

    try:
        if force:
            secret_store.set_auth_status(False)
            profile_dir = os.path.join(USER_DATA_DIR, "browser_profile")
            try:
                shutil.rmtree(profile_dir)
            except Exception:
                pass
        # 打开一个无意义但能调起浏览器的起始页
        crawler = WebCrawler(start_url="https://oa.ksyun.com", max_depth=1, headless=False)
        await crawler.run()
        verifier = WebCrawler(start_url="https://oa.ksyun.com", max_depth=1, headless=True)
        last_err: Optional[Exception] = None
        for _ in range(3):
            try:
                data = await verifier.get_page_snapshot("https://oa.ksyun.com")
                if _looks_like_login_page(data.get("title", ""), data.get("content", ""), data.get("url", "")):
                    secret_store.set_auth_status(False)
                    raise HTTPException(status_code=400, detail="检测到仍未登录。请在弹出的窗口完成登录后，再关闭窗口。")
                secret_store.set_auth_status(True)
                return {"status": "success", "message": "授权已保存，可直接访问需要登录的页面"}
            except HTTPException:
                raise
            except Exception as e:
                last_err = e
                await asyncio.sleep(0.6)

        secret_store.set_auth_status(True)
        msg = "授权窗口已关闭，凭证已保存；暂时无法自动校验登录态，请直接发起一次批量任务验证。"
        if last_err is not None:
            msg = f"{msg}（校验错误：{last_err}）"
        return {"status": "success", "message": msg}
    except Exception as e:
        logger.error(f"授权过程出错: {e}")
        if isinstance(e, TimeoutError):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/auth/status")
def get_auth_status():
    return {
        "authorized": bool(secret_store.get_auth_status()),
        "user_data_dir": USER_DATA_DIR,
        "profile_dir": os.path.join(USER_DATA_DIR, "browser_profile"),
        "store_path": secret_store.store_path,
    }


@app.get("/api/auth/verify")
async def verify_auth():
    if not secret_store.get_auth_status():
        return {"authorized": False, "status": "not_authorized"}
    try:
        verifier = WebCrawler(start_url="https://oa.ksyun.com", max_depth=1, headless=True)
        data = await verifier.get_page_snapshot("https://oa.ksyun.com", lock_timeout_s=2)
        if _looks_like_login_page(data.get("title", ""), data.get("content", ""), data.get("url", "")):
            secret_store.set_auth_status(False)
            return {"authorized": False, "status": "expired"}
        return {"authorized": True, "status": "ok"}
    except TimeoutError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        return {"authorized": bool(secret_store.get_auth_status()), "status": "unknown", "detail": str(e)}

class HistoryImportRequest(BaseModel):
    history_db_path: Optional[str] = None
    drill_db_path: Optional[str] = None


@app.post("/api/history/import-path")
def import_history_from_path(req: HistoryImportRequest):
    os.makedirs(DATA_DIR, exist_ok=True)
    result: Dict[str, object] = {"imported": [], "skipped": []}

    def _backup_if_exists(dst: str) -> Optional[str]:
        if not os.path.exists(dst):
            return None
        ts = str(int(time.time()))
        bak = f"{dst}.bak.{ts}"
        shutil.copy2(dst, bak)
        return bak

    def _count_table(db_path: str, table: str) -> int:
        con = sqlite3.connect(db_path)
        try:
            cur = con.cursor()
            cur.execute(f"select count(*) from {table}")
            row = cur.fetchone()
            return int(row[0] if row else 0)
        finally:
            try:
                con.close()
            except Exception:
                pass

    items = [
        ("history", (req.history_db_path or "").strip(), os.path.join(DATA_DIR, "history.db"), "history_events"),
        ("drill", (req.drill_db_path or "").strip(), os.path.join(DATA_DIR, "drill.db"), "drill_sessions"),
    ]
    for kind, src, dst_path, table in items:
        if not src:
            result["skipped"].append({"kind": kind, "reason": "no_path"})
            continue
        try:
            if not os.path.exists(src):
                raise FileNotFoundError(f"not_found:{src}")
            if not os.path.isfile(src):
                raise ValueError(f"not_a_file:{src}")
            count = _count_table(src, table)
            bak = _backup_if_exists(dst_path)
            shutil.copy2(src, dst_path)
            result["imported"].append({"kind": kind, "dst": dst_path, "backup": bak, "count": count})
        except Exception as e:
            result["skipped"].append({"kind": kind, "reason": str(e)})

    return result


@app.post("/api/task/batch")
async def process_batch_tasks(req: BatchRequest, background_tasks: BackgroundTasks):
    """
    批量执行任务 API，检查是否授权，启动后台批处理任务
    """
    if any(urlparse(u).netloc.endswith("oa.ksyun.com") for u in (req.urls or []) if u):
        if not secret_store.get_auth_status():
            raise HTTPException(status_code=401, detail="首次运行请先点击右上角「手动授权访问」完成登录授权")
        try:
            verifier = WebCrawler(start_url="https://oa.ksyun.com", max_depth=1, headless=True)
            data = await verifier.get_page_snapshot("https://oa.ksyun.com", lock_timeout_s=2)
            if _looks_like_login_page(data.get("title", ""), data.get("content", ""), data.get("url", "")):
                secret_store.set_auth_status(False)
                raise HTTPException(status_code=401, detail="授权已失效，请点击右上角「手动授权访问」重新登录")
        except HTTPException:
            raise
        except TimeoutError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except Exception:
            pass

    llm_config = secret_store.load_config()
    if not llm_config:
        raise HTTPException(status_code=400, detail="LLM 配置未初始化，请先在设置中配置 API")

    llm_processor = LLMProcessor(
        api_token=llm_config.get("api_token", ""),
        base_url=llm_config.get("base_url", ""),
        model=llm_config.get("model", ""),
        timeout=llm_config.get("timeout", 60),
        retry=llm_config.get("retry", 3)
    )

    event_id = history_store.create_event(
        kind="batch",
        title="批量任务",
        url=req.urls[0] if req.urls else "",
        path=None,
        meta={"count": len(req.urls)},
    )
    history_store.update_event(
        event_id,
        status="queued",
        total=len(req.urls),
        processed=0,
        success=0,
        failed=0,
        updated_ts=int(time.time()),
    )
    processor = BatchProcessor(
        config_manager,
        llm_processor,
        output_dir=OUTPUT_DIR,
        history_store=history_store,
        event_id=event_id,
        drill_config=req.drill_config,
        skill_name=req.skill_name,
    )

    _submit_background(processor.process_batch, req.urls, req.custom_skills)

    return {
        "status": "success",
        "message": f"已将 {len(req.urls)} 个任务加入后台队列，结果将实时写入 {processor.excel_path}",
        "report_path": processor.excel_path
    }


@app.post("/api/task/batch-ingest")
async def batch_ingest(req: BatchIngestRequest, background_tasks: BackgroundTasks):
    urls = [str(u or "").strip() for u in (req.urls or []) if str(u or "").strip()]
    seen = set()
    deduped_urls = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        deduped_urls.append(u)
    urls = deduped_urls

    inline_payloads: Dict[str, Dict[str, object]] = {}
    json_inputs = req.json_inputs or []
    for idx, it in enumerate(json_inputs):
        try:
            u = (it.url or "").strip() or f"jsonfile://upload#{idx + 1}"
            if u in inline_payloads:
                u = f"{u}#{idx + 1}"
            inline_payloads[u] = {
                "payload": it.payload,
                "meta": it.meta if isinstance(it.meta, dict) else {"source": "json_input"},
            }
        except Exception:
            continue

    for u in inline_payloads.keys():
        if u not in seen:
            seen.add(u)
            urls.append(u)

    if not urls:
        raise HTTPException(status_code=400, detail="请选择导入方式并提供 URL 或 JSON 数据")

    if any(urlparse(u).netloc.endswith("oa.ksyun.com") for u in urls if u):
        if not secret_store.get_auth_status():
            raise HTTPException(status_code=401, detail="首次运行请先点击右上角「手动授权访问」完成登录授权")
        try:
            verifier = WebCrawler(start_url="https://oa.ksyun.com", max_depth=1, headless=True)
            data = await verifier.get_page_snapshot("https://oa.ksyun.com", lock_timeout_s=2)
            if _looks_like_login_page(data.get("title", ""), data.get("content", ""), data.get("url", "")):
                secret_store.set_auth_status(False)
                raise HTTPException(status_code=401, detail="授权已失效，请点击右上角「手动授权访问」重新登录")
        except HTTPException:
            raise
        except TimeoutError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except Exception:
            pass

    event_id = _submit_batch_job(
        urls=urls,
        inline_payloads=inline_payloads,
        custom_skills=req.custom_skills,
        drill_config=req.drill_config,
        skill_name=req.skill_name,
        title=(req.title or "").strip() or "批量审批",
        llm_profile=req.llm_profile,
        json_concurrency=req.json_concurrency,
    )

    event = history_store.get_event(event_id)
    report_path = ""
    try:
        report_path = str((event.meta or {}).get("report_path") or "")
    except Exception:
        report_path = ""
    return {
        "status": "success",
        "event_id": event_id,
        "message": "已创建批量审批任务",
        "report_path": report_path,
    }


@app.post("/api/ab/records")
async def ab_create_record(req: ABCreateRequest):
    urls = [str(u or "").strip() for u in (req.urls or []) if str(u or "").strip()]
    json_inputs = req.json_inputs or []
    json_inputs_norm: List[Dict[str, object]] = []
    for it in json_inputs:
        try:
            json_inputs_norm.append(
                {
                    "url": (it.url or "").strip() if hasattr(it, "url") else "",
                    "payload": it.payload if hasattr(it, "payload") else None,
                    "meta": it.meta if isinstance(getattr(it, "meta", None), dict) else None,
                }
            )
        except Exception:
            continue

    input_obj: Dict[str, object] = {"urls": urls, "json_inputs": json_inputs_norm}
    rec = ab_audit_store.create_record(
        {
            "title": (req.title or "").strip(),
            "skill_name": (req.skill_name or "").strip(),
            "criteria": (req.criteria or "").strip(),
            "input": input_obj,
            "custom_skills": req.custom_skills,
            "drill_config": req.drill_config,
        }
    )
    return {"status": "success", "record": rec}


@app.get("/api/ab/records")
def ab_list_records(limit: int = 200, offset: int = 0):
    return ab_audit_store.list_records(limit=limit, offset=offset)


@app.get("/api/ab/records/{record_id}")
def ab_get_record(record_id: str):
    rec = ab_audit_store.get_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="未找到 AB 记录")
    return {"record": rec}


@app.post("/api/ab/records/{record_id}/update")
def ab_update_record(record_id: str, req: ABUpdateRequest):
    updated = ab_audit_store.update_record_fields(
        record_id,
        {"title": req.title, "criteria": req.criteria, "note": req.note, "conclusion": req.conclusion},
    )
    if not updated:
        raise HTTPException(status_code=404, detail="未找到 AB 记录")
    return {"status": "success", "record": updated}


def _ab_judge_prompt() -> str:
    return (
        "你是 A/B 对照评审模型（Judge）。\n"
        "你会收到同一输入在两个模型（A 与 B）下的产出，以及用户给定的判定标准。\n"
        "请从“准确性、完整性、可解释性、可执行性、风险/幻觉”五个维度为 A 与 B 打分，并给出 Winner 与置信度。\n\n"
        "严格输出 JSON（不要输出多余文本，不要 Markdown）：\n"
        "{\n"
        "  \"winner\": \"A|B|Tie\",\n"
        "  \"confidence\": 0.0,\n"
        "  \"scores\": {\n"
        "    \"accuracy\": {\"A\": 0, \"B\": 0},\n"
        "    \"completeness\": {\"A\": 0, \"B\": 0},\n"
        "    \"explainability\": {\"A\": 0, \"B\": 0},\n"
        "    \"actionability\": {\"A\": 0, \"B\": 0},\n"
        "    \"risk_hallucination\": {\"A\": 0, \"B\": 0}\n"
        "  },\n"
        "  \"reasons\": [\"...\"],\n"
        "  \"risks\": [\"...\"],\n"
        "  \"suggestions\": [\"如果要提升更差的一方，应该怎么改提示词/怎么补充数据...\"]\n"
        "}\n"
    )


@app.post("/api/ab/records/{record_id}/run")
async def ab_run(record_id: str, req: ABRunRequest):
    rec = ab_audit_store.get_record(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="未找到 AB 记录")

    which = str(req.which or "").strip().upper()
    if which not in ["A", "B", "J", "ALL"]:
        raise HTTPException(status_code=400, detail="which 必须是 A/B/J/ALL")

    input_obj = rec.get("input") if isinstance(rec.get("input"), dict) else {}
    urls = [str(u or "").strip() for u in (input_obj.get("urls") or []) if str(u or "").strip()]
    json_inputs = input_obj.get("json_inputs") or []
    inline_payloads: Dict[str, Dict[str, object]] = {}
    for idx, it in enumerate(json_inputs):
        try:
            if isinstance(it, dict):
                u = str(it.get("url") or "").strip() or f"jsonfile://ab#{idx + 1}"
                payload = it.get("payload")
                meta = it.get("meta")
            else:
                u = str(getattr(it, "url", None) or "").strip() or f"jsonfile://ab#{idx + 1}"
                payload = getattr(it, "payload", None)
                meta = getattr(it, "meta", None)
            inline_payloads[u] = {"payload": payload, "meta": meta if isinstance(meta, dict) else {"source": "json_input"}}
        except Exception:
            pass

    skill_name = str(rec.get("skill_name") or "").strip() or "summary"
    criteria = str(rec.get("criteria") or "").strip()
    custom_skills = rec.get("custom_skills") if isinstance(rec.get("custom_skills"), dict) else None
    drill_config = rec.get("drill_config") if isinstance(rec.get("drill_config"), dict) else None
    runs = rec.get("runs") if isinstance(rec.get("runs"), dict) else {"A": None, "B": None, "J": None}

    def run_single(letter: str) -> int:
        title = f"[AB:{letter}] {str(rec.get('title') or rec.get('id') or '').strip()}"
        return _submit_batch_job(
            urls=urls + list(inline_payloads.keys()),
            inline_payloads=inline_payloads,
            custom_skills=custom_skills,
            drill_config=drill_config,
            skill_name=skill_name,
            title=title,
            llm_profile=letter,
        )

    if which in ["A", "ALL"]:
        runs["A"] = run_single("A")
        ab_audit_store.update_record_fields(record_id, {"runs": runs})

    if which in ["B", "ALL"]:
        runs["B"] = run_single("B")
        ab_audit_store.update_record_fields(record_id, {"runs": runs})

    if which in ["J", "ALL"]:
        if not runs.get("A") or not runs.get("B"):
            raise HTTPException(status_code=400, detail="Judge 需要先完成 A 与 B")
        a_event = history_store.get_event(int(runs["A"]))
        b_event = history_store.get_event(int(runs["B"]))
        a_items = history_store.list_items(int(runs["A"])) or []
        b_items = history_store.list_items(int(runs["B"])) or []
        a_text = (a_items[0].result if a_items else "") or ""
        b_text = (b_items[0].result if b_items else "") or ""

        judge_payload = {
            "title": str(rec.get("title") or ""),
            "skill_name": skill_name,
            "criteria": criteria,
            "input": input_obj,
            "output_A": a_text,
            "output_B": b_text,
            "meta": {"A_event": getattr(a_event, "id", None), "B_event": getattr(b_event, "id", None)},
        }

        j_inline = {"jsonfile://ab_judge": {"payload": judge_payload, "meta": {"source": "ab_judge"}}}
        j_event = _submit_batch_job(
            urls=["jsonfile://ab_judge"],
            inline_payloads=j_inline,
            custom_skills={"ab_judge": _ab_judge_prompt()},
            drill_config=None,
            skill_name="ab_judge",
            title=f"[AB:J] {str(rec.get('title') or rec.get('id') or '').strip()}",
            llm_profile="J",
        )
        runs["J"] = j_event
        ab_audit_store.update_record_fields(record_id, {"runs": runs})

    updated = ab_audit_store.get_record(record_id)
    return {"status": "success", "record": updated}


_MB_META_KEY_PROBLEM_CORE = "mb_problem_core"
_MB_META_KEY_ADJUSTMENT = "mb_adjustment_measures"
_MB_META_KEY_ACTUAL_RESULT = "mb_actual_result"
_MB_META_KEY_INPUT_PAYLOAD = "input_payload"


def _mb_to_cell(v: object) -> str:
    """
    将任意值转成表格单元格可展示的短文本。
    """
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        parts = []
        for x in v:
            s = _mb_to_cell(x)
            if s:
                parts.append(s)
        return " / ".join(parts)[:500]
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)[:500]
    return str(v)[:500]


def _mb_to_excel_cell(v: object) -> str:
    """
    Excel 单元格最大长度约 32767，这里做保守裁剪，避免导出失败。
    """
    s = _mb_to_cell(v)
    return s if len(s) <= 32000 else s[:32000] + "…"


def _mb_normalize_result(v: object) -> str:
    """
    将 AI/人工结果统一为：通过 / 驳回 / 人工复核 三者之一（尽量容错）。
    """
    s = str(v or "").strip()
    if not s:
        return ""
    low = s.lower()
    if "人工复核" in s or "review" in low or "manual" in low:
        return "人工复核"
    if "驳回" in s or "拒绝" in s or "reject" in low or "deny" in low:
        return "驳回"
    if "通过" in s or "approve" in low or "pass" in low or "accept" in low:
        return "通过"
    return s


def _mb_format_risks(parsed_obj: object) -> str:
    """
    将 LLM 返回 JSON 里的 风险点/风险依据 拼成“测试返回结果描述”字段。
    """
    if not isinstance(parsed_obj, dict):
        return ""
    risks = parsed_obj.get("风险点")
    bases = parsed_obj.get("风险依据")
    if not isinstance(risks, list):
        risks = []
    if not isinstance(bases, list):
        bases = []
    lines: List[str] = []
    n = max(len(risks), len(bases))
    for i in range(n):
        r = risks[i] if i < len(risks) else ""
        b = bases[i] if i < len(bases) else ""
        r_s = str(r or "").strip()
        b_s = str(b or "").strip()
        if r_s:
            lines.append(f"{i+1}. 风险点：{r_s}")
        if b_s:
            lines.append(f"   风险依据：{b_s}")
    return "\n".join(lines).strip()


def _mb_base_columns(show_pay_type: bool) -> List[Dict[str, object]]:
    """
    返回 mb_JSON_test.xlsx 对齐的列定义（同时标注可编辑列）。
    """
    return [
        {"key": "seq", "label": "序号"},
        {"key": "test_result", "label": "测试结果"},
        {"key": "flow_id", "label": "json id/流程id"},
        {"key": "flow_name", "label": "流程名称"},
        {"key": "pay_type", "label": "付款类型", "defaultVisible": bool(show_pay_type)},
        {"key": "risk_level", "label": "风险等级"},
        {"key": "ai_suggestion", "label": "ai建议结果"},
        {"key": "actual_result", "label": "实际流程结果", "editable": True},
        {"key": "consistency", "label": "结果一致性"},
        {"key": "return_json", "label": "返回JSON"},
        {"key": "test_desc", "label": "测试返回结果描述"},
        {"key": "problem_core", "label": "问题点核心简述", "editable": True},
        {"key": "adjustment_measures", "label": "调整措施", "editable": True},
    ]


def _mb_find_first(obj: object, keys: List[str]) -> Optional[object]:
    """
    在嵌套 JSON（dict/list）里按 key 候选列表“递归查找第一个命中值”。
    """
    if obj is None:
        return None
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and obj.get(k) is not None:
                return obj.get(k)
        for v in obj.values():
            found = _mb_find_first(v, keys)
            if found is not None:
                return found
    if isinstance(obj, list):
        for v in obj:
            found = _mb_find_first(v, keys)
            if found is not None:
                return found
    return None


def _mb_find_by_path(obj: object, path: List[str]) -> Optional[object]:
    """
    按固定路径在嵌套 dict 中取值（不做递归搜索）。
    """
    cur = obj
    for k in path:
        if not isinstance(cur, dict):
            return None
        if k not in cur:
            return None
        cur = cur.get(k)
    return cur


def _mb_get_value_by_dotpath(obj: object, dotpath: str) -> Optional[object]:
    """
    根据点路径从 JSON 中取值；遇到 list 时会尽力合并为可展示的值。
    """
    parts = [p for p in str(dotpath or "").split(".") if p]
    cur: object = obj
    for p in parts:
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(p)
            continue
        if isinstance(cur, list):
            vals = []
            for it in cur:
                if isinstance(it, dict) and p in it:
                    vals.append(it.get(p))
            cur = vals if vals else None
            continue
        return None
    return cur


def _mb_flatten_paths(obj: object, prefix: str = "") -> List[str]:
    """
    将 JSON 扁平化为字段路径列表（点连接），便于前端做“字段列增删”。
    """
    paths: List[str] = []

    def _walk(v: object, pfx: str, depth: int):
        if depth > 8:
            return
        if v is None:
            return
        if isinstance(v, (str, int, float, bool)):
            if pfx:
                paths.append(pfx)
            return
        if isinstance(v, dict):
            for k, vv in v.items():
                kk = str(k)
                np = f"{pfx}.{kk}" if pfx else kk
                _walk(vv, np, depth + 1)
            return
        if isinstance(v, list):
            if not pfx:
                return
            if len(v) == 0:
                paths.append(pfx)
                return
            if all(isinstance(x, (str, int, float, bool)) or x is None for x in v):
                paths.append(pfx)
                return
            for it in v[:30]:
                _walk(it, pfx, depth + 1)
            return

    _walk(obj, prefix, 0)
    dedup = list(dict.fromkeys([p for p in paths if p]))
    dedup.sort()
    return dedup


def _mb_build_columns(show_pay_type: bool, extra_fields: Optional[List[MBExtraField]] = None) -> List[Dict[str, object]]:
    """
    生成完整列：基础列 + 动态字段列。
    """
    cols = _mb_base_columns(show_pay_type)
    for f in (extra_fields or []):
        src = str(getattr(f, "source", "") or "").strip().lower()
        path = str(getattr(f, "path", "") or "").strip()
        if not path or src not in ["input", "output"]:
            continue
        key = f"{src}.{path}"
        label = f"{'输入' if src == 'input' else '输出'} JSON：{path}"
        cols.append({"key": key, "label": label, "defaultVisible": True})
    return cols


def _build_mb_table_row(item: object, seq: int, extra_fields: Optional[List[MBExtraField]] = None) -> Dict[str, object]:
    """
    将 history_items 的单条记录，映射为 mb_JSON_test.xlsx 对应的一行数据。
    """
    it = item
    item_id = int(getattr(it, "id", 0) or 0)
    status = str(getattr(it, "status", "") or "")
    evidence = str(getattr(it, "evidence", "") or "")
    result_text = str(getattr(it, "result", "") or "")
    meta = getattr(it, "meta", {}) or {}

    parsed_obj, _ = _parse_json_from_text(result_text)
    is_json = parsed_obj is not None

    input_obj: object = None
    if isinstance(meta, dict):
        input_obj = meta.get(_MB_META_KEY_INPUT_PAYLOAD)

    flow_id = _mb_find_first(input_obj, ["单据编码", "单据编号", "单号", "bill_code", "billCode"]) if input_obj is not None else None
    flow_name = None
    if input_obj is not None:
        flow_name = _mb_find_by_path(input_obj, ["field_values", "标题"])
        if flow_name is None:
            flow_name = _mb_find_by_path(input_obj, ["formData", "标题"])
        if flow_name is None:
            flow_name = _mb_find_first(input_obj, ["标题"])

    input_flow_name = _mb_find_first(input_obj, ["flow_name", "flowName", "流程名称"]) if input_obj is not None else None
    pay_type = _mb_find_first(input_obj, ["费用类别", "费用分类", "费用类型"]) if input_obj is not None else None

    risk_level = _mb_find_first(parsed_obj, ["风险等级", "risk_level", "riskLevel", "level"]) if is_json else None
    ai_suggestion = _mb_find_first(parsed_obj, ["流程审批建议", "ai_suggestion", "suggestion", "recommendation", "ai建议结果", "建议结果"]) if is_json else None

    ai_s = _mb_normalize_result(ai_suggestion)
    actual_s = "通过"
    if isinstance(meta, dict):
        actual_s = _mb_normalize_result(meta.get(_MB_META_KEY_ACTUAL_RESULT) or "") or "通过"
    consistency = "一致" if ai_s and actual_s and ai_s == actual_s else ("不一致" if ai_s and actual_s else "")

    problem_core = ""
    adjustment_measures = ""
    if isinstance(meta, dict):
        problem_core = _mb_to_cell(meta.get(_MB_META_KEY_PROBLEM_CORE) or "")
        adjustment_measures = _mb_to_cell(meta.get(_MB_META_KEY_ADJUSTMENT) or "")
    test_desc = _mb_format_risks(parsed_obj) if is_json else evidence

    return_json_text = ""
    if is_json:
        try:
            return_json_text = json.dumps(parsed_obj, ensure_ascii=False, indent=2)
        except Exception:
            return_json_text = result_text
    else:
        return_json_text = result_text

    row: Dict[str, object] = {
        "item_id": item_id,
        "seq": seq,
        "test_result": "pass" if status == "success" else "fail",
        "flow_id": _mb_to_cell(flow_id),
        "flow_name": _mb_to_cell(flow_name),
        "pay_type": _mb_to_cell(pay_type),
        "risk_level": _mb_to_cell(risk_level),
        "ai_suggestion": ai_s,
        "actual_result": actual_s,
        "consistency": consistency,
        "return_json": return_json_text,
        "test_desc": test_desc,
        "problem_core": problem_core,
        "adjustment_measures": adjustment_measures,
        "_pay_type_default_visible": bool(str(input_flow_name or "").strip() == "金山云采购付款申请"),
    }

    for f in (extra_fields or []):
        try:
            src = str(getattr(f, "source", "") or "").strip().lower()
            path = str(getattr(f, "path", "") or "").strip()
        except Exception:
            continue
        if not path or src not in ["input", "output"]:
            continue
        obj = input_obj if src == "input" else parsed_obj
        row[f"{src}.{path}"] = _mb_get_value_by_dotpath(obj, path) if obj is not None else ""

    return row


@app.get("/api/history/events")
def list_history_events(q: Optional[str] = None, kind: Optional[str] = None, limit: int = 50, offset: int = 0):
    events = history_store.list_events(q=q, kind=kind, limit=limit, offset=offset)
    return {
        "events": [
            {
                "id": e.id,
                "ts": e.ts,
                "kind": e.kind,
                "title": e.title,
                "url": e.url,
                "path": e.path,
                "status": e.status,
                "updated_ts": e.updated_ts,
                "started_ts": e.started_ts,
                "finished_ts": e.finished_ts,
                "total": e.total,
                "processed": e.processed,
                "success": e.success,
                "failed": e.failed,
                "meta": e.meta,
            }
            for e in events
        ]
    }


@app.get("/api/history/events/{event_id}")
def get_history_event(event_id: int):
    event = history_store.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="未找到历史记录")
    items = history_store.list_items(event_id)
    return {
        "event": {
            "id": event.id,
            "ts": event.ts,
            "kind": event.kind,
            "title": event.title,
            "url": event.url,
            "path": event.path,
            "status": event.status,
            "updated_ts": event.updated_ts,
            "started_ts": event.started_ts,
            "finished_ts": event.finished_ts,
            "total": event.total,
            "processed": event.processed,
            "success": event.success,
            "failed": event.failed,
            "meta": event.meta,
        },
        "items": [
            {
                "id": i.id,
                "ts": i.ts,
                "url": i.url,
                "skill": i.skill,
                "evidence": i.evidence,
                "result": i.result,
                "status": i.status,
                "meta": i.meta,
            }
            for i in items
        ],
    }


@app.post("/api/history/events/{event_id}/retry_failed")
def retry_failed_items(event_id: int, req: Optional[RetryFailedRequest] = None):
    """
    对指定批处理任务进行“失败项重试”：
    - 会创建一个新的 event（不覆盖旧记录）
    - 默认仅重试旧任务中 status != success 的条目
    """
    ev = history_store.get_event(event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="未找到历史记录")
    if str(ev.kind or "") != "batch":
        raise HTTPException(status_code=400, detail="仅支持批量任务(batch)的重试")
    if str(ev.status or "") in ["queued", "running"]:
        raise HTTPException(status_code=409, detail="任务仍在运行/排队中，请等待结束后再重试")

    only_failed = True
    try:
        only_failed = bool(req.only_failed) if req is not None else True
    except Exception:
        only_failed = True

    items = history_store.list_items(event_id) or []
    urls = []
    for it in items:
        st = str(getattr(it, "status", "") or "")
        if only_failed and st == "success":
            continue
        u = str(getattr(it, "url", "") or "").strip()
        if u:
            urls.append(u)
    urls = list(dict.fromkeys(urls))
    if not urls:
        raise HTTPException(status_code=400, detail="未找到可重试条目")

    meta = ev.meta if isinstance(ev.meta, dict) else {}
    snapshot_path = str(meta.get("input_snapshot_path") or "").strip()

    inline_payloads: Dict[str, Dict[str, object]] = {}
    custom_skills: Optional[Dict[str, str]] = None
    drill_config: Optional[Dict[str, object]] = None
    skill_name: Optional[str] = None
    llm_profile: Optional[str] = None
    json_concurrency: Optional[int] = None

    if snapshot_path and os.path.exists(snapshot_path):
        try:
            with open(snapshot_path, "r", encoding="utf-8") as f:
                snap = json.load(f) or {}
            inline_all = snap.get("inline_payloads") or {}
            if isinstance(inline_all, dict):
                for u in urls:
                    if u in inline_all:
                        inline_payloads[u] = inline_all.get(u) or {}
            custom_skills = snap.get("custom_skills") if isinstance(snap.get("custom_skills"), dict) else None
            drill_config = snap.get("drill_config") if isinstance(snap.get("drill_config"), dict) else None
            skill_name = str(snap.get("skill_name") or "").strip() or None
            llm_profile = str(snap.get("llm_profile") or "").strip() or None
            try:
                json_concurrency = int(snap.get("json_concurrency")) if snap.get("json_concurrency") is not None else None
            except Exception:
                json_concurrency = None
        except Exception:
            pass

    if llm_profile is None:
        llm_profile = str(meta.get("llm_profile") or "").strip() or None

    new_event_id = _submit_batch_job(
        urls=urls,
        inline_payloads=inline_payloads,
        custom_skills=custom_skills,
        drill_config=drill_config,
        skill_name=skill_name,
        title=f"{str(ev.title or '').strip()}（重试失败项）",
        llm_profile=llm_profile,
        json_concurrency=json_concurrency,
    )
    return {"status": "success", "event_id": new_event_id}


@app.get("/api/history/events/{event_id}/mb_table")
def get_history_mb_table(event_id: int):
    event = history_store.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="未找到历史记录")
    items = history_store.list_items(event_id)
    rows = [_build_mb_table_row(it, idx + 1, extra_fields=None) for idx, it in enumerate(items or [])]
    show_pay_type = any(bool(r.get("_pay_type_default_visible")) for r in rows)
    for r in rows:
        r.pop("_pay_type_default_visible", None)
    return {"columns": _mb_build_columns(show_pay_type, extra_fields=None), "rows": rows}


@app.post("/api/mb_table/collect")
def collect_mb_table(req: MBTableCollectRequest):
    """
    批量汇总多条历史记录，生成用于“表格分析”的合并表。
    """
    event_ids = [int(x) for x in (req.event_ids or []) if int(x) > 0]
    event_ids = list(dict.fromkeys(event_ids))
    rows: List[Dict[str, object]] = []
    seq = 1
    show_pay_type = False
    for eid in event_ids:
        event = history_store.get_event(eid)
        if not event:
            continue
        items = history_store.list_items(eid) or []
        for it in items:
            row = _build_mb_table_row(it, seq, extra_fields=req.extra_fields)
            if bool(row.get("_pay_type_default_visible")):
                show_pay_type = True
            rows.append(row)
            seq += 1
    for r in rows:
        r.pop("_pay_type_default_visible", None)

    cols = _mb_build_columns(show_pay_type, extra_fields=req.extra_fields)
    if req.visible_keys:
        visible = set([str(k) for k in (req.visible_keys or []) if str(k).strip()])
        visible.add("item_id")
        cols = [c for c in cols if str(c.get("key") or "") in visible]
        for r in rows:
            for k in list(r.keys()):
                if k not in visible:
                    r.pop(k, None)
    return {"columns": cols, "rows": rows}


@app.post("/api/mb_table/fields")
def list_mb_table_fields(req: MBTableFieldsRequest):
    """
    根据选择的历史记录，列出“输入/输出 JSON”可用于新增列的字段路径（支持搜索/多选）。
    """
    event_ids = [int(x) for x in (req.event_ids or []) if int(x) > 0]
    event_ids = list(dict.fromkeys(event_ids))

    input_paths: List[str] = []
    output_paths: List[str] = []

    for eid in event_ids:
        items = history_store.list_items(eid) or []
        for it in items:
            meta = getattr(it, "meta", {}) or {}
            input_obj = meta.get(_MB_META_KEY_INPUT_PAYLOAD) if isinstance(meta, dict) else None
            if input_obj is not None:
                input_paths.extend(_mb_flatten_paths(input_obj))
            out_text = str(getattr(it, "result", "") or "")
            out_obj, _ = _parse_json_from_text(out_text)
            if out_obj is not None:
                output_paths.extend(_mb_flatten_paths(out_obj))

    input_paths = list(dict.fromkeys(input_paths))
    output_paths = list(dict.fromkeys(output_paths))
    input_paths.sort()
    output_paths.sort()

    exclude_input = set(["单据编码", "单据编号", "field_values.标题", "formData.标题", "费用类别", "费用分类", "费用类型", "flow_name", "flowName", "流程名称"])
    exclude_output = set(["风险等级", "流程审批建议", "风险点", "风险依据"])

    fields: List[Dict[str, str]] = []
    for p in input_paths:
        if p in exclude_input:
            continue
        fields.append({"source": "input", "path": p, "label": p})
    for p in output_paths:
        if p in exclude_output:
            continue
        fields.append({"source": "output", "path": p, "label": p})

    return {"fields": fields}


@app.get("/api/history/events/{event_id}/mb_table.xlsx")
def export_history_mb_table_excel(event_id: int):
    """
    导出 mb_JSON_test.xlsx 结构的 Excel 文件（包含可编辑字段的最新保存值）。
    """
    try:
        from openpyxl import Workbook
    except Exception:
        raise HTTPException(status_code=500, detail="缺少 openpyxl 依赖，无法导出 Excel")

    payload = get_history_mb_table(event_id)
    cols = payload.get("columns") or []
    rows = payload.get("rows") or []

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    headers = [str(c.get("label") or c.get("key") or "") for c in cols]
    keys = [str(c.get("key") or "") for c in cols]

    ws.append(headers)
    for r in rows:
        line = []
        for k in keys:
            v = r.get(k, "")
            s = _mb_to_excel_cell(v)
            line.append(s)
        ws.append(line)

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)

    filename = f"mb_JSON_test_event_{event_id}.xlsx"
    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/mb_table/export.xlsx")
def export_mb_table_excel(req: MBTableCollectRequest):
    """
    批量导出 Excel：将多条历史记录合并成一张表导出（列结构对齐 mb_JSON_test.xlsx）。
    """
    try:
        from openpyxl import Workbook
    except Exception:
        raise HTTPException(status_code=500, detail="缺少 openpyxl 依赖，无法导出 Excel")

    payload = collect_mb_table(req)
    cols = payload.get("columns") or []
    rows = payload.get("rows") or []

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    headers = [str(c.get("label") or c.get("key") or "") for c in cols]
    keys = [str(c.get("key") or "") for c in cols]
    ws.append(headers)
    for r in rows:
        ws.append([_mb_to_excel_cell(r.get(k, "")) for k in keys])

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)

    filename = "mb_JSON_test_export.xlsx"
    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.put("/api/history/items/{item_id}/mb_notes")
def update_history_mb_notes(item_id: int, req: MBNotesUpdateRequest):
    patch: Dict[str, object] = {"_mb_updated_ts": int(time.time())}
    if req.problem_core is not None:
        patch[_MB_META_KEY_PROBLEM_CORE] = str(req.problem_core or "")
    if req.adjustment_measures is not None:
        patch[_MB_META_KEY_ADJUSTMENT] = str(req.adjustment_measures or "")
    if req.actual_result is not None:
        patch[_MB_META_KEY_ACTUAL_RESULT] = _mb_normalize_result(req.actual_result)
    updated = history_store.update_item_meta(item_id, patch)
    if updated is None:
        raise HTTPException(status_code=404, detail="未找到明细记录")
    return {
        "status": "success",
        "meta": updated,
        "problem_core": str(updated.get(_MB_META_KEY_PROBLEM_CORE) or ""),
        "adjustment_measures": str(updated.get(_MB_META_KEY_ADJUSTMENT) or ""),
        "actual_result": str(updated.get(_MB_META_KEY_ACTUAL_RESULT) or ""),
    }


@app.post("/api/history/open")
def open_history_path(req: HistoryOpenRequest):
    event = history_store.get_event(req.event_id)
    if not event or not event.path:
        raise HTTPException(status_code=404, detail="该记录没有可打开的路径")
    try:
        open_in_file_manager(event.path, [OUTPUT_DIR, LOG_DIR, os.path.join(os.path.dirname(__file__), "docs")])
    except PathOpenError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success"}


@app.post("/api/drill/start")
async def drill_start(req: DrillStartRequest):
    parsed = urlparse(req.url)
    if parsed.scheme not in ["http", "https"]:
        raise HTTPException(status_code=400, detail="URL 格式不正确")
    if parsed.netloc.endswith("oa.ksyun.com"):
        if not secret_store.get_auth_status():
            raise HTTPException(status_code=401, detail="首次运行请先完成浏览器授权")
        try:
            verifier = WebCrawler(start_url="https://oa.ksyun.com", max_depth=1, headless=True)
            data = await verifier.get_page_snapshot("https://oa.ksyun.com")
            if _looks_like_login_page(data.get("title", ""), data.get("content", ""), data.get("url", "")):
                secret_store.set_auth_status(False)
                raise HTTPException(status_code=401, detail="授权已失效，请重新点击「手动授权访问」完成登录")
        except HTTPException:
            raise
        except Exception:
            pass
    max_depth = max(1, min(int(req.max_depth), 5))
    event_id = history_store.create_event(
        kind="drill",
        title="网页下钻",
        url=req.url,
        meta={"max_depth": max_depth},
    )
    session_id = drill_store.create_session(max_depth=max_depth, event_id=event_id)
    crawler = WebCrawler(start_url=req.url, max_depth=max_depth, headless=True)
    page_data = await crawler.get_page_snapshot(req.url)
    if parsed.netloc.endswith("oa.ksyun.com") and not (page_data.get("content") or "").strip():
        try:
            headful = WebCrawler(start_url=req.url, max_depth=max_depth, headless=False)
            page2 = await headful.get_page_snapshot(req.url)
            if (page2.get("content") or "").strip():
                page_data = page2
        except Exception:
            pass
    if parsed.netloc.endswith("oa.ksyun.com") and _looks_like_login_page(
        page_data.get("title", ""), page_data.get("content", ""), page_data.get("url", "")
    ):
        secret_store.set_auth_status(False)
        raise HTTPException(status_code=401, detail="检测到当前页面仍为登录页，请重新完成登录授权后再下钻")
    stack = [page_data]
    drill_store.save_stack(session_id, stack)
    history_store.add_item(event_id, req.url, "", "打开页面", page_data.get("title", ""), "visited")
    return {"session_id": session_id, "max_depth": max_depth, "stack": stack}


@app.post("/api/drill/navigate")
async def drill_navigate(req: DrillNavigateRequest):
    parsed = urlparse(req.url)
    if parsed.scheme not in ["http", "https"]:
        raise HTTPException(status_code=400, detail="URL 格式不正确")
    session = drill_store.get_session(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    if len(session.stack) >= session.max_depth:
        raise HTTPException(status_code=400, detail="已达到最大下钻深度")
    if parsed.netloc.endswith("oa.ksyun.com"):
        if not secret_store.get_auth_status():
            raise HTTPException(status_code=401, detail="授权已失效，请重新点击「手动授权访问」完成登录")
        try:
            verifier = WebCrawler(start_url="https://oa.ksyun.com", max_depth=1, headless=True)
            data = await verifier.get_page_snapshot("https://oa.ksyun.com")
            if _looks_like_login_page(data.get("title", ""), data.get("content", ""), data.get("url", "")):
                secret_store.set_auth_status(False)
                raise HTTPException(status_code=401, detail="授权已失效，请重新点击「手动授权访问」完成登录")
        except HTTPException:
            raise
        except Exception:
            pass
    crawler = WebCrawler(start_url=req.url, max_depth=session.max_depth, headless=True)
    page_data = await crawler.get_page_snapshot(req.url)
    if parsed.netloc.endswith("oa.ksyun.com") and not (page_data.get("content") or "").strip():
        try:
            headful = WebCrawler(start_url=req.url, max_depth=session.max_depth, headless=False)
            page2 = await headful.get_page_snapshot(req.url)
            if (page2.get("content") or "").strip():
                page_data = page2
        except Exception:
            pass
    if parsed.netloc.endswith("oa.ksyun.com") and _looks_like_login_page(
        page_data.get("title", ""), page_data.get("content", ""), page_data.get("url", "")
    ):
        secret_store.set_auth_status(False)
        raise HTTPException(status_code=401, detail="检测到当前页面仍为登录页，请重新完成登录授权后再继续下钻")
    stack = list(session.stack) + [page_data]
    drill_store.save_stack(req.session_id, stack)
    history_store.add_item(session.event_id, req.url, "", "导航进入", page_data.get("title", ""), "visited")
    return {"session_id": req.session_id, "max_depth": session.max_depth, "stack": stack}


@app.post("/api/drill/back")
def drill_back(req: DrillBackRequest):
    session = drill_store.get_session(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    if len(session.stack) <= 1:
        return {"session_id": req.session_id, "max_depth": session.max_depth, "stack": session.stack}
    stack = list(session.stack[:-1])
    drill_store.save_stack(req.session_id, stack)
    return {"session_id": req.session_id, "max_depth": session.max_depth, "stack": stack}


@app.get("/api/drill/session/{session_id}")
def drill_get_session(session_id: str):
    session = drill_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"session_id": session_id, "max_depth": session.max_depth, "stack": session.stack}


@app.post("/api/task/process")
async def process_task(req: CrawlRequest):
    """
    核心业务流程：接收 URL -> 爬取网页内容 -> 读取本地配置 -> 拼装 Prompt -> 调用 LLM -> 返回结果
    """
    # 1. 初始化爬虫并抓取网页
    crawler = WebCrawler(
        start_url=req.url,
        max_depth=req.max_depth,
        headless=req.headless
    )
    try:
        results = await crawler.run()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"网页抓取失败: {str(e)}")

    if not results:
        raise HTTPException(status_code=404, detail="未抓取到有效内容")

    # 提取所有抓取内容的文本部分合并
    combined_content = "\n\n".join([item["content"] for item in results])

    # 2. 读取本地配置拼装最终 Prompt
    try:
        skill_name = req.skill_name
        if (not (req.custom_skill_content or "").strip()) and (not skill_name or skill_name == "summary"):
            matched, _candidates = config_manager.match_skill(req.url, custom_skills=None)
            if matched:
                skill_name = matched
        # 执行插件：在组装 Prompt 前允许插件修改或清洗抓取的内容
        plugin_context = {"content": combined_content, "url": req.url}
        processed_context = plugin_manager.execute_plugins(plugin_context)
        final_prompt = config_manager.build_final_prompt(
            skill_name,
            processed_context.get("content", combined_content),
            req.custom_skill_content
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=f"配置读取失败: {str(e)}")

    # 3. 调用 LLM 服务进行智能处理
    llm_config = secret_store.load_config()
    if not llm_config:
        raise HTTPException(status_code=400, detail="LLM 配置未初始化，请先在设置中配置 API")

    llm_processor = LLMProcessor(**llm_config)

    try:
        llm_result = llm_processor.generate(final_prompt)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM 推理失败: {str(e)}")

    evidence = ""
    result = llm_result

    return {
        "status": "success",
        "crawled_pages": len(results),
        "llm_result": llm_result,
        "evidence": evidence,
        "result": result,
        "raw": llm_result,
    }

# 确保在模块被直接运行时启动服务器
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
