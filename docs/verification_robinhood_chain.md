# Robinhood Chain 池与数据 API 验证报告

> 本报告记录 Robinhood Chain 全池筛选器使用的链与数据源。结论先行：
>
> 1. **Robinhood Chain 主网真实存在**：Chain ID **4663**，EVM（Arbitrum Orbit）L2，gas 为 ETH，官方 RPC `https://rpc.mainnet.chain.robinhood.com/`，主网 2026-07-01 上线。已通过 `eth_chainId` 实测返回 `0x1237`（=4663）。
> 2. Robinhood Chain 全池筛选使用 **Uniswap V2 / V3 / V4 与 Slipstream**（rhpools 索引的已审核部署）。Solana Meteora DLMM 由独立扫描器处理，与本模块分开。

## 一、链验证（启动时执行，已实测）

| 检查项 | 结果 | 证据 |
|---|---|---|
| Robinhood Chain 主网 Chain ID | **4663**（测试网 46630） | 官方文档 docs.robinhood.com/chain/connecting；实测 `eth_chainId` → `0x1237` |
| 官方 RPC | `https://rpc.mainnet.chain.robinhood.com/` | 官方文档；实测可通 |
| 链类型 | EVM 兼容，Arbitrum Orbit L2，ETH 为 gas 代币 | 官方文档 / Arbitrum Foundation |
| 主网时间 | 2026-07-01（测试网 2026-02-10） | Robinhood newsroom / 多家媒体 |
| 浏览器 | robinhoodchain.blockscout.com | 官方文档 |

## 二、数据 API 验证（全部真实可用，已实测）

### 1. rhpools 索引服务（主数据源，Robinhood Chain 链上事件索引）

- 公开服务：`https://rhpools.lol`（OpenAPI 3.1：`/api/v1/openapi.json`）
- 本地服务：`modules/robinhoodpools` 模块，`python -m rhpools.lp_server`，默认 `127.0.0.1:8196`（本仓库 `app/infrastructure/rhpools/client.py` 已封装进程管理）
- 数据来源：直接读 Robinhood Chain 官方 RPC，索引 Swap/Mint/Burn/Collect 等链上事件到 SQLite
- 实测路由（已验证返回真实数据）：
  - `GET /api/lp/status` — 索引状态（head、lag、coverage）
  - `GET /api/lp/pools?window=24h|7d|30d&sort=volume|fees|flow&order=desc&limit=N` — 池子列表
  - `GET /api/lp/search?q=SYMBOL` — 代币/池子搜索
  - `GET /api/lp/tape?limit=N` — 最近链上事件（含逐事件价格 `price0_usd/price1_usd`）
  - `GET /api/lp/dislocations` — 池间价差
- 实测字段（WETH/USDG v3 池，24h 窗口）：`volume_usd=12,162,094`、`fees_usd=1,216`、`fee_ppm=100`、`tvl_usd=22,336,907`、`observed_active_tvl_usd=47,094`、`swaps=9633`、`lp_count=7`、`price`、`price_change_pct`、`risks=["partial window history"]`、`coverage.complete=false`（索引追赶中）
- 一致性校验：`fees_usd / volume_usd = 1216/12162094 ≈ 0.01% = fee_ppm(100)` ✓ 实际费率与配置费率吻合
- 重要语义（来自其 PUBLIC_API.md，必须遵守）：
  - `fees_usd` 是**毛交易费估算，不是 LP 净收益**（`fees_basis: "gross trading-fee estimate, not LP net earnings"`）
  - `active_tvl_usd` 字段当前为 `null`；`observed_active_tvl_usd` 是观测到的区间内 TVL。**不得用总 TVL 冒充 Active TVL**
  - `coverage.complete=false` 时窗口数据为部分历史，必须展示该标志
  - 索引冷启动有追赶期（实测 lag_s≈136047，约 38 小时历史需回填），回填完成前 24h 窗口是部分数据

### 2. DexScreener（市场数据补充，chainId `robinhood`）

- `GET /latest/dex/pairs/robinhood/{pairAddress}` — 实测返回 `priceChange{m5,h1,h6,h24}`、`volume{m5,h1,h6,h24}`、`liquidity.usd`、`fdv`、`marketCap`、`pairCreatedAt`
- `GET /latest/dex/search?q=...` — 可搜出 robinhood 链池子（实测 ROBINHOOD/ETH 等）
- **无 OHLCV 图表 API**（`/io/dex/charts/...` 路由不存在，实测 404）→ 5 分钟 K 线无法直接从 DexScreener 获取
- **无 4h 涨跌幅**（只有 m5/h1/h6/h24）→ 4h 指标用 6h 代替并明确标注

### 3. DexPaprika（Robinhood Uniswap 候选池扫描）

- 页面：`pages/fee_screen.py` 的「📡 池发现与索引观测」Tab（组件 `app/interfaces/robinhood_observer.py`）中的「DexPaprika Robinhood Uniswap 池扫描」区。
- 接口：`GET https://api.dexpaprika.com/networks/robinhood/pools/search`，按 `uniswap_v2`、`uniswap_v3`、`uniswap_v4` 分别查询，并支持 24h 成交量、流动性、24h 交易数筛选与游标分页。
- 可选配置：仓库根目录 `.env` 中的 `DEXPAPRIKA_BASE_URL`、`DEXPAPRIKA_API_KEY`；公开 API 可不带 Key 调用。
- 该扫描用于发现符合门槛的候选池，不需要本机 RPC 扫描或等待本地索引追块；rhpools 仍提供本地链上事件、手续费估算和索引诊断等详细数据。
- 官方文档：[`Robinhood Chain pools`](https://dexpaprika.com/robinhood)、[DexPaprika network pool search API](https://docs.dexpaprika.com/api-reference/pools/advanced-pool-filtering-on-a-specific-network)。
- 风险筛查：DexPaprika 的 24h 买卖笔数只作为活动异常提示；开启 GMGN 检测后，页面对唯一代币合约地址调用 GMGN `/v1/token/security`（`chain=robinhood`），按 `is_honeypot` 字段排除明确标记的代币。
- GMGN 未返回结论、接口报错或超过本次查询上限的代币显示为“未知 / 人工复核”，不会标记为安全；扫描使用根目录 `.env` 或部署环境变量 / Streamlit Secret 中的 `GMGN_API_KEY`。云端仅需 Python `requests`，不需要安装 `gmgn-cli`。

### 4. GeckoTerminal

- GeckoTerminal 页面提供 Robinhood Chain 池列表，可用于人工查看；当前扫描器采用 DexPaprika 做池发现，GMGN 做安全标记。
- Robinhood 网络页面：[GeckoTerminal Robinhood pools](https://www.geckoterminal.com/robinhood/pools)

### 5. robinhood-chain-lp-tools（区间 APR 工具，Node/TS 子模块）

- `uni-range-apr`：基于当前流动性 + 24h 成交量 feed 估算指定区间 APR（秒级，近似）
- `uni-range-replay`：逐笔回放 Swap 事件实测区间收益（1–4 分钟，精确），`--validate` 可对账 feeGrowthGlobal
- 公式：`share = L_you / (L_active + L_you)`；`fees/yr = volume × feeRate × lpShareOfFee × share × 365`

## 三、指标可得性矩阵（需求书 §十 第 3 条）

| 指标 | 获取方式 | 状态 |
|---|---|---|
| 链 ID / 链验证 | RPC `eth_chainId` | ✅ 直接获取 |
| 池子列表、协议、代币地址 | rhpools `/api/lp/pools` | ✅ 直接获取 |
| 24h 成交量 | rhpools `volume_usd` | ✅ 直接获取（链上事件索引） |
| 24h 手续费 | rhpools `fees_usd` | ✅ 直接获取（毛额，非 LP 净得） |
| 实际平均手续费率 | `fees_usd / volume_usd` | 🧮 计算（与 `fee_ppm` 交叉验证） |
| TVL | rhpools `tvl_usd`（区块钉住） | ✅ 直接获取 |
| Active TVL | rhpools `observed_active_tvl_usd` | ⚠️ 观测值，可能为 null；缺失时标记"数据不可用"，不用总 TVL 冒充 |
| 7d 成交量 | rhpools `window=7d` | ✅ 直接获取 |
| 每日成交量序列（7 天） | — | ❌ 公开 API 未暴露日桶序列。持续性用「24h 成交量 ÷ 7 天日均」近似并标注；严格"近 3 天 vs 此前 4 天"指标保留接口，标记数据不可用 |
| 1h / 24h 涨跌幅 | DexScreener `priceChange.h1/h24` | ✅ 直接获取 |
| 4h 涨跌幅 | — | ❌ 无 4h 字段，用 6h 代替并标注 |
| 5m / 1m K 线 | rhpools 逐事件价格重建 | ⚠️ 用 `/api/lp/tape` 逐事件价格按 5 分钟分桶重建，样本有限（仅最近事件）；无数据时标记不可用 |
| 池子创建时间 | DexScreener `pairCreatedAt` | ✅ 直接获取（rhpools `created_at` 当前为 null） |
| 奖励收入 | — | ❌ Robinhood Chain 池无第三方奖励数据，按 0 处理并标注 |
| 手续费收入估算 | 公式计算 | 🧮 估算（标注假设：有效流动性占比 = 资金×区间内时间占比 / (Active TVL + 资金×区间内时间占比)） |
| LP 相对净收益 | 常积 IL 近似 + 费用 | 🧮 估算（全区间常积假设）；缺完整价格路径时显示"净收益无法准确计算" |

## 四、结论与开发边界

1. Robinhood Chain 筛选宇宙 = rhpools 索引的已审核 Uniswap V2/V3/V4/Slipstream 池（链 4663），市场数据用 DexScreener `robinhood` 网络补充。
2. 所有"不可用"指标在界面标记"数据不可用"，不编造。
3. 第一阶段只做监控、筛选、排名、收益分析；不自动交易、不自动加减流动性（rhpools 服务本身也默认禁止交易准备）。
