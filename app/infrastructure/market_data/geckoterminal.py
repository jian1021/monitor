"""GeckoTerminal 适配器：OHLCV K线（fetch_ohlcv 优先 OKX、回退 Gecko）。"""

import datetime
import sys
import time
from typing import Optional

from app.infrastructure.market_data.chains import (
    _RESOLUTION_DAYS,
    _RESOLUTION_TO_GECKO,
    _to_gecko_network,
)
from app.infrastructure.market_data.dexscreener import _resolve_pool_address
from app.infrastructure.market_data.http import _safe_get, requests
from app.infrastructure.market_data.okx import _fetch_ohlcv_okx_dex

GECKO_BASE = "https://api.geckoterminal.com/api/v2"


def fetch_ohlcv(chain: str, token_address: str, resolution: str = "1h",
                days: Optional[float] = None) -> tuple:
    """从 GeckoTerminal 拉取 OHLCV K线数据.

    参数:
      chain:       链名 (sol/bsc/base/eth/...)
      token_address: Token 合约地址
      resolution:  K线周期 (30s/1m/5m/15m/1h/4h/1d)
      days:        拉取天数 (None=按周期自动计算)

    返回:
      (t, o, h, l, c, v) — 时间标签、开高低收量 (numpy array)

    异常:
      ValueError / SystemExit (与旧 load_gmgn 行为一致)
    """
    import numpy as np

    if requests is None:
        raise ValueError("缺少 requests 库 (pip install requests)")

    # 优先 OKX DEX：一次请求即可（无需解析池子）、限流宽松；
    # 链不支持或取不到时回退下面的 GeckoTerminal 逻辑。
    okx_result = _fetch_ohlcv_okx_dex(chain, token_address, resolution, days)
    if okx_result is not None:
        return okx_result

    # 解析 resolution → GeckoTerminal timeframe + aggregate
    tf_info = _RESOLUTION_TO_GECKO.get(resolution)
    if tf_info is None:
        raise ValueError(f"不支持的 K线周期: {resolution} (可选: {', '.join(_RESOLUTION_TO_GECKO)})")
    timeframe, aggregate = tf_info

    # 计算时间范围
    bar_seconds = {"30s": 30, "1m": 60, "5m": 300, "15m": 900,
                   "1h": 3600, "4h": 14400, "1d": 86400}.get(resolution, 3600)
    window_days = float(days) if days is not None else float(
        _RESOLUTION_DAYS.get(resolution, 4.16))
    to_ts = int(time.time())
    from_ts = to_ts - int(window_days * 86400)

    # 第一步: resolve token → pool_address
    pool_address = _resolve_pool_address(chain, token_address)
    if not pool_address:
        raise SystemExit(f"未找到 {token_address[:8]}... 在 {chain} 上的交易对 (无法查询 K线)")

    # 第二步: 拉取 OHLCV
    network = _to_gecko_network(chain)
    url = (f"{GECKO_BASE}/networks/{network}/pools/{pool_address}"
           f"/ohlcv/{timeframe}?aggregate={aggregate}"
           f"&before_timestamp={to_ts}&limit=1000")
    data = _safe_get(url)

    if not data or "data" not in data:
        raise SystemExit(f"GeckoTerminal OHLCV 请求失败 (pool={pool_address[:8]}...)")

    ohlcv_list = (data.get("data") or {}).get("attributes", {}).get("ohlcv_list") or []
    if not ohlcv_list:
        raise SystemExit(f"GeckoTerminal OHLCV 返回为空 (pool={pool_address[:8]}...)")

    # 解析: 每条 [timestamp, open, high, low, close, volume]
    # 1) GeckoTerminal 返回倒序 → 反转成升序
    # 2) limit=1000 会返回超出请求窗口的旧K线 → 按 from_ts/to_ts 过滤
    # 3) Gecko 偶发返回重复时间戳 → 去重
    t, o, h, l, c, v = [], [], [], [], [], []
    prev_ts = None
    for candle in reversed(ohlcv_list):
        if len(candle) < 6:
            continue
        ts = int(candle[0])
        if ts < from_ts or ts > to_ts:
            continue
        if ts == prev_ts:
            continue
        prev_ts = ts
        t.append(datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M"))
        o.append(float(candle[1]))
        h.append(float(candle[2]))
        l.append(float(candle[3]))
        c.append(float(candle[4]))
        v.append(float(candle[5]))

    if not c:
        raise SystemExit(f"GeckoTerminal OHLCV 解析后为空 (pool={pool_address[:8]}...)")

    expected = int(window_days * 86400 / bar_seconds)
    if len(c) < expected:
        hrs = len(c) * bar_seconds / 3600
        print(f"  ⚠️ GeckoTerminal OHLCV 仅覆盖最近 {hrs:.1f} 小时"
              f" (请求窗口为 {window_days:.0f} 天)", file=sys.stderr)

    print(f"✅ 已从 GeckoTerminal 拉取 {len(c)} 根 {resolution} K线 ({chain})", file=sys.stderr)
    return t, np.array(o), np.array(h), np.array(l), np.array(c), np.array(v)
