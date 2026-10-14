# Monitor — 链上与市场监控看板

这是统一的 Streamlit 监控项目，包含 Meteora 和 Robinhood Chain 全池扫描、Meme 代币监控、LP 与退出策略告警，以及股票和加密货币监控。

## 项目结构

```text
index.py                         Streamlit 看板入口
main.py                          后台监控循环入口
app/
  core/                           配置与模块契约
  domain/                         纯业务规则
  application/                    业务模块与用例编排
    meteora_scanner/               Meteora DLMM API、扫描、筛选、评分与数据
    fee_screen/                    Robinhood Chain Uniswap 全池扫描与筛选
    exit_strategy/                 人工选池与退出告警
    monitors/                      RSI、Meme、价格等监控
  infrastructure/                 数据库、行情 API 与飞书通知适配
  interfaces/                      页面共用组件
pages/                             Streamlit 页面
scripts/                           CLI 与兼容入口
modules/                           Meteora 雷达、反转监控及 Robinhood 模块
tests/                             主项目及 Meteora 扫描器测试
docs/                              架构、功能与 API 说明
```

页面、应用逻辑、基础设施和外部适配各有明确位置。`pages/onchain_hub.py` 将链上代币管理、Meme RSI、Meteora DLMM 扫描和 Robinhood Uniswap 扫描整合为标签页；各页面的扫描逻辑保留在对应应用模块中。

所有项目源码均纳入本仓库的 `modules/` 目录：Meteora 雷达、代币反转监控、Robinhood 池服务和 LP 工具作为独立模块维护，共用仓库级版本管理。各模块可单独启动，但密钥、数据库、运行状态和 `node_modules` 均不提交。

## 本地运行

需要 Python 3.12。克隆后安装项目依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

启动网页看板：

```bash
streamlit run index.py
```

需要后台定时监控时，在另一个终端启动主循环；保持进程运行：

```bash
python main.py
```

主循环会按各模块页面中的后台监控设置执行任务，Meteora DLMM 全池扫描默认每 60 分钟运行一次。后台进程与网页必须使用相同的 `DB_PATH` 和持久化数据目录，扫描状态及结果才会在页面中显示。`python main.py --once` 会等待本轮 Meteora 扫描完成后退出。

运行测试：

```bash
python -m pip install pytest
python -m pytest -q
```

## 配置

主应用配置由 `app/core/config.py` 加载，可将 `.env` 放在 `app/core/.env`。例如：

```dotenv
ADMIN_USER=admin
ADMIN_PASS=请替换为安全密码
FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/...
LIBSQL_URL=
LIBSQL_TOKEN=
ROBINHOOD_RPC=
```

Meteora 扫描器仅支持 DLMM，数据源与飞书配置可放在 `app/application/meteora_scanner/.env`。本地 `.env` 和数据库文件不会提交到仓库。

## 功能说明

统一看板在左侧导航中提供全链扫描、策略与数据和系统管理模块。“全链扫描”页面使用标签页直接展示链上代币管理、批量 Meme RSI、Solana Meteora DLMM 扫描及 Robinhood Chain Uniswap 全池扫描。Meteora 扫描结果可人工挑选并加入链上 Meme 监控；退出策略可人工选池，并按价格下限、RSI(3) 或布林带条件发送飞书告警。后台 `main.py` 负责按模块设置的间隔执行监控。

## 文档

- [架构与模块边界](docs/architecture.md)
- [Meteora 扫描器说明](docs/meteora_scanner/README.md)
- [Robinhood Chain Uniswap 全池扫描](docs/fee_screen.md)
- [Robinhood LP 模块](docs/robinhood_lp_modules.md)
- [Robinhood Chain 池与行情 API 验证](docs/verification_robinhood_chain.md)
