# lprange · LP 区间宽度历史统计

回答一个问题：**开仓后持有 1 周 / 2 周 / 3 周 / 1 个月、区间不调整，想让价格在区间内的“时间占比”达到 70% / 80% / 90%，区间至少要多宽？**

> 统计的是**时间占比**，不是“持有期内一次都不出界”的概率：出界后价格回到区间内，回来之后的时间继续计入。
> 所有结果都是历史统计，不代表未来，不含手续费与无常损失。

## 安装

在 monitor 仓库根目录：

```bash
.venv/bin/pip install -r lprange/requirements.txt   # 依赖与 monitor 基本相同
.venv/bin/python -m lprange --help
# 可选：alias lprange="$PWD/.venv/bin/python -m lprange"
```

Python 3.10+（3.10 需要额外安装 `tomli`，requirements 已按版本自动处理）。

## 常用命令

```bash
python -m lprange                                   # 无参数：列出 watchlist，输入序号/关键字/地址运行
python -m lprange run <池子或代币地址> --bin-step 100
python -m lprange run 3                              # watchlist 第 3 个
python -m lprange run sol-usdc                       # 名称/备注关键字（多个匹配时让你选）
python -m lprange run <地址> --network robinhood       # Robinhood Chain（Uniswap 池子地址或 v4 poolId）
python -m lprange run <地址> --network base --holds 168,720 --targets 0.8 --days 90
python -m lprange run <地址> --token quote            # 反转价格方向（如看 SOL/cbBTC）
python -m lprange run --csv kline.csv                # 现成 K 线（timestamp/high/low/close）
python -m lprange run <地址> --out result.json        # 导出（.csv / .json）

python -m lprange backtest <地址> --network robinhood   # 策略回测：宽度 × 调仓规则网格
python -m lprange backtest <地址> --fee 0.02 --tvl 300000 --liq-width 0.3 --out bt.csv
python -m lprange all --sort 1m --desc --out batch.csv
python -m lprange all --source turso --sync-turso
python -m lprange all --tag core                     # 名称或备注含 core 的池子

python -m lprange list | add <地址> [--name] [--note] | rm <序号/地址/关键字> | note <选择> "备注"
python -m lprange import-file pools.txt              # 每行：地址 / 地址 network / network:地址
python -m lprange import-turso --dry-run             # 先看会导入什么
python -m lprange import-turso
```

全局参数：`-v`（DEBUG 日志）、`--config`、`--db`。

主要统计参数：`--holds`（小时，默认 168,336,504,720）、`--targets`（默认 0.7,0.8,0.9）、`--days`（默认 180）、
`--bin-step` / `--max-bins`（默认 69）、`--min-history-mult`（默认 3）、`--refresh`、`--no-cache`、`--interval`（默认 2.5 秒）。

## 输入解析

1. 先按**池子地址**请求 GeckoTerminal；返回 404 则当作**代币地址**，取该代币流动性最大的池子；
2. 不像地址时，按 watchlist **序号**或**关键字**匹配；
3. `--bin-step` 不传且 network=solana 时，会尝试从 Meteora DLMM 接口自动读取 binStep（读不到就不输出 bin 表）。

## 统计口径

- 数据统一为 UTC 1h K 线、升序；缺失小时用前一根 close 补齐（OHLC=close，volume=0），记 `gap_ratio`；
  价格为 0 / NaN 的 K 线剔除并计入 gap；窗口终点补到当前整点。
- **样本**：每根 K 线 i 都是开仓点，开仓价 P = close[i]，持有窗口为 close[i+1 … i+H]（不足 H 根的开仓点丢弃）。
- **区间**：[P×(1−d), P×(1+u)]，边界含。
- `time_in_range` = 窗口内 close 落在区间内的 K 线占比。
- 每档宽度汇总：均值、中位数、p10（取不插值的下侧样本值，保证 ≥90% 样本不低于它）、各目标的达标样本比例。
- **推荐宽度**：典型档 = 中位数 ≥ T 的最小对称宽度；保守档 = p10 ≥ T 的最小对称宽度。
- **偏斜区间**：在 (d, u) 二维网格上找满足目标、且 `ln(1+u) − ln(1−d)` 最小的组合（典型/保守各一组）。
- 候选宽度：1%~80% 几何间距 40 档。实现全部向量化，180 天 × 4 个周期 × 40×40 偏斜网格约 2 秒。

### 出界行为（表四，用 T=80% 典型档的对称宽度）

- 向上 / 向下出界占比：所有出界小时里，高于上沿和低于下沿各占多少；
- 平均出界次数：每个持有期内从区间内变为区间外的次数（开仓时刻视为在区间内）；
- 平均回归(h)：每段出界持续多少小时后回到区间内（没回来的那段不计入）；
- 永不回归：出过界的样本里，到持有期结束仍在区间外的比例。

### 数据质量（第 7 节）

| 条件 | 处理 |
|---|---|
| `history_days < 持有天数 × 3` | 该周期标「样本不足」，不给推荐宽度 |
| `history_days ÷ 持有天数 < 3` | 标「仅供参考」（默认倍数 3 时不会出现，见下） |
| `gap_ratio > 20%` | 警告“成交稀疏，结果偏窄” |
| 实际拿到的天数 < 请求天数 | 警告“回溯不足”，并说明是接口没有更早数据还是池子较新 |

默认倍数从第 7 节写的 1.5 改成了 3，以满足验收第 7 条（历史不足 90 天时，1 个月一档显示「样本不足」）。
因此默认情况下：1 周需 21 天、2 周需 42 天、3 周需 63 天、1 个月需 90 天历史才给推荐；
够了门槛时独立窗口必然 ≥ 3，「仅供参考」不会出现。想要更宽松的第 7 节原规则，加 `--min-history-mult 1.5`。

样本数（`n_samples`）高度重叠，只作参考；真正独立的窗口数是 `history_days ÷ 持有天数`。

### bin 换算（DLMM）

```
bins_down = ceil(ln(1/(1−d)) / ln(1 + binStep/10000))
bins_up   = ceil(ln(1+u)     / ln(1 + binStep/10000))
总 bin 数 = bins_down + bins_up + 1（含当前活跃 bin）
positions = ceil(总 bin 数 / max_bins)   # >1 时提示拆分或换更大的 binStep
```

## 数据源与限流

- 默认 GeckoTerminal 公共接口 `https://api.geckoterminal.com/api/v2`；
  设置环境变量 `CG_API_KEY` 后改用 `https://pro-api.coingecko.com/api/v3/onchain`（请求头 `x-cg-pro-api-key`）。
- 已核实（CoinGecko 官方文档，2026-11）：
  - 免费接口约 **30 次/分钟**，默认请求间隔 2.5 秒（24 次/分钟）；
  - 单次最多 1000 根，用 `before_timestamp = 上一批最旧时间戳 − 1` 翻页；
  - Basic 档历史只到**过去 6 个月**，所以默认回看 180 天；更早历史需要 Analyst 及以上套餐。
    公共免费接口的实际回溯深度以返回为准——拿不够时报告里会写明实际天数。
- 429 / 5xx：按 `Retry-After` 或指数退避重试，最多 5 次；超时 15 秒。

## 本地文件

`lprange/data/watchlist.db`（路径见 `config.toml` 的 `[paths] db`，或 `--db` / `LPRANGE_DB`）：

- `pools`：watchlist（`network + address` 唯一；每次成功查询自动 upsert，更新 `last_used`、`use_count`；
  已有的名称、备注、来源不会被覆盖）；
- `ohlcv`：K 线缓存，只存接口原始数据（不存补齐值）。主键是 `(network, pool, series, ts)`——
  比需求多了 `series`（如 `usd:base`），因为同一池子的 USD 口径和币本位口径不能共用缓存；
- `ohlcv_meta`：记录“接口已经没有更早数据”的位置，避免新池子每次都往前白翻页。

每次运行只补拉缓存之后的增量，需要更早历史时再往前补；网络中断时已拉到的页已经写入缓存。
`--refresh` 清掉该池子缓存重拉，`--no-cache` 完全不读写缓存。

## 从 Turso 导入

连接走环境变量 `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN`；未设置时自动回退到 monitor 项目 `.env` 里的
`LIBSQL_URL` / `LIBSQL_TOKEN`。来源表在 `lprange/config.toml` 里配置，已按 monitor 项目的真实表结构填好：

| 来源 | 表 | 地址列 | 名称列 | network | 过滤 |
|---|---|---|---|---|---|
| 批量 Meme 超卖超买 | `meme_watchlist` | `address` | `symbol` | 固定 solana | `enabled = 1` |
| LP 仓位 / 池子价格告警 | `lp_position_alert` | `pool_address` | `pool_name` | `chain` 列（sol→solana，robinhood→robinhood） | `enabled = 1 AND kind IN ('dlmm','pool_price','evm_v4')` |
| RSI 监控里的 Meteora 池 | `asset_config` | `code` | `name` | 固定 solana | `asset_type = 'meteora' AND enabled = 1` |

需求里提到的 `monitor_meteora_pump` 已从项目中移除，没有对应的表。`lp_position_alert` 里 `kind = 'evm_v4'`
的行是 Robinhood Chain 上的 Uniswap v4 仓位，`pool_address` 存的是 poolId——GeckoTerminal 的 v4 池子正是用
poolId 寻址，所以一并导入。

**Robinhood Chain**：GeckoTerminal network id 为 `robinhood`。Uniswap 池子没有 DLMM 的 bin，
所以 Robinhood / EVM 池子不输出 bin 表（`--bin-step` 只对 Meteora DLMM 有意义）。单个来源查询失败只会跳过该来源；
Turso 连不上时会给出明确提示，不影响本地 watchlist。

## 测试

```bash
.venv/bin/python -m pytest tests/test_lprange_stats.py tests/test_lprange_io.py -q
```

覆盖验收标准 1~4、7，以及取数翻页、增量缓存、网络中断、watchlist、批量模式、Turso dry-run/导入（全部离线，用模拟接口）。

## 策略回测（`backtest`）

对 (宽度 × 调仓规则) 网格逐小时模拟，按净收益年化排序。默认 16 种宽度 × 4 种调仓 = 64 个组合，180 天约 1 秒。

| 维度 | 网格 |
|---|---|
| 固定宽度 | 半宽 ±2% ±3% ±5% ±8% ±12% ±18% ±25% ±35% ±50%（`--widths`） |
| 波动率定宽 | 半宽 = k × σ_EWMA × √H，k = 1.0 ~ 2.5 每 0.25（`--ks`）；σ 为小时对数收益率 EWMA（半衰期 `--halflife`，默认 72h），只用开仓时刻之前的数据 |
| 调仓规则 | `hold` 到期调仓（每 H 小时重新居中）、`out:1` 出界即调、`out:6` / `out:24` 连续出界 6 / 24 小时才调（`--rules`） |
| H | 默认 168（`--holds`） |

每个组合输出：净收益年化、手续费年化、无常损失年化、调仓次数与调仓成本年化、时间占比、成交量占比
（区间内成交量 ÷ 总成交量）、按 H 切成不重叠窗口后每个窗口净收益的中位数与 p10、独立窗口数（模拟小时 ÷ H）。

口径：

- **价格**：池子自身比价（`1 base = X quote`），不是美元价；成交量单独取美元口径，与 TVL（美元）相除。
  接口参数实际是 `currency=token&token=base`（GeckoTerminal 的 `currency` 只允许 `usd`/`token`，
  代码里的 `quote` 是内部口径名，发请求时自动转换）；`--token quote` 取倒数比价；
- **基准**：相对「持有建仓时那一篮子代币」的超额收益，不含代币本身涨跌；
- **手续费**：收盘价在区间内的小时计入 `成交量 × 费率 × liq_width ÷ (区间对数宽度 × TVL)`。
  `liq_width` 表示「池子其他 LP 的流动性相当于 TVL 均匀铺在多宽的对数区间里」（默认 0.5，约 ±25%），
  它和 TVL 一起决定手续费的**绝对水平**——组合之间的相对排序比绝对 APR 更可信；
- **无常损失**：用 Uniswap v3 集中流动性公式（仓位价值 ÷ 持币价值）近似 DLMM，每段仓位结束时结算、多段连乘；
- **调仓成本**：每次 `0.5 × 费率 + 额外成本`（默认 0.2%，`--rebalance-extra`），模拟出界后换掉约一半仓位；
- **费率**：`--fee` 优先；Solana 池子读 Meteora `pool_config.base_fee_pct`（不含动态费，偏保守）；
  Uniswap 池子从 GeckoTerminal 池子名称里的费率档解析；都读不到时报错要求指定；
- **TVL**：`--tvl` 优先，否则用 GeckoTerminal 当前 `reserve_in_usd`——整段历史都按当前 TVL，
  TVL 变化大的池子手续费会有偏差；
- 前 `max(24, 半衰期)` 小时只用来估计 σ，不计收益，所有组合共用同一段模拟区间。

> 同时修改：`run` 的价格口径默认值由 `usd` 改为 `quote`（池子比价）。LP 区间是按池子比价定义的，
> 用美元价会把 quote 币（如 SOL）自身的涨跌也算进去。需要美元口径时加 `--currency usd`。

## 明确不做

不预测价格；不执行链上交易。（手续费与无常损失只在 `backtest` 里按上面的近似口径估算。）
