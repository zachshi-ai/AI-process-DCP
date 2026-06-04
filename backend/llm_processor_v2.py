import requests
from logger_setup import global_logger as logger
import os


class LLMProcessor:
    """
    智能处理与输出系统核心逻辑，负责调用配置的 LLM API 进行推理处理。
    """

    def __init__(self, api_token: str, base_url: str, model: str, timeout: int = 60, retry: int = 3):
        self.api_token = api_token
        self.base_url = base_url.rstrip("/")
        self.model = model
        try:
            self.timeout = int(timeout)
        except Exception:
            self.timeout = 60
        self.timeout = max(1, self.timeout)

        try:
            self.retry = int(retry)
        except Exception:
            self.retry = 3
        self.retry = max(1, self.retry)

    def _normalize_api_token(self, token: str) -> str:
        """
        规范化 API Token：
        - 去除首尾空白与换行
        - 去除常见的“全角中文括号/引号”等非 ASCII 包裹字符
        - 若仍包含非 ASCII 字符，则抛出可读性更强的错误（避免 requests 在构造请求头时触发 latin-1 编码异常）
        """
        if token is None:
            return ""
        t = str(token).strip()
        if not t:
            return ""

        remove_chars = {
            "\ufeff",
            "\u200b",
            "\u3000",
            "\uff08",
            "\uff09",
            "\u201c",
            "\u201d",
            "\u2018",
            "\u2019",
        }
        cleaned = "".join(ch for ch in t if ch not in remove_chars)
        cleaned = cleaned.strip()

        if cleaned.lower().startswith("bearer "):
            cleaned = cleaned[7:].strip()

        try:
            cleaned.encode("ascii")
        except UnicodeEncodeError as e:
            bad = cleaned[e.start:e.start + 1] if e.start is not None else ""
            raise ValueError(
                f"LLM API Token 含有非 ASCII 字符（例如：{bad!r}），会导致请求头无法编码。"
                "请在「LLM 配置」里重新粘贴纯 token（不要带中文括号/引号/备注、不要带 Bearer 前缀）。"
            )

        return cleaned

    def _build_candidate_urls(self) -> list:
        base = (self.base_url or "").strip().rstrip("/")
        if not base:
            return []

        urls: list = []

        if base.endswith("/chat/completions"):
            urls.append(base)
        elif base.endswith("/v1"):
            urls.append(f"{base}/chat/completions")
            base_no_v1 = base[:-3].rstrip("/")
            if base_no_v1:
                urls.append(f"{base_no_v1}/chat/completions")
        elif "/v1" in base:
            urls.append(f"{base}/chat/completions")
        else:
            urls.append(f"{base}/v1/chat/completions")
            urls.append(f"{base}/chat/completions")

        seen = set()
        deduped = []
        for u in urls:
            if u not in seen:
                deduped.append(u)
                seen.add(u)
        return deduped

    def _build_primary_headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _build_fallback_headers(self, token: str) -> list:
        return [
            {"Authorization": token, "Content-Type": "application/json"},
            {"X-API-Key": token, "Content-Type": "application/json"},
            {"X-Auth-Token": token, "Content-Type": "application/json"},
        ]

    def _is_auth_error(self, response) -> bool:
        if response is None:
            return False
        try:
            code = int(getattr(response, "status_code", 0) or 0)
        except Exception:
            code = 0
        return code in (401, 403)

    def _format_auth_error(self, response) -> str:
        status = ""
        try:
            status = str(int(getattr(response, "status_code", 0) or 0))
        except Exception:
            status = ""
        detail = ""
        try:
            detail = (getattr(response, "text", "") or "").strip()
        except Exception:
            detail = ""
        if len(detail) > 800:
            detail = detail[:800] + "...(truncated)"
        if status and detail:
            return f"LLM 请求失败：鉴权未通过（status={status}） | 响应详情: {detail}"
        if status:
            return f"LLM 请求失败：鉴权未通过（status={status}）"
        if detail:
            return f"LLM 请求失败：鉴权未通过 | 响应详情: {detail}"
        return "LLM 请求失败：鉴权未通过"

    def generate(self, prompt: str) -> str:
        """
        根据提供的 prompt，调用 LLM API 获取处理后的结果输出。
        此处兼容类似 OpenAI 的 API 格式，支持自定义 Base URL。
        """
        token = self._normalize_api_token(self.api_token)
        if not token:
            raise ValueError("LLM API Token 为空，请先在「LLM 配置」里填写并保存。")
        if not (self.base_url or "").strip():
            raise ValueError("LLM Base URL 为空，请先在「LLM 配置」里填写并保存。")
        if not (self.model or "").strip():
            raise ValueError("LLM 模型(model)为空，请先在「LLM 配置」里填写并保存。")

        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7
        }

        urls = self._build_candidate_urls()
        if not urls:
            raise ValueError("LLM Base URL 非法或为空，请在「LLM 配置」里检查 base_url。")

        primary_headers = self._build_primary_headers(token)
        fallback_headers = self._build_fallback_headers(token)
        enable_fallback = str(os.environ.get("AI_DCP_LLM_AUTH_FALLBACK") or "").strip().lower() in ["1", "true", "yes"]

        for attempt in range(self.retry):
            last_error_msg = ""
            try:
                for idx, url in enumerate(urls):
                    logger.info(f"第 {attempt + 1} 次请求 LLM 服务... URL({idx + 1}/{len(urls)}): {url}")
                    response = requests.post(url, headers=primary_headers, json=payload, timeout=self.timeout)
                    if 200 <= int(response.status_code or 0) < 300:
                        result_json = response.json()
                        return result_json["choices"][0]["message"]["content"]

                    status = int(getattr(response, "status_code", 0) or 0)

                    if status in (404, 405):
                        last_error_msg = f"status={status} | 响应详情: {(getattr(response, 'text', '') or '')[:800]}"
                        continue

                    if self._is_auth_error(response):
                        if not enable_fallback:
                            last_error_msg = self._format_auth_error(response)
                            continue

                        first_auth_error = self._format_auth_error(response)
                        for hdr in fallback_headers:
                            r2 = requests.post(url, headers=hdr, json=payload, timeout=self.timeout)
                            if 200 <= int(r2.status_code or 0) < 300:
                                result_json = r2.json()
                                return result_json["choices"][0]["message"]["content"]
                        last_error_msg = f"{first_auth_error}（已尝试备用鉴权头仍失败）"
                        continue

                    response.raise_for_status()

            except requests.exceptions.RequestException as e:
                last_error_msg = str(e)
                if hasattr(e, "response") and e.response is not None:
                    try:
                        last_error_msg += f" | 响应详情: {e.response.text}"
                    except Exception:
                        pass
                logger.error(f"LLM 请求失败: {last_error_msg}")
                if attempt == self.retry - 1:
                    raise Exception(f"已达到最大重试次数，LLM 响应失败。最后一次报错: {last_error_msg}")

            except UnicodeEncodeError as e:
                raise ValueError(
                    "LLM 请求在构造 HTTP 请求头时发生编码错误（latin-1），通常是 Token 或自定义 Header 含中文字符。"
                    "请重新保存 LLM 配置并确保 Token 仅包含英文/数字/常见符号。"
                ) from e

            if attempt == self.retry - 1:
                raise Exception(f"已达到最大重试次数，LLM 响应失败。最后一次报错: {last_error_msg}")

        return "未能生成结果"

