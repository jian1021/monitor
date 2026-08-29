import requests
import os

FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")

def send_feishu_msg(webhook, msg):
    if not webhook:
        print(f"未配置 Webhook，仅日志打印:\n{msg}")
        return
    try:
        resp = requests.post(webhook, json={"msg_type": "text", "content": {"text": msg}}, timeout=10)
        print(f"📩飞书推送 status={resp.status_code}")
    except Exception as e:
        import traceback
        print(f"❌飞书消息发送异常 {e}")
        traceback.print_exc()
