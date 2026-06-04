import os
import time
import json
import re
import asyncio
import openpyxl
import logging
import hashlib
from typing import List, Optional
from urllib.parse import urlparse
from logger_setup import global_logger as logger
from config_manager import ConfigManager
from crawler import WebCrawler
from llm_processor import LLMProcessor
from history_store import HistoryStore
from rate_limiter import get_global_limiter, estimate_tokens_for_prompt


def _parse_json_from_text(text: str):
    """
    从任意 LLM 文本中尽力解析出 JSON。
    适配常见情况：
    1) 直接返回 JSON；
    2) Markdown 代码块 ``` ... ```（或 ```json ... ```）内包含 JSON；
    3) 文本前后有解释，但中间夹着一段 JSON。
    返回 (parsed_obj, extracted_json_text)；若无法解析则返回 (None, "")。
    """
    s = str(text or "").strip()
    if not s:
        return None, ""

    def _try_load(candidate: str):
        c = str(candidate or "").strip()
        if not c:
            return None, ""
        try:
            return json.loads(c), c
        except Exception:
            return None, ""

    obj, used = _try_load(s)
    if obj is not None:
        return obj, used

    for m in re.finditer(r"```(?:json|JSON)?\s*([\s\S]*?)```", s, flags=re.MULTILINE):
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


def _find_first_in_obj(obj: object, keys: List[str]):
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
            hit = _find_first_in_obj(v, keys)
            if hit is not None:
                return hit
    if isinstance(obj, list):
        for v in obj:
            hit = _find_first_in_obj(v, keys)
            if hit is not None:
                return hit
    return None


def _extract_fee_category(payload: object) -> str:
    """
    从“上传 JSON 输入”里尽力提取费用类别/费用分类，用于后续表格分析页的“付款类型”字段。
    """
    hit = _find_first_in_obj(
        payload,
        [
            "费用类别",
            "费用分类",
            "费用类型",
            "fee_category",
            "feeCategory",
            "expense_category",
            "expenseCategory",
        ],
    )
    if hit is None:
        return ""
    try:
        return str(hit).strip()
    except Exception:
        return ""


def _trim_json_for_history(obj: object, max_depth: int = 6, max_list: int = 60, max_str: int = 2000):
    """
    将“输入 JSON”裁剪到适合写入历史库 meta 的尺寸：
    - 限制递归深度
    - 限制数组长度
    - 限制超长字符串
    """
    def _walk(v: object, depth: int):
        if depth >= max_depth:
            return "[TRUNCATED_DEPTH]"
        if v is None:
            return None
        if isinstance(v, (int, float, bool)):
            return v
        if isinstance(v, str):
            s = v
            return s if len(s) <= max_str else s[:max_str] + "…"
        if isinstance(v, list):
            out = []
            for x in v[:max_list]:
                out.append(_walk(x, depth + 1))
            if len(v) > max_list:
                out.append(f"[TRUNCATED_LIST:{len(v)}]")
            return out
        if isinstance(v, dict):
            out = {}
            for k, x in list(v.items())[:500]:
                out[str(k)] = _walk(x, depth + 1)
            if len(v) > 500:
                out["_truncated_keys"] = len(v)
            return out
        try:
            s = str(v)
            return s if len(s) <= max_str else s[:max_str] + "…"
        except Exception:
            return "[UNSERIALIZABLE]"

    return _walk(obj, 0)


class BatchProcessor:
    """
    批量处理引擎：支持读取任务列表，动态匹配 Skill，实时写入 Excel，并支持断点续跑。
    """

    def __init__(
        self,
        config_manager: ConfigManager,
        llm_processor: LLMProcessor,
        output_dir: str = "output",
        history_store: Optional[HistoryStore] = None,
        event_id: Optional[int] = None,
        drill_config: Optional[dict] = None,
        skill_name: Optional[str] = None,
        json_concurrency: Optional[int] = None,
    ):
        self.config_manager = config_manager
        self.llm_processor = llm_processor
        self.output_dir = os.path.abspath(output_dir)
        os.makedirs(self.output_dir, exist_ok=True)
        self.history_store = history_store
        self.event_id = event_id
        self.drill_config = drill_config or {}
        self.skill_name = skill_name
        self.json_concurrency = max(1, int(json_concurrency or int(os.environ.get("AI_DCP_JSON_CONCURRENCY") or "8")))
        self.max_output_tokens = max(1, int(os.environ.get("AI_DCP_LLM_MAX_OUTPUT_TOKENS") or "2048"))

        # Excel 报告和断点续跑文件路径
        self.run_id = int(time.time())
        self.run_dir = os.path.join(self.output_dir, f"run_{self.run_id}")
        os.makedirs(self.run_dir, exist_ok=True)
        self.excel_path = os.path.join(self.run_dir, f"report_{self.run_id}.xlsx")
        self.log_path = os.path.join(self.run_dir, f"run_{self.run_id}.log")
        self.breakpoint_file = os.path.join(self.run_dir, "breakpoint.json")

        self._init_excel()
        if self.history_store and self.event_id:
            self.history_store.update_event(self.event_id, path=self.run_dir)
            self.history_store.merge_event_meta(
                self.event_id,
                {"report_path": self.excel_path, "log_path": self.log_path},
            )

    def _init_excel(self):
        """初始化 Excel 文件表头"""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["URL", "匹配技能", "分析证据", "处理结果"])
        wb.save(self.excel_path)
        logger.info(f"已创建 Excel 报告: {self.excel_path}")

    def _load_processed_urls(self) -> set:
        """加载已成功处理的 URL（断点续跑）"""
        if os.path.exists(self.breakpoint_file):
            try:
                with open(self.breakpoint_file, "r", encoding="utf-8") as f:
                    return set(json.load(f))
            except Exception as e:
                logger.warning(f"读取断点文件失败: {e}")
        return set()

    def _save_processed_url(self, url: str):
        """记录已成功处理的 URL"""
        urls = self._load_processed_urls()
        urls.add(url)
        with open(self.breakpoint_file, "w", encoding="utf-8") as f:
            json.dump(list(urls), f)

    def append_to_excel(self, url: str, skill: str, evidence: str, result: str):
        """将单条记录实时追加到 Excel 中"""
        try:
            wb = openpyxl.load_workbook(self.excel_path)
            ws = wb.active
            ws.append([url, skill, evidence, result])
            wb.save(self.excel_path)
        except Exception as e:
            logger.error(f"写入 Excel 失败 (URL: {url}): {e}")

    def _extract_fields_and_targets(self, page_data: dict, *, max_targets: int = 200) -> tuple:
        fields = {}
        targets = []
        target_seen = set()
        target_total = 0

        def _is_javascript_value(v: str) -> bool:
            s = (v or "").strip().lower()
            if not s:
                return False
            if s == "javascript:void(0)" or s == "void(0)" or s.startswith("javascript:"):
                return True
            return False

        try:
            for ff in (page_data or {}).get("form_fields") or []:
                if not isinstance(ff, dict):
                    continue
                k = str(ff.get("id") or ff.get("name") or "").strip()
                if not k:
                    continue
                display = str(ff.get("display") or "").strip()
                val = str(ff.get("value") or "").strip()
                v = display.splitlines()[0].strip() if display else val
                if not v:
                    continue
                if _is_javascript_value(v):
                    continue
                if k not in fields:
                    fields[k] = v[:200]
                if len(fields) >= 50:
                    break
        except Exception:
            pass

        try:
            content = str((page_data or {}).get("content") or "")
            for line in content.splitlines():
                s = line.strip()
                if not s:
                    continue
                m = re.match(r"^(.{1,30})[：:]\s*(.{1,200})$", s)
                if not m:
                    continue
                k = m.group(1).strip()
                v = m.group(2).strip()
                if not k or not v:
                    continue
                kl = k.lower()
                if any(x in kl for x in ["href", "onclick", "javascript"]):
                    continue
                if _is_javascript_value(v):
                    continue
                if any(x in k.lower() for x in ["http", "https"]):
                    continue
                if k not in fields:
                    fields[k] = v[:200]
                if len(fields) >= 80:
                    break
        except Exception:
            pass

        try:
            for l in (page_data or {}).get("links") or []:
                if not isinstance(l, dict):
                    continue
                u = str(l.get("url") or "").strip()
                raw_u = str(l.get("raw_url") or "").strip()
                onclick = str(l.get("onclick") or "").strip()
                resolved = bool(l.get("resolved"))
                if not u and not raw_u:
                    continue
                target_total += 1
                u_lower = (u or raw_u).lower()
                if u_lower.startswith("javascript:") or u_lower in ["javascript:void(0)", "void(0)", "javascript:void(0);", "void(0);"]:
                    key = ("js", raw_u.lower(), onclick[:200].lower())
                    if key in target_seen:
                        continue
                    target_seen.add(key)
                    targets.append(
                        {
                            "text": (str(l.get("text") or "").strip())[:80],
                            "url": "",
                            "raw_url": raw_u,
                            "onclick": onclick[:200],
                            "resolved": resolved,
                        }
                    )
                    if len(targets) >= max_targets:
                        break
                    continue
                t = str(l.get("text") or "").strip()
                key = ("url", (u or "").lower())
                if key in target_seen:
                    continue
                target_seen.add(key)
                targets.append({"text": t[:80], "url": u or "", "raw_url": raw_u, "onclick": onclick[:200], "resolved": resolved})
                if len(targets) >= max_targets:
                    break
        except Exception:
            pass

        truncated = bool(target_total > len(targets))
        return fields, targets, target_total, truncated

    async def process_batch(self, urls: List[str], custom_skills_dict: dict = None, inline_payloads: dict = None):
        """
        核心批量执行逻辑：
        urls 格式: ["http://url1", "http://url2", ...]
        inline_payloads:
          - 用于“上传 JSON 作为页面内容”的场景
          - 形如 { url: { "payload": <任意JSON>, "meta": {...} } }
        """
        file_handler = logging.FileHandler(self.log_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        logger.addHandler(file_handler)
        logger.setLevel(logging.DEBUG)
        processed_urls = self._load_processed_urls()
        logger.info(f"开始批量处理，共 {len(urls)} 条任务。已发现 {len(processed_urls)} 条断点记录。")
        pending_urls = [u for u in urls if u not in processed_urls]
        if self.history_store and self.event_id:
            now = int(time.time())
            self.history_store.update_event(
                self.event_id,
                status="running",
                updated_ts=now,
                started_ts=now,
                total=len(pending_urls),
                processed=0,
                success=0,
                failed=0,
            )

        try:
            inline_payloads = inline_payloads or {}
            limiter = get_global_limiter()
            io_lock = asyncio.Lock()

            async def _append_to_excel_safe(u: str, skill: str, evidence: str, result: str) -> None:
                async with io_lock:
                    await asyncio.to_thread(self.append_to_excel, u, skill, evidence, result)

            async def _save_processed_url_safe(u: str) -> None:
                async with io_lock:
                    await asyncio.to_thread(self._save_processed_url, u)

            async def _write_inline_payload_file(u: str, payload_obj: object) -> str:
                dig = hashlib.md5(str(u).encode("utf-8")).hexdigest()[:12]
                p = os.path.join(self.run_dir, f"input_{dig}.json")

                def _write():
                    with open(p, "w", encoding="utf-8") as f:
                        json.dump(payload_obj, f, ensure_ascii=False, indent=2)

                await asyncio.to_thread(_write)
                return p

            async def _acquire_llm_quota(*, item_id: Optional[int], estimated_tokens: int) -> None:
                while True:
                    ok, wait_s = limiter.try_acquire(estimated_tokens=estimated_tokens)
                    if ok:
                        if self.history_store and item_id:
                            self.history_store.update_item(item_id, status="running", evidence="")
                        return
                    if self.history_store and item_id:
                        self.history_store.update_item(item_id, status="waiting_rate_limit", evidence=f"等待限流 {wait_s:.1f}s")
                    await asyncio.sleep(wait_s)

            async def _process_one(url: str) -> None:
                item_id: Optional[int] = None
                matched_skill = None
                candidates = []
                try:
                    if self.skill_name:
                        matched_skill = self.skill_name
                    elif not custom_skills_dict:
                        matched_skill, candidates = self.config_manager.match_skill(url, custom_skills=None)
                        if not matched_skill:
                            matched_skill = "summary"
                    elif custom_skills_dict and len(custom_skills_dict.keys()) == 1:
                        matched_skill = list(custom_skills_dict.keys())[0]
                    else:
                        matched_skill, candidates = self.config_manager.match_skill(url, custom_skills=custom_skills_dict)

                    if not matched_skill:
                        cand_str = ", ".join(candidates) if candidates else "无"
                        evidence = "技能匹配失败"
                        result = f"未匹配到唯一技能 (候选项: {cand_str})"
                        await _append_to_excel_safe(url, "无", evidence, result)
                        if self.history_store and self.event_id:
                            self.history_store.add_item(
                                self.event_id,
                                url,
                                "无",
                                evidence[:200],
                                result[:500],
                                "unmatched",
                                meta={"drill": {"enabled": bool(self.drill_config.get("enabled"))}},
                            )
                            self.history_store.increment_event_progress(self.event_id, processed_delta=1, failed_delta=1)
                        logger.warning(f"URL: {url} 未匹配到唯一技能")
                        return

                    logger.debug(f"[{url}] 匹配到技能: {matched_skill}")

                    is_inline_payload = url in inline_payloads
                    enabled_raw = self.drill_config.get("enabled", None)
                    drill_enabled = bool(enabled_raw) if enabled_raw is not None else False
                    if enabled_raw is None:
                        try:
                            drill_enabled = int(self.drill_config.get("max_depth", 0) or 0) > 1
                        except Exception:
                            drill_enabled = False
                    if is_inline_payload:
                        drill_enabled = False

                    if self.history_store and self.event_id:
                        item_id = self.history_store.add_item(
                            self.event_id,
                            url,
                            matched_skill,
                            "",
                            "",
                            "running",
                            meta={"drill": {"enabled": bool(drill_enabled)}},
                        )

                    root_page = None
                    inline_payload_text = None
                    inline_payload_meta = {}
                    inline_fee_category = ""
                    payload = None
                    if is_inline_payload:
                        try:
                            payload = inline_payloads.get(url, {}).get("payload", {})
                            payload_text = json.dumps(payload, ensure_ascii=False, indent=2)
                        except Exception:
                            payload_text = str(inline_payloads.get(url, {}).get("payload", ""))
                            payload = inline_payloads.get(url, {}).get("payload", {})
                        try:
                            inline_payload_meta = inline_payloads.get(url, {}).get("meta", {}) or {}
                        except Exception:
                            inline_payload_meta = {}
                        inline_fee_category = _extract_fee_category(payload)
                        inline_payload_text = payload_text
                        root_page = {"url": url, "title": "JSON 输入", "content": payload_text}
                        crawl_results = [{"url": url, "depth": 1, "content": payload_text}]
                        drill_meta = {"enabled": False, "source": "json_input"}

                        if self.history_store and item_id:
                            input_path = await _write_inline_payload_file(url, payload)
                            self.history_store.update_item_meta(
                                item_id,
                                {
                                    "input_fee_category": inline_fee_category,
                                    "input_meta": inline_payload_meta,
                                    "input_payload": _trim_json_for_history(payload),
                                    "input_payload_path": input_path,
                                },
                            )
                    elif drill_enabled:
                        max_depth = int(self.drill_config.get("max_depth", 3))
                        max_depth = max(1, max_depth)
                        max_pages = 30
                        same_domain_only = True
                        include_patterns = []
                        retries = 1
                        rules = [
                            {"name": "status", "type": "status_code"},
                            {"name": "keywords", "type": "keyword_count", "keywords": ["已处理", "待处理"]},
                        ]
                        crawler = WebCrawler(start_url=url, max_depth=max_depth, headless=True)
                        pages = await crawler.drill_traverse(
                            start_url=url,
                            max_depth=max_depth,
                            include_url_patterns=include_patterns,
                            same_domain_only=same_domain_only,
                            max_pages=max_pages,
                            retries=retries,
                            extraction_rules=rules,
                        )
                        crawl_results = [
                            {"url": p.get("url"), "depth": p.get("depth"), "content": p.get("content", "")}
                            for p in pages.get("pages", [])
                            if p.get("included", True)
                        ]
                        try:
                            for p in pages.get("pages", []):
                                if isinstance(p, dict) and int(p.get("depth") or 0) == 1:
                                    root_page = p
                                    break
                        except Exception:
                            root_page = None
                        dig = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
                        drill_report_path = os.path.join(self.run_dir, f"drill_{dig}.json")
                        with open(drill_report_path, "w", encoding="utf-8") as f:
                            json.dump(pages, f, ensure_ascii=False, indent=2)
                        drill_meta = {
                            "enabled": True,
                            "max_depth": max_depth,
                            "visited": pages.get("summary", {}).get("visited", 0),
                            "included": pages.get("summary", {}).get("included", 0),
                            "failed": pages.get("summary", {}).get("failed", 0),
                            "report_path": drill_report_path,
                        }
                        if self.history_store and item_id:
                            self.history_store.update_item_meta(item_id, {"drill": drill_meta})
                    else:
                        crawler = WebCrawler(start_url=url, max_depth=1, headless=True)
                        snap = await crawler.get_page_snapshot(url)
                        root_page = snap
                        crawl_results = [{"url": snap.get("url"), "depth": 1, "content": snap.get("content", "")}]
                        drill_meta = {"enabled": False}

                    if not crawl_results:
                        raise Exception("未抓取到有效内容 (页面可能空白或超时)")

                    try:
                        parsed = urlparse(url)
                        is_oa = bool(parsed.netloc and parsed.netloc.endswith("oa.ksyun.com"))
                    except Exception:
                        is_oa = False

                    if is_oa:
                        try:
                            has_any_text = any(
                                (it.get("content") or "").strip() for it in (crawl_results or []) if isinstance(it, dict)
                            )
                        except Exception:
                            has_any_text = False
                        if not has_any_text:
                            try:
                                headful = WebCrawler(start_url=url, max_depth=1, headless=False)
                                snap2 = await headful.get_page_snapshot(url)
                                if (snap2.get("content") or "").strip():
                                    root_page = snap2
                                    crawl_results = [{"url": snap2.get("url"), "depth": 1, "content": snap2.get("content", "")}]
                                    if isinstance(drill_meta, dict):
                                        drill_meta["headful_fallback"] = True
                            except Exception:
                                pass

                    extracted_fields, drill_targets, drill_targets_total, drill_targets_truncated = self._extract_fields_and_targets(
                        root_page or {}, max_targets=200
                    )

                    root_texts = []
                    drill_texts = []
                    try:
                        root_items = [it for it in (crawl_results or []) if isinstance(it, dict) and int(it.get("depth") or 0) <= 1]
                        drill_items = [it for it in (crawl_results or []) if isinstance(it, dict) and int(it.get("depth") or 0) > 1]
                    except Exception:
                        root_items = []
                        drill_items = []

                    for it in root_items[:2]:
                        t = (it.get("content") or "").strip()
                        if t:
                            root_texts.append(t)
                    for it in drill_items[:8]:
                        t = (it.get("content") or "").strip()
                        if t:
                            drill_texts.append(t)

                    if is_inline_payload:
                        combined_content = (inline_payload_text or "\n\n".join(root_texts)).strip()
                    else:
                        combined_lines = [
                            "=== 主体页面（原始链接，depth=1）===",
                            "\n\n".join(root_texts).strip(),
                        ]
                        if drill_texts:
                            combined_lines.extend(["", "=== 下钻内容（辅助材料，depth>=2）===", "\n\n".join(drill_texts).strip()])

                        try:
                            items = list(extracted_fields.items())
                            items.sort(key=lambda x: str(x[0]))
                            items = items[:180]
                            extracted_preview = json.dumps(dict(items), ensure_ascii=False)
                        except Exception:
                            extracted_preview = ""
                        if extracted_preview:
                            combined_lines.extend(["", "=== 主体页抽取字段（结构化）===", extracted_preview[:12000]])

                        target_lines = []
                        try:
                            for t in (drill_targets or [])[:30]:
                                if isinstance(t, dict):
                                    u = (t.get("url") or t.get("raw_url") or "").strip()
                                    label = (t.get("text") or "").strip()
                                    if u:
                                        target_lines.append(f"{label} -> {u}" if label else u)
                        except Exception:
                            target_lines = []
                        if target_lines:
                            combined_lines.extend(["", "=== 发现的可下钻目标（最多30条）===", "\n".join(target_lines)])

                        combined_content = "\n".join([str(x) for x in combined_lines if str(x).strip()])

                    if not (combined_content or "").strip():
                        status = None
                        title = ""
                        links = []
                        try:
                            status = (root_page or {}).get("status")
                            title = (root_page or {}).get("title") or ""
                            links = (root_page or {}).get("links") or []
                        except Exception:
                            status = None
                            title = ""
                            links = []

                        link_lines = []
                        try:
                            for it in links[:30]:
                                if isinstance(it, dict):
                                    u = (it.get("url") or it.get("raw_url") or "").strip()
                                else:
                                    u = str(it or "").strip()
                                if u:
                                    link_lines.append(u)
                        except Exception:
                            link_lines = []

                        fallback_lines = [
                            "=== 抓取内容为空（兜底信息）===",
                            f"url: {url}",
                            f"status: {status}",
                            f"title: {title}",
                            "提示: 页面正文为空通常意味着未登录/需要二次跳转/SPA 尚未渲染完成/内容在 iframe 内。建议先执行一次授权登录（可视化登录）后再重试。",
                        ]
                        if link_lines:
                            fallback_lines.append("=== 页面中发现的链接（最多30条）===")
                            fallback_lines.extend(link_lines)
                        combined_content = "\n".join([str(x) for x in fallback_lines if str(x).strip()])

                    custom_skill_content = custom_skills_dict.get(matched_skill) if custom_skills_dict else None
                    final_prompt = self.config_manager.build_final_prompt(matched_skill, combined_content, custom_skill_content)

                    est = estimate_tokens_for_prompt(final_prompt, max_output_tokens=self.max_output_tokens)
                    await _acquire_llm_quota(item_id=item_id, estimated_tokens=est)
                    llm_response = await asyncio.to_thread(self.llm_processor.generate, final_prompt)

                    evidence = ""
                    result = str(llm_response or "")

                    await _append_to_excel_safe(url, matched_skill, evidence, result)
                    if self.history_store and self.event_id and item_id:
                        self.history_store.update_item(
                            item_id,
                            evidence=evidence,
                            result=result,
                            status="success",
                        )
                        if not is_inline_payload:
                            self.history_store.update_item_meta(item_id, {"drill": drill_meta})
                        self.history_store.increment_event_progress(self.event_id, processed_delta=1, success_delta=1)
                    elif self.history_store and self.event_id and not item_id:
                        self.history_store.add_item(self.event_id, url, matched_skill, evidence, result, "success", meta={"drill": drill_meta})
                        self.history_store.increment_event_progress(self.event_id, processed_delta=1, success_delta=1)

                    await _save_processed_url_safe(url)
                    logger.info(f"✅ 成功处理: {url}")

                except Exception as e:
                    error_msg = f"处理异常: {str(e)}"
                    logger.error(f"❌ [{url}] {error_msg}")
                    await _append_to_excel_safe(url, "异常", error_msg[:200], "异常")
                    if self.history_store and self.event_id:
                        if item_id:
                            self.history_store.update_item(item_id, skill="异常", evidence=error_msg[:200], result="异常", status="exception")
                        else:
                            self.history_store.add_item(
                                self.event_id,
                                url,
                                "异常",
                                error_msg[:200],
                                "异常",
                                "exception",
                                meta={"drill": {"enabled": bool(self.drill_config.get("enabled"))}},
                            )
                        self.history_store.increment_event_progress(self.event_id, processed_delta=1, failed_delta=1)

            inline_urls = [u for u in pending_urls if u in inline_payloads]
            normal_urls = [u for u in pending_urls if u not in inline_payloads]

            sem = asyncio.Semaphore(self.json_concurrency)

            async def _run_inline(u: str) -> None:
                async with sem:
                    await _process_one(u)

            inline_tasks = [asyncio.create_task(_run_inline(u)) for u in inline_urls]
            for u in normal_urls:
                await _process_one(u)
            if inline_tasks:
                await asyncio.gather(*inline_tasks)
        finally:
            if self.history_store and self.event_id:
                now = int(time.time())
                ev = self.history_store.get_event(self.event_id)
                status = "completed"
                if ev and ev.failed > 0 and ev.success == 0 and ev.processed > 0:
                    status = "failed"
                self.history_store.update_event(
                    self.event_id,
                    status=status,
                    updated_ts=now,
                    finished_ts=now,
                )
            logger.removeHandler(file_handler)
            file_handler.close()
