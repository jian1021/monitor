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
  infrastructure/  db/{client,assets,module_settings,intervals,pump_alerts,lp_alert}.py
                   market_data/{http,chains,dexscreener,okx,geckoterminal,meteora,evm_rpc}.py
                   notifications/feishu.py
```

## 兼容入口（保留，勿在未迁移前删除）

- `main.py` — CLI 主循环：注册监控到 `MonitorRegistry`，由 `MonitoringScheduler` 计算到期与睡眠。
- `lp_position_alert.py` — LP 告警 CLI + 转发层。
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
