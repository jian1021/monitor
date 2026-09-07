#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lp_bands_tool.py — 流动池自适应上下区间生成器
流程: 外部输入OHLCV → BOCPD分段 → ATR宽度边界 → (可选)段起点盈亏预估
仅依赖 numpy。用法见文件末尾或 --help。

数据源:
  --file <csv>        本地CSV (time,open,high,low,close,volume)
  --input             手动键入
  --token <地址>      自动从 GMGN 拉取K线 (需先配置 gmgn-cli API Key)
"""
import argparse, csv, sys, os, json, subprocess, time, datetime
from math import lgamma, log, pi, sqrt, exp
import numpy as np

# ---------------- 数据输入 ----------------

def load_csv(path):
    """支持带表头(time/open/high/low/close/volume, 不区分大小写)或无表头(按此顺序)"""
    rows = []
    with open(path, newline='', encoding='utf-8-sig') as f:
        rdr = csv.reader(f)
        rows = [r for r in rdr if r and not r[0].startswith('#')]
    header = None
    if rows and any(not _isnum(cell) for cell in rows[0]):
        header = [c.strip().lower() for c in rows[0]]; rows = rows[1:]
    if header:
        def col(*names):
            for n in names:
                if n in header: return header.index(n)
            return None
        i_t  = col('time','date','datetime','ts')
        i_o, i_h, i_l, i_c, i_v = col('open'), col('high'), col('low'), col('close'), col('volume','vol')
    else:
        i_t, i_o, i_h, i_l, i_c, i_v = (0,1,2,3,4,5) if len(rows[0])>=6 else (None,None,None,None,0,None)
        if len(rows[0])==2: i_c = 1
    t,o,h,l,c,v = [],[],[],[],[],[]
    for k,r in enumerate(rows):
        try:
            c.append(float(r[i_c]))
            t.append(r[i_t] if i_t is not None else str(k))
            if i_o is not None and i_o < len(r) and _isnum(r[i_o]):
                o.append(float(r[i_o])); h.append(float(r[i_h])); l.append(float(r[i_l]))
                v.append(float(r[i_v]) if i_v is not None and i_v < len(r) and _isnum(r[i_v]) else 0.0)
            else:  # 只有收盘价 → 合成OHLCV
                o.append(c[-2] if len(c)>1 else c[-1])
                h.append(max(o[-1],c[-1])); l.append(min(o[-1],c[-1])); v.append(0.0)
        except (ValueError, IndexError):
            continue
    return t, np.array(o), np.array(h), np.array(l), np.array(c), np.array(v)

def _isnum(s):
    try: float(s); return True
    except (ValueError, TypeError): return False

def load_interactive():
    """手动键入: 每行一条, 格式 [时间] 收盘价 或 时间,开,高,低,收,量 ; 空行结束"""
    print("请输入K线数据, 每行一条。格式: 时间,开盘,最高,最低,收盘,成交量", file=sys.stderr)
    print("（或只输: 收盘；输空行结束）", file=sys.stderr)
    lines = []
    while True:
        try: line = input().strip()
        except EOFError: break
        if not line: break
        lines.append(line)
    t,o,h,l,c,v = [],[],[],[],[],[]
    for k,line in enumerate(lines):
        parts = [p.strip() for p in line.replace(',', ' ').split() if p.strip()]
        nums = [p for p in parts if _isnum(p)]
        if not nums: continue
        lead = []
        for p in parts:
            if _isnum(p): break
            lead.append(p)
        t.append(' '.join(lead) if lead else str(k))
        if len(nums) >= 5:
            o_,h_,l_,c_,v_ = float(nums[-5]),float(nums[-4]),float(nums[-3]),float(nums[-2]),float(nums[-1])
        else:
            c_ = float(nums[-1]); o_ = c[-1] if c else c_
            h_ = max(o_,c_); l_ = min(o_,c_); v_ = 0.0
        o.append(o_); h.append(h_); l.append(l_); c.append(c_); v.append(v_)
    return t, np.array(o), np.array(h), np.array(l), np.array(c), np.array(v)

def load_demo():
    rng = np.random.default_rng(7)
    n = 600; c = np.empty(n)
    c[:150] = 100 + np.cumsum(rng.normal(0.05,0.5,150))
    c[150:300] = c[149] + np.cumsum(rng.normal(0.0,0.6,150))
    c[300:] = c[299] + 25 + np.cumsum(rng.normal(-0.02,0.8,300))
    c[450:] += np.linspace(0,-15,150)
    o = np.concatenate([[c[0]], c[:-1]])
    wk = np.abs(rng.normal(0,0.35,n))
    h = np.maximum(o,c)+wk; l = np.minimum(o,c)-wk
    v = 1000*(1+40*np.abs(np.diff(c,prepend=c[0]))/c)*rng.lognormal(0,0.4,n); v[300]*=6
    return [str(k) for k in range(n)], o,h,l,c,v

# ---------------- GMGN 数据拉取 ----------------

RESOLUTION_DAYS = {'30s': 0.034, '1m': 0.069, '5m': 0.34, '15m': 1.04, '1h': 4.16, '4h': 16.6, '1d': 100}
MAX_BARS = 5000
_BAR_SECONDS = {'30s': 30, '1m': 60, '5m': 300, '15m': 900, '1h': 3600, '4h': 14400, '1d': 86400}

def gmgn_resolution_days(resolution):
    return RESOLUTION_DAYS.get(resolution, 30)

def _gmgn_cli(args_list, timeout=120):
    try:
        r = subprocess.run(['gmgn-cli'] + args_list, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        sys.exit('未找到 gmgn-cli: 请先运行 npm install -g gmgn-cli')
    except subprocess.TimeoutExpired:
        sys.exit(f'gmgn-cli 超时({timeout}s): {" ".join(args_list)}')

def gmgn_config_check():
    code, out, err = _gmgn_cli(['config', '--check'])
    if code == 0:
        return
    print('⚠️ GMGN 未配置 API Key: 请打开下面链接创建, 然后把 Key 发给我配置', file=sys.stderr)
    _, out2, _ = _gmgn_cli(['config'])
    print(out2, file=sys.stderr)
    sys.exit(2)

def parse_gmgn_kline(text):
    """gmgn-cli market kline --raw 的 JSON → (t,o,h,l,c,v)。volume 字段是 USD 成交额"""
    data = json.loads(text)
    lst = data.get('list') or []
    t, o, h, l, c, v = [], [], [], [], [], []
    for k in lst:
        ts_ms = int(k['time'])
        t.append(datetime.datetime.fromtimestamp(ts_ms / 1000.0).strftime('%m-%d %H:%M'))
        o.append(float(k['open'])); h.append(float(k['high']))
        l.append(float(k['low']));  c.append(float(k['close']))
        v.append(float(k.get('volume') or 0.0))
    return t, np.array(o), np.array(h), np.array(l), np.array(c), np.array(v)

def load_gmgn(chain, address, resolution, days=None):
    if days is None:
        days = gmgn_resolution_days(resolution)
    to_ts = int(time.time())
    from_ts = to_ts - int(days * 86400)
    code, out, err = _gmgn_cli(['market', 'kline', '--chain', chain, '--address', address,
                                '--resolution', resolution, '--from', str(from_ts),
                                '--to', str(to_ts), '--raw'])
    if code != 0:
        sys.exit(f'gmgn-cli kline 失败:\n{err or out}')
    try:
        t, o, h, l, c, v = parse_gmgn_kline(out)
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        sys.exit(f'解析 gmgn-cli 输出失败 ({e}): {out[:300]}')
    if len(c) == 0:
        sys.exit('GMGN 返回空 K 线 (检查 --address / --chain / --resolution)')
    if len(c) > MAX_BARS:
        sys.exit(f'K线数量 {len(c)} 超过上限 {MAX_BARS} (BOCPD 是 O(T²), 请用 --days 缩短窗口)')
    expected = int(days * 86400 / _BAR_SECONDS.get(resolution, 3600))
    if len(c) < expected:
        hrs = len(c) * _BAR_SECONDS.get(resolution, 3600) / 3600
        print(f'  ⚠️ GMGN kline 单次返回上限 100 根: 实际仅覆盖最近 {hrs:.1f} 小时'
              f' (请求窗口为 {days:.0f} 天)', file=sys.stderr)
    print(f'✅ 已从 GMGN 拉取 {len(c)} 根 {resolution} K线 ({chain})', file=sys.stderr)
    return t, o, h, l, c, v

# ---------------- BOCPD 分段 (numpy-only) ----------------

def _student_t_logpdf(x, df, mu, scale):
    lg = np.array([lgamma((d+1)/2)-lgamma(d/2) for d in df])
    z = (x-mu)/scale
    return lg - 0.5*np.log(df*pi) - 0.5*(df+1)*np.log1p(z*z/df) - np.log(scale)

def bocpd_segments(x, lam=120, kappa0=0.5, alpha0=1.0):
    """返回 [(start_idx, end_idx, level)], 完全因果(只用<=起点处的数据)"""
    T = len(x); mu0 = x[0]; beta0 = np.var(x[:50]) + 1e-6
    cs = np.concatenate([[0.0], np.cumsum(x)]); cs2 = np.concatenate([[0.0], np.cumsum(x**2)])
    R = np.zeros((T+1, T+1)); R[0,0] = 1.0
    starts, levels = [0], [x[0]]; prev_level = x[0]; prev_map = 0; H = 1.0/lam
    for tt in range(1, T+1):
        nn = np.arange(1, tt+1)
        sx = cs[tt]-cs[tt-nn]; sx2 = cs2[tt]-cs2[tt-nn]; xbar = sx/nn
        kappa = kappa0+nn; mu = (kappa0*mu0+sx)/kappa
        alpha = alpha0+nn/2
        beta = beta0 + 0.5*(sx2-sx**2/nn) + kappa0*nn*(xbar-mu0)**2/(2*kappa)
        scale = np.sqrt(beta*(kappa+1)/(alpha*kappa))
        lp = _student_t_logpdf(x[tt-1], 2*alpha, mu, scale)
        pred = np.exp(np.maximum(lp, -700))
        grow = R[tt-1,:tt]*pred*(1-H); cpm = np.sum(R[tt-1,:tt]*pred*H)
        R[tt,0] = cpm; R[tt,1:tt+1] = grow; R[tt] /= R[tt].sum()
        r_map = int(np.argmax(R[tt]))
        if r_map < prev_map:
            s = tt-1-r_map
            if s > starts[-1]:
                starts.append(s)
                nl = (kappa0*prev_level + x[tt-1])/(kappa0+1)   # 用坍缩当刻的价格, 不取可能偏早的x[s]
                levels.append(nl); prev_level = nl
        prev_map = r_map
    return list(zip(starts, starts[1:]+[T], levels))

# ---------------- 边界与盈亏预估 ----------------

def build_bands(close, high, low, segs, lam=120, width_mult=2.0, atr_win=14):
    """每段: upper/lower = exp(level ± width_mult * ATR_log * sqrt(lam)/1.5)"""
    cp = np.log(np.maximum(close,1e-12)); pc = np.roll(cp,1); pc[0]=cp[0]
    tr = np.maximum(np.log(np.maximum(high,1e-12)/np.maximum(low,1e-12)),
                    np.maximum(np.abs(np.log(np.maximum(high,1e-12)/np.exp(pc))), np.abs(np.log(np.maximum(low,1e-12)/np.exp(pc)))))
    out = []
    for s,e,L in segs:
        seg = tr[max(0,s-atr_win):s+1]; atr = seg.mean() if len(seg) else tr[0]
        d = width_mult*atr*sqrt(lam)/1.5
        out.append((s,e,float(np.exp(L+d)),float(np.exp(L-d)),float(np.exp(L))))
    return out

def pnl_estimate(p0, d, sigma_h, T_bar, V_h, fee, cap, cost_rate, mu=0.0, npaths=5000, seed=1):
    """段起点蒙特卡洛盈亏预估, 返回 dict"""
    rng = np.random.default_rng(seed)
    a,b = p0*exp(-d), p0*exp(d); sa,sb = sqrt(a),sqrt(b)
    ell = cap/((1/sqrt(p0)-1/sb)*p0 + (sqrt(p0)-sa))
    x0 = ell*(1/sqrt(p0)-1/sb); y0 = ell*(sqrt(p0)-sa)
    z = rng.standard_normal((npaths,int(T_bar)))
    logp = np.cumsum(mu - 0.5*sigma_h**2 + sigma_h*z, axis=1)
    out = np.abs(logp) >= d
    eidx = np.where(out.any(axis=1), out.argmax(axis=1), int(T_bar)-1)
    hours = eidx+1
    pe = p0*np.exp(logp[np.arange(npaths),eidx])
    px = np.clip(pe,a,b)
    xe = ell*(1/np.sqrt(px)-1/sb); ye = ell*(np.sqrt(px)-sa)
    vlp = np.where(pe<a, xe*pe, np.where(pe>b, ye, xe*pe+ye))
    fees = fee*V_h*hours; cost = 2*cost_rate*cap
    net = vlp + fees - cap - cost
    return dict(E_net=float(net.mean()), P_loss=float((net<0).mean()),
                P5=float(np.percentile(net,5)), E_fees=float(fees.mean()),
                touch=float((hours<T_bar).mean()))

# ---------------- 主流程 ----------------

def main():
    ap = argparse.ArgumentParser(description='流动池自适应上下区间生成器 (BOCPD + ATR边界 + 盈亏预估)')
    src = ap.add_mutually_exclusive_group()
    src.add_argument('--file', help='CSV文件路径 (列: time,open,high,low,close,volume; 可省略表头/部分列)')
    src.add_argument('--token', metavar='ADDR', help='GMGN token 合约地址 (自动拉取K线, 需已配置 API Key)')
    src.add_argument('--input', action='store_true', help='手动键入数据(空行结束)')
    ap.add_argument('--demo', action='store_true', help='使用内置演示数据')
    ap.add_argument('--chain', default='sol', help='GMGN 链: sol/bsc/base/eth/robinhood/arc/stable, 默认 sol')
    ap.add_argument('--resolution', default='1h', help='GMGN K线周期: 30s/1m/5m/15m/1h/4h/1d, 默认 1h')
    ap.add_argument('--days', type=float, help='GMGN 拉取天数 (默认按周期取满 API 的 100 根上限)')
    ap.add_argument('--lam', type=float, default=120, help='BOCPD期望段长(根K线), 默认120')
    ap.add_argument('--width', type=float, default=2.0, help='边界宽度系数C, 默认2.0')
    ap.add_argument('--estimate-pnl', action='store_true', help='在每段起点做盈亏预估')
    ap.add_argument('--fee', type=float, default=0.003, help='池子费率, 默认0.003')
    ap.add_argument('--capital', type=float, default=10000.0, help='预估本金, 默认10000')
    ap.add_argument('--cost', type=float, default=0.001, help='单边再平衡成本率, 默认0.001')
    ap.add_argument('--volume-scale', type=float, default=1.0, help='成交量单位换算(预估用), 默认1')
    ap.add_argument('--out', help='输出逐K线的区间CSV路径')
    args = ap.parse_args()

    if args.file:   t,o,h,l,c,v = load_csv(args.file)
    elif args.token: gmgn_config_check(); t,o,h,l,c,v = load_gmgn(args.chain, args.token, args.resolution, args.days)
    elif args.input: t,o,h,l,c,v = load_interactive()
    else:            t,o,h,l,c,v = load_demo(); print('（使用内置演示数据; --file/--token/--input 可接外部数据）\n')

    if len(c) < 10:
        print(f'✗ 数据太少: 仅 {len(c)} 根 K 线, 至少需要 10 根。', file=sys.stderr)
        if args.token:
            print('  该代币可能刚上线(历史K线不足): 尝试 --resolution 5m/1m, '
                  '或换一个交易更久的代币。', file=sys.stderr)
        sys.exit(1)
    logc = np.log(np.maximum(c,1e-12))
    segs = bocpd_segments(logc, lam=args.lam)
    bands = build_bands(c,h,l,segs,lam=args.lam,width_mult=args.width)

    print(f'共 {len(bands)} 段 (期望段长 lam={args.lam}, 宽度系数 C={args.width})\n')
    hdr = f'{"#":>3} {"起点t_k":>8} {"终点":>6} {"中枢L":>10} {"上界":>10} {"下界":>10}'
    if args.estimate_pnl: hdr += f' {"E[净盈亏]":>10} {"P(亏)":>7} {"P5":>9} {"触界率":>7}'
    print(hdr)
    sig_h = None
    for k,(s,e,u,lo,L) in enumerate(bands):
        row = f'{k:>3} {t[s]:>8} {t[e-1]:>6} {L:>10.3f} {u:>10.3f} {lo:>10.3f}'
        if args.estimate_pnl:
            if sig_h is None:  # 用全样本对数收益率std作每小时sigma的朴素估计
                sig_h = float(np.std(np.diff(logc))*sqrt(args.lam)/sqrt(args.lam))  # 每根K线sigma
            V_h = float(np.mean(v))*args.volume_scale if np.mean(v)>0 else 0.0
            d_ = log(u/L)
            r = pnl_estimate(L, d_, sig_h, args.lam, V_h, args.fee, args.capital, args.cost)
            row += f' {r["E_net"]:>10.0f} {r["P_loss"]:>7.0%} {r["P5"]:>9.0f} {r["touch"]:>7.0%}'
        print(row)

    if args.out:
        with open(args.out,'w',newline='') as f:
            w = csv.writer(f); w.writerow(['time','close','seg','level','upper','lower','in_range'])
            for k,(s,e,u,lo,L) in enumerate(bands):
                for j in range(s,e):
                    w.writerow([t[j], f'{c[j]:.6f}', k, f'{L:.6f}', f'{u:.6f}', f'{lo:.6f}',
                                int(l[j]>lo and h[j]<u)])
        print(f'\n逐K线区间已写入: {args.out}')

if __name__ == '__main__':
    main()
