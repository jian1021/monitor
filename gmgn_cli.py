"""gmgn-cli 定位、子进程环境构建与 GMGN 数据拉取（跨平台 / 免安装兜底）.

背景: 本项目价格数据来自 GMGN OpenAPI, 命令行工具为 gmgn-cli (npm 全局包).
- Windows 上 npm 全局安装的是 .cmd 垫片, 必须经由 cmd.exe (shell=True) 派发;
- Linux/macOS 上是无扩展名脚本, shell=False 即可直接运行;
- Streamlit / 服务进程的 PATH 可能不完整 (npm 全局目录不在其中),
  本模块负责在常见位置定位 gmgn-cli, 并给子进程补全 PATH 与代理环境;
- 目标主机可能完全没有 node/npm (云部署 / VPS / CI 运行器), 此时自动降级为
  纯 Python 直连 GMGN OpenAPI (exist-auth: X-APIKEY + timestamp + client_id)。

用法:
    from gmgn_cli import fetch_token_info
    data, source = fetch_token_info("sol", EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v)
    # source: "cli" / "api" 成功; data 与 OpenAPI /v1/token/info 的 data 对象同构
    # 失败: (None, 错误提示字符串)
"""
import json
import os
import shutil
import socket
import subprocess
import time
import uuid

GMGN_OPENAPI_HOST = "https://openapi.gmgn.ai"
DEFAULT_PROXY = "http://127.0.0.1:7897"
GMGN_CLI_MISSING_MSG = (
    "未找到 gmgn-cli 可执行文件。请先安装:\n"
    "  1) 确保 Node.js 已安装 (node --version)\n"
    "  2) 全局安装 GMGN 命令行:  npm install -g gmgn-cli\n"
    "  3) 若已安装仍找不到, 可用环境变量 GMGN_CLI_PATH 指定完整路径"
)

# 代理探测结果缓存 {checked: bool, url: str|None}
_PROXY_CACHE = {}


def _is_port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _proxy_url():
    """返回当前应使用的 HTTP(S) 代理 URL; 无需代理时返回 None.

    规则:
      - GMGN_PROXY 显式非空(且非 direct/off/0/none) -> 采用
      - GMGN_PROXY 为 direct/off/0/none            -> 强制直连
      - 未设置 -> 探测本机 127.0.0.1:7897 是否可达, 可达才用默认代理
        (仅在开发机本地代理存活时走代理, 部署机/CI 没有该端口则直连)
    """
    explicit = (os.getenv("GMGN_PROXY") or "").strip()
    if explicit:
        if explicit.lower() in ("direct", "off", "0", "none"):
            return None
        return explicit
    if not _PROXY_CACHE.get("checked"):
        _PROXY_CACHE["checked"] = True
        _PROXY_CACHE["url"] = DEFAULT_PROXY if _is_port_open("127.0.0.1", 7897) else None
    return _PROXY_CACHE.get("url")


def _npm_global_prefix():
    """查询 npm prefix -g 获取全局安装前缀; 失败返回 None."""
    try:
        proc = subprocess.run(
            ["npm", "prefix", "-g"], capture_output=True, text=True, timeout=15
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    prefix = proc.stdout.strip().strip('"').strip("'")
    return prefix or None


def get_gmgn_cli():
    """定位 gmgn-cli 可执行文件, 返回绝对路径; 未找到返回 None.

    查找顺序:
      1) 环境变量 GMGN_CLI_PATH (显式指定)
      2) Windows: %APPDATA%\\npm\\gmgn-cli.cmd (npm 全局默认目录)
      3) 当前 PATH 常规查找 (shutil.which)
      4) npm prefix -g 动态定位
      5) 常见 Linux / nvm 全局目录兜底
    """
    env_path = os.environ.get("GMGN_CLI_PATH")
    if env_path:
        p = env_path.strip().strip('"')
        if os.path.exists(p):
            return p

    if os.name == "nt":
        cand = os.path.expandvars(r"%APPDATA%\npm\gmgn-cli.cmd")
        if os.path.exists(cand):
            return cand

    found = shutil.which("gmgn-cli")
    if found:
        if os.name == "nt":
            # which 可能命中无扩展名的 bash 垫片, cmd.exe 无法直接派发,
            # 改指同目录的 .cmd 版本。
            if not found.lower().endswith((".cmd", ".exe", ".bat")):
                alt = found + ".cmd"
                if os.path.exists(alt):
                    return alt
        return found

    prefix = _npm_global_prefix()
    if prefix:
        for name in ("gmgn-cli", "gmgn-cli.cmd"):
            cand = os.path.join(prefix, name)
            if os.path.exists(cand):
                return cand

    for cand in (
        "/usr/local/bin/gmgn-cli",
        "/usr/bin/gmgn-cli",
        os.path.expanduser("~/.npm-global/bin/gmgn-cli"),
        os.path.expanduser("~/.local/bin/gmgn-cli"),
        os.path.expanduser("~/node_modules/.bin/gmgn-cli"),
    ):
        if os.path.exists(cand):
            return cand
    return None


def build_env():
    """构造子进程环境: 按需注入代理 + 补全 PATH(npm 全局 bin / node 目录)."""
    env = dict(os.environ)
    proxy = _proxy_url()
    if proxy:
        env.setdefault("HTTPS_PROXY", proxy)
        env.setdefault("HTTP_PROXY", proxy)

    extra = []
    if os.name == "nt":
        extra.append(os.path.expandvars(r"%APPDATA%\npm"))
    node = shutil.which("node")
    if node:
        node_dir = os.path.dirname(node)
        if node_dir not in extra:
            extra.append(node_dir)
    if extra:
        existing = (env.get("PATH") or "").split(os.pathsep)
        merged = extra + [p for p in existing if p and p not in extra]
        env["PATH"] = os.pathsep.join(merged)
    return env


def _load_gmgn_api_key():
    """读取 GMGN API key: 环境变量 GMGN_API_KEY 优先, 其次 ~/.config/gmgn/.env.

    该位置与 gmgn-cli 一致 (gmgn-cli config --apply 写入的 .env)。
    """
    key = (os.getenv("GMGN_API_KEY") or "").strip()
    if key:
        return key
    base = os.getenv("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    env_path = os.path.join(base, "gmgn", ".env")
    try:
        with open(env_path, "r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == "GMGN_API_KEY":
                    return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _parse_json_output(stdout):
    """宽容解析 gmgn-cli 输出中的 JSON 对象 (容忍提示行 / 前缀)."""
    text = (stdout or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for line in text.splitlines():
        if line.strip().startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def fetch_token_info_via_api(chain, address):
    """直连 GMGN OpenAPI /v1/token/info (exist-auth, 无需 gmgn-cli).

    exist-auth: 请求参数带 timestamp(Unix秒) + client_id(UUID), header 带 X-APIKEY。

    返回:
      (data_dict, None)  成功
      (None, error)      失败原因
    """
    api_key = _load_gmgn_api_key()
    if not api_key:
        return None, "未配置 GMGN_API_KEY（环境变量或 ~/.config/gmgn/.env）"
    try:
        import requests
    except ImportError:
        return None, "环境缺少 requests 库（见 requirements.txt）"
    params = {
        "chain": chain,
        "address": address.strip(),
        "timestamp": str(int(time.time())),
        "client_id": str(uuid.uuid4()),
    }
    headers = {
        "X-APIKEY": api_key,
        "Content-Type": "application/json",
        "User-Agent": "gmgn-cli/python-fallback/1",
    }
    proxies = None
    proxy = _proxy_url()
    if proxy:
        proxies = {"http": proxy, "https": proxy}
    try:
        resp = requests.get(
            f"{GMGN_OPENAPI_HOST}/v1/token/info",
            params=params, headers=headers, proxies=proxies, timeout=20,
        )
    except requests.RequestException as e:
        return None, f"GMGN OpenAPI 请求失败: {e}"
    try:
        payload = resp.json()
    except ValueError:
        return None, f"GMGN OpenAPI 非 JSON 响应 (HTTP {resp.status_code})"
    if resp.status_code != 200 or payload.get("code") != 0:
        msg = payload.get("message") or payload.get("error") or f"HTTP {resp.status_code}"
        return None, f"GMGN OpenAPI 调用失败: {msg}"
    data = payload.get("data")
    if not isinstance(data, dict):
        return None, "GMGN OpenAPI 响应缺少 data 对象"
    return data, None


def _fetch_token_info_via_cli(cli_path, chain, address):
    """经 gmgn-cli token info --raw 拉取 data dict.

    返回:
      (data_dict, None)  成功
      (None, error)      失败原因
    """
    cmd = [cli_path, "token", "info", "--chain", chain, "--address", address.strip(), "--raw"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            env=build_env(), shell=os.name == "nt",
        )
    except Exception as e:
        return None, f"gmgn-cli 调用失败: {e}"
    if proc.returncode != 0:
        err = (proc.stderr.strip() or proc.stdout.strip())[:300] or f"退出码 {proc.returncode}"
        return None, f"gmgn-cli 返回码 {proc.returncode}: {err}"
    data = _parse_json_output(proc.stdout)
    if data is None:
        return None, f"无法解析 gmgn-cli 输出: {proc.stdout[:200]!r}"
    return data, None


def fetch_token_info(chain, address, *, fallback_to_api=True):
    """统一拉取 GMGN token 数据（应用内唯一入口）.

    优先 gmgn-cli; 缺失或调用失败时自动降级为纯 Python 直连 OpenAPI。

    返回:
      (data_dict, "cli")  成功 - data 与 OpenAPI /v1/token/info 的 data 对象同构
      (data_dict, "api")  成功 - 直连 OpenAPI (目标主机无需 gmgn-cli / node)
      (None, 错误提示)    所有路径均失败
    """
    cli_path = get_gmgn_cli()
    errors = []
    if cli_path:
        data, err = _fetch_token_info_via_cli(cli_path, chain, address)
        if data is not None:
            return data, "cli"
        errors.append(err or "gmgn-cli 调用失败")
    else:
        errors.append(GMGN_CLI_MISSING_MSG)
    if fallback_to_api:
        data, err = fetch_token_info_via_api(chain, address)
        if data is not None:
            return data, "api"
        errors.append(err or "OpenAPI 直连失败")
    return None, "；".join(f"({i + 1}) {e}" for i, e in enumerate(errors, start=1))