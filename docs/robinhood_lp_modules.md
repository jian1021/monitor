# Robinhood Chain LP 模块

Robinhood 池服务与区间 APR 工具已纳入本仓库 `modules/`，与主看板共用仓库版本管理，内部代码保持模块边界。

| 目录 | 技术 | 用途 | 主看板入口 |
|---|---|---|---|
| `modules/robinhoodpools` | Python | Robinhood Chain LP 池索引服务与 API | `app/infrastructure/rhpools/client.py`（服务进程管理，由 `app/infrastructure/market_data/rh_fee_sources.py` 复用，供退出策略取数） |
| `modules/robinhood-chain-lp-tools` | TypeScript/Node | Uniswap v3/v4 区间 APR 估算与回放 | `pages/lp_apr.py`、`app/infrastructure/lp_tools/runner.py` |

## 安装与运行

```bash
python -m pip install -r requirements.txt
python -m pip install -e modules/robinhoodpools
cd modules/robinhood-chain-lp-tools && npm ci
```

主看板从仓库根目录启动：

```bash
streamlit run index.py
```

本地 `.env`、SQLite 文件和 `node_modules` 不纳入版本控制。
