# 模块化架构第 2–6 阶段实施计划

- 日期：2026-09-24
- 关联：`docs/architecture.md`、`docs/superpowers/plans/2026-09-18-modular-architecture-phase-1.md`
- 状态：P2 执行中，P3–P6 待逐阶段展开

## 1. 背景

第一阶段已落地 `app/` 骨架（core / domain / application / infrastructure）与守卫测试，但**没有任何生产代码接入**。根目录仍是平铺模块，且存在多处重复实现，这是"看不懂"的根因：

- `ensure_asset_schema` 在 `db.py`、`asset_config.py`、`pages/app.py` 三处并存。
- `compute_sleep_seconds` 在 `main.py` 与 `app/application/monitoring/scheduler.py` 各有一份。
- `pages/app.py` 内联复制了 `asset_config.py` 的整套资产增删改查函数。
- `lp_position_alert.py`（1151 行）把 DB、外部数据源、判定、消息、循环、CLI 混在一个文件，并重复声明了 `dex_client.py` 的 BASE/HEADERS 常量。

本计划把生产代码逐步迁入 `app/`，每阶段保持系统可运行、可回滚。

## 2. 不变式（每阶段必须成立）

1. `python main.py`、`python lp_position_alert.py`、`python monitor_price.py` 的行为与输出不变（GitHub Actions 依赖）。
2. `index.py` 的 Streamlit 路由与各页面行为不变。
3. 旧模块名（`db`、`dex_client`、`lp_position_alert`、`asset_config`）保留**转发 shim**，旧 import 继续可用；shim 只在最后阶段清理。
4. `app/domain` 不得 import streamlit / requests / libsql_client / pandas / db / send_feishu_msg（`test_app_architecture.py` 守卫）。
5. 不改动日志文案、飞书推送文案、SQL 语句语义。

## 3. 目标结构

```text
app/
  core/            contracts.py, settings.py              [已有]
  domain/
    monitoring/    registry.py                            [已有]
    assets/        rules.py            # 纯规则: looks_like_address / token_candidates / format_candidate / stale_module_names
    lp_alert/      evaluator.py, models.py  # 纯逻辑: evaluate / needs_rearm / units_plausible / parse_* / floor_from_ohlcv
  application/
    monitoring/    scheduler.py                           [已有]
    assets/        service.py          # 资产增删改查编排
    lp_alert/      service.py          # check_rule / run_once 编排
  infrastructure/
    notifications/ feishu.py                              [已有]
    db/            client.py, assets.py, module_settings.py, intervals.py, pump_alerts.py, lp_alert.py
    market_data/   dexscreener.py, geckoterminal.py, okx.py, meteora.py, evm_rpc.py
  interfaces/
    cli/           main_loop.py, lp_alert.py, price_monitor.py
```

`pages/` 因 Streamlit 约定保留原位，但收敛为"只渲染、业务调 application service"。

## 4. 通用工作流（每阶段一致）

1. 先补/扩展守卫测试或"表面检查"，明确本阶段的对外契约。
2. 迁移实现到 `app/` 对应层。
3. 旧模块降级为转发 shim。
4. 运行验证命令（见 §11）。
5. 单独提交，提交信息说明阶段。

## 5. Phase 2 — `db.py` → repositories（本次执行）

### 5.1 文件结构

```text
app/infrastructure/db/
  __init__.py
  client.py           # get_db_client
  assets.py           # ensure_asset_schema, update_asset_timeframe, load_instruments, update_asset_alert_state
  module_settings.py  # ensure_module_settings, get_module_settings, update_module_setting
  intervals.py        # DEFAULT_INTERVALS, ensure_interval_schema, get_module_intervals, update_module_interval
  pump_alerts.py      # PUMP_ALERT_TTL_HOURS, ensure_pump_alert_schema, filter_unpushed, mark_pushed
```

### 5.2 任务

- **T2.1** 新建包与 5 个 repository 模块，代码**逐字迁移**，不改逻辑、文案、SQL。
- **T2.2** `db.py` 降级为转发 shim，导出全部旧公共名（见 5.4 清单）。
- **T2.3** 表面检查：逐名 import 成功，且 `getattr(db, "ensure_asset_schema")` 可调用（`asset_config.py` / `pages/app.py` 依赖此反射）。
- **T2.4** 验证：`py_compile` + `pytest test_app_architecture.py` + CLI 干跑。

### 5.3 依赖方向

`db.py`（兼容入口）→ `app/infrastructure/db/*` → `app/core/settings.py`（或直接 `config`）。
repository 层**不得** import streamlit（当前 `db.py` 本就无 streamlit 依赖，保持）。

### 5.4 必须保留的公共名（shim 导出清单）

`get_db_client`、`ensure_asset_schema`、`update_asset_timeframe`、`load_instruments`、`update_asset_alert_state`、`ensure_module_settings`、`get_module_settings`、`update_module_setting`、`DEFAULT_INTERVALS`、`ensure_interval_schema`、`get_module_intervals`、`update_module_interval`、`PUMP_ALERT_TTL_HOURS`、`ensure_pump_alert_schema`、`filter_unpushed`、`mark_pushed`。

### 5.5 验收

- 上列每个名字 `from db import <name>` 均成功。
- `python -c "import db; assert callable(db.ensure_asset_schema)"` 成功。
- `pytest -q test_app_architecture.py` 全绿。
- 三个 CLI 入口 `--help` 或干跑不抛 ImportError。

## 6. Phase 3 — `dex_client.py` → market_data adapters

- 按数据源拆分：`dexscreener.py`、`geckoterminal.py`、`okx.py`，共用 `_safe_get` 与限流重试抽到 `http.py`。
- `dex_client.py` 保留转发 shim。
- 让 `lp_position_alert.py` 复用适配器，删除其重复的 `DEXSCREENER_BASE` / `GECKO_BASE` / `HEADERS`。
- 验收：`lookup_token` / `search_tokens` / `search_okx_symbols` / `fetch_ohlcv` / `fetch_token_info` 行为不变。

## 7. Phase 4 — `lp_position_alert.py` 拆分

- repository → `app/infrastructure/db/lp_alert.py`（建表/CRUD/`_execute`）。
- 纯判定 → `app/domain/lp_alert/evaluator.py`（`evaluate` / `needs_rearm` / `needs_rearm_target` / `units_plausible` / `floor_from_ohlcv` / `_target_price`）。
- 解析/模型 → `app/domain/lp_alert/models.py`（`parse_dlmm_position` / `parse_dexscreener_pair` / `to_float`）。
- 编排 → `app/application/lp_alert/service.py`（`check_rule` / `run_once`）。
- 数据源 → `app/infrastructure/market_data/{meteora,evm_rpc}.py`。
- 顶层 `lp_position_alert.py` 变薄 CLI（`run_loop` / `main` 调 service），GH Actions 命令不变。
- 验收：`python lp_position_alert.py --help` 及 `run_once` 干跑不触发真实推送。

## 8. Phase 5 — `main.py` → scheduler + registry

- 四个 `run_*_monitor` 注册进 `MonitorRegistry`，主循环改用 `MonitoringScheduler`。
- 删除 `main.py` 中重复的 `compute_sleep_seconds`，统一用 `app/application/monitoring/scheduler.py`。
- `main.py` 变薄入口。
- 验收：`python main.py` 的到期计算与调度行为与迁移前一致（可用日志比对）。

## 9. Phase 6 — `pages/` 瘦身去重

- `pages/app.py` 删除内联的 `asset_config` 副本，统一调 `app/application/assets/service.py`。
- 其余页面同样只做渲染，业务操作移入 application service。
- 验收：Streamlit 各页面加载无异常，增删改查行为不变。

## 10. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 无业务单测，重构易引入静默回归 | 每阶段 py_compile + 守卫测试 + CLI 干跑 + Streamlit 冒烟；shim 保证零中断 |
| 线上 Streamlit / GH Actions 依赖旧入口 | 不变式 1、2、3 强制保留入口与转发 shim |
| `getattr(_db, ...)` 反射依赖 | T2.3 显式检查可调用性 |
| 工作区已有未提交改动 | 迁移前确认 `git status`，不覆盖无关变更 |

## 11. 验证命令汇总

```bash
# 全量语法检查
python -m py_compile $(git ls-files '*.py')

# 架构守卫
pytest -q test_app_architecture.py

# shim 表面检查
python -c "import db; [getattr(db, n) for n in ('get_db_client','ensure_asset_schema','load_instruments','update_asset_alert_state','get_module_settings','get_module_intervals','DEFAULT_INTERVALS','filter_unpushed','mark_pushed')]"
```

## 12. 后续阶段展开

P3–P6 在本阶段完成后逐个展开为可执行细节，展开时遵循 §4 通用工作流与 §2 不变式。
