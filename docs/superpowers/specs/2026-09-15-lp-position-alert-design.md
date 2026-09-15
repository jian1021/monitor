# LP 仓位 / 池子价格告警 设计

日期: 2026-09-15
状态: 已批准（用户确认方案 B：新建独立模块，不碰旧代码；Solana 读链上真实区间，Robinhood 只做价格层）

## 目标

链上建池后，在界面输入池子地址（+ 链、+ 钱包），确认「连接成功」，然后设定两个触发条件：

1. **盈利目标**：如 +10%
2. **跌穿池子最低价**：价格跌破 LP 仓位的区间下界

命中任一条件时，通过飞书 Webhook 告警。

## 范围

一个模块，一张表，两种 `kind`：

| kind | 链 / DEX | 盈利基准 | 「最低价」来源 |
|---|---|---|---|
| `dlmm` | Solana / Meteora DLMM | 真实持仓盈亏（`pnlPctChange`） | 链上仓位区间真实下界（`minPrice`） |
| `pool_price` | Robinhood Chain / Uniswap | 价格涨幅（相对登记时现价快照） | 建池以来历史最低价，或手填 |

用户明确「在 Solana 及 Robinhood 链做 LP」，故两条链都要覆盖。

## 产品 | Pages

`pages/lp_position_alert.py`，两个 Tab，纳入现有 Streamlit 系统（自动继承 `index.py` 的登录守卫）。

## 数据源（全部已实测联通）

### Solana / Meteora DLMM — `https://dlmm.datapi.meteora.ag`（无需 API Key，限速 30 req/s）

| 用途 | 端点 | 实测结果 |
|---|---|---|
| 验证「连接成功」+ 池子信息 | `GET /pools/{pool_address}` | 200，返回 `name` / `current_price` / `token_x`、`token_y`（含 `decimals`、`symbol`）/ `tvl` / `volume` / `is_blacklisted` / `created_at` |
| 列出该钱包在该池的仓位 | `GET /positions/{pool_address}/pnl?user={wallet}&status=open&page_size=100` | 200 |

仓位对象字段（取自官方 OpenAPI `PositionPnLData`）：

- 必需：`positionAddress`、`minPrice`、`maxPrice`、`lowerBinId`、`upperBinId`、`isClosed`、`pnlUsd`、`pnlPctChange`、`feePerTvl24h`、`allTimeDeposits`、`allTimeWithdrawals`、`allTimeFees`
- 可选：`isOutOfRange`（可空布尔）、`poolActivePrice`（可空字符串）、`poolActiveBinId`、`unrealizedPnl`、`createdAt`、`closedAt`

**类型陷阱：`minPrice` / `maxPrice` / `pnlPctChange` / `pnlUsd` / `poolActivePrice` 在 schema 中全部是 `string`，必须显式转 `float`。** 不转会在比较时抛 `TypeError` 或做字符串比较，静默给出错误结论。

### Robinhood Chain / Uniswap — 无需 API Key

| 用途 | 端点 | 实测结果 |
|---|---|---|
| 验证「连接成功」+ 池子价格 | `GET https://api.dexscreener.com/latest/dex/pairs/{chainId}/{pool_address}` | 200，返回 `{schemaVersion, pairs:[...]}` |
| 建池以来最低价（OHLCV） | `GET https://api.geckoterminal.com/api/v2/networks/robinhood/pools/{pool_address}/ohlcv/day?limit=100` | 200 |

关键约定：

- **`chainId` 是字符串 `robinhood`，不是 EIP-155 的 `4663`。** 传 `4663` 返回 200 + 空数组（静默失败）。该链真实链 ID 为 4663、RPC `https://rpc.mainnet.chain.robinhood.com`（已验证 `eth_chainId` 返回 `0x1237`），但 Dexscreener 用自己的 slug 命名空间。
- Dexscreener 返回体是**包装对象** `{pairs:[...]}`；`priceUsd` / `priceNative` 是**字符串**，`liquidity.usd` / `volume.h24` 是数字。需分别处理。
- Dexscreener **没有 OHLCV 端点**，历史最低价必须走 GeckoTerminal。
- GeckoTerminal `ohlcv_list` 为**倒序（新 → 旧）**，且 `limit` 上限 100。池子建成超过 100 天时需用 `before_timestamp` 翻页才能拿到「建池以来」全历史。

## 表 `lp_position_alert`（Turso / libSQL）

```sql
CREATE TABLE IF NOT EXISTS lp_position_alert (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    kind                TEXT    NOT NULL DEFAULT 'dlmm',    -- dlmm | pool_price
    chain               TEXT    NOT NULL DEFAULT 'sol',
    pool_address        TEXT    NOT NULL,
    wallet              TEXT,                               -- 仅 dlmm
    position_address    TEXT,                               -- 仅 dlmm
    pool_name           TEXT,
    token_x_symbol      TEXT,
    token_y_symbol      TEXT,
    lower_bin_id        INTEGER,                            -- 仅 dlmm
    upper_bin_id        INTEGER,                            -- 仅 dlmm
    min_price           REAL,                               -- 仅 dlmm：仓位区间下界快照
    max_price           REAL,                               -- 仅 dlmm：仓位区间上界快照
    floor_price         REAL,                               -- 跌穿阈值，两种 kind 共用
    target_mode         TEXT    NOT NULL DEFAULT 'pnl_pct', -- pnl_pct | price_pct
    target_pct          REAL,                               -- 10 表示 +10%
    entry_price         REAL,                               -- price_pct 的基准快照（登记时池子现价）
    enable_target_alert INTEGER NOT NULL DEFAULT 1,
    enable_floor_alert  INTEGER NOT NULL DEFAULT 1,
    rearm               INTEGER NOT NULL DEFAULT 1,         -- 仅作用于跌穿告警
    enabled             INTEGER NOT NULL DEFAULT 1,
    target_alerted      INTEGER NOT NULL DEFAULT 0,
    floor_alerted       INTEGER NOT NULL DEFAULT 0,
    last_pnl_pct        REAL,
    last_active_price   REAL,
    last_checked_at     TEXT,
    is_out_of_range     INTEGER,
    status              TEXT    NOT NULL DEFAULT 'open',    -- open | closed | error
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_lp_position_alert_enabled
    ON lp_position_alert(enabled, status);
```

`kind='dlmm'` 用 `wallet` / `position_address` / `lower_bin_id` / `upper_bin_id` / `min_price` / `max_price` / `target_mode='pnl_pct'`；`kind='pool_price'` 用 `entry_price` / `target_mode='price_pct'`，`dlmm` 专属列留空。两种 kind 共用 `floor_price` / `target_pct` / 开关 / 去重标记。

`token_x_symbol` / `token_y_symbol` 两种 kind 都填交易对的两个币种符号，只是来源不同：`dlmm` 取 Meteora 的 `token_x` / `token_y`，`pool_price` 取 Dexscreener 的 `baseToken` / `quoteToken`。

## 监控程序 `lp_position_alert.py`

结构与 `monitor_price.py` 同构：`ensure_table()` + CRUD + `run_once()` + `--loop / --interval`。同一模块内也提供 DDL 与 CRUD 供页面 `import`（不像 `monitor_price.py` / `st_monitor_price.py` 那样把建表语句和 CRUD 复制两份）。

`run_once()` 每条启用且 `status='open'` 的规则：

1. 按 `kind` 分派拉取（见下）
2. 刷新快照字段（`min_price` / `max_price` / `last_*` / `is_out_of_range`），区间被调整时下界随之更新
3. 判定两个条件
4. 命中则飞书推送并落 `*_alerted` 标记

### kind = `dlmm`

拉 `GET /positions/{pool}/pnl?user={wallet}&status=open&page_size=100`，按 `position_address` 找到目标仓位：

- 找不到，或 `isClosed=true` → `status='closed'`，推送一次收尾通知后不再拉取该规则
- **盈利**：`float(pnlPctChange) >= target_pct`
- **跌穿**：`float(poolActivePrice) <= floor_price`

跌穿判定**刻意使用同一次响应里的 `poolActivePrice`，而不是 `/pools` 的 `current_price`** —— 两者同源、同单位，绕开「`minPrice` 究竟以 token X 还是 token Y 计价」的单位陷阱。`isOutOfRange` 只作交叉校验，不能单独用作跌穿信号：它上穿或下穿都会为 `true`，无法区分是跌破下界还是涨破上界。

### kind = `pool_price`

拉 `GET /latest/dex/pairs/{chainId}/{pool_address}`，取 `pairs[0]`：

- 空 `pairs` → 记 `status='error'`，本轮跳过（不误报）
- **盈利**：`float(priceUsd) >= entry_price * (1 + target_pct/100)`
- **跌穿**：`float(priceUsd) <= floor_price`

## 告警语义

- **空值即跳过**：`target_pct` 为 `NULL` 或 `enable_target_alert=0` 时不做盈利判定；`floor_price` 为 `NULL` 或 `enable_floor_alert=0` 时不做跌穿判定。Python 3 中 `None` 与 `float` 比较会抛 `TypeError`（不是静默 False），所以空值必须显式短路，不能依赖比较结果。
- **保存时校验至少启用一个触发条件**，且启用盈利时 `target_pct` 必须为有效数字、启用跌穿时 `floor_price` 必须大于 0；否则页面拒绝保存并给出提示。
- 两个条件**各自独立去重**，一个触发不会压掉另一个（两个标记位）。
- **跌穿告警支持自动重新武装**（`rearm=1`，默认开）：价格回升到 `floor_price` 之上后清空 `floor_alerted`，下次再跌破会再次告警。否则第一次跌破后这条规则永久哑火。
- **盈利告警为一次性**：触发后不再重武装，需在页面上手动重置。避免反复刷屏。
- 界面提供**手动重置告警**按钮（清 `target_alerted` / `floor_alerted`）。
- API 失败只记日志与本轮跳过，**绝不因取数失败而告警**。
- 任何规则连续取数失败时会记 `status='error'` 并在页面显示，不静默。

## 界面 `pages/lp_position_alert.py`

### Tab 1「Solana LP 仓位」

两步式：

1. **连接**：填池子地址 + 钱包地址 + 链（默认 sol）→ 点「连接」→ 调 `/pools/{addr}` 验活，成功则显示 `✅ 连接成功` 与池子名 / 现价 / TVL / 是否黑名单；随后调 `/positions/.../pnl` 列出该钱包在此池的开放仓位表格（区间上下界、当前 PnL%）。查到 0 个仓位时明确提示「该钱包在此池没有开放仓位」，而不是笼统报错。
2. **设阈值**：从列表选一个仓位 → 填盈利目标 %（默认 10）与跌穿价（默认自动填该仓位的 `minPrice`，可改，例如留缓冲填 `minPrice × 0.95`）→ 保存。

### Tab 2「池子价格」（Robinhood 等）

1. **连接**：填池子地址 + 链（默认 robinhood）→ 调 `/latest/dex/pairs/{chainId}/{addr}` 验活 → 显示 `✅ 连接成功` + 交易对名 / 现价 / 流动性 / 建池时间。
2. **设阈值**：填盈利目标 %；跌穿价可一键「填入建池以来最低价」（调 GeckoTerminal OHLCV 求 `min(low)`），也可手填。

### 规则列表（两个 Tab 共用底部）

状态、当前 PnL% 或现价、现价 vs 下界、暂停/启用、重置告警、删除。

## 调度

新增 `.github/workflows/lp_position_monitor.yml`，cron 每 5 分钟（GitHub Actions 最小粒度，与现有 `price_monitor.yml` 一致），并保留 `workflow_dispatch` 手动触发。要更快只能本地 `--loop` 常驻。

## 技术决策

| 项 | 决策 |
|---|---|
| 架构 | 新模块独立（方案 B），复用 `db.py` / `config.py` / `send_feishu_msg.py` 的**只读导入**，不修改它们 |
| Solana 仓位区间 | Meteora DLMM Data API（纯 HTTP，`requests` 即可）；**不需要 Solana SDK**（`solders` / `anchorpy` 均未安装） |
| Robinhood 价格 | Dexscreener；历史最低价走 GeckoTerminal |
| 跌穿比较基准 | 同响应的 `poolActivePrice`，而非 `/pools` 的 `current_price` |
| 表 | 单表 + `kind` 判别，避免两张近乎重复的表 |
| DDL / CRUD 位置 | 只写在 `lp_position_alert.py`，页面 import，不复制两份 |
| 告警通道 | 复用 `send_feishu_msg.send_feishu_msg` |
| 去重 | 双标记位 + 跌穿自动重武装 |

## 边界（明确不做）

- **不修改** `monitor_price.py` / `pages/st_monitor_price.py` / `price_alert` 表 / `dex_client.py` / `db.py` / `config.py` / `send_feishu_msg.py`。
- 唯一触及的既有文件：`index.py` 增加一行页面注册（该文件用 `st.navigation` 显式列表，未注册的 `pages/` 文件不可见），仅追加、不改逻辑。
- **不做 Robinhood 仓位区间读取**。Uniswap v4 仓位在 PoolManager 中以 `(owner, tickLower, tickUpper, salt)` 哈希为键存储，**无法从钱包地址枚举**，输入地址列出仓位在链上不可行；v3 虽可经 `eth_call` 读 `NonfungiblePositionManager.positions(tokenId)`，但需另接 EVM RPC 并解析 tick，本次不做。
- 不做 Solana / Robinhood 之外的链。
- 不修 `monitor_meteora_pump.py` 中已全路径 404 的数据源 `dlmm-api.meteora.ag`，也不清理两个 workflow 里已冗余的 `gmgn-cli` 安装步骤（代码早已切到 `dex_client.py` 直连）。两者单独提。
- 不做 LP 手续费收益 / 无常损失的独立建模，盈利一律采用数据源给出的口径。

## 风险与上线前必做的验证

1. **`minPrice` / `poolActivePrice` 的单位可比性 —— 已用真实仓位验证通过（2026-09-15 关闭）。**

   验证数据（用户钱包 `4vjpuTxYnAKJvNpQjfRYLu6zjsiWck3T8N1E79Y9qMNd`，该钱包 209 个已关闭仓位、0 个开放仓位，故取关闭仓位——`minPrice` / `poolActivePrice` 在关闭仓位上同样返回）：

   池 `AsSyvUnbfaZJPRrNh3kUuvZTeHKoMVWEoHz86f4Q5D9x`（MET-SOL，bin_step=20）位置 `H6kbrC3NXtrVP1U9YaSMc5zBEGWPPwBxmjacPwyxCRrL`：
   `minPrice=0.00210697863166232`、`maxPrice=0.002418426632579231`、`lowerBinId=373`、`upperBinId=442`、`poolActivePrice=0.00204068788709657`、`poolActiveBinId=357`、`isOutOfRange=True`。

   三条判据全部通过：
   - `minPrice < maxPrice` ✓（区间方向正确）
   - `poolActiveBinId(357) < lowerBinId(373)` 与 `poolActivePrice < minPrice` **同向一致**，且与 `isOutOfRange=True` 吻合 ✓（bin 序与价格序一致）
   - **`poolActivePrice / /pools.current_price = 1.000000`（15 位有效数字完全相同）** ✓ —— 而 `/pools` 的 `current_price` 已独立证明是 token-Y-per-token-X（PUMP-SOL 对照 GeckoTerminal，反方向相差 7.8×10⁸）。故两者同单位、同价格空间。

   数学交叉验证：`(1+0.002)^69 = 1.14781735` 与 `maxPrice/minPrice` 吻合到 **1.7e-15**；`(1+0.002)^16` 与 `minPrice/poolActivePrice` 吻合到 **2.2e-16** —— bin↔价格映射与 `bin_step` 精确自洽。

   结论：**`poolActivePrice <= floor_price`（floor 取仓位 `minPrice`）这一比较成立**，跌穿判定不会因单位问题静默失效。

   → **纵深防御（已实现并随代码上线）**：`kind=dlmm` 每轮检查调用 `units_plausible(active_price, pool_current_price)` 交叉校验；两者相差超过 100 倍（单位反转会造成 10⁴~10¹⁰ 倍偏差）则**记日志并跳过该轮跌穿判定**。这条守卫在本次真实数据上返回 `True`。它现在是防未来 API 字段变更的常规保险，而不再是弥补未验证假设的补丁。

   （附：为何这项一度无法自查——公共 Solana RPC 的 `getProgramAccounts` 已被付费墙封死，实测 8 个公共端点返回 403/429/401，Tatum 明确回 "paid plans only"；扫描活跃池 93 笔交易 / 446 个账户得到 0 个仓位，因为 swap 不引用仓位账户。最终由用户提供钱包地址后经 `/portfolio` 找到关闭仓位解决。）
2. GeckoTerminal OHLCV `limit` 上限 100，建池超 100 天的池子需 `before_timestamp` 翻页，「建池以来最低价」才准确。
3. GitHub Actions cron 最快 5 分钟，非实时。
4. Meteora Data API 为第三方服务，端点可能变更；本仓库的 `dlmm-api.meteora.ag` 已被弃用（全路径 404）即为例证。取数失败一律不告警，避免误报。

## 成功标准

1. Tab 1：输入一个真实 Meteora DLMM 池子地址 + 钱包 → 显示「连接成功」并列出该钱包在此池的仓位；选中一个 → 保存后规则出现在列表。
2. 用真实钱包验证 `poolActivePrice` 与 `minPrice` 单位可比（风险 1 的验证步骤），并在规则列表正确显示「现价 vs 下界」。
3. 手动把某规则 `floor_price` 设到现价之上 → 跑 `run_once()` → 飞书收到跌穿告警，且 `floor_alerted=1`；再次运行不重复推送。
4. 价格回升到 `floor_price` 之上，然后再次跌破 → 因 `rearm=1` 再次推送。
5. 盈利条件：手动把 `target_pct` 设到低于当前 `pnlPctChange` → 触发盈利告警且 `target_alerted=1`，重复运行不重发。
6. Tab 2：输入一个真实 Robinhood 池子地址 → 「连接成功」并显示现价；「填入建池以来最低价」能取到数值。
7. 取数失败（故意传错地址）→ 页面与日志报错，但**不发送任何飞书消息**。
8. `monitor_price.py` 与现有「价格监控」页面行为与改动前完全一致（回归检查）。

## 追加范围：`kind='evm_v4'`（2026-09-15 实现）

原设计把 Robinhood 限定为「只做池子价格」，因为当时判断链上无法按钱包枚举仓位。**该判断在 v3 上错误、在 v4 上可以通过事件日志绕过**，故追加第三种 kind。

**可行性结论（均以真实链上数据验证）**

| 合约 | 地址 | ERC721Enumerable | 能否按钱包枚举 |
|---|---|---|---|
| v3 NonfungiblePositionManager | `0x73991a25c818bf1f1128deaab1492d45638de0d3` | ✅ | 可以（`balanceOf`+`tokenOfOwnerByIndex`） |
| v4 PositionManager | `0x58daec3116aae6d93017baaea7749052e8a04fa7` | ❌ | **靠 `eth_getLogs` 抓 Transfer 事件绕过** |

注意：Robinhood Chain 上 Uniswap 用的是**非标准地址**（与主网确定性地址不同），排查时勿套用主网地址。

**实现要点**

- 枚举：`eth_getLogs(address=v4PM, topics=[Transfer, null, wallet])` → tokenId 集合 → `ownerOf` 复核
- 区间：`getPoolAndPositionInfo(tokenId)` → PoolKey(5 word) + PositionInfo(1 word)；**tick 从第 8 位起**（`tickLower = bits[8:32]`、`tickUpper = bits[32:56]`），此偏移由「tick 必须整除 tickSpacing」约束在 25 个真实样本上暴力搜索确认
- 当前价：`poolId = keccak256(abi.encode(PoolKey))` → `StateView.getSlot0(poolId)` → activeTick
- `currency0 = address(0)` 是 v4 的原生 ETH，非解码错误

**判定语义**：跌穿用 `activeTick < lower_bin_id`（tick 空间比较，精确且完全避开代币小数位）；盈利目标用对数空间 `Δtick >= ln(1+pct/100)/ln(1.0001)`，避免 `1.0001^Δ` 溢出。价格仅用于展示。

**复用的列**：`lower_bin_id`/`upper_bin_id` 存 tick 上下界，`pool_address` 存 poolId，`floor_price`/`min_price`/`max_price` 存展示用价格快照。新增列仅 `token_id`、`entry_tick`（`ensure_table` 用 `ALTER TABLE` 幂等迁移）。

**已验证**：真实钱包 26 个 v4 仓位全部解出（同池两个仓位当前价一致，为解码正确性的内部佐证）；端到端触发跌穿 → 飞书 200 → 跨轮去重 → 重武装 → 清理，全部通过。
