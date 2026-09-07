# LP Bands 可视化页面 — 实现计划

日期: 2026-09-05
依据: docs/superpowers/specs/2026-09-05-lp-bands-viz-design.md

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `pages/lp_bands.py` | 新建 | Streamlit 页面: 控制区 + plotly 图表 + 分段表格 |
| `index.py` | 修改 | 注册新页面到导航（"策略与数据"组） |
| `requirements.txt` | 修改 | 添加 plotly |
| `test_lp_bands.py` | 修改 | 新增 chart-data 纯函数测试 |

## 关键设计

- 复用 `lp_bands_tool` 纯函数: `load_gmgn / bocpd_segments / build_bands`（不 subprocess）
- `gmgn_config_check`/`load_gmgn` 内部 `sys.exit()` → 页面用 `try/except SystemExit` 捕获 → `st.error`
- 页内纯函数 `build_chart_df(t, c, h, l, bands)` → 每行一根 K 线: time/close/seg/level/upper/lower/in_range → 供 plotly 与表格共用
- plotly: `log_y=True`, close 实线 + upper/lower 阶梯虚线 + 每 segment 一个矩形色带（`add_vrect` 或 shapes），hover 显示价格
- 登录无需自检（index.py 路由守卫已处理）

## 任务

### T1 — 依赖与测试底座
1. `requirements.txt` 末尾加 `plotly`
2. `test_lp_bands.py` 新增测试（RED）:
   - `build_chart_df` 输入 3 段 bands/600 根 → 行数=600、含 time/close/seg/level/upper/lower/in_range 列、seg 编号单调、in_range∈{0,1}
   - 空 bands 输入 → 空 DataFrame 不崩
3. 跑测试确认失败（build_chart_df 不存在）

### T2 — 实现 build_chart_df
4. 在 `pages/lp_bands.py` 实现 `build_chart_df(t, c, h, l, bands)`
5. 跑测试全绿

### T3 — Streamlit 页面
6. sidebar 控件: token 地址(必填)、chain、resolution、days(可选)、lam(默认120)、width(默认2.0)、"生成区间"按钮
7. 点击后: try/except SystemExit 调 `gmgn_config_check()` + `load_gmgn()`，数据<10根或解析失败 → st.error 展示工具原有提示文案
8. 成功后: `bocpd_segments(logc)` + `build_bands()` → `build_chart_df()` → `st.plotly_chart`（log_y、三条线、segment vrect 色带, `use_container_width=True`）
9. 下方 `st.dataframe`: 每段 start/end/中枢/上界/下界/L内占比；顶部用 `st.metric` 显示段数与数据根数

### T4 — 注册与验证
10. `index.py` 添加 `st.Page("pages/lp_bands.py", title="LP 区间可视化", icon=":material/ssid_chart:")` 并挂到 "策略与数据" 组
11. `pip install plotly`（若缺失）; 跑全部测试; `streamlit run index.py --server.headless true` 冒烟（无 ImportError/语法错）
12. 真实端到端: 页面逻辑以脚本头方式直接调用 `build_chart_df` + SOL 地址 `So11111111111111111111111111111111111111112` 验证数据链路

## 边界

- 不做 PnL 预估可视化
- 不修改 lp_bands_tool.py 的算法逻辑
- 图表交互只依赖 plotly 内建（无自定义 JS）