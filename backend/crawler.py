import asyncio
import os
import fnmatch
import re
import time
import fcntl
import shutil
from urllib.parse import urljoin, urlparse
from typing import List, Tuple
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
import aiohttp
import fitz  # PyMuPDF
import openpyxl
from logger_setup import global_logger as logger


class WebCrawler:
    """
    网页爬虫模块，支持 JS 渲染、持久化浏览器上下文 (保存登录状态) 和最多3级的递归抓取。
    """

    def __init__(self, start_url: str, max_depth: int = 3, headless: bool = True):
        self.start_url = start_url
        self.max_depth = max_depth
        self.headless = headless
        self.visited = set()  # 去重机制，记录已访问的 URL
        self.results = []     # 存储抓取到的内容
        # 指定一个专用的文件夹来永久保存浏览器的配置、Cookie、LocalStorage
        base_dir = os.path.dirname(__file__)
        user_data_dir = os.environ.get("AI_DCP_USER_DATA_DIR") or os.path.join(base_dir, "data")
        self.profile_dir = os.path.join(user_data_dir, "browser_profile")
        legacy_profile_dir = os.path.join(base_dir, "browser_profile")
        if legacy_profile_dir != self.profile_dir:
            try:
                legacy_exists = os.path.isdir(legacy_profile_dir) and any(os.scandir(legacy_profile_dir))
            except Exception:
                legacy_exists = False
            try:
                new_exists = os.path.isdir(self.profile_dir) and any(os.scandir(self.profile_dir))
            except Exception:
                new_exists = False
            if legacy_exists and not new_exists:
                os.makedirs(os.path.dirname(self.profile_dir), exist_ok=True)
                try:
                    os.replace(legacy_profile_dir, self.profile_dir)
                except Exception:
                    try:
                        shutil.copytree(legacy_profile_dir, self.profile_dir, dirs_exist_ok=True)
                    except Exception:
                        pass
        self._profile_lock_fd = None

    async def _acquire_profile_lock(self, timeout_s: int = 30) -> None:
        os.makedirs(self.profile_dir, exist_ok=True)
        lock_path = os.path.join(self.profile_dir, ".profile.lock")
        fd = open(lock_path, "w", encoding="utf-8")
        start = time.time()
        while True:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._profile_lock_fd = fd
                return
            except BlockingIOError:
                if time.time() - start > timeout_s:
                    fd.close()
                    raise TimeoutError("浏览器授权正在进行或浏览器配置被占用，请稍后重试")
                await asyncio.sleep(0.2)

    def _release_profile_lock(self) -> None:
        fd = self._profile_lock_fd
        self._profile_lock_fd = None
        if not fd:
            return
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            fd.close()
        except Exception:
            pass

    def _cleanup_profile_singleton_files(self) -> None:
        for name in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
            p = os.path.join(self.profile_dir, name)
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

    def is_valid_url(self, url: str) -> bool:
        """
        判断 URL 是否有效且属于同一域名或有效路径，防止无限跳出当前域外太多（根据需求可调整）。
        这里暂时只做简单的 scheme 过滤。
        """
        parsed = urlparse(url)
        return parsed.scheme in ["http", "https"]

    def _is_oa(self, url: str) -> bool:
        try:
            return urlparse(url).netloc.endswith("oa.ksyun.com")
        except Exception:
            return False

    def _get_goto_options(self, url: str) -> dict:
        wait_until = os.environ.get("AI_DCP_NAV_WAIT_UNTIL", "").strip() or None
        fallback_wait_until = os.environ.get("AI_DCP_NAV_FALLBACK_WAIT_UNTIL", "").strip() or None
        timeout_raw = os.environ.get("AI_DCP_NAV_TIMEOUT_MS", "").strip()
        fallback_timeout_raw = os.environ.get("AI_DCP_NAV_FALLBACK_TIMEOUT_MS", "").strip()

        if self._is_oa(url):
            default_wait_until = "domcontentloaded"
            default_timeout = 60000
        else:
            default_wait_until = "domcontentloaded"
            default_timeout = 45000

        try:
            timeout = int(timeout_raw) if timeout_raw else default_timeout
        except Exception:
            timeout = default_timeout

        try:
            fallback_timeout = int(fallback_timeout_raw) if fallback_timeout_raw else min(20000, timeout)
        except Exception:
            fallback_timeout = min(20000, timeout)

        if not wait_until:
            wait_until = default_wait_until

        if not fallback_wait_until:
            fallback_wait_until = "domcontentloaded" if wait_until == "networkidle" else "load"

        return {
            "wait_until": wait_until,
            "timeout": timeout,
            "fallback_wait_until": fallback_wait_until,
            "fallback_timeout": fallback_timeout,
        }

    async def _goto_with_fallback(self, page, url: str, *, wait_until: str, timeout: int, fallback_wait_until: str, fallback_timeout: int):
        try:
            return await page.goto(url, wait_until=wait_until, timeout=timeout)
        except Exception:
            return await page.goto(url, wait_until=fallback_wait_until, timeout=fallback_timeout)

    async def _stabilize_page(self, page, url: str, *, depth: int) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        if self._is_oa(url):
            try:
                await page.wait_for_selector('input[id^="field"], input[name^="field"]', timeout=8000)
            except Exception:
                pass
        try:
            await page.wait_for_function(
                "document.body && document.body.innerText && document.body.innerText.trim().length > 40",
                timeout=6000 if depth == 1 else 3000,
            )
        except Exception:
            pass
        try:
            await page.wait_for_timeout(800 if depth == 1 else 300)
        except Exception:
            pass

    def _extract_text_urls(self, base_url: str, text: str) -> List[str]:
        if not text:
            return []
        urls: List[str] = []
        seen = set()

        for m in re.finditer(r"https?://[^\s<>\"']+", text):
            u = m.group(0).strip()
            u = u.rstrip(").,;\"'>]}、，。；）")
            if not u or not self.is_valid_url(u):
                continue
            if u in seen:
                continue
            seen.add(u)
            urls.append(u)
            if len(urls) >= 120:
                return urls

        for m in re.finditer(r"(?:^|[\s\(\[])\/[^\s<>\"']+", text):
            u = m.group(0).strip()
            u = u.lstrip("([")
            u = u.rstrip(").,;\"'>]}、，。；）")
            if not u:
                continue
            abs_u = urljoin(base_url, u)
            if not self.is_valid_url(abs_u):
                continue
            if abs_u in seen:
                continue
            seen.add(abs_u)
            urls.append(abs_u)
            if len(urls) >= 160:
                break

        return urls

    async def fetch_pdf_content(self, url: str) -> str:
        """
        下载并提取 PDF 文件中的文本内容。
        """
        try:
            logger.info(f"正在下载 PDF: {url}")
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        pdf_data = await response.read()
                        doc = fitz.open(stream=pdf_data, filetype="pdf")
                        text = ""
                        for page in doc:
                            text += page.get_text()
                        return text
            return ""
        except Exception as e:
            logger.error(f"处理 PDF {url} 时出错: {e}")
            return ""

    def _infer_file_ext(self, url: str, content_type: str = "", content_disposition: str = "") -> str:
        path = urlparse(url).path.lower()
        for ext in [".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt"]:
            if path.endswith(ext):
                return ext
        cd = content_disposition or ""
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^\";]+)"?', cd, flags=re.IGNORECASE)
        if m:
            filename = m.group(1)
            filename = filename.split("/")[-1]
            filename = filename.split("\\")[-1]
            lower = filename.lower()
            for ext in [".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt"]:
                if lower.endswith(ext):
                    return ext
        ct = (content_type or "").lower()
        if "pdf" in ct:
            return ".pdf"
        if "word" in ct:
            return ".docx"
        if "spreadsheet" in ct or "excel" in ct:
            return ".xlsx"
        if "presentation" in ct or "powerpoint" in ct:
            return ".pptx"
        return ""

    def _is_same_site(self, netloc: str, start_domain: str) -> bool:
        if not netloc:
            return False
        if netloc == start_domain:
            return True
        parts = start_domain.split(".")
        root = start_domain
        if len(parts) >= 2:
            root = ".".join(parts[-2:])
        return netloc == root or netloc.endswith("." + root)

    def _looks_like_attachment_url(self, url: str) -> bool:
        u = url.lower()
        if self._infer_file_ext(url):
            return True
        return any(k in u for k in ["download", "file", "attachment", "weaver", "doc", "pdf", "excel", "word", "xls", "docx", "xlsx", "ppt"])

    async def _download_bytes_via_page(self, page, url: str) -> Tuple[int, dict, bytes]:
        resp = await page.request.get(url, timeout=15000)
        status = resp.status
        headers = resp.headers
        body = await resp.body()
        return status, headers, body

    def _extract_docx_text(self, data: bytes) -> str:
        try:
            from docx import Document
        except Exception:
            return ""
        from io import BytesIO
        bio = BytesIO(data)
        doc = Document(bio)
        parts = []
        for p in doc.paragraphs[:400]:
            t = (p.text or "").strip()
            if t:
                parts.append(t)
        return "\n".join(parts)

    def _extract_xlsx_text(self, data: bytes) -> str:
        try:
            from io import BytesIO
            wb = openpyxl.load_workbook(BytesIO(data), data_only=True, read_only=True)
        except Exception:
            return ""
        parts = []
        try:
            for ws in wb.worksheets[:3]:
                parts.append(f"[Sheet] {ws.title}")
                row_count = 0
                for row in ws.iter_rows(min_row=1, max_row=50, values_only=True):
                    vals = [str(v).strip() for v in row if v is not None and str(v).strip()]
                    if vals:
                        parts.append(" | ".join(vals[:20]))
                    row_count += 1
                    if row_count >= 50:
                        break
        finally:
            try:
                wb.close()
            except Exception:
                pass
        return "\n".join(parts)

    async def _extract_attachment(self, url: str, page) -> dict:
        status, headers, body = await self._download_bytes_via_page(page, url)
        content_type = headers.get("content-type", "")
        content_disposition = headers.get("content-disposition", "")
        ext = self._infer_file_ext(url, content_type=content_type, content_disposition=content_disposition)
        text = ""
        if ext == ".pdf":
            try:
                doc = fitz.open(stream=body, filetype="pdf")
                parts = []
                for p in doc:
                    parts.append(p.get_text())
                    if sum(len(x) for x in parts) > 12000:
                        break
                text = "\n".join(parts)
            except Exception:
                text = ""
        elif ext in [".docx", ".doc"]:
            text = self._extract_docx_text(body)
        elif ext in [".xlsx", ".xls"]:
            text = self._extract_xlsx_text(body)
        return {
            "url": url,
            "title": f"attachment:{ext or 'unknown'}",
            "content": (text or "")[:2000],
            "links": [],
            "status": status,
            "form_fields": [],
            "attachment": {"ext": ext, "content_type": content_type},
        }

    def _extract_attachment_from_bytes(self, url: str, status: int, headers: dict, body: bytes) -> dict:
        content_type = headers.get("content-type", "")
        content_disposition = headers.get("content-disposition", "")
        ext = self._infer_file_ext(url, content_type=content_type, content_disposition=content_disposition)
        text = ""
        if body:
            if ext == ".pdf":
                try:
                    doc = fitz.open(stream=body, filetype="pdf")
                    parts = []
                    for p in doc:
                        parts.append(p.get_text())
                        if sum(len(x) for x in parts) > 12000:
                            break
                    text = "\n".join(parts)
                except Exception:
                    text = ""
            elif ext in [".docx", ".doc"]:
                text = self._extract_docx_text(body)
            elif ext in [".xlsx", ".xls"]:
                text = self._extract_xlsx_text(body)
        return {
            "url": url,
            "title": f"attachment:{ext or 'unknown'}",
            "content": (text or "")[:2000],
            "links": [],
            "status": status,
            "form_fields": [],
            "attachment": {"ext": ext, "content_type": content_type},
        }

    async def crawl(self, url: str, depth: int, page, browser_context):
        """
        核心的递归抓取逻辑。
        """
        if depth > self.max_depth or url in self.visited:
            return

        self.visited.add(url)
        logger.info(f"正在抓取 (深度: {depth}): {url}")

        try:
            # 判断是否为 PDF
            if url.lower().endswith(".pdf"):
                text_content = await self.fetch_pdf_content(url)
                if text_content:
                    self.results.append({
                        "url": url,
                        "depth": depth,
                        "content": text_content[:2000]  # 截断
                    })
                return

            opts = self._get_goto_options(url)
            await self._goto_with_fallback(
                page,
                url,
                wait_until=opts["wait_until"],
                timeout=opts["timeout"],
                fallback_wait_until=opts["fallback_wait_until"],
                fallback_timeout=opts["fallback_timeout"],
            )
            await self._stabilize_page(page, url, depth=depth)
            content = await self._safe_page_content(page, url_hint=url)

            # 使用 BeautifulSoup 解析 HTML 文本
            soup = BeautifulSoup(content, "html.parser")

            # 提取文本内容（去除 script 和 style）
            for script in soup(["script", "style"]):
                script.extract()
            text_content = soup.get_text(separator="\n", strip=True)

            self.results.append({
                "url": url,
                "depth": depth,
                "content": text_content[:2000]  # 截断部分内容避免内存过大，实际项目中可按需保存
            })

            # 如果尚未达到最大深度，提取所有链接并递归
            if depth < self.max_depth:
                links = soup.find_all('a', href=True)
                for link in links:
                    next_url = urljoin(url, link['href'])
                    if not (self._is_oa(url) and "#/" in next_url):
                        next_url = next_url.split('#')[0]
                    if self.is_valid_url(next_url) and next_url not in self.visited:
                        # 递归调用
                        await self.crawl(next_url, depth + 1, page, browser_context)

        except Exception as e:
            logger.error(f"抓取 {url} 时出错: {e}")

    async def _safe_page_content(self, page, url_hint: str = "", retries: int = 8) -> str:
        content = ""
        last_err = None
        for attempt in range(max(1, retries)):
            try:
                content = await page.content()
                last_err = None
                break
            except Exception as e:
                last_err = e
                msg = str(e)
                try:
                    if "page is navigating" in msg or "Execution context was destroyed" in msg:
                        await page.wait_for_load_state("networkidle", timeout=8000)
                    else:
                        await page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
                try:
                    await page.wait_for_timeout(300 + attempt * 250)
                except Exception:
                    pass
                try:
                    if url_hint:
                        _ = page.url
                except Exception:
                    pass
        if last_err is not None and not content:
            raise last_err
        return content

    async def get_page_snapshot(self, url: str, lock_timeout_s: int = 30) -> dict:
        async with async_playwright() as p:
            await self._acquire_profile_lock(timeout_s=lock_timeout_s)
            context = None
            try:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=self.profile_dir,
                    headless=self.headless,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                page = context.pages[0] if context.pages else await context.new_page()
                data = await self._extract_page(url, page, depth=1)
                return data
            finally:
                if context is not None:
                    await context.close()
                self._release_profile_lock()

    async def _read_select_options_via_click(self, page, container_el, raw_value: str) -> Tuple[str, List[str]]:
        if container_el is None:
            return "", []
        click_target = await container_el.query_selector('[role="combobox"]')
        if click_target is None:
            click_target = container_el

        options: List[str] = []
        selected = ""
        try:
            await click_target.click(timeout=1000)
            await page.wait_for_selector('[role="listbox"]', timeout=800)
            option_els = await page.query_selector_all('[role="listbox"] [role="option"], [role="listbox"] option, [role="listbox"] li')
            for el in option_els:
                t = (await el.inner_text()).strip()
                if not t:
                    continue
                if t in options:
                    continue
                options.append(t)
                if len(options) >= 20:
                    break
            for el in option_els:
                sel = (await el.get_attribute("aria-selected") or "").lower()
                if sel == "true":
                    t = (await el.inner_text()).strip()
                    if t:
                        selected = t
                        break
        except Exception:
            return "", []
        finally:
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass

        if not selected and options and raw_value.isdigit():
            idx = int(raw_value)
            if 1 <= idx <= len(options):
                selected = options[idx - 1]

        return selected, options

    async def _extract_form_fields(self, page) -> Tuple[List[dict], List[str]]:
        form_fields: List[dict] = []
        field_lines: List[str] = []
        seen = set()
        interactive_attempts = 0

        inputs = await page.query_selector_all('input[id^="field"], input[name^="field"]')
        for el in inputs[:80]:
            fid = (await el.get_attribute("id")) or ""
            name = (await el.get_attribute("name")) or ""
            key = (fid or name).strip()
            if not key or key in seen:
                continue
            seen.add(key)

            etype = ((await el.get_attribute("type")) or "").lower().strip()
            raw_value = ""
            try:
                raw_value = (await el.input_value()).strip()
            except Exception:
                raw_value = ((await el.get_attribute("value")) or "").strip()

            container_handle = await el.evaluate_handle(
                """
                (el) => el.closest('[ecid*="WeaSelect"],[class*="WeaSelect"],[class*="weaSelect"],[role="combobox"]')
                   || el.closest('td')
                   || el.parentElement
                """
            )
            container_el = container_handle.as_element() if container_handle else None

            display = ""
            options: List[str] = []
            try:
                if container_el is not None:
                    display = (await container_el.inner_text()).strip()
                    option_nodes = await container_el.query_selector_all('[role="option"], option, li')
                    for n in option_nodes:
                        t = (await n.inner_text()).strip()
                        if not t or t in options:
                            continue
                        options.append(t)
                        if len(options) >= 10:
                            break
            except Exception:
                display = ""
                options = []

            selected = ""
            if options and raw_value.isdigit():
                idx = int(raw_value)
                if 1 <= idx <= len(options):
                    selected = options[idx - 1].strip()

            if not selected and (not options) and raw_value.isdigit() and container_el is not None and interactive_attempts < 15:
                interactive_attempts += 1
                selected2, options2 = await self._read_select_options_via_click(page, container_el, raw_value)
                if options2:
                    options = options2
                if selected2:
                    selected = selected2

            if not selected and options and display:
                for opt in options:
                    if opt and opt in display:
                        selected = opt
                        break

            if not selected and display:
                selected = display.splitlines()[0].strip()

            record = {"id": fid, "name": name, "type": etype, "value": raw_value, "display": display, "options": options}
            form_fields.append(record)

            if selected:
                field_lines.append(f"{key}: value={raw_value} -> {selected}")
            else:
                field_lines.append(f"{key}: value={raw_value}")

        return form_fields, field_lines

    def _is_js_void_link(self, href: str) -> bool:
        s = (href or "").strip().lower()
        if not s:
            return False
        if s in ["javascript:void(0)", "void(0)", "javascript:void(0);", "void(0);"]:
            return True
        return s.startswith("javascript:")

    def _normalize_candidate_url(self, base_url: str, candidate: str) -> str:
        c = (candidate or "").strip()
        if not c:
            return ""
        if c.startswith(("http://", "https://")):
            u = c
        elif c.startswith("//"):
            u = "https:" + c
        elif c.startswith("/"):
            u = urljoin(base_url, c)
        else:
            u = urljoin(base_url, c)
        if not (self._is_oa(base_url) and "#/" in u):
            u = u.split("#")[0]
        return u if self.is_valid_url(u) else ""

    def _extract_url_from_onclick(self, onclick: str) -> str:
        s = (onclick or "").strip()
        if not s:
            return ""
        m = re.search(r"""(?P<u>https?://[^\s'"\\)]+)""", s)
        if m:
            return m.group("u")
        m = re.search(r"""(?P<u>/[A-Za-z0-9_\-./?=&%:#]+)""", s)
        if m:
            return m.group("u")
        return ""

    def _looks_like_document_or_asset(self, url: str) -> bool:
        u = (url or "").lower()
        if any(u.endswith(ext) for ext in [".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2"]):
            return True
        return False

    async def _resolve_js_void_anchor(self, base_url: str, page, el) -> str:
        """
        对 href=javascript:void(0) 这类“假链接”做最佳努力解析：
        - 优先捕获 popup 的 url
        - 其次捕获页面导航后的 url（再尝试返回上一页）
        - 最后从点击触发的 request 里挑一个像“页面/详情”的 url
        """
        candidates: List[str] = []

        def on_request(req):
            try:
                u = req.url
            except Exception:
                return
            if not u:
                return
            if self._looks_like_document_or_asset(u):
                return
            if self.is_valid_url(u):
                candidates.append(u)

        try:
            try:
                page.on("request", on_request)
            except Exception:
                pass
            before_url = ""
            try:
                before_url = str(getattr(page, "url", "") or "")
            except Exception:
                before_url = ""
            try:
                await el.click(timeout=800)
            except Exception:
                pass
            try:
                await page.wait_for_timeout(800)
            except Exception:
                pass

            try:
                after_url = str(getattr(page, "url", "") or "")
                if self.is_valid_url(after_url) and after_url != base_url and after_url != before_url:
                    try:
                        await page.go_back(timeout=1000)
                    except Exception:
                        pass
                    return after_url
            except Exception:
                pass

            try:
                u = str(getattr(page, "url", "") or "")
                if self.is_valid_url(u) and u != base_url:
                    try:
                        await page.go_back(timeout=1000)
                    except Exception:
                        pass
                    return u
            except Exception:
                pass

            for u in candidates:
                if self.is_valid_url(u) and u != base_url:
                    return u
        finally:
            try:
                page.off("request", on_request)
            except Exception:
                try:
                    page.remove_listener("request", on_request)
                except Exception:
                    pass

        return ""

    async def _extract_anchor_links(self, base_url: str, page, *, enable_js_resolve: bool, js_resolve_limit: int) -> List[dict]:
        out: List[dict] = []
        try:
            els = await page.query_selector_all("a")
        except Exception:
            return out

        js_resolved = 0
        for el in els[:600]:
            href = ""
            text = ""
            onclick = ""
            data_url = ""
            try:
                href = (await el.get_attribute("href")) or ""
            except Exception:
                href = ""
            try:
                text = ((await el.inner_text()) or "").strip().replace("\n", " ")
            except Exception:
                text = ""
            try:
                onclick = (await el.get_attribute("onclick")) or ""
            except Exception:
                onclick = ""
            try:
                data_url = (await el.get_attribute("data-url")) or ""
            except Exception:
                data_url = ""
            if not data_url:
                try:
                    data_url = (await el.get_attribute("data-href")) or ""
                except Exception:
                    data_url = ""

            raw_href = href.strip()
            resolved = ""
            if raw_href and not self._is_js_void_link(raw_href):
                resolved = self._normalize_candidate_url(base_url, raw_href)
            else:
                candidate = data_url.strip() or self._extract_url_from_onclick(onclick)
                resolved = self._normalize_candidate_url(base_url, candidate)
                if not resolved and enable_js_resolve and js_resolved < js_resolve_limit:
                    clicked = await self._resolve_js_void_anchor(base_url, page, el)
                    if clicked:
                        resolved = clicked
                        js_resolved += 1

            item = {
                "url": resolved,
                "text": text[:80],
                "raw_url": raw_href,
                "onclick": onclick[:200],
                "resolved": bool(resolved and self._is_js_void_link(raw_href)),
            }
            out.append(item)
            if len(out) >= 400:
                break
        return out

    async def _extract_page(self, url: str, page, depth: int = 1) -> dict:
        opts = self._get_goto_options(url)
        resp = await self._goto_with_fallback(
            page,
            url,
            wait_until=opts["wait_until"],
            timeout=opts["timeout"],
            fallback_wait_until=opts["fallback_wait_until"],
            fallback_timeout=opts["fallback_timeout"],
        )
        status = None
        try:
            status = resp.status if resp else None
        except Exception:
            status = None
        await self._stabilize_page(page, url, depth=depth)
        final_url = url
        try:
            final_url = page.url or url
        except Exception:
            final_url = url
        dom_stats = {}
        try:
            dom_stats = await page.evaluate(
                """
                () => {
                  const bodyText = (document.body && document.body.innerText) ? document.body.innerText.trim() : '';
                  const docText = (document.documentElement && document.documentElement.innerText) ? document.documentElement.innerText.trim() : '';
                  return {
                    bodyTextLen: bodyText.length,
                    docTextLen: docText.length,
                    iframeCount: document.querySelectorAll('iframe').length,
                    inputCount: document.querySelectorAll('input').length,
                    aCount: document.querySelectorAll('a').length,
                  };
                }
                """
            )
            if not isinstance(dom_stats, dict):
                dom_stats = {}
        except Exception:
            dom_stats = {}
        content = ""
        last_err = None
        try:
            content = await self._safe_page_content(page, url_hint=url, retries=8)
            last_err = None
        except Exception as e:
            last_err = e
        if last_err is not None and not content:
            raise last_err
        soup = BeautifulSoup(content, "html.parser")
        for script in soup(["script", "style"]):
            script.extract()
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        text_content = soup.get_text(separator="\n", strip=True)
        if not (text_content or "").strip():
            try:
                t = await page.evaluate(
                    "() => (document.documentElement && document.documentElement.innerText) ? document.documentElement.innerText : ''"
                )
                t = (t or "").strip()
                if t:
                    text_content = t
            except Exception:
                pass
        if not (text_content or "").strip():
            try:
                t = await page.inner_text("body")
                t = (t or "").strip()
                if t:
                    text_content = t
            except Exception:
                pass
        if not (text_content or "").strip():
            try:
                for fr in page.frames:
                    if fr == page.main_frame:
                        continue
                    try:
                        t = await fr.inner_text("body")
                        t = (t or "").strip()
                        if t:
                            text_content = t
                            break
                    except Exception:
                        continue
            except Exception:
                pass
        if not (text_content or "").strip():
            try:
                await page.wait_for_timeout(1200)
                content2 = await self._safe_page_content(page, url_hint=url, retries=5)
                soup2 = BeautifulSoup(content2, "html.parser")
                for script in soup2(["script", "style"]):
                    script.extract()
                if soup2.title and soup2.title.string:
                    title = (soup2.title.string or "").strip() or title
                text_content = soup2.get_text(separator="\n", strip=True) or text_content
            except Exception:
                pass

        form_fields, field_lines = await self._extract_form_fields(page)
        if not form_fields and self._is_oa(url):
            try:
                await page.wait_for_timeout(800)
                form_fields, field_lines = await self._extract_form_fields(page)
            except Exception:
                pass

        field_section = ""
        if field_lines:
            field_section = "=== 表单字段(已解析) ===\n" + "\n".join(field_lines[:40])

        if field_section:
            text_content = f"{field_section}\n\n{text_content}"
        links = []
        seen_links = set()
        try:
            enable_js = (depth == 1 and self._is_oa(url)) or (os.environ.get("AI_DCP_RESOLVE_JS_LINKS") == "1")
            try:
                limit = int(os.environ.get("AI_DCP_RESOLVE_JS_LINKS_LIMIT") or "12")
            except Exception:
                limit = 12
            anchor_links = await self._extract_anchor_links(url, page, enable_js_resolve=enable_js, js_resolve_limit=max(0, limit))
            for it in anchor_links:
                u = (it.get("url") or "").strip()
                if u and u not in seen_links:
                    seen_links.add(u)
                    links.append(
                        {
                            "url": u,
                            "text": it.get("text", ""),
                            "raw_url": it.get("raw_url", ""),
                            "onclick": it.get("onclick", ""),
                            "resolved": bool(it.get("resolved")),
                        }
                    )
                elif (it.get("raw_url") or "").strip():
                    links.append(
                        {
                            "url": "",
                            "text": it.get("text", ""),
                            "raw_url": it.get("raw_url", ""),
                            "onclick": it.get("onclick", ""),
                            "resolved": False,
                        }
                    )
                if len(links) >= 400:
                    break
        except Exception:
            pass

        try:
            candidates = await page.evaluate(
                """
                () => {
                  const out = [];
                  const push = (el) => {
                    try {
                      const href = el.getAttribute && (el.getAttribute('href') || '');
                      const dataUrl = el.getAttribute && (el.getAttribute('data-url') || '');
                      const dataHref = el.getAttribute && (el.getAttribute('data-href') || '');
                      const onclick = el.getAttribute && (el.getAttribute('onclick') || '');
                      const text = (el.innerText || el.textContent || '').trim().replace(/\\s+/g,' ').slice(0,80);
                      if (!href && !dataUrl && !dataHref && !onclick) return;
                      out.push({href, dataUrl, dataHref, onclick, text});
                    } catch (e) {}
                  };
                  const els = Array.from(document.querySelectorAll('[data-url],[data-href],[onclick],[role="link"],button'));
                  for (const el of els.slice(0, 400)) push(el);
                  return out;
                }
                """
            )
            if isinstance(candidates, list):
                for it in candidates:
                    if not isinstance(it, dict):
                        continue
                    href = str(it.get("href") or "").strip()
                    data_url = str(it.get("dataUrl") or "").strip()
                    data_href = str(it.get("dataHref") or "").strip()
                    onclick = str(it.get("onclick") or "").strip()
                    text = str(it.get("text") or "").strip()
                    raw = href or data_url or data_href or self._extract_url_from_onclick(onclick)
                    raw = (raw or "").strip()
                    if not raw:
                        continue
                    low = raw.lower()
                    if low.startswith("javascript:") or low in ["#", "#/"]:
                        continue
                    resolved = self._normalize_candidate_url(final_url, raw)
                    if not resolved or resolved in seen_links:
                        continue
                    if self._looks_like_document_or_asset(resolved):
                        continue
                    seen_links.add(resolved)
                    links.append({"url": resolved, "text": text[:80], "raw_url": raw[:200], "onclick": onclick[:200], "resolved": True})
                    if len(links) >= 400:
                        break
        except Exception:
            pass

        for tag, attr in [("iframe", "src"), ("embed", "src"), ("object", "data")]:
            for el in soup.find_all(tag):
                val = el.get(attr)
                if not val:
                    continue
                next_url = urljoin(final_url, val)
                if not (self._is_oa(final_url) and "#/" in next_url):
                    next_url = next_url.split("#")[0]
                if not self.is_valid_url(next_url):
                    continue
                if next_url in seen_links:
                    continue
                seen_links.add(next_url)
                links.append({"url": next_url, "text": ""})
                if len(links) >= 200:
                    break
            if len(links) >= 200:
                break

        for u in self._extract_text_urls(final_url, text_content):
            if u in seen_links:
                continue
            seen_links.add(u)
            links.append({"url": u, "text": ""})
            if len(links) >= 160:
                break
        try:
            max_text_len = int(os.environ.get("AI_DCP_PAGE_TEXT_LIMIT") or "2000")
        except Exception:
            max_text_len = 2000
        if self._is_oa(final_url):
            try:
                max_text_len = int(os.environ.get("AI_DCP_OA_PAGE_TEXT_LIMIT") or "12000")
            except Exception:
                max_text_len = max(max_text_len, 12000)
        return {
            "url": final_url,
            "final_url": final_url,
            "requested_url": url,
            "title": title,
            "content": text_content[:max_text_len],
            "links": links,
            "status": status,
            "form_fields": form_fields,
            "debug": {
                "depth": depth,
                "html_len": len(content or ""),
                "text_len": len((text_content or "")),
                "dom": dom_stats,
            },
        }

    def _match_any_pattern(self, url: str, patterns: List[str]) -> bool:
        if not patterns:
            return True
        for p in patterns:
            if fnmatch.fnmatch(url, p):
                return True
        return False

    def _apply_extraction_rules(self, page: dict, rules: list) -> dict:
        result = {}
        evidence_parts = []
        text = page.get("content", "")
        for r in rules or []:
            if not isinstance(r, dict):
                continue
            name = str(r.get("name") or "").strip()
            rtype = str(r.get("type") or "").strip()
            if not name or not rtype:
                continue
            if rtype == "keyword_count":
                keywords = r.get("keywords") or []
                if isinstance(keywords, str):
                    keywords = [k.strip() for k in keywords.splitlines() if k.strip()]
                counts = {}
                total = 0
                for kw in keywords:
                    c = text.count(str(kw))
                    counts[str(kw)] = c
                    total += c
                result[name] = {"type": rtype, "total": total, "counts": counts}
                evidence_parts.append(f"{name}:hits={total}")
            elif rtype == "status_code":
                result[name] = {"type": rtype, "status": page.get("status")}
                evidence_parts.append(f"{name}:status={page.get('status')}")
        return {"data": result, "evidence": ";".join(evidence_parts)[:200]}

    async def drill_traverse(
        self,
        start_url: str,
        max_depth: int,
        include_url_patterns: List[str],
        same_domain_only: bool,
        max_pages: int,
        retries: int,
        extraction_rules: list,
    ) -> dict:
        start_parsed = urlparse(start_url)
        start_domain = start_parsed.netloc
        visited = set()
        pages = []
        failed = 0
        included = 0
        queue = [(start_url, 1, None)]

        async with async_playwright() as p:
            await self._acquire_profile_lock()
            context = None
            try:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=self.profile_dir,
                    headless=self.headless,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                page = context.pages[0] if context.pages else await context.new_page()
                while queue and len(visited) < max_pages:
                    url, depth, parent_url = queue.pop(0)
                    if depth > max_depth or url in visited:
                        continue
                    netloc = urlparse(url).netloc
                    if same_domain_only and not self._is_same_site(netloc, start_domain):
                        continue
                    visited.add(url)
                    attempt = 0
                    last_err = None
                    page_data = None
                    while attempt <= retries:
                        try:
                            ext = self._infer_file_ext(url)
                            known = ext in [".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt"]
                            if known:
                                page_data = await self._extract_attachment(url, page)
                                break

                            if self._looks_like_attachment_url(url):
                                resp = await page.request.get(url, timeout=15000)
                                status = resp.status
                                headers = resp.headers
                                ct = (headers.get("content-type") or "").lower()
                                cd = (headers.get("content-disposition") or "").lower()
                                inferred = self._infer_file_ext(url, content_type=headers.get("content-type", ""), content_disposition=headers.get("content-disposition", ""))
                                attachmentish = (
                                    inferred in [".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt"]
                                    or ("application/pdf" in ct)
                                    or ("application/octet-stream" in ct and ("attachment" in cd or inferred))
                                )
                                if attachmentish:
                                    max_bytes = 15_000_000
                                    size = headers.get("content-length")
                                    body = b""
                                    if size and str(size).isdigit() and int(size) > max_bytes:
                                        body = b""
                                    else:
                                        body = await resp.body()
                                    page_data = self._extract_attachment_from_bytes(url, status, headers, body)
                                    break

                            page_data = await self._extract_page(url, page, depth=depth)
                            break
                        except Exception as e:
                            last_err = str(e)
                            attempt += 1
                            if attempt <= retries:
                                await page.wait_for_timeout(200 + attempt * 200)
                    if page_data is None:
                        failed += 1
                        pages.append(
                            {
                                "url": url,
                                "depth": depth,
                                "parent_url": parent_url,
                                "included": False,
                                "error": last_err or "unknown",
                                "extraction": {"data": {}, "evidence": ""},
                            }
                        )
                        continue
                    should_include = self._match_any_pattern(url, include_url_patterns)
                    if depth == 1:
                        should_include = True
                    extraction = self._apply_extraction_rules(page_data, extraction_rules)
                    pages.append(
                        {
                            **page_data,
                            "depth": depth,
                            "parent_url": parent_url,
                            "included": bool(should_include),
                            "extraction": extraction,
                        }
                    )
                    if should_include:
                        included += 1
                    if depth < max_depth:
                        for link in page_data.get("links", []):
                            next_url = link.get("url")
                            if not next_url:
                                continue
                            if next_url in visited:
                                continue
                            queue.append((next_url, depth + 1, url))
            finally:
                if context is not None:
                    await context.close()
                self._release_profile_lock()

        return {
            "start_url": start_url,
            "summary": {"visited": len(visited), "included": included, "failed": failed, "max_depth": max_depth},
            "pages": pages,
        }

    async def run(self):
        """
        启动爬虫任务。
        """
        async with async_playwright() as p:
            await self._acquire_profile_lock()
            context = None
            try:
                try:
                    context = await p.chromium.launch_persistent_context(
                        user_data_dir=self.profile_dir,
                        headless=self.headless,
                        args=["--disable-blink-features=AutomationControlled"],
                    )
                except Exception as e:
                    msg = str(e)
                    if "ProcessSingleton" in msg or "SingletonLock" in msg:
                        self._cleanup_profile_singleton_files()
                        context = await p.chromium.launch_persistent_context(
                            user_data_dir=self.profile_dir,
                            headless=self.headless,
                            args=["--disable-blink-features=AutomationControlled"],
                        )
                    else:
                        raise

                page = context.pages[0] if context.pages else await context.new_page()

                if not self.headless:
                    logger.info("已开启可视化模式，请在弹出的浏览器中完成登录，登录完成后请关闭浏览器窗口...")
                    candidates = []
                    if self.start_url:
                        candidates.append(self.start_url)
                    if "oa.ksyun.com" in (self.start_url or ""):
                        candidates.append("https://oa.ksyun.com")
                        candidates.append("https://oa.ksyun.com/spa/")

                    tried = set()
                    last_err = None
                    for u in candidates:
                        u = (u or "").strip()
                        if not u or u in tried:
                            continue
                        tried.add(u)
                        try:
                            await page.goto(u, wait_until="domcontentloaded", timeout=60000)
                            last_err = None
                            break
                        except Exception as e:
                            last_err = e
                            continue

                    if last_err is not None:
                        raise last_err
                    try:
                        await page.wait_for_event("close", timeout=0)
                    except Exception as e:
                        logger.warning(f"等待浏览器关闭时遇到问题: {e}")

                    return [{"url": self.start_url, "depth": 1, "content": "授权成功，未提取内容"}]

                await self.crawl(self.start_url, depth=1, page=page, browser_context=context)
                return self.results
            finally:
                if context is not None:
                    await context.close()
                self._release_profile_lock()


# 简单的测试入口
if __name__ == "__main__":
    crawler = WebCrawler("https://example.com", max_depth=1)
    results = asyncio.run(crawler.run())
    print(f"共抓取 {len(results)} 个页面。")
