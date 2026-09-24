"""共享 HTTP 传输：带 429 限流退避重试的 GET。"""

import sys
import time
from typing import Optional

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

# GeckoTerminal 免费额度很紧（约 30 次/分钟），批量监控时容易撞 429
RATE_LIMIT_RETRIES = 4
RATE_LIMIT_BACKOFF = 3.0
RATE_LIMIT_MAX_WAIT = 60.0


def _safe_get(url: str, timeout: int = 15, **kwargs) -> Optional[dict]:
    """带错误处理的 GET 请求；遇到 429 限流会退避重试而不是直接放弃."""
    if requests is None:
        return None
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=timeout, **kwargs)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429 and attempt < RATE_LIMIT_RETRIES:
                # 服务器常回 Retry-After: 0，直接采信会导致「等 0 秒」瞬间重试完、
                # 重试形同虚设；因此以指数退避为下限，仅当服务器要求更久时才加长。
                wait = RATE_LIMIT_BACKOFF * (2 ** attempt)
                try:
                    wait = max(wait, float(resp.headers.get("Retry-After")))
                except (TypeError, ValueError):
                    pass
                wait = min(wait, RATE_LIMIT_MAX_WAIT)
                print(f"⏳ HTTP 429 限流，{wait:.0f}s 后重试 ({attempt + 1}/{RATE_LIMIT_RETRIES})",
                      file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"⚠️ HTTP {resp.status_code}: {url}", file=sys.stderr)
            return None
        except Exception as e:
            # SSL 中断 / 连接超时这类瞬时故障也应退避重试，否则一次抖动就丢一个标的
            if attempt < RATE_LIMIT_RETRIES:
                wait = RATE_LIMIT_BACKOFF * (2 ** attempt)
                print(f"⏳ 请求异常，{wait:.0f}s 后重试 ({attempt + 1}/{RATE_LIMIT_RETRIES}): {e}",
                      file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"⚠️ 请求异常: {e}", file=sys.stderr)
            return None
    return None
