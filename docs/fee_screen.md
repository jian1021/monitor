# fee-screen：Robinhood Chain 高手续费流动池筛选监控模块

> 开发前置验证结论见 [`docs/verification_robinhood_chain_meteora.md`](verification_robinhood_chain_meteora.md)。
> **Meteora 未在 Robinhood Chain 部署**（Solana 专属），本模块的筛选宇宙为
> Robinhood Chain（链 4663）上真实存在的 **Uniswap V2 / V3 / V4 / Slipstream** 流动池。

## 功能（对应需求书四个页面）

| 页面 | 文件 | 功能 |
|---|---|---|
| 单页四 Tab | `pages/fee_screen.py` | 同一模块四个功能合并一页，Tab 切换： |
| ├ 筛选参数 | 〃 | 链验证、Meteora 部署提示、全部初筛参数/权重（写库，监控共用） |
| ├ 流动池列表 | 〃 | 排名表（24h 量/费、实际费率、TVL、Active TVL、Fees/ActiveTVL、持续性、1h/6h/24h 涨跌、风险、评分）、排序搜索筛选、未通过原因 |
| ├ 单池详情 | 〃 | 5 分钟 K 线（链上事件重建）、量/费 5 分钟序列、窗口聚合对比、风险事件、手续费收入估算、LP 相对净收益 |
| └ 监控报警 | 〃 | 启停、轮询间隔、飞书 Webhook、下降阈值、立即执行、告警记录 |

主循环模块名 `fee_screen`（`main.py` 注册，默认 30 分钟一轮），与既有模块同样受
`module_settings` / `module_intervals` 控制。

## 数据链路

```text
Robinhood RPC (eth_chainId=4663 验证)
        ↓
rhpools 索引 (/api/lp/pools window=24h|7d)   ← 链上事件：volume_usd / fees_usd /
        ↓                                       tvl_usd / observed_active_tvl_usd / fee_ppm
归一化 PoolSnapshot + DexScreener(robinhood)  ← priceChange h1/h6/h24、pairCreatedAt
        ↓
筛选（初筛 + 持续性 + 价格趋势）→ 归一化评分 → 排名
        ↓
Streamlit 四页面 / 主循环告警（飞书 + 去重）
```

## 安装与启动

```bash
pip install -r requirements.txt            # 无新增依赖
git submodule update --init                # robinhoodpools 索引服务（如未拉取）
streamlit run index.py                     # 网页界面（登录后见"高手续费池"四个页面）
python main.py                             # 监控主循环（含 fee_screen 模块）
```

rhpools 服务由 `app/infrastructure/rhpools/client.py` 自动拉起（默认
`127.0.0.1:8196`，数据落 `~/.local/share/rhpools`）；也可手动
`PYTHONPATH=modules/robinhoodpools/src python -m rhpools.lp_server`。

## 配置

- 筛选参数：页面一修改并保存（`fee_screen_settings` 表，主循环共用）。
- 监控参数：页面四（Webhook、下降阈值、top_n、轮询间隔）。
- 飞书：页面四 Webhook 优先，留空用 `config.py` 的 `FEISHU_WEBHOOK`。

## 告警与去重

| 类型 | 触发 | 去重 TTL |
|---|---|---|
| 新池进入名单 | 上一轮状态中不存在 | 24h |
| 指标大幅下降 | 成交量/手续费/Fees-ActiveTVL 较上轮下降超阈值 | 6h（按池+指标） |
| 单边下跌风险 | 趋势分析判定（连续大阴线/更低高低点/超阈值跌幅） | 6h（按池） |
| 数据源异常/过期 | 链验证失败、服务不可用、索引落后 | 1h |

去重复用 `pump_alert_sent` 表；数据库不可用时宁可重复也不漏报（与既有模块一致）。
报警内容含池子地址、链、成交量、手续费率、Fees/Active TVL、价格变化、风险原因、数据时间。

## 测试

```bash
python -m pytest test_fee_screen_screening.py -q   # 23 个纯筛选逻辑用例
python -m pytest test_fee_screen_monitor.py -q     # 3 个监控模块用例（CI 静默跳过等）
python -m pytest -q                                # 全仓回归
```

覆盖需求书关键口径：缺失字段一律"数据不可用"（不冒充 0）、实际费率=费/量、
Active TVL 缺失不拿总 TVL 冒充、净收益不重复扣 IL、集中流动性 share 公式、
连续大阴线/更低高低点检测、排名扣分。

## 已知限制（如实标注，不编造）

1. **每日成交量序列不可用**：持续性用 `24h ÷ 7d 日均` 近似，严格的"近 3 天 vs 此前 4 天"保留接口。
2. **4h 涨跌幅不可用**：DexScreener 只有 m5/h1/h6/h24，用 6h 代替并标注。
3. **5 分钟 K 线为链上事件重建**：样本限于 tape 最近事件，非完整历史。
4. **索引冷启动回填期**（实测约 38h）内窗口数据为部分历史，页面展示 `窗口完整 ⚠️`。
5. **奖励收入按 0**：数据源无 Robinhood Chain 池的第三方奖励数据。
6. **净收益为常积全区间近似**：集中流动性的真实 IL 取决于区间；精确区间收益
   请用「LP 区间 APR」页面的 `uni-range-replay` 逐笔回放。
7. 本模块只做监控、筛选、排名与收益分析，**不自动交易、不自动加减流动性**。
