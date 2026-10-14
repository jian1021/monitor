"""
通用 HTTP 客户端：超时、限频、重试、错误处理。

限频采用“最小请求间隔”令牌方式（线程安全），并在收到 429 时遵循 Retry-After。
所有对外部 API 的访问都必须经过本客户端，统一记录错误。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """API 调用失败。"""

    def __init__(self, message: str, *, url: str = "", status: Optional[int] = None):
        super().__init__(message)
        self.url = url
        self.status = status


class RateLimiter:
    """线程安全的最小间隔限流器。"""

    def __init__(self, requests_per_second: float):
        self._min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> None:
        if self._min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_allowed = now + self._min_interval


class HttpClient:
    """带限频与重试的 JSON HTTP 客户端。"""

    def __init__(
        self,
        *,
        base_url: str,
        requests_per_second: float,
        timeout: float = 30.0,
        max_retries: int = 4,
        user_agent: str = "MeteoraPoolScanner/3.1",
        headers: Optional[dict[str, str]] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.limiter = RateLimiter(requests_per_second)
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": user_agent})
        if headers:
            self.session.headers.update(headers)
        self.request_count = 0
        self.error_count = 0

    def get_json(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
        *,
        full_url: Optional[str] = None,
    ) -> Any:
        """发起 GET 请求并返回解析后的 JSON。失败抛出 ApiError。"""
        url = full_url or f"{self.base_url}{path}"
        last_err: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            self.limiter.acquire()
            try:
                self.request_count += 1
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_err = exc
                logger.warning("请求失败(%s/%s) %s: %s", attempt, self.max_retries, url, exc)
                self._sleep_backoff(attempt)
                continue
            except requests.RequestException as exc:
                self.error_count += 1
                raise ApiError(f"请求异常: {exc}", url=url) from exc

            if resp.status_code == 429:
                retry_after = self._parse_retry_after(resp)
                logger.warning("触发限频(429) %s，等待 %.1fs", url, retry_after)
                time.sleep(retry_after)
                last_err = ApiError("HTTP 429 限频", url=url, status=429)
                continue

            if 500 <= resp.status_code < 600:
                last_err = ApiError(f"HTTP {resp.status_code}", url=url, status=resp.status_code)
                logger.warning("服务端错误(%s/%s) %s: %s", attempt, self.max_retries, url, resp.status_code)
                self._sleep_backoff(attempt)
                continue

            if resp.status_code >= 400:
                self.error_count += 1
                raise ApiError(
                    f"HTTP {resp.status_code}: {resp.text[:200]}",
                    url=url,
                    status=resp.status_code,
                )

            try:
                return resp.json()
            except ValueError as exc:
                self.error_count += 1
                raise ApiError(f"响应不是合法 JSON: {exc}", url=url) from exc

        self.error_count += 1
        raise ApiError(f"重试 {self.max_retries} 次后仍失败: {last_err}", url=url)

    @staticmethod
    def _parse_retry_after(resp: requests.Response) -> float:
        raw = resp.headers.get("Retry-After")
        if raw:
            try:
                return max(1.0, float(raw))
            except ValueError:
                pass
        return 5.0

    def _sleep_backoff(self, attempt: int) -> None:
        time.sleep(min(30.0, 1.5 ** attempt))
