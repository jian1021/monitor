"""数据库时间戳展示：库里统一存 UTC（`datetime('now')`），页面按本机时区展示。

SQLite/Turso 的 `datetime('now')` 返回 UTC，不带时区标记。若直接把字符串丢到
表格里，北京时间会显示成比实际早 8 小时，看起来像「告警没发出去」。
"""

from datetime import datetime, timezone

_PARSERS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def to_local_display(value, fmt: str = "%Y-%m-%d %H:%M", fallback: str = "-") -> str:
    """把 UTC 时间串转成本机时区；无法解析时原样返回，空值返回 fallback。

    已经是带时区的 ISO 串（含 +08:00 / Z）时按原时区解析后再转换。
    """
    if value is None:
        return fallback
    # pandas 的缺失值有两种：None 和 NaN（NaN != NaN）
    if isinstance(value, float) and value != value:
        return fallback
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return fallback

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
    if parsed is not None and parsed.tzinfo is not None:
        return parsed.astimezone().strftime(fmt)

    for parser in _PARSERS:
        try:
            naive = datetime.strptime(text, parser)
        except ValueError:
            continue
        return naive.replace(tzinfo=timezone.utc).astimezone().strftime(fmt)
    return str(value)
