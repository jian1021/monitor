# 项目模块化边界

项目已按渐进式模块化完成迁移：业务实现位于 `app/` 分层包，根目录旧模块保留为薄入口 / 转发层，行为与对外接口不变。

依赖方向：

```text
interfaces -> application -> domain
application -> infrastructure
domain -X-> Streamlit / database / external APIs
```

## 目录结构

```text
app/
  core/            contracts.py（MonitorSpec/MonitorResult）、settings.py（稳定配置入口）
  domain/          monitoring/registry.py、lp_alert/{models,evaluator}.py（纯逻辑，无 IO）
  application/     monitoring/scheduler.py、lp_alert/service.py、assets/service.py
                   rhpools/monitor.py（观测站健康 + 池间价差告警）
                   lp_tools/monitor.py（区间 APR 阈值告警）
  infrastructure/  db/{client,assets,module_settings,intervals,pump_alerts,lp_alert,apr_watchlist}.py
                   market_data/{http,chains,dexscreener,okx,geckoterminal,meteora,evm_rpc}.py
                   rhpools/client.py（rhpools 子进程管理 + HTTP 客户端）
                   lp_tools/runner.py（Node CLI 运行 + 输出文本解析）
                   notifications/feishu.py
modules/           git 子模块（外部代码，勿直接改）
  robinhoodpools/          rhpools 观测站服务（Python，链 4663 LP 索引）
  robinhood-chain-lp-tools/ Uniswap 区间 APR 估算/回放工具（TypeScript/Node）
```

## 兼容入口（保留，勿在未迁移前删除）

- `main.py` — CLI 主循环：注册监控到 `MonitorRegistry`，由 `MonitoringScheduler` 计算到期与睡眠。
- `lp_position_alert.py` — LP 告警 CLI + 转发层。
- `monitor_rhpools.py` / `monitor_lp_apr.py` — 新模块的薄入口（实现在 `app/application/`）。
- `db.py` / `dex_client.py` / `asset_config.py` — 转发层，导出旧名字。
- `pages/` — Streamlit 页面，仅渲染；业务调 application service。

## 约定

1. `app/domain` 不得 import streamlit / requests / libsql_client / pandas / db / send_feishu_msg（`test_app_architecture.py` 守卫）。
2. 改业务逻辑改 `app/` 下的实现；根目录转发层只负责导出旧名字。
3. **页面文件名不得与顶层包/模块重名**。Streamlit 把页面当主脚本执行时会把 `pages/` 置于 `sys.path` 首位，重名会让 `import X` 命中页面自身。历史教训：`pages/app.py` 曾与 `app/` 包冲突，已改名为 `pages/dashboard.py`。

## 迁移记录

1. ✅ `db.py` → `app/infrastructure/db/*` repository（旧 `db.py` 为转发层）。
2. ✅ `dex_client.py` → `app/infrastructure/market_data/*` adapters（旧模块为转发层）。
3. ✅ LP 告警 → `domain/lp_alert` + `infrastructure/db/lp_alert` + `market_data/{meteora,evm_rpc}` + `application/lp_alert/service`，顶层为 CLI。
4. ✅ `main.py` 轮询 → scheduler + monitor registry，删除重复的 `compute_sleep_seconds`。
5. ✅ Streamlit 页面收敛为 UI 层：`pages/app.py` 的内联 `asset_config` 副本已删除，资产逻辑移入 `app/application/assets/service.py`。
6. ✅ rhpools 观测站接入：`modules/robinhoodpools` 子模块 + `infrastructure/rhpools/client`（进程管理）+ `application/rhpools/monitor`（健康/价差告警），主循环模块名 `rhpools`，页面 `pages/robinhood_pools.py`。
7. ✅ robinhood-chain-lp-tools 接入：`modules/robinhood-chain-lp-tools` 子模块 + `infrastructure/lp_tools/runner`（Node CLI 运行与解析）+ `application/lp_tools/monitor`（APR 阈值告警）+ `infrastructure/db/apr_watchlist`，主循环模块名 `lp_apr`，页面 `pages/lp_apr.py`。详见 `docs/robinhood_lp_modules.md`。
