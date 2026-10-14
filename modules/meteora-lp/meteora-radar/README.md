# meteora-radar

Meteora（Solana / DLMM）meme 代币分层筛选与反弹候选提醒系统。**只筛选、只提醒，不自动下单。**

## 流程

```text
Meteora 池子数据
      ↓
第一层：代币基本属性筛选（TVL / 成交量 / 市值 / 风险）  → PASS / REJECT / REVIEW
      ↓   仅 PASS
第二层：1H RSI < 10                                    → 超跌候选
      ↓
第三层：5m 止跌 + 突破局部高点（可选放量确认）          → 反弹确认
      ↓
飞书提醒（去重 + 冷却）→ 人工复核
```

## 目录结构

```text
meteora-radar/
├── main.py                    # 主程序（一轮 / --loop 常驻）
├── config.py                  # 阈值与配置（全部可用环境变量覆盖）
├── requirements.txt
├── requirements-dev.txt
├── .env / .env.example        # 飞书 Webhook、可选阈值覆盖
├── data/candidates.json       # 候选名单与提醒状态（运行时生成）
├── src/
│   ├── meteora_api.py         # 拉取池子列表与元数据
│   ├── token_filter.py        # 第一层：基本属性与风险筛选
│   ├── market_data.py         # 价格与 K 线
│   ├── indicators.py          # RSI 与 5m 止跌/反弹信号（纯函数）
│   ├── candidate_store.py     # 候选名单、去重冷却、信号后表现
│   └── feishu.py              # 飞书告警
├── logs/radar.log             # 运行日志
└── tests/                     # 单元测试（pytest）
```

## 安装与运行

```bash
cd meteora-radar
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # 填入 FEISHU_WEBHOOK

python main.py --dry-run    # 先空跑一轮，不发送飞书，检查筛选结果
python main.py              # 正式跑一轮
python main.py --loop       # 常驻，按 RADAR_POLL_INTERVAL_SECONDS 轮询
```

常用参数：`--dry-run`（不发飞书）、`--verbose`、`--loop`、`--interval N`、
`--pool-pages N`、`--rsi-max N`、`--rsi-top-n N`、`--require-volume`、
`--min-tvl N`、`--min-volume N`。

定时运行（cron 示例）：

```cron
*/10 * * * * cd /path/to/meteora-radar && .venv/bin/python main.py >> logs/cron.log 2>&1
```

## 三层规则

**第一层（`src/token_filter.py`）** —— 优先级高于 RSI，先排除明显不适合的池子：

| 结果 | 含义 |
| --- | --- |
| `PASS` | 通过，进入 RSI 监控 |
| `REJECT` | 命中明确排除条件（TVL/成交量/量比过低、被拉黑、冻结权限未关闭、TVL 骤降、非 meme 池） |
| `REVIEW` | 核心数据缺失或风险未知，暂不进入自动候选 |

无法从数据源获取的风险项（增发/铸造权限、持币集中度、近期交易笔数等）一律记为
「未知」，绝不默认视为安全；`RADAR_STRICT_RISK_UNKNOWN=1` 时未知会直接把池子降为 `REVIEW`。

**第二层（`src/indicators.py`）** —— `RSI(period, 1h) < 10` 触发超跌候选。
默认 period 为 14（标准口径，可用 `RADAR_RSI_PERIOD` 调整；现有 monitor 项目其他模块用的是 3）。

**第三层** —— 最近 3~6 根 5m K 线不再创出新低（止跌），随后某根收盘价突破此前窗口的
局部高点即视为反弹确认；突破量高于近期均量时标记为「放量」，
`RADAR_REQUIRE_VOLUME_CONFIRMATION=1` 可要求必须放量才提醒。

> ⚠️ RSI < 10 只是超跌条件，不代表价格一定反弹；5m 规则是待验证的交易假设，可能假突破。
> 所有筛选都无法保证代币不会归零，请人工复核后再决定是否交易 / 评估单边 LP / 放弃。

## 候记录内容

`data/candidates.json` 里每个候选记录：链、代币与池子地址、TVL、24h 成交量、
量/TVL、市值、风险检查结果与未知字段、1H RSI 与触发时间价格、5m 止跌/突破状态、
提醒时间，以及信号后 15 分钟 / 1 小时 / 4 小时的价格表现（`followups`，用于后续回测）。

## 配置

所有阈值集中在 `config.py` 的 `RadarConfig`，并可用环境变量覆盖
（`RADAR_` + 字段名大写；飞书 Webhook 例外，用 `FEISHU_WEBHOOK`）。常用项：

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `FEISHU_WEBHOOK` | 空 | 飞书机器人 Webhook，空则只打印 |
| `RADAR_MIN_POOL_TVL_USD` | 50000 | 最低池子 TVL |
| `RADAR_MIN_VOLUME_24H_USD` | 50000 | 最低 24h 成交量 |
| `RADAR_MIN_VOLUME_TVL_RATIO` | 0.5 | 最低 成交量/TVL |
| `RADAR_MIN_MARKET_CAP_USD` | 0 | 最低市值（0=不启用） |
| `RADAR_RSI_PERIOD` / `RADAR_RSI_1H_MAX` | 14 / 10 | RSI 周期与超跌阈值 |
| `RADAR_RSI_TOP_N` | 40 | 初筛后只对成交量前 N 个算 RSI |
| `RADAR_POOL_MAX_PAGES` | 3 | 拉取活跃池子页数（每页 200） |
| `RADAR_DRY_RUN` | 0 | 置 1 时不发送飞书 |

## 测试

```bash
pip install -r requirements-dev.txt
pytest -q
```

## 数据来源

Meteora DLMM datapi：`https://dlmm.datapi.meteora.ag`
（`/pools` 列表、`/pools/{address}` 元数据、`/pools/{address}/ohlcv` K 线）。
