"""robinhood-chain-lp-tools CLI 运行器与输出解析（Node/tsx 子模块）.

上游是两个 TypeScript 脚本：
  uni-range-apr.ts    秒级估算给定区间的 APR（volume feed + 当前流动性）
  uni-range-replay.ts 精确回放窗口内每笔 Swap（分钟级，实测）
输出为人类可读文本，这里用正则提取关键指标，解析函数保持纯函数便于测试。

环境变量：
  ROBINHOOD_RPC   非空时透传为 ROBINHOOD_RPC_URL（默认走公共 RPC）
"""

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from app.core.settings import ROBINHOOD_RPC

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOLS_DIR = REPO_ROOT / "modules" / "robinhood-chain-lp-tools"
APR_SCRIPT = TOOLS_DIR / "src" / "uni-range-apr.ts"
REPLAY_SCRIPT = TOOLS_DIR / "src" / "uni-range-replay.ts"
TSX_BIN = TOOLS_DIR / "node_modules" / ".bin" / "tsx"

APR_TIMEOUT_S = 300.0      # 估算：秒级（含 RPC/Gecko 请求）
REPLAY_TIMEOUT_S = 900.0   # 回放：README 说 1~4 分钟，繁忙池给足余量
INSTALL_TIMEOUT_S = 600.0

_PCT = r"([-+]?\d+(?:\.\d+)?)%"


def tools_available() -> bool:
    """子模块代码是否就位."""
    return APR_SCRIPT.exists() and REPLAY_SCRIPT.exists()


def node_available() -> bool:
    return shutil.which("node") is not None


def deps_installed() -> bool:
    return TSX_BIN.exists()


def ensure_installed() -> bool:
    """首次使用时执行 npm install；成功或已安装返回 True."""
    if not tools_available() or not node_available():
        return False
    if deps_installed():
        return True
    npm = shutil.which("npm")
    if not npm:
        print("❌ 找不到 npm，请先安装 Node.js")
        return False
    print("📦 首次运行，正在安装 robinhood-chain-lp-tools 依赖（npm install）...")
    try:
        subprocess.run(
            [npm, "install", "--no-audit", "--no-fund"],
            cwd=str(TOOLS_DIR), timeout=INSTALL_TIMEOUT_S,
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        )
    except Exception as e:
        print(f"❌ npm install 失败: {e}")
        return False
    if not deps_installed():
        print(f"❌ npm install 后仍找不到 {TSX_BIN}")
        return False
    return True


def ready() -> bool:
    return tools_available() and node_available() and deps_installed()


def _run(script: Path, args: list[str], timeout: float) -> tuple[bool, str]:
    """执行脚本，返回 (ok, stdout+stderr 文本)."""
    if not ensure_installed():
        return False, ""
    env = dict(os.environ)
    if ROBINHOOD_RPC:
        env["ROBINHOOD_RPC_URL"] = ROBINHOOD_RPC
    try:
        proc = subprocess.run(
            [str(TSX_BIN), str(script), *args],
            cwd=str(TOOLS_DIR), env=env, timeout=timeout,
            capture_output=True, text=True,
        )
    except subprocess.TimeoutExpired:
        return False, f"超时（{timeout:.0f}s）"
    except Exception as e:
        return False, str(e)
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        return False, out.strip()
    return True, out


def run_apr(pool: str, width: float | None = None, capital: float = 10000,
            window: str = "h24", sweep: bool = False, measure: bool = False,
            timeout: float = APR_TIMEOUT_S) -> tuple[bool, dict | str]:
    """跑一次 APR 估算；成功返回解析后的 dict，失败返回错误文本."""
    args = ["--pool", pool.strip(), "--capital", str(capital), "--window", window]
    if sweep:
        args.append("--sweep")
    else:
        args += ["--width", str(width if width is not None else 10)]
    if measure:
        args.append("--measure")
    ok, text = _run(APR_SCRIPT, args, timeout)
    if not ok:
        return False, text or "工具执行失败（无输出）"
    parsed = parse_apr(text)
    if sweep:
        parsed["sweep"] = parse_sweep(text)
    if not parsed:
        return False, f"输出无法解析：\n{text[-2000:]}"
    parsed["raw"] = text
    return True, parsed


def run_replay(pool: str, width: float | None = None, capital: float = 10000,
               hours: int = 24, validate: bool = True,
               timeout: float = REPLAY_TIMEOUT_S) -> tuple[bool, dict | str]:
    """跑一次精确回放；成功返回解析后的 dict，失败返回错误文本."""
    args = ["--pool", pool.strip(), "--capital", str(capital),
            "--hours", str(hours), "--width", str(width if width is not None else 10)]
    if validate:
        args.append("--validate")
    ok, text = _run(REPLAY_SCRIPT, args, timeout)
    if not ok:
        return False, text or "工具执行失败（无输出）"
    parsed = parse_replay(text)
    if not parsed:
        return False, f"输出无法解析：\n{text[-2000:]}"
    parsed["raw"] = text
    return True, parsed


# ================= 输出解析（纯函数，test_lp_apr_parser.py 覆盖） =================

def _pct(text: str, label: str, flags=re.M) -> float | None:
    m = re.search(rf"^{re.escape(label)}\s+{_PCT}", text, flags)
    return float(m.group(1)) / 100 if m else None


def _usd_to_float(token: str) -> float | None:
    """'$12.23M' / '$5.6k' / '$29.32' → 数值；n/a → None."""
    token = token.strip()
    if token in {"n/a", "-"}:
        return None
    mult = 1.0
    if token.endswith(("M", "k")):
        mult = 1e6 if token.endswith("M") else 1e3
        token = token[:-1]
    try:
        return float(token.lstrip("$")) * mult
    except ValueError:
        return None


def parse_apr(text: str) -> dict:
    """uni-range-apr 单区间模式的文本 → 指标 dict（解析不到的部分不出现）."""
    out: dict = {}
    m = re.search(r"^=== (.+?)\s+\(Uniswap (v[34]),", text, re.M)
    if m:
        out["name"], out["version"] = m.group(1).strip(), m.group(2)
    m = re.search(r"^pool\s+(0x[0-9a-fA-F]+)", text, re.M)
    if m:
        out["pool"] = m.group(1)
    m = re.search(r"^tokens\s+(.+)$", text, re.M)
    if m:
        out["tokens"] = m.group(1).strip()
    m = re.search(r"^fee\s+([\d.]+)%(.*)$", text, re.M)
    if m:
        out["fee_pct"] = float(m.group(1))
        m2 = re.search(r"LPs keep " + _PCT, m.group(2))
        if m2:
            out["lp_fee_share"] = float(m2.group(1)) / 100
        out["dynamic_fee"] = "DYNAMIC" in m.group(2)
    for label, key in (
        ("pool-average APR", "pool_avg_apr"),
        ("APR while in range", "apr_in_range"),
        ("APR in range (measured)", "apr_in_range_measured"),
        ("vol-adjusted APR", "vol_adjusted_apr"),
        ("time in range", "time_in_range_modelled"),
    ):
        value = _pct(text, label)
        if value is not None:
            out[key] = value
    m = re.search(r"->\s+" + _PCT + r" of active liquidity", text)
    if m:
        out["liquidity_share"] = float(m.group(1)) / 100
    m = re.search(r"^fees/day in range\s+(\S+)", text, re.M)
    if m:
        out["fees_per_day_usd"] = _usd_to_float(m.group(1))
    m = re.search(r"\(([\d.]+)x the pool-average APR\)", text)
    if m:
        out["vs_pool_avg"] = float(m.group(1))
    if "UNUSABLE" in text:
        out["volume_method"] = "unusable_dynamic_fee"
    return out


def parse_sweep(text: str) -> list[dict]:
    """--sweep 表格 → 每行一个 dict（width/share/apr/...）."""
    rows = []
    pattern = re.compile(
        r"^\+/-\s*(\d+)%\s+"          # width
        r"(\S+)\s+-\s+(\S+)\s+"       # low - high
        r"(\S+)\s+"                   # share
        r"(\S+)\s+"                   # APR in-range
        r"(\S+)x\s+"                  # x pool avg
        r"(\S+)\s+"                   # in-range horizon
        r"(\S+)\s*$",                 # vol-adj APR
        re.M,
    )
    for m in pattern.finditer(text):
        def pct_or_none(s: str) -> float | None:
            return None if s == "n/a" else (float(s[:-1]) / 100 if s.endswith("%") else None)
        rows.append({
            "width_pct": int(m.group(1)),
            "low": m.group(2),
            "high": m.group(3),
            "share": pct_or_none(m.group(4)),
            "apr_in_range": pct_or_none(m.group(5)),
            "x_pool_avg": None if m.group(6) == "n/a" else float(m.group(6)),
            "time_in_range": pct_or_none(m.group(7)),
            "vol_adjusted_apr": pct_or_none(m.group(8)),
        })
    return rows


def parse_replay(text: str) -> dict:
    """uni-range-replay 的文本 → 指标 dict."""
    out: dict = {}
    m = re.search(r"^=== (.+?)\s+\(Uniswap (v[34]),", text, re.M)
    if m:
        out["name"], out["version"] = m.group(1).strip(), m.group(2)
    m = re.search(r"^pool\s+(0x[0-9a-fA-F]+)", text, re.M)
    if m:
        out["pool"] = m.group(1)
    m = re.search(r"^window\s+last (\d+)h\s+\(blocks ([\d,]+) - ([\d,]+)\)", text, re.M)
    if m:
        out["hours"] = int(m.group(1))
        out["from_block"] = int(m.group(2).replace(",", ""))
        out["to_block"] = int(m.group(3).replace(",", ""))
    m = re.search(r"^swaps\s+([\d,]+)\s+\(([\d,]+) inside", text, re.M)
    if m:
        out["swaps"] = int(m.group(1).replace(",", ""))
        out["swaps_in_range"] = int(m.group(2).replace(",", ""))
    m = re.search(r"^volume \(in-legs\)\s+(\S+)", text, re.M)
    if m:
        out["volume_usd"] = _usd_to_float(m.group(1))
    m = re.search(r"^LP fees, whole pool\s+(\S+)", text, re.M)
    if m:
        out["pool_fees_usd"] = _usd_to_float(m.group(1))
    value = _pct(text, "time in range")
    if value is not None:
        out["time_in_range"] = value
    m = re.search(r"^fees earned\s+(\S+)", text, re.M)
    if m:
        out["fees_earned_usd"] = _usd_to_float(m.group(1))
    m = re.search(r"^return\s+" + _PCT + r" over (\d+)h", text, re.M)
    if m:
        out["return_pct"] = float(m.group(1)) / 100
        out["hours_returned"] = int(m.group(2))
    value = _pct(text, "annualised APR")
    if value is not None:
        out["annualised_apr"] = value
    m = re.search(r"^share of pool fees\s+(n/a|" + _PCT + r")", text, re.M)
    if m:
        out["share_of_pool_fees"] = None if m.group(1) == "n/a" else float(m.group(1)[:-1]) / 100
    ratios = [float(x) for x in re.findall(r"ratio\s+([\d.]+)", text)]
    if ratios:
        out["validation_ratios"] = ratios
    if "POOL ACCRUED NOTHING" in text or "HOOK is taking the fee" in text:
        out["validation_warning"] = True
    return out
