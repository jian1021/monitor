"""gmgn-cli 可执行文件定位与子进程环境构建（跨平台）.

背景: 本项目价格数据来自 GMGN OpenAPI, 命令行工具为 gmgn-cli (npm 全局包).
- Windows 上 npm 全局安装的是 .cmd 垫片, 必须经由 cmd.exe (shell=True) 派发;
- Linux/macOS 上是无扩展名脚本, shell=False 即可直接运行;
- Streamlit / 服务进程的 PATH 可能不完整 (npm 全局目录不在其中),
  本模块负责在常见位置定位 gmgn-cli, 并给子进程补全 PATH 与代理环境。

用法:
    from gmgn_cli import build_env, get_gmgn_cli, GMGN_CLI_MISSING_MSG
    cli = get_gmgn_cli()
    if not cli:
        print(GMGN_CLI_MISSING_MSG)   # 或返回带该文案的错误
        ...
    proc = subprocess.run([cli, "token", ...], env=build_env(), shell=os.name == "nt")
"""
import os
import shutil
import subprocess

GMGN_CLI_MISSING_MSG = (
    "未找到 gmgn-cli 可执行文件。请先安装:\n"
    "  1) 确保 Node.js 已安装 (node --version)\n"
    "  2) 全局安装 GMGN 命令行:  npm install -g gmgn-cli\n"
    "  3) 若已安装仍找不到, 可用环境变量 GMGN_CLI_PATH 指定完整路径"
)


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
    """构造子进程环境: 注入 GMGN 代理 + 补全 PATH(npm 全局 bin / node 目录)."""
    env = dict(os.environ)
    proxy = os.getenv("GMGN_PROXY", "http://127.0.0.1:7897")
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