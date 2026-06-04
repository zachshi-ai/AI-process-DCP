import os
import time
import threading
from dataclasses import dataclass
from typing import Optional, Tuple
from logger_setup import global_logger as logger


@dataclass
class RateLimitConfig:
    rpm_limit: int
    tpm_limit: int


class DualTokenBucketLimiter:
    """
    一个简单的“双桶限流器”，同时限制：
    - RPM（每分钟请求数）
    - TPM（每分钟 token 数）

    设计目标：
    - 不依赖第三方组件（Redis/Celery）
    - 线程安全（后端会用 ThreadPoolExecutor 跑后台任务）
    - 提供 try_acquire()，让调用方可以把“等待限流”状态暴露到 UI/历史记录中
    """

    def __init__(self, *, rpm_limit: int, tpm_limit: int):
        self.rpm_limit = max(0, int(rpm_limit or 0))
        self.tpm_limit = max(0, int(tpm_limit or 0))

        self._lock = threading.Lock()
        self._last_ts = time.monotonic()

        self._req_tokens = float(self.rpm_limit) if self.rpm_limit > 0 else float("inf")
        self._tok_tokens = float(self.tpm_limit) if self.tpm_limit > 0 else float("inf")

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = max(0.0, now - self._last_ts)
        self._last_ts = now

        if self.rpm_limit > 0:
            self._req_tokens = min(float(self.rpm_limit), self._req_tokens + elapsed * (float(self.rpm_limit) / 60.0))
        else:
            self._req_tokens = float("inf")

        if self.tpm_limit > 0:
            self._tok_tokens = min(float(self.tpm_limit), self._tok_tokens + elapsed * (float(self.tpm_limit) / 60.0))
        else:
            self._tok_tokens = float("inf")

    def try_acquire(self, *, estimated_tokens: int) -> Tuple[bool, float]:
        """
        尝试获取一次“LLM 调用配额”。

        返回：
        - ok: 是否立即可用
        - wait_s: 若不可用，建议等待的秒数（>=0）
        """
        need_req = 1.0
        need_tok = float(max(1, int(estimated_tokens or 1)))

        with self._lock:
            self._refill()

            has_req = self._req_tokens >= need_req
            has_tok = self._tok_tokens >= need_tok
            if has_req and has_tok:
                self._req_tokens -= need_req
                self._tok_tokens -= need_tok
                return True, 0.0

            wait_req = 0.0
            if not has_req and self.rpm_limit > 0:
                rate = float(self.rpm_limit) / 60.0
                wait_req = max(0.0, (need_req - self._req_tokens) / rate) if rate > 0 else 1.0

            wait_tok = 0.0
            if not has_tok and self.tpm_limit > 0:
                rate = float(self.tpm_limit) / 60.0
                wait_tok = max(0.0, (need_tok - self._tok_tokens) / rate) if rate > 0 else 1.0

            wait_s = max(wait_req, wait_tok)
            if not (wait_s > 0):
                wait_s = 0.2
            return False, wait_s


_GLOBAL_LIMITER: Optional[DualTokenBucketLimiter] = None


def get_global_limiter() -> DualTokenBucketLimiter:
    """
    获取全局限流器（单例）。

    环境变量：
    - AI_DCP_LLM_RPM_LIMIT：默认 500
    - AI_DCP_LLM_TPM_LIMIT：默认 1000000
    """
    global _GLOBAL_LIMITER
    if _GLOBAL_LIMITER is not None:
        return _GLOBAL_LIMITER

    rpm = int(os.environ.get("AI_DCP_LLM_RPM_LIMIT") or "500")
    tpm = int(os.environ.get("AI_DCP_LLM_TPM_LIMIT") or "1000000")
    _GLOBAL_LIMITER = DualTokenBucketLimiter(rpm_limit=rpm, tpm_limit=tpm)
    logger.info(f"LLM 限流器已启用：RPM={rpm}, TPM={tpm}")
    return _GLOBAL_LIMITER


def estimate_tokens_for_prompt(prompt: str, *, max_output_tokens: int) -> int:
    """
    估算一次调用会消耗的 token 数，用于 TPM 控制。

    说明：
    - 不引入 tiktoken 之类依赖，使用经验估算：1 token ≈ 4 字符（英文/数字更接近，中文会更复杂）
    - 用“保守估算”更安全：宁可少并发、少撞限流
    """
    s = str(prompt or "")
    in_tok = max(1, int(len(s) / 4))
    out_tok = max(1, int(max_output_tokens or 1))
    return in_tok + out_tok

