# 项目模块化边界

当前项目采用渐进式模块化，不一次性重写现有监控器。新代码遵循以下依赖方向：

```text
interfaces -> application -> domain
application -> infrastructure
domain -X-> Streamlit / database / external APIs
```

第一阶段的 `app/` 包提供稳定边界，旧的 `main.py`、`lp_position_alert.py`、`db.py` 和 `pages/` 仍作为兼容入口。后续迁移顺序为：

1. 将 `db.py` 按业务拆成 repository，并保留旧函数转发层。
2. 将 `dex_client.py` 按数据源拆成 market-data adapters。
3. 将 LP 告警拆成 repository、evaluator、application service 和 CLI adapter。
4. 将 `main.py` 的轮询迁移到 scheduler 与 monitor registry。
5. 将 Streamlit 页面收敛为 UI 层，业务操作移入 application services。
