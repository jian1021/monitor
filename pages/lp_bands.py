# -*- coding: utf-8 -*-
"""pages/lp_bands.py — LP 自适应区间可视化 (Streamlit + plotly)"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import lp_bands_tool as tool
from lp_bands_chart import build_chart_df

CHAIN_OPTS = ['sol', 'bsc', 'base', 'eth', 'robinhood', 'arc', 'stable']
RES_OPTS = ['30s', '1m', '5m', '15m', '1h', '4h', '1d']

st.set_page_config(page_title='LP 区间可视化', layout='wide')

with st.sidebar:
    st.header('参数')
    token = st.text_input('Token 地址', placeholder='So11111111111111111111111111111111111111112')
    chain = st.selectbox('链', CHAIN_OPTS, index=0)
    res = st.selectbox('K线周期', RES_OPTS, index=4)
    days = st.number_input('天数 (0=按周期自动取满100根)', min_value=0.0, value=0.0, step=1.0)
    lam = st.number_input('lam · 期望段长', min_value=10.0, value=120.0, step=10.0)
    width = st.number_input('宽度系数 C', min_value=0.1, value=2.0, step=0.1)
    run = st.button('生成区间', type='primary', use_container_width=True)

st.title('LP 自适应区间可视化')

if not run:
    st.info('左侧输入 Token 地址后点击「生成区间」')
    st.stop()
if not token.strip():
    st.error('请输入 Token 合约地址')
    st.stop()

try:
    if days and days > 0:
        t, o, h, l, c, v = tool.load_gmgn(chain, token.strip(), res, days)
    else:
        t, o, h, l, c, v = tool.load_gmgn(chain, token.strip(), res)
except (SystemExit, ValueError) as e:
    st.error(str(e))
    st.stop()

if len(c) < 10:
    st.error(f'数据太少: 仅 {len(c)} 根 K 线。该代币可能刚上线 — 尝试更细周期 (5m/1m) 或换更成熟的代币。')
    st.stop()

lam = int(lam)

logc = np.log(np.maximum(c, 1e-12))
segs = tool.bocpd_segments(logc, lam=lam)
bands = tool.build_bands(c, h, l, segs, lam=lam, width_mult=float(width))
df = build_chart_df(t, c, h, l, bands)
if df.empty:
    st.error('BOCPD 未分出有效段，请调整 lam 参数后重试')
    st.stop()

m1, m2, m3 = st.columns(3)
m1.metric('分段数', len(df['seg'].unique()))
m2.metric('K线根数', len(df))
m3.metric('区间内占比', f"{df['in_range'].mean():.1%}")

fig = go.Figure()
fig.add_trace(go.Candlestick(x=df['time'], open=o, high=h, low=l, close=c, name='K线',
                             increasing=dict(line=dict(color='#26a69a', width=1), fillcolor='#26a69a'),
                             decreasing=dict(line=dict(color='#ef5350', width=1), fillcolor='#ef5350'),
                             whiskerwidth=0.5))
fig.add_trace(go.Scatter(x=df['time'], y=df['upper'], mode='lines', name='上界',
                         line=dict(color='#e57373', width=1.2, dash='dot')))
fig.add_trace(go.Scatter(x=df['time'], y=df['lower'], mode='lines', name='下界',
                         line=dict(color='#81c784', width=1.2, dash='dot')))
fill_colors = ['rgba(255,255,255,0.04)', 'rgba(255,255,255,0.08)']
for k in sorted(int(x) for x in df['seg'].unique()):
    seg_df = df.iloc[df['seg'].to_numpy() == k]
    if len(seg_df) >= 2 and pd.notna(seg_df['upper'].iloc[0]):
        fig.add_vrect(xref='x',
                      x0=seg_df['time'].iloc[0], x1=seg_df['time'].iloc[-1],
                      fillcolor=fill_colors[int(k) % 2], line_width=0, layer='below')
fig.update_yaxes(type='log', title='价格 (log)')
fig.update_xaxes(title='时间')
fig.update_layout(height=520, margin=dict(l=10, r=10, t=30, b=10),
                  hovermode='x unified', template='plotly_dark')
st.plotly_chart(fig, use_container_width=True)

rows = []
for k in sorted(int(x) for x in df['seg'].unique()):
    s = df.iloc[df['seg'].to_numpy() == k]
    if s['upper'].isna().all():
        continue
    r = s.iloc[0]
    rows.append({'段': k,
                 '起点': r['time'], '终点': s.iloc[-1]['time'],
                 '中枢': round(float(r['level']), 6),
                 '上界': round(float(r['upper']), 6),
                 '下界': round(float(r['lower']), 6),
                 'in_range 占比': f"{s['in_range'].mean():.0%}"})
st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)