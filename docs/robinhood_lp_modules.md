# Robinhood Chain LP 模块（rhpools + 区间 APR）

两个外部工具以 git 子模块接入，配套封装全部在 `app/` 分层里：

| 子模块 | 语言 | 作用 | 本仓库封装 |
|---|---|---|---|
| `modules/robinhoodpools` | Python | 链 4663 的 LP 池索引服务（HTTP API + 终端 UI，端口 8196） | `app/infrastructure/rhpools/client.py`、`app/application/rhpools/monitor.py` |
| `modules/robinhood-chain-lp-tools` | TypeScript/Node | Uniswap v3/v4 **指定区间**的 APR 估算与精确回放 | `app/infrastructure/lp_tools/runner.py`、`app/application/lp_tools/monitor.py` |

## 首次安装

```bash
git submodule update --init          # 拉取两个子模块
pip install -r requirements.txt      # rhpools 的 Python 依赖（eth-abi/eth-utils/websockets 等）
brew install node                    # lp-tools 需要 Node（已装可跳过）
cd modules/robinhood-chain-lp-tools && npm install
```

## Streamlit Cloud（运行时自举）

Streamlit Community Cloud **不拉取 git 子模块**，镜像里也没有可用的 Node（Debian 11 的
apt nodejs 是 v12，tsx 要 ≥18）。两个页面为此提供运行时自举，云端开箱即用：

- **观测站页面**：子模块缺失时侧栏出现「📦 一键拉取子模块」；点「启动服务」时
  `ensure_running()` 也会先 `ensure_repo()` 自动 clone 再拉起服务。
- **LP 区间 APR 页面**：工具链未就绪时出现「📦 一键安装」按钮，链路是
  `ensure_repo()`（git clone 子模块）→ `ensure_node()`（从 nodejs.org 下载官方 LTS
  二进制到 `~/.local/share/monitor-node`，免 root，自动注入 PATH）→ `npm install`。
  全程约 1~2 分钟。
- 云端容器每次重启/重新部署后文件系统还原，需要再点一次（按钮会自动检测当前缺什么）。
- CI（`monitor.yml`）已开启 `submodules: true`：GitHub runner 自带 Node，
  `lp_apr` 模块可在 CI 里正常跑观察列表告警；`rhpools` 仍保持
  `RHP_AUTO_START=0`（一次性进程不起常驻服务）。
- 本机已 init 子模块、已有 Node 时，自举全部直接跳过，无任何额外开销。

## rhpools 观测站（主循环模块 `rhpools`，默认 5 分钟）

- `app/infrastructure/rhpools/client.py`：探活 `/api/lp/status`；不通时自动拉起
  `python -m rhpools.lp_server`（单实例，日志 `logs/rhpools.log`），等就绪后返回状态。
- `app/application/rhpools/monitor.py` 每轮做两件事并推飞书（去重表 `pump_alert_sent`）：
  1. **服务/索引健康**：`state` 非 live/warming/catching_up（即 degraded/stopped）、
     `lag_s > 600`、预热失败 → 1 小时去重；服务完全不可用 → 1 小时去重。
  2. **池间价差**：`/api/lp/dislocations` 中价差 ≥ 50 bps、浅边深度 ≥ $300、
     最近 1 小时有状态更新的交易对 → 同一交易对 2 小时去重。
- 面板 `pages/robinhood_pools.py`：索引状态（head/落后/历史覆盖/数据源）、
  池子列表（窗口/排序/协议/搜索）、价差表；侧栏可探活与启动服务。

环境变量：`RHP_HOST`/`RHP_PORT`（默认 127.0.0.1:8196）、`RHP_AUTO_START=0` 关闭自启
（monitor.yml 的 CI 里已关）、`RHP_DATA_DIR` 等透传上游（见 `rhpools --help`）。
`ROBINHOOD_RPC` 非空时会作为 `RHP_RPC_URL` 传给服务。

停止服务：`pkill -f rhpools.lp_server`（主循环下轮会按需再拉起）。

## 区间 APR（主循环模块 `lp_apr`，默认 60 分钟）

- `runner.py`：用 `node_modules/.bin/tsx` 跑两个脚本并解析文本输出（纯函数，
  `test_lp_apr_parser.py` 覆盖）：
  - `uni-range-apr --pool X --width 10 --capital 10000`：秒级估算；
    输出池均 APR、区间内 APR、波动调整后 APR、时间在区间占比等。
  - `uni-range-replay --hours N [--validate]`：回放每笔 Swap，实测区间收益；
    `--validate` 会给出 replay vs feeGrowthGlobal 的比值（应≈1）。
- 观察列表存 `lp_apr_watchlist` 表（`app/infrastructure/db/apr_watchlist.py`）：
  池子、宽度、仓位、APR 上下限（**存百分数**：5.0 = 5%）。
  主循环对启用的池逐个估算，越界推飞书，同池同方向 6 小时去重；
  结果写回 `last_apr`。
- 面板 `pages/lp_apr.py`：模块启停、计算器（估算 / 宽度扫描 / 回放）、观察列表增删。

要点：池均 APR 是混合所有流动性的数字，区间 APR 才是你的；只计手续费收益，
不含无常损失与 gas；单窗口是样本不是预测（工具 README 有完整限制说明）。

## 告警与去重

两个模块都复用 `pump_alert_sent` 表（module + key + TTL）：

| module | key | TTL |
|---|---|---|
| `rhpools_status` | `state:*` / `lag` / `warm_error` / `service_down` | 1 小时 |
| `rhpools_dislocation` | `token0:token1` | 2 小时 |
| `lp_apr` | `pool:low` / `pool:high` | 6 小时 |

数据库不可用时去重失效（全放行，宁可重复也不漏报）——与既有 pump 模块行为一致。

## 测试

- `test_rhpools_module.py` — 状态/价差告警构建、主流程打桩、环境开关
- `test_lp_apr_parser.py` — 两个 CLI 的真实输出格式解析、布局断言
- `test_lp_apr_monitor.py` — 阈值判定（百分数语义）
- `pytest.ini` 把 `modules/` 排除在收集之外（子模块自带测试不属于本仓库）
