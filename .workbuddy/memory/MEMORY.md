# 项目长期记忆 — monitor（链上与市场监控看板）

## 架构约定（改代码前必读）
- 分层：`app/core`（配置/契约）→ `app/domain`（纯逻辑）→ `app/application`（用例编排）→
  `app/infrastructure`（DB/行情/通知适配）→ `app/interfaces`（页面共用组件）；`pages/` 只做 UI。
- `app/domain` **禁止** import streamlit / requests / libsql_client / pandas / db / send_feishu_msg，
  由 `tests/test_app_architecture.py` 守卫。
- 外部数据源统一在 `app/infrastructure/market_data/*` 适配（dexscreener / geckoterminal / okx /
  meteora / evm_rpc / rh_fee_sources …）；**不要**再引入已删除的旧顶层模块名（如 `dex_client.py`）。
- 多层包装易漏改：迁移/删除模块后必须全局搜索旧模块名，`scripts/*` 是兼容转发层，
  真的调用点在 `app/` 与 `pages/`。
- `pages/` 文件名不得与顶层包/模块重名（Streamlit 会把 `pages/` 置于 `sys.path` 首位）。
- 测试里 monkeypatch 要打在**实现模块**上，打在 `scripts/` 转发层不生效。

## 运行与测试
- 依赖装在 `.venv`；测试用 `.venv/bin/python -m pytest -q`。
- `modules/` 下是外部仓库（不属本仓库约束，pytest 已 norecursedirs 排除）。
- **`tmp_path` 报错的解法**：默认 TMPDIR 不可写时 pytest 报
  `PermissionError: EEXIST ... pytest-of-unknown`。跑全量前先建一个全新临时目录：
  `TD=$(mktemp -d /tmp/monitor-pytest-XXXXXX) && TMPDIR=$TD .venv/bin/python -m pytest -q`
  （**每次必须用全新目录**，复用同一目录会因 `pytest-N` 序号冲突再次报错）。
  这样 `tests/test_lp_tools_bootstrap.py`（17 例）可正常通过，无需 `--ignore`。
- **页面冒烟**：`streamlit.testing.v1.AppTest.from_file(<绝对路径>).run()`，可逐个加载页面抓运行时异常
  （对 `if __name__ == "__main__"` 守卫的页面也会执行主体）。相对路径按调用文件解析，务必传绝对路径。

## 界面约定（改页面前必读）
- 标题一致性：`st.title` = emoji + 侧边栏名（可加限定语）；`page_title` = 去掉 emoji 的完整标题；
  `page_icon` = 标题 emoji；`dashboard.py` 也要有 `set_page_config`。
- 分区标题统一用 `st.subheader`；页面标题用 `st.title`。**不要**用 `st.markdown("#…")` 冒充
  任何一层标题（含 `app/interfaces/*` 的共用组件，如 monitor_controls）。
- 提示信息：`st.success/error` 之后**不要**跟 `st.rerun()`（会被冲掉）；需要刷新列表时用
  `st.toast(msg, icon="✅") + st.rerun()`。
- 链选项唯一来源：`app/interfaces/asset_config.py` 的 `TOKEN_CHAINS` / `CHAIN_LABELS`，页面不得硬编码。
- onchain_hub 内嵌子页面的渲染入口统一叫 `render_page(embedded: bool = False)`，嵌入时
  **不渲染任何大标题**——标签名即标题（页内再输出 header 就是同屏双标题）。
  hub 挂载时必须显式传 `embedded=True`。
- 标签名前缀统一用 emoji，**不用** ①②③ 这类圆圈数字编号。
- 多标签页容器内每个标签的说明统一由一份「标签名 → 描述」映射提供，子页面内部不再各写一条
  「页面简介」caption（否则顶部堆 1~4 条说明）；映射的 key 必须与标签名一一对应。
- 以上各条由 `tests/test_ui_consistency.py` 强制（10 条：AST 守卫 + AppTest 运行时守卫）。

## 已达成的修复基线（不要回退）
- LP 告警规则：取数失败写 `status='error'` 后**必须**继续被监控（`load_rules` 用
  `status IN ('open','error')`）。
- `fetch_all_assets` 必须按列数逐档回退，保证老库缺列时页面不崩、返回带列结构的 DataFrame。
- A 股代码解析统一走 `_split_a_share_code()`，baostock 与腾讯兜底源共用，支持 `sh.`/`sz.` 前缀。
- 页面里不要在展示提示信息之后无条件 `st.rerun()`（会把成功/失败提示立刻冲掉）。
