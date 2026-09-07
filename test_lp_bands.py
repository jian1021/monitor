# -*- coding: utf-8 -*-
"""lp_bands_tool GMGN 数据加载功能的测试 (TDD, 纯本地无网络)"""
import contextlib
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lp_bands_tool as tool
import numpy as np


# ---------------- helper: 假的 gmgn-cli 可执行文件 ----------------

@contextlib.contextmanager
def fake_gmgn_cli(script_body):
    d = tempfile.mkdtemp()
    binpath = os.path.join(d, 'gmgn-cli')
    with open(binpath, 'w') as f:
        f.write('#!/usr/bin/env python3\n' + script_body)
    os.chmod(binpath, 0o755)
    logpath = os.path.join(d, 'log.txt')
    old_path, old_log = os.environ.get('PATH', ''), os.environ.get('FAKE_LOG')
    os.environ['PATH'] = d + os.pathsep + old_path
    os.environ['FAKE_LOG'] = logpath
    try:
        yield d
    finally:
        os.environ['PATH'] = old_path
        if old_log is None:
            os.environ.pop('FAKE_LOG', None)
        else:
            os.environ['FAKE_LOG'] = old_log


SAMPLE_JSON = ('{"list":['
               '{"time":1700000000000,"open":"100.0","high":"101.0","low":"99.0",'
               '"close":"100.5","volume":"1000.0","amount":"10"},'
               '{"time":1700003600000,"open":"100.5","high":"102.0","low":"100.0",'
               '"close":"101.0","volume":"2000.0","amount":"20"}]}')


# ---------------- 纯解析函数 ----------------

def test_parse_gmgn_kline_full():
    t, o, h, l, c, v = tool.parse_gmgn_kline(SAMPLE_JSON)
    assert len(t) == 2
    assert list(c) == [100.5, 101.0]
    assert list(o) == [100.0, 100.5]
    assert list(h) == [101.0, 102.0]
    assert list(l) == [99.0, 100.0]
    assert list(v) == [1000.0, 2000.0]          # volume 是 USD 数值
    assert re.match(r'\d{2}-\d{2} \d{2}:\d{2}', t[0])  # time(ms) → 可读标签


def test_parse_gmgn_kline_empty_list():
    t, o, h, l, c, v = tool.parse_gmgn_kline('{"list":[]}')
    assert len(c) == 0


def test_parse_gmgn_kline_missing_volume():
    text = '{"list":[{"time":1700000000000,"open":"1","high":"2","low":"0.5","close":"1.5"}]}'
    t, o, h, l, c, v = tool.parse_gmgn_kline(text)
    assert list(v) == [0.0]


# ---------------- load_gmgn: subprocess 端到端 (mock CLI) ----------------

def test_load_gmgn_end_to_end():
    body = (
        'import sys,os\n'
        'open(os.environ["FAKE_LOG"],"a").write("|".join(sys.argv[1:])+"\\n")\n'
        'if sys.argv[1:3] == ["market","kline"]:\n'
        '    print(%r)\n    sys.exit(0)\n'
        'sys.exit(1)\n' % SAMPLE_JSON
    )
    with fake_gmgn_cli(body):
        t, o, h, l, c, v = tool.load_gmgn('sol', 'FAKEADDR', '1h', days=1)
        logpath = os.environ['FAKE_LOG']
    assert len(c) == 2 and list(c) == [100.5, 101.0]
    with open(logpath) as f:
        argv = f.read().strip().split('|')
    assert argv[:2] == ['market', 'kline']
    assert '--chain' in argv and argv[argv.index('--chain') + 1] == 'sol'
    assert '--address' in argv and argv[argv.index('--address') + 1] == 'FAKEADDR'
    assert '--resolution' in argv and argv[argv.index('--resolution') + 1] == '1h'
    assert '--from' in argv and '--to' in argv and '--raw' in argv
    i_from, i_to = argv.index('--from'), argv.index('--to')
    assert int(argv[i_from + 1]) < int(argv[i_to + 1])


def test_load_gmgn_empty_result_raises():
    body = 'import sys\nprint(\'{"list":[]}\')\nsys.exit(0)\n'
    with fake_gmgn_cli(body):
        try:
            tool.load_gmgn('sol', 'FAKEADDR', '1h', days=1)
            assert False, '应当抛出 SystemExit'
        except SystemExit as e:
            assert '空 K 线' in str(e)


def test_load_gmgn_too_many_bars_raises():
    candles = ','.join(
        '{"time":%d000,"open":"1","high":"1.1","low":"0.9","close":"1","volume":"1"}' % (1700000000 + i * 3600)
        for i in range(4)
    )
    body = 'import sys\nprint(\'{"list":[%s]}\')\nsys.exit(0)\n' % candles
    old_max = tool.MAX_BARS
    tool.MAX_BARS = 3
    try:
        with fake_gmgn_cli(body):
            try:
                tool.load_gmgn('sol', 'FAKEADDR', '1h', days=1)
                assert False, '应当抛出 SystemExit'
            except SystemExit as e:
                assert '上限' in str(e)
    finally:
        tool.MAX_BARS = old_max


def test_load_gmgn_cap_warning():
    candles = ','.join(
        '{"time":%d000,"open":"1","high":"1.1","low":"0.9","close":"1","volume":"1"}'
        % (1700000000 + i * 3600) for i in range(100)
    )
    body = 'import sys\nprint(\'{"list":[%s]}\')\nsys.exit(0)\n' % candles
    import io
    buf = io.StringIO()
    with fake_gmgn_cli(body), contextlib.redirect_stderr(buf):
        tool.load_gmgn('sol', 'FAKEADDR', '1h', days=30)
    assert '上限' in buf.getvalue()


def test_load_gmgn_days_default_matches_cap():
    assert tool.gmgn_resolution_days('1h') <= 5  # 1h×5天=120根, 贴合 100 根单次上限


# ---------------- build_chart_df 纯函数 ----------------

def test_build_chart_df_basic():
    from lp_bands_chart import build_chart_df
    n = 600
    t = [str(k) for k in range(n)]
    c = np.linspace(100, 120, n)
    h = c + 1.0
    l = c - 1.0
    bands = [
        (0, 200, 125.0, 95.0, 110.0),
        (200, 400, 130.0, 100.0, 115.0),
        (400, 600, 135.0, 105.0, 120.0),
    ]
    df = build_chart_df(t, c, h, l, bands)

    assert len(df) == n
    assert list(df.columns) == ['time', 'close', 'seg', 'level', 'upper', 'lower', 'in_range']
    assert df['seg'].is_monotonic_increasing
    assert set(df['in_range'].unique()).issubset({0, 1})
    assert df['seg'].nunique() == 3
    assert df['seg'].iloc[0] == 0 and df['seg'].iloc[199] == 0
    assert df['seg'].iloc[200] == 1 and df['seg'].iloc[399] == 1
    assert df['seg'].iloc[400] == 2 and df['seg'].iloc[599] == 2


def test_build_chart_df_empty_bands():
    from lp_bands_chart import build_chart_df
    t = ['0', '1', '2']
    c = np.array([1.0, 2.0, 3.0])
    h = np.array([1.5, 2.5, 3.5])
    l = np.array([0.5, 1.5, 2.5])
    df = build_chart_df(t, c, h, l, [])

    assert len(df) == 0
    assert list(df.columns) == ['time', 'close', 'seg', 'level', 'upper', 'lower', 'in_range']


def test_build_chart_df_in_range_convention():
    from lp_bands_chart import build_chart_df
    t = ['0', '1', '2']
    c = np.array([100.0, 100.0, 100.0])
    h = np.array([105.0, 110.0, 105.0])
    l = np.array([95.0, 95.0, 90.0])
    bands = [(0, 3, 108.0, 92.0, 100.0)]
    df = build_chart_df(t, c, h, l, bands)

    assert list(df['in_range']) == [1, 0, 0]


# ---------------- 运行 ----------------

def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f'PASS  {fn.__name__}')
        except AssertionError as e:
            failed += 1
            print(f'FAIL  {fn.__name__}: {e}')
    print(f'\n{len(tests) - failed}/{len(tests)} passed')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()