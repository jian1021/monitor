"""数据库时间戳展示：库里统一存 UTC，页面固定按 UTC+8（北京时间）展示。

SQLite/Turso 的 `datetime('now')` 返回 UTC，不带时区标记。若直接把字符串丢到
表格里，会显示成比北京时间早 8 小时（看起来像「告警没发出去」）。这里显式用
`DISPLAY_TZ`，不用裸 `.astimezone()`——后者跟随服务器时区，Streamlit Cloud
默认 UTC 时又会显示错。
"""

from datetime import datetime, timezone

from app.core.timezones import DISPLAY_TZ

_PARSERS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def to_local_display(value, fmt: str = "%Y-%m-%d %H:%M", fallback: str = "-") -> str:
    """把库里的时间串统一转成 UTC+8（北京时间）展示；空值返回 fallback。

    - 无时区的 naive 串按 UTC 解析（数据库 `datetime('now')` 的约定）；
    - 带时区的 ISO 串（含 +08:00 / Z）按其原时区解析后再换算到 UTC+8；
    - 无法解析时原样返回。
    输出与运行环境的 TZ 无关，本地 / Cloud / 服务器显示一致。
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
        return parsed.astimezone(DISPLAY_TZ).strftime(fmt)

    for parser in _PARSERS:
        try:
            naive = datetime.strptime(text, parser)
        except ValueError:
            continue
        return naive.replace(tzinfo=timezone.utc).astimezone(DISPLAY_TZ).strftime(fmt)
    return str(value)
