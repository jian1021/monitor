# -*- coding: utf-8 -*-
"""回归：dex_client 已拆分为 app/infrastructure/market_data/* 适配器.

历史 bug：模块删除后调用方仍 `from dex_client import ...`，导致
  - 链上代币 RSI 后台监控每轮都抛 ModuleNotFoundError（被兜底吞掉 → 永不告警）
  - LP 区间可视化的 K 线取数永久失败
  - OKX 交易对搜索被 ImportError 静默降级为不可用
本文件既验证转发生效，也防止今后再引入指向已删除模块的导入。
"""

import pathlib
import re
from unittest.mock import patch

import numpy as np

from app.application.monitors import rsi as rsi_mod


def _fake_ohlcv(closes):
    arr = np.array(closes, dtype=float)
    return ([str(index) for index in range(len(arr))],
            arr, arr, arr, arr, arr)


def test_onchain_token_rsi_uses_geckoterminal_adapter():
    payload = _fake_ohlcv(range(1, 40))
    with patch("app.infrastructure.market_data.geckoterminal.fetch_ohlcv",
               return_value=payload) as fetch:
        rsi, price = rsi_mod.get_token_rsi("sol", "token-addr", "1h", 3, 5)

    fetch.assert_called_once_with("sol", "token-addr", "1h", 5)
    assert rsi is not None
    assert price is not None


def test_lp_bands_tool_uses_geckoterminal_adapter():
    from app.application.lp_bands import tool

    payload = _fake_ohlcv([1.0, 2.0, 3.0])
    with patch("app.infrastructure.market_data.geckoterminal.fetch_ohlcv",
               return_value=payload) as fetch:
        out = tool.load_gmgn("sol", "addr", "1h")

    fetch.assert_called_once_with("sol", "addr", "1h", None)
    assert out == payload


def test_no_source_imports_the_removed_dex_client_module():
    root = pathlib.Path(__file__).resolve().parent.parent
    pattern = re.compile(r"^\s*(?:from|import)\s+dex_client\b", re.MULTILINE)
    candidates = [
        *root.glob("app/**/*.py"),
        *root.glob("pages/**/*.py"),
        *root.glob("scripts/**/*.py"),
        root / "main.py",
        root / "index.py",
    ]
    offenders = [
        str(path.relative_to(root))
        for path in candidates
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
