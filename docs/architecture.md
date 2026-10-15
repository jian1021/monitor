# 项目模块化边界

项目采用分层模块结构：业务实现位于 `app/`，Streamlit 页面位于 `pages/`，命令行兼容入口集中在 `scripts/`；根目录只保留 `index.py` 和 `main.py` 两个应用入口。

依赖方向：

```text
interfaces(pages) -> application -> domain
application -> infrastructure
domain -X-> Streamlit / database / external APIs（由架构测试检查）
```

## 目录组织规则

**文件夹跟着"边界"走，不跟"文件数"走**——判断标准是这段代码和谁内聚，而不是它今天有几个文件：

| 层 | 组织方式 | 现状示例 |
|---|---|---|
| `app/application/<功能>/` | 一个功能域一个文件夹；今天只有 1 个文件也用文件夹（功能会长，晚改目录不如早定边界） | `lp_tools/`、`lp_alert/`、`assets/`、`monitoring/`、`lp_bands/` |
| `app/application/monitors/` | **遗留单文件监控的隔离区**：4 个老监控脚本平铺于此，只许改不许长；新功能一律建自己的文件夹，不再往这里加 | `rsi.py`、`meme_underval.py`、`meme_rsi_batch.py`、`price.py` |
| `app/domain/<子域>/` | 按子域分文件夹（纯逻辑，无 IO） | `lp_alert/`、`monitoring/` |
| `app/infrastructure/<边界>/` | 按外部系统或技术边界分文件夹 | `db/`(每功能一个 store 文件)、`market_data/`(每数据源一个文件)、`rhpools/`、`lp_tools/`、`notifications/` |
| `app/interfaces/` | 页面共用的 UI 组件（渲染层，依赖 streamlit） | `asset_config.py`、`asset_grid.py` |
| `app/core/` | 稳定契约，平铺即可（只有 3 个文件） | `contracts.py`、`config.py`(.env/secrets 加载)、`settings.py`(统一出口) |

由此推断的两个实用结论：

1. 看到 `lp_bands/tool.py` 这种"单文件文件夹"是**故意的**——它是一个边界，不是一个孤立的文件。
2. 判断新代码放哪：新功能 → `app/application/<新文件夹>/`；改老监控 → 只改 `monitors/` 里对应文件；新的数据源封装 → `infrastructure/market_data/` 加一个文件；新的存储 → `infrastructure/db/` 加一个 store。

## 目录结构

```text
index.py               # Streamlit 入口（登录 + 导航）
main.py                # 主循环入口（注册 MonitorSpec，scheduler 调度）
scripts/               # 命令行兼容入口（具体实现位于 app/ 下）
app/                   # 分层实现（见上方规则）
pages/                 # Streamlit 页面（仅渲染，业务调 application 层）
modules/               # git 子模块（外部代码，勿直接改）
  robinhoodpools/          rhpools 观测站服务（Python，链 4663 LP 索引）
  robinhood-chain-lp-tools/ Uniswap 区间 APR 估算/回放工具（TypeScript/Node）
tests/                 # 主项目与 Meteora 扫描器测试（pytest）
docs/                  # 架构、功能模块与数据源说明
```

`modules/` 下两个子模块是**外部仓库**，内部布局（一个 `src/` 布局、一个扁平）不由本仓库约束；结构差异由 `app/infrastructure/{rhpools,lp_tools}` 封装层吸收，调用方只见统一接口。

## 应用入口

- `main.py` — CLI 主循环：注册监控到 `MonitorRegistry`，由 `MonitoringScheduler` 计算到期与睡眠。
- `scripts/` — CLI/兼容入口集中目录；本地通过 `python -m scripts.<entrypoint>` 调用。页面和主循环直接依赖 `app/` 实现。
- 全局配置：`app/core/config.py`（.env / st.secrets 加载），`app/core/settings.py` 为统一导入出口；主应用逻辑已迁入 `app/` 分层目录，根目录不保留业务转发模块。

## 约定

1. `app/domain` 不得 import streamlit / requests / libsql_client / pandas / db / send_feishu_msg（`tests/test_app_architecture.py` 守卫）。
2. 业务逻辑改 `app/` 下的实现；`scripts/` 只保留兼容和命令行调用入口。
3. **页面文件名不得与顶层包/模块重名**。Streamlit 把页面当主脚本执行时会把 `pages/` 置于 `sys.path` 首位，重名会让 `import X` 命中页面自身。历史教训：`pages/app.py` 曾与 `app/` 包冲突，已改名为 `pages/dashboard.py`。
4. 测试统一放 `tests/`；monkeypatch 要打在**实现模块**上（`scripts/` 转发层是另一个命名空间，打在那里不生效——`tests/test_meme_underval.py` 有示例）。

## 迁移记录

1. ✅ `db.py` → `app/infrastructure/db/*` repository（旧 `db.py` 为转发层）。
2. ✅ `dex_client.py` → `app/infrastructure/market_data/*` adapters（旧模块为转发层）。
3. ✅ LP 告警 → `domain/lp_alert` + `infrastructure/db/lp_alert` + `market_data/{meteora,evm_rpc}` + `application/lp_alert/service`，顶层为 CLI。
4. ✅ `main.py` 轮询 → scheduler + monitor registry，删除重复的 `compute_sleep_seconds`。
5. ✅ Streamlit 页面收敛为 UI 层：`pages/app.py` 的内联 `asset_config` 副本已删除，资产逻辑移入 `app/application/assets/service.py`。
6. ✅ rhpools 观测站接入：`modules/robinhoodpools` 模块 + `infrastructure/rhpools/client`（进程管理）。该观测面板与主循环 `rhpools` 模块**已在后续精简中移除**（见第 11 条），`infrastructure/rhpools/client` 仅作为服务进程管理保留，由 `market_data/rh_fee_sources` 复用。
7. ✅ robinhood-chain-lp-tools 接入：`modules/robinhood-chain-lp-tools` 模块 + `infrastructure/lp_tools/runner`（Node CLI 运行与解析）+ `application/lp_tools/monitor`（APR 阈值告警）+ `infrastructure/db/apr_watchlist`，主循环模块名 `lp_apr`，页面 `pages/lp_apr.py`。详见 `docs/robinhood_lp_modules.md`。
8. ✅ Robinhood Chain Uniswap 全池扫描：`domain/fee_screen`（纯筛选逻辑）+ `market_data/rh_fee_sources`（Robinhood 链验证/rhpools+DexScreener 取数）+ `application/fee_screen/{service,monitor,ui_data}` + `db/fee_screen_store`。Robinhood 与 Meteora DLMM 扫描器直接嵌入 `pages/onchain_hub.py` 的全链扫描标签页；飞书通知使用共享配置，不提供重复的页面级 Webhook 输入。（该全池筛选/监控栈已在第 11 条移除，`rh_fee_sources` 保留供退出策略复用。）
9. ✅ 监控实现迁入 `application/monitors/`；兼容/CLI 入口集中到 `scripts/`，页面与主循环直接调用应用层实现；命令行入口通过 `python -m scripts.*` 启动。其他实现迁移：`keccak_pure` → `infrastructure/market_data/`、飞书发送 → `infrastructure/notifications/sender.py`、LP bands → `application/lp_bands/`。
10. ✅ 根契约层退役：`config.py` → `core/config.py`（`settings.py` 为统一出口）；`db.py`/`dex_client.py` 转发壳删除（调用方直连 `infrastructure/db/*`、`market_data/dexscreener`）；`asset_config.py`/`asset_grid.py` → 新增 `interfaces/` 层；顺带修复 `fetch_all_assets()` 空结果无列结构导致的无数据库页面 KeyError（HEAD 上即存在的老 bug）。
11. ✅ Robinhood 精简：`pages/fee_screen.py` 只保留 DexPaprika Uniswap 候选池扫描（`app/interfaces/dexpaprika_scan.py`）；删除 `domain/fee_screen`、`application/fee_screen`、`db/fee_screen_store`、`interfaces/robinhood_observer`、主循环 `fee_screen` 模块及 `scripts/monitor_fee_screen.py`；`market_data/rh_fee_sources` 与 `infrastructure/rhpools/client` 保留（退出策略沿用）。
12. ✅ 第 10 条遗漏的调用方收尾 + 一批存量缺陷修复：
    - `dex_client` 删除后仍有 3 处旧导入（`application/monitors/rsi.get_token_rsi`、`application/lp_bands/tool.load_gmgn`、`pages/st_mainstream_crypto`），分别改指 `market_data/geckoterminal.fetch_ohlcv` 与 `market_data/okx.search_okx_symbols`。修复前链上代币 RSI 监控每轮抛 `ModuleNotFoundError`（被兜底吞成 `(None, None)`，永不告警），LP 区间可视化取数必然失败。
    - `db/lp_alert.load_rules(enabled_only=True)` 原来只取 `status='open'`，而取数失败会把 `status` 写成 `'error'`，一次网络抖动就让规则永久退出监控；改为 `status IN ('open','error')`，失败后下一轮自动重试。
    - `application/assets/service.fetch_all_assets` 的 except 回退分支复制了主查询（列完全相同），缺列时异常直接冒到页面；改为按列数逐档真实回退。
    - `market_data/okx.search_okx_symbols` 读的是不存在的 `base`/`quote`（OKX v5 现货为 `baseCcy`/`quoteCcy`），候选名恒为 `/`。
    - `application/monitors/rsi` 的 A 股代码解析统一为 `_split_a_share_code`：`_to_bs_code` 支持 `sh.`/`sz.` 写法，腾讯兜底源却按首字符判市场，会拼出 `szsh.600000` 而必然失败。
    - `pages/lp_apr`、`pages/lp_position_alert` 里无条件 `st.rerun()` 会把成功/失败提示立刻冲掉，导致「安装失败」「检查完成」等反馈用户看不到。
