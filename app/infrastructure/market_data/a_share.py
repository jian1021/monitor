"""A 股 / 场内 ETF 实时行情适配（腾讯财经公开接口）。

链上代币走 dexscreener，A 股没有合约地址，只能按交易所代码查询，
所以单独放在这里，由 price 监控按 asset_type 分流。

接口返回 GBK 编码的 `v_sz159967="51~创业板成长ETF华夏~159967~0.451~..."`，
字段以 `~` 分隔，第 2 位是名称、第 4 位是当前价（下标 1 / 3）。
"""
import requests

_HEADERS = {"User-Agent": "Mozilla/5.0"}
QUOTE_URL = "https://qt.gtimg.cn/q={market_code}"


def to_market_code(code: str) -> str:
    """6 位代码补市场前缀。

    沪市：5/6 开头（ETF / 股票）、11 开头（可转债 110/111/113/118）；
    深市：0/3 开头（股票）、15/16 开头（ETF/LOF）、12 开头（可转债 123/127/128）。
    """
    raw = str(code or "").strip()
    if raw[:2].lower() in ("sh", "sz"):
        return raw.lower()
    if len(raw) != 6 or not raw.isdigit():
        raise ValueError(f"无法识别的 A 股代码: {code!r}")
    if raw[0] in "56" or raw[:2] == "11":
        return "sh" + raw
    return "sz" + raw


def is_convertible_bond(code: str) -> bool:
    """可转债代码：沪市 110/111/113/118，深市 123/127/128。"""
    raw = str(code or "").strip()[-6:]
    return len(raw) == 6 and raw.isdigit() and raw[:3] in {
        "110", "111", "113", "118", "123", "127", "128"}


def fetch_realtime_price(code: str):
    """拉取最新价，返回 (price, name)；失败返回 (None, None) 或 (None, name)。

    停牌 / 未开盘时接口给出的当前价为 0，此时认为无有效报价。
    """
    name = None
    try:
        market_code = to_market_code(code)
        resp = requests.get(QUOTE_URL.format(market_code=market_code),
                            headers=_HEADERS, timeout=10)
        resp.encoding = "gbk"
        text = resp.text or ""
        if '="' not in text:
            print(f"⚠️ A 股行情 [{code}] 返回格式异常: {text[:80]}")
            return None, None
        parts = text.split('="', 1)[1].rstrip('";\n').split("~")
        if len(parts) < 5:
            print(f"⚠️ A 股行情 [{code}] 字段不足: {len(parts)}")
            return None, None
        name = parts[1].strip() or None
        price = float(parts[3])
        if price <= 0:
            print(f"⚠️ A 股行情 [{code}] 无有效报价（停牌或未开盘）")
            return None, name
        return price, name
    except Exception as exc:
        print(f"❌ A 股行情 [{code}] 获取失败: {exc}")
        return None, name
