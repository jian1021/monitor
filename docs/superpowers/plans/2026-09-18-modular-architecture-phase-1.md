# 模块化架构第一阶段实施计划

## 目标

在不改变现有 CLI、GitHub Actions、Streamlit 路由和监控行为的前提下，为项目建立可逐步迁移的模块边界。第一阶段只提供稳定的核心协议、配置入口、通知端口、监控注册表和调度接口，不搬迁现有业务实现。

## 当前约束

- 现有入口：`main.py`、`lp_position_alert.py`、`index.py`。
- Streamlit 页面仍由 `index.py` 直接挂载 `pages/*.py`。
- GitHub Actions 直接执行 `python main.py` 和 `python lp_position_alert.py`。
- 工作区已有未提交修改，尤其是 LP 告警相关文件；不得覆盖或重写这些变更。
- 当前全量测试存在与本次工作无关的既有失败：`fetch_tokens_by_strategy 定义唯一`。

## 第一阶段目录

```text
app/
  __init__.py
  core/
    __init__.py
    settings.py          # 对现有 config.py 的稳定配置读取接口
    contracts.py         # 监控结果、监控协议等轻量类型
  infrastructure/
    __init__.py
    notifications/
      __init__.py
      feishu.py          # 飞书通知适配器，暂不替换旧调用
  domain/
    __init__.py
    monitoring/
      __init__.py
      registry.py        # 监控名称/启停/间隔的注册协议
  application/
    __init__.py
    monitoring/
      __init__.py
      scheduler.py       # 可测试的到期计算与执行协调接口
```

## 任务

### 1. 建立包骨架和基础导出

新增各级 `__init__.py`，保持无副作用导入。包初始化不得连接数据库、读取网络或导入 Streamlit。

验收：`python -c "import app; import app.core; import app.domain.monitoring; import app.application.monitoring"` 成功。

### 2. 抽象核心类型

在 `app/core/contracts.py` 定义：

- `MonitorResult`：名称、是否成功、消息和附加数据；
- `MonitorSpec`：监控名、间隔键、执行函数；
- `MonitorContext`：当前时间、模块设置和间隔的只读输入。

类型只描述边界，不复制任何现有业务逻辑。避免 `Any`、类型抑制和隐式全局状态。

### 3. 提供兼容配置入口

在 `app/core/settings.py` 提供对现有 `config.py` 的薄封装或稳定访问函数。第一阶段不移动 `config.py`，不改变环境变量和 Streamlit secrets 的优先级。

验收：配置未设置时导入不报错；已有 `FEISHU_WEBHOOK`、`LIBSQL_URL` 等调用结果与旧模块一致。

### 4. 提供通知端口

在 `app/infrastructure/notifications/feishu.py` 提供 `FeishuNotifier`，内部复用现有 `send_feishu_msg.py`。通知失败应返回明确结果或抛出明确异常，不吞掉异常。第一阶段不改现有监控器的调用方。

### 5. 提供监控注册表

在 `app/domain/monitoring/registry.py` 实现：

- 注册监控规格；
- 按名称查询；
- 返回稳定、可测试的注册顺序；
- 拒绝重复名称和空名称。

注册表不负责执行监控、不访问数据库、不依赖 Streamlit。

### 6. 提供调度纯逻辑

在 `app/application/monitoring/scheduler.py` 提取与现有 `main.py` 中 `compute_sleep_seconds` 等价的纯函数，并提供最小的 `MonitoringScheduler` 协调器。第一阶段不替换 `main.py` 主循环，只让新函数可独立测试。

验收：启用/停用任务、空任务、到期任务和轮询上限等行为均有测试覆盖。

### 7. 增加架构守卫测试

新增 `test_app_architecture.py`，验证：

- 新包可导入；
- domain 不导入 Streamlit、requests、数据库实现；
- registry 的重复注册和查询行为；
- scheduler 的边界行为；
- notifier 可用依赖注入替身测试，不发送真实网络请求。

### 8. 文档化迁移规则

新增 `docs/architecture.md`，写明依赖方向：

```text
interfaces -> application -> domain
application -> infrastructure
domain -X-> Streamlit / database / external APIs
```

并记录后续迁移顺序：数据库 repository、外部数据源、LP 告警服务、主循环、Streamlit 页面。

## 后续阶段（本阶段不执行）

1. 将 `db.py` 按 repository 拆分，并保留旧函数兼容层。
2. 将 `dex_client.py` 按数据源拆分。
3. 将 `lp_position_alert.py` 拆为 repository、evaluator、service、CLI adapter。
4. 将 `main.py` 主循环迁移到 scheduler + monitor registry。
5. 将 Streamlit 页面改为薄 UI，业务操作移入 application services。

## 验证命令

```bash
pytest -q test_app_architecture.py
pytest -q test_main_loop.py test_module_intervals.py test_lp_position_alert.py
python -m py_compile app/core/*.py app/domain/monitoring/*.py app/application/monitoring/*.py app/infrastructure/notifications/*.py
```

全量 `pytest -q` 仍需记录现有的 `fetch_tokens_by_strategy 定义唯一` 失败，不得通过删除测试或改变无关模块来掩盖。
