# LP Bands 可视化页面设计

日期: 2026-09-05
状态: 已批准（用户确认 plotly 方案）

## 目标

为 `lp_bands_tool.py` 提供 Streamlit 可视化页面，展示价格、自适应上下界与时间的关系。

## 产品 | Pages

`pages/lp_bands.py`（纳入现有 Streamlit 监控系统，自动继承登录）

## 功能

1. 控制区（sidebar）: token 地址、链（默认 sol）、周期（默认 1h）、`--days`（可选）、`lam`、`width` 参数
2. 数据流: 直接 `import lp_bands_tool`，复用 `gmgn_config_check → load_gmgn → bocpd_segments → build_bands` 纯函数（不包 subprocess）
3. 主图（plotly）:
   - X = 时间, Y = 价格（**对数刻度**，meme 币与 SOL 差 7 个数量级）
   - 三条线: close（收盘价）、upper（上界）、lower（下界）
   - 分段色带: 每个 segment 一个矩形背景，显示价格处于/偏离区间的时段
   - 交互: 悬停 tooltip、缩放、图例
4. 副表格: 每段的中枢 L、上界、下界、起止时间、`in_range` 占比

## 技术决策

| 项 | 决策 |
|---|---|
| 图表库 | plotly（需加入 requirements.txt） |
| 数据源 | 页面输入地址 → gmgn-cli 实时拉取 |
| 复用 | lp_bands_tool 纯函数，不 subprocess |
| Y 轴 | log 刻度 |
| PnL | 不进首版；表格保留 in_range 占比 |

## 边界

- 最多 100 根 K 线（GMGN API 单次上限），轻量
- 不实现 PnL 预估的可视化（后续迭代）
- 路径参数校验: 空地址/空结果给出可操作提示（沿用工具内提示文案）

## 成功标准

- 页面在 Streamlit 中可访问（登录后）
- 输入 SOL 地址 So111...12 → 5 段区间、三条线、色带正常渲染
- 输入年轻 meme 币 → 友好错误提示（无崩溃）