# 代币反转监控（MVP）

## 功能
- Streamlit 页面输入代币合约地址，选择 Solana 或 Robinhood Chain。
- 监控列表支持开始/停止、删除。
- 通过 GeckoTerminal API 查询代币交易池和 5 分钟 K 线。
- 检查“停止创新低”与“收盘价突破局部高点”。
- 配置飞书自定义机器人 Webhook，满足双条件时发送消息。
- 不自动交易。

## 运行（macOS）
```bash
cd token-reversal-monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export FEISHU_WEBHOOK='粘贴你的飞书机器人Webhook'  # 可选，也可在网页侧栏输入
streamlit run app.py
```

## 监控规则
- 使用 5 分钟 K 线。
- “停止创新低”：最近 N 根已收盘 K 线的最低价，不低于此前 N 根 K 线的最低价。
- “突破局部高点”：最新已收盘 K 线的收盘价，大于此前 M 根已收盘 K 线的最高价。
- 默认 N=3、M=6。它们只是可调整的初始规则，不是经过验证的盈利策略。

## 数据源与限制
- K 线和交易池：GeckoTerminal 公共 API，接口形如 `/networks/{network}/tokens/{token_address}/pools` 与 `/networks/{network}/pools/{pool_address}/ohlcv/minute`。
- GeckoTerminal 公共 API 有请求频率限制；默认刷新设置至少 60 秒，监控地址较多时应提高间隔。
- Robinhood Chain 采用网络 ID `robinhood` 尝试查询；其是否已被 GeckoTerminal 完整收录需以接口返回为准。如果接口不支持或没有交易池，程序会显示错误状态，不会生成信号。
- 本版本页面不是后台守护进程；页面关闭后不会持续监控。需要长期运行可部署到服务器并改成后台任务。
- 飞书 Webhook 是秘密信息，请勿提交到公开仓库。
