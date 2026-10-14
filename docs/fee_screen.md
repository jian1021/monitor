# Robinhood Chain 池扫描（DexPaprika）

> Robinhood Chain 与数据源验证见 [`docs/verification_robinhood_chain.md`](verification_robinhood_chain.md)。

`pages/fee_screen.py` 只保留一个功能：通过 **DexPaprika** 扫描 Robinhood Chain（链 4663）
上的 **Uniswap V2 / V3 / V4** 候选池。原先基于本地 rhpools 索引的筛选参数、流动池列表、
单池详情、监控报警，以及「Robinhood Pools 观测站」和主循环 `fee_screen` 监控模块**均已移除**。

## 功能

| 区域 | 组件 | 功能 |
|---|---|---|
| 全链扫描标签 | `pages/onchain_hub.py` | 在主标签页中直接渲染 Robinhood 扫描页面 |
| 池扫描 | `app/interfaces/dexpaprika_scan.py` | 按协议、24h 成交量/交易数、流动性筛选候选池；可选基础貔貅风险过滤与 GMGN 合约检测；结果可下载 CSV |

该扫描直接从 DexPaprika 获取数据，不依赖本机 RPC 扫描，也不等待本地 rhpools 索引追块。

## 数据链路

```text
DexPaprika /networks/robinhood/pools/search (uniswap_v2|v3|v4)
        ↓
按 24h 成交量 / 流动性 / 24h 交易数筛选
        ↓
基础活动风险过滤（每个代币 24h 卖出数、买卖笔数比）
        ↓
GMGN /v1/token/security（chain=robinhood，可选，按唯一代币地址查询）
        ↓
结果表（可下载 CSV）
```

## 配置

- `DEXPAPRIKA_BASE_URL`、`DEXPAPRIKA_API_KEY`（可选，公开 API 可不带 Key）。
- `GMGN_API_KEY`：当勾选「使用 GMGN 检测貔貅合约」时使用；未配置时所有代币标记为「未知 / 人工复核」。
- 只需 Python 依赖中的 `requests`，不要求安装 `gmgn-cli`。

## 风险标注（如实标注，不编造）

1. **基础活动风险**只依据 DexPaprika 的 24h 买卖笔数，用于提示活动异常，不是安全结论。
2. **GMGN 未覆盖 / 报错 / 超上限**的代币显示为「未知 / 人工复核」，不会显示为安全通过。
3. **GMGN 明确标记貔貅**的池会从结果中排除。
4. 不自动交易、不自动加减流动性。
