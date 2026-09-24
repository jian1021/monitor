"""链名 / K线周期映射。"""

# GMGN 链名 → Dexscreener chainId
_CHAIN_TO_DEXSCREENER = {
    "sol": "solana",
    "bsc": "bsc",
    "base": "base",
    "eth": "ethereum",
    "robinhood": "robinhood",
    "arc": "arc",
    "stable": "stable",
}

# GMGN 链名 → GeckoTerminal network id
_CHAIN_TO_GECKO = {
    "sol": "solana",
    "bsc": "bsc",
    "base": "base",
    "eth": "eth",
    "robinhood": "robinhood",
    "arc": "arc",
    "stable": "stable",
}

# GMGN K线周期 → GeckoTerminal (timeframe, aggregate)
_RESOLUTION_TO_GECKO = {
    "30s": ("minute", 1),   # GeckoTerminal 无 30s，降级到 1m
    "1m":  ("minute", 1),
    "5m":  ("minute", 5),
    "15m": ("minute", 15),
    "1h":  ("hour", 1),
    "4h":  ("hour", 4),
    "1d":  ("day", 1),
}

# 未显式指定 days 时，按周期估算取数窗口（1d 默认 100 天，与历史行为一致）
_RESOLUTION_DAYS = {
    "30s": 0.034, "1m": 0.069, "5m": 0.34, "15m": 1.04,
    "1h": 4.16, "4h": 16.6, "1d": 100,
}


def _to_dexscreener_chain(chain: str) -> str:
    return _CHAIN_TO_DEXSCREENER.get(chain.lower().strip(), chain.lower().strip())


def _to_gecko_network(chain: str) -> str:
    return _CHAIN_TO_GECKO.get(chain.lower().strip(), chain.lower().strip())
