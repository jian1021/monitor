# -*- coding: utf-8 -*-
"""lp_bands_chart.py — LP 区间可视化的纯数据层 (numpy+pandas, 无 streamlit 依赖)"""
import numpy as np
import pandas as pd


def build_chart_df(t, c, h, l, bands):
    """把 bands=[(start,end,upper,lower,level)] 映射到逐 K 线 DataFrame。

    列: time/close/seg/level/upper/lower/in_range
    in_range 沿用 lp_bands_tool 约定: 整根K线(低>=下界 且 高<=上界) 内为 1
    """
    n = len(c)
    if not bands:
        return pd.DataFrame(columns=['time', 'close', 'seg', 'level', 'upper', 'lower', 'in_range'])
    seg_of = np.full(n, -1, dtype=int)
    lo_a = np.full(n, np.nan)
    up_a = np.full(n, np.nan)
    lv_a = np.full(n, np.nan)
    for k, (s, e, u, lo, L) in enumerate(bands):
        seg_of[s:e] = k
        lo_a[s:e] = lo
        up_a[s:e] = u
        lv_a[s:e] = L
    inr = np.where((np.asarray(l) > lo_a) & (np.asarray(h) < up_a), 1, 0).astype(int)
    return pd.DataFrame({
        'time': t, 'close': c, 'seg': seg_of,
        'level': lv_a, 'upper': up_a, 'lower': lo_a, 'in_range': inr,
    })