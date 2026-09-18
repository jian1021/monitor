# 主循环独立间隔 + UI 可配置 — 设计方案

日期: 2026-09-18

## 背景 / 目标

用户要求主循环中每个子程序以独立间隔执行，并把加密货币（OKX）与链上代币监控拆为独立子程序，所有间隔可在界面上设置。

现状（已核实）：
- `main.py` 已有主循环 + 硬编码 `INTERVALS`（秒）：rsi=86400(1440min)、meteora_pump=300、robinhood_pump=300、lp_alert=300。
- 加密货币 / 链上代币监控内嵌在 `run_rsi_monitor` 内（第 1 节 / 第 5 节），跟随 RSI 的 1440 分钟节奏。
- `db.py` 已有 `module_settings` 表（启停），`pages/app.py` 有启停开关 UI。

目标状态：
1. 六个子程序各自独立间隔：rsi / crypto / onchain_token / meteora_pump / robinhood_pump / lp_alert。
2. 间隔持久化到 DB，UI 可改（分钟）。
3. 主循环启动时从 DB 加载间隔，周期刷新，失败回退默认。

## 决策

- **默认间隔（分钟）**：rsi=1440（24h，原值不变）、crypto=30、onchain_token=30（用户选定，降低外部 API 频率）、meteora_pump=5、robinhood_pump=5、lp_alert=5。
- **存哪**：新建 `module_intervals` 表（module_name PRIMARY KEY, interval_minutes, updated_at），与 `module_settings` 同模式；不改动启停表。
- **默认值放哪**：`db.DEFAULT_INTERVALS`（分钟），避免 `pages/app.py` 反向 import `main.py` 拖入 baostock/requests 等重依赖。
- **间隔中的单位**：DB/UI 一律用分钟；主循环内部换算为秒。
- **刷新策略**：主循环每轮最多每 60 秒重读一次 DB 间隔（缓存），读取失败静默沿用缓存。
- **告警行为**：拆分后 rsi / crypto / onchain 各自独立发送飞书告警（不再是合并一条）。
- **不拆 meteora_pump / robinhood_pump / lp_alert 内部实现**，仅接入间隔读取。

## 改动文件

| 文件 | 改动 |
|---|---|
| `db.py` | 新增 `DEFAULT_INTERVALS`、`ensure_interval_schema()`、`get_module_intervals(defaults)`、`update_module_interval(name, minutes)` |
| `main.py` | `INTERVALS`(秒) → 从 `db` 导入 `DEFAULT_INTERVALS`+`get_module_intervals`；拆分 `run_crypto_monitor(config)` / `run_onchain_token_monitor(config)`（原第 1/5 节，各自发送告警）；`run_rsi_monitor(config)` 去掉第 1/5 节；主循环新增 crypto/onchain 两个任务槽，间隔读取走 60s 缓存刷新 |
| `pages/app.py` | 「🎛️ 监控模块启停」下方新增「⏱️ 监控模块执行间隔（分钟）」区块：6 个 number_input，改动即 `update_module_interval` + rerun |
| `test_module_intervals.py` | 新增：mock `get_db_client` 验证 get/update 读写、默认回退、分钟→秒换算 |

## 验收标准

1. `python test_imports.py` 与 `pytest` 全绿。
2. `main.py` 启动打印六个子程序各自的间隔（分钟），与 DB 值一致。
3. UI 修改间隔后，主循环在不重启的情况下（≤60s 内）采用新间隔。
4. 拆出的 crypto / onchain_token 独立于 RSI 执行与告警，受 `module_settings` 启停开关控制。