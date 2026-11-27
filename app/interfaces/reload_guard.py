"""Streamlit 页面模块热更新守卫。

Streamlit 每次交互只重跑「页面脚本」，已导入的 `app.*` / `lprange.*` 模块会
常驻 sys.modules。Cloud 推送部署后页面文件已是新版、模块还是旧版：新增符号会
ImportError，改了实现则「看起来没生效」。页面在从模块取名字之前调用
`reload_if_changed(mod)`，按源文件 mtime 判断是否需要原地重载。

只用于无导入副作用的纯展示/工具模块；不要对会建表、起线程的模块使用。
"""
from __future__ import annotations

import importlib
import os
import time
from types import ModuleType


def reload_if_changed(mod: ModuleType) -> None:
    """源文件比上次加载时更新就 reload（原地更新，模块对象身份不变）。"""
    src = getattr(mod, "__file__", None)
    if not src or not os.path.exists(src):
        return
    if os.path.getmtime(src) <= getattr(mod, "_ui_loaded_at", 0.0):
        return
    importlib.reload(mod)
    mod._ui_loaded_at = time.time()


def fresh_timefmt():
    """取当前磁盘版本的 `to_local_display`（页面顶部一行接入即可）。

    页面不要写 `from app.interfaces.timefmt import to_local_display`——那是值拷贝，
    Cloud 推送部署后拿到的仍是旧函数。
    """
    from app.interfaces import timefmt

    reload_if_changed(timefmt)
    return timefmt.to_local_display
