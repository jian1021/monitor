# Meteora DLMM 扫描器

这是统一看板中的 Solana Meteora DLMM 扫描模块。模块只扫描 DLMM 池，通过官方池发现 API 分页获取池与代币指标，并提供筛选、评分及人工加入 Meme 超买超卖监控列表功能。后台完整扫描结束后会按已保存的筛选条件评估结果并发送飞书报警。旧数据库里的其他池类型记录保留，但不会进入扫描器候选列表。

## 功能

- DLMM 全池扫描：分页、去重、进度显示、失败重试、断点续扫及手动/定时扫描。
- 筛选：代币市值、成交量、TVL、Fees/TVL、bin step、代币持仓和池完整性。
- 结果：排序、导出 CSV、勾选通过筛选的池并导入 Meme 超买超卖监控列表。
- 后台扫描：运行主项目 `python main.py`；所有模块统一读取 `FEISHU_WEBHOOK`，Meteora 可选签名密钥使用 `FEISHU_WEBHOOK_SECRET`。扫描器与 Streamlit 必须使用同一个数据库路径，后台扫描完成后才可评估筛选结果并报警。
- 价格区间计算：在“全链扫描”工作区的独立“📐 价格区间计算”标签中使用，不再嵌套于 Meteora 扫描器标签。
- 行情指标：使用 DLMM K 线计算趋势、RSI 与波动相关指标。
- API 状态：显示扫描进度、接口错误与覆盖数量。

## 运行

在仓库根目录安装依赖并启动统一看板：

```bash
python -m pip install -r requirements.txt
streamlit run index.py
```

入口页面为 `pages/meteora_scanner.py`，业务实现位于 `app/application/meteora_scanner/`。数据库默认位于 `app/application/meteora_scanner/data/meteora.db`，可通过 `DB_PATH` 修改。全项目环境变量统一从仓库根目录 `.env` 读取；CoinGecko 与 DEX Paprika 的可选 API Key 也可配置在该文件。

## 数据源

- 池发现与池详情：`https://pool-discovery-api.datapi.meteora.ag`
- DLMM K 线：`https://dlmm.datapi.meteora.ag`
- 页面价格区间使用的可选数据源：CoinGecko、DEX Paprika

所有缺失数据保持为空并在界面中标示，不生成模拟值。API 可访问范围不等于链上全部历史或现存池。

## 数据与安全

数据库文件及本地 `.env` 不提交到 Git。扫描记录会保存在模块数据库中；不支持的历史池类型保留在原文件内，但不参与筛选或策略选池。
