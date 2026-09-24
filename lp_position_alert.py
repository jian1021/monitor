"""lp_position_alert.py — LP 仓位 / 池子价格告警（CLI 入口 + 兼容转发层）.

三种 kind:
  dlmm       Solana / Meteora DLMM，读链上仓位区间（minPrice）+ 真实持仓盈亏（pnlPctChange）
  evm_v4     Robinhood Chain / Uniswap v4，按钱包枚举仓位 NFT，用 tick 判定跌破区间下界
  pool_price 只有池子价格，盈利按价格涨幅、最低价取历史或手填

用法:
  python lp_position_alert.py            # 单轮检查后退出（GitHub Actions cron）
  python lp_position_alert.py --loop     # 本地常驻

实现已迁至 app/ 分层；此处仅保留 CLI 与旧导入名。
"""

import sys
import time
import traceback

from app.application.lp_alert.service import (
    CHAIN_OPTIONS,
    POOL_PRICE_CHAINS,
    RUN_TIME_FIELDS,
    _cur_dlmm,
    _cur_evm_v4,
    _cur_pool_price,
    build_message,
    check_rule,
    preview_dlmm,
    preview_pool_price,
    preview_wallet,
    run_once,
)
from app.domain.lp_alert.evaluator import (
    _current_price,
    _target_price,
    evaluate,
    needs_rearm,
    needs_rearm_target,
    units_plausible,
)
from app.domain.lp_alert.models import (
    STATUS_CLOSED,
    STATUS_ERROR,
    STATUS_OPEN,
    floor_from_ohlcv,
    parse_dexscreener_pair,
    parse_dlmm_position,
    to_float,
)
from app.infrastructure.db.lp_alert import (
    COLUMNS,
    CREATE_INDEX_SQL,
    CREATE_TABLE_SQL,
    MIGRATION_COLUMNS,
    _execute,
    _row_to_rule,
    add_rule,
    clear_alert_flag,
    delete_rule,
    ensure_table,
    load_rules,
    mark_alerted,
    set_enabled,
    set_status,
    update_runtime,
)
from app.infrastructure.market_data.evm_rpc import (
    EVM_RPC,
    INFO_TICK_SHIFT,
    MULTICALL3,
    RECENT_BLOCK_WINDOW,
    SEL_AGGREGATE3,
    SEL_BALANCE_OF,
    SEL_DECIMALS,
    SEL_GET_SLOT0,
    SEL_OWNER_OF,
    SEL_POOL_AND_POSITION_INFO,
    SEL_POSITION_LIQUIDITY,
    SEL_SYMBOL,
    TICK_BASE,
    TRANSFER_TOPIC,
    USDG_ADDRESS,
    V4_POSITION_MANAGER,
    V4_STATE_VIEW,
    _decode_abi_string,
    _evm_call,
    _evm_rpc,
    _fail,
    _hex_address,
    _multicall,
    _pad32,
    _pad_uint,
    _pool_id,
    _scan_transfer_token_ids,
    _signed24,
    _token_decimals,
    _token_meta,
    _uint_from,
    _word,
    call_data,
    decode_aggregate3,
    encode_aggregate3,
    fetch_evm_v4_positions,
    preview_evm_wallet,
    quote_price,
)
from app.infrastructure.market_data.http import HEADERS
from app.infrastructure.market_data.meteora import (
    DEXSCREENER_BASE,
    DEXSCREENER_CHAIN,
    GECKO_BASE,
    GECKO_NETWORK,
    METEORA_BASE,
    _dexscreener_chain,
    _gecko_network,
    fetch_dexscreener_pair,
    fetch_meteora_pool,
    fetch_meteora_positions,
    fetch_open_portfolio,
    fetch_pool_floor,
    http_get_json,
)

VERSION = "2026-09-15.9"
DEFAULT_INTERVAL = 300


def run_loop(interval):
    print(f"🔁 常驻监控模式: 每 {interval} 秒检查一次 (Ctrl+C 退出)")
    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            print("\n👋 已退出常驻监控")
            break
        except Exception:
            traceback.print_exc()
        time.sleep(interval)


def main():
    ensure_table()
    args = sys.argv[1:]
    if "--loop" in args:
        interval = DEFAULT_INTERVAL
        if "--interval" in args:
            idx = args.index("--interval")
            if len(args) > idx + 1:
                interval = int(args[idx + 1])
        run_loop(interval)
    else:
        run_once()


if __name__ == "__main__":
    main()
