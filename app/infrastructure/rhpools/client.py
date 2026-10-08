"""rhpools 观测站的进程管理与 HTTP 客户端（modules/robinhoodpools 子模块）.

上游是一个独立 Python 服务（链 4663 的 LP 池索引 + HTTP API），
默认监听 127.0.0.1:8196，索引数据落在 ~/.local/share/rhpools。
本模块负责三件事：
  1. 探活（GET /api/lp/status）
  2. 需要时拉起服务子进程（单实例、日志落 logs/rhpools.log）
  3. 统一的 JSON GET

环境变量：
  RHP_HOST / RHP_PORT   服务地址（默认 127.0.0.1:8196）
  RHP_AUTO_START        为 0/false/no 时不自动拉起（CI 里关掉）
  RHP_DATA_DIR 等       透传给上游服务（见其 --help）
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import requests

from app.core.settings import ROBINHOOD_RPC

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_DIR = REPO_ROOT / "modules" / "robinhoodpools"
SRC_DIR = MODULE_DIR / "src"
LOG_FILE = REPO_ROOT / "logs" / "rhpools.log"

_START_TIMEOUT_S = 60.0
_HEALTH_TIMEOUT_S = 2.5


def base_url() -> str:
    host = os.getenv("RHP_HOST", "127.0.0.1")
    port = os.getenv("RHP_PORT", "8196")
    return f"http://{host}:{port}"


def available() -> bool:
    """子模块代码是否就位（git submodule 已拉取）."""
    return (SRC_DIR / "rhpools" / "lp_server.py").exists()


def auto_start_enabled() -> bool:
    return os.getenv("RHP_AUTO_START", "1").strip().lower() not in {"0", "false", "no", "off"}


def health(timeout: float = _HEALTH_TIMEOUT_S) -> dict | None:
    """服务存活则返回 /api/lp/status，否则 None."""
    try:
        resp = requests.get(f"{base_url()}/api/lp/status", timeout=timeout)
        if resp.status_code == 200:
            payload = resp.json()
            return payload if isinstance(payload, dict) else None
    except Exception:
        pass
    return None


def get(path: str, params: dict | None = None, timeout: float = 15.0) -> dict | None:
    try:
        resp = requests.get(f"{base_url()}{path}", params=params, timeout=timeout)
        if resp.status_code == 200:
            payload = resp.json()
            return payload if isinstance(payload, dict) else None
        print(f"⚠️ rhpools HTTP {resp.status_code}: {path}")
    except Exception as e:
        print(f"⚠️ rhpools 请求失败 {path}: {e}")
    return None


def _spawn() -> bool:
    """后台拉起 rhpools 服务；成功返回 True（不代表已就绪，需继续探活）."""
    if not available():
        print("⏭️ modules/robinhoodpools 子模块不存在，请先 git submodule update --init")
        return False
    host = os.getenv("RHP_HOST", "127.0.0.1")
    port = os.getenv("RHP_PORT", "8196")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    if ROBINHOOD_RPC:
        env.setdefault("RHP_RPC_URL", ROBINHOOD_RPC)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        # start_new_session：脱离本进程存活；一个服务进程独占其 SQLite 库
        subprocess.Popen(
            [sys.executable, "-m", "rhpools.lp_server", "--host", host, "--port", str(port)],
            cwd=str(REPO_ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=open(LOG_FILE, "ab"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        print(f"🚀 已拉起 rhpools 服务（{host}:{port}，日志 {LOG_FILE}）")
        return True
    except Exception as e:
        print(f"❌ 拉起 rhpools 服务失败: {e}")
        return False


def ensure_running(wait_s: float = _START_TIMEOUT_S) -> dict | None:
    """确保服务可用，返回 status；不可用（未装 / 禁止自启 / 启动失败）返回 None."""
    status = health()
    if status is not None:
        return status
    if not available() or not auto_start_enabled():
        return None
    if not _spawn():
        return None
    deadline = time.time() + wait_s
    while time.time() < deadline:
        status = health()
        if status is not None:
            return status
        time.sleep(1.0)
    print(f"❌ rhpools 服务 {wait_s:.0f}s 内未就绪，详见 {LOG_FILE}")
    return None
