"""轻量冒烟测试（无框架）: python3 test_imports.py"""
import inspect
import sys

failures = []


def check(name, cond):
    if cond:
        print(f"✅ {name}")
    else:
        failures.append(name)
        print(f"❌ {name}")


from db import load_instruments, get_db_client

check("db.load_instruments 可导入", callable(load_instruments))
check("db.get_db_client 可导入", callable(get_db_client))

import main

check("main 模块可正常导入", main is not None)
check("main.load_instruments 已正确导入", hasattr(main, "load_instruments"))

import monitor_meteora_pump as mmp

src = inspect.getsource(mmp)
check(
    "run_pump_strategy_monitor 定义唯一",
    src.count("def run_pump_strategy_monitor") == 1,
)
check(
    "fetch_tokens_by_strategy 定义唯一",
    src.count("def fetch_tokens_by_strategy") == 1,
)

import pages.app  # noqa: F401

check("pages.app 可正常导入", True)

if failures:
    print(f"\n共 {len(failures)} 项失败: {failures}")
    sys.exit(1)
print("\n✅ 全部通过")