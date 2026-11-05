import requests
import os


def send_feishu_msg(webhook, msg) -> bool:
    """发送文本消息；只有飞书业务码为 0 才算成功。

    飞书自定义机器人在「签名校验失败」「限流(9499)」「关键词不匹配」等情况下
    同样返回 HTTP 200，body 里带非 0 的 code。若只判 status_code，
    调用方会把失败当成功、写入「已推送」标记，导致后续 N 小时内不再重试 → 漏报。
    """
    if not webhook:
        print(f"未配置 Webhook，仅日志打印:\n{msg}")
        return False
    try:
        resp = requests.post(webhook, json={"msg_type": "text", "content": {"text": msg}}, timeout=10)
        print(f"📩飞书推送 status={resp.status_code}")
        if resp.status_code != 200:
            return False
    except Exception as e:
        import traceback
        print(f"❌飞书消息发送异常 {e}")
        traceback.print_exc()
        return False

    try:
        body = resp.json()
    except ValueError:
        # 非 JSON 响应（多为网关/代理页面）：HTTP 200 视为已送达
        return True
    if not isinstance(body, dict):
        return True
    code = body.get("code", body.get("StatusCode", 0))
    if code in (0, None):
        return True
    print(f"❌飞书返回业务错误 code={code} msg={body.get('msg') or body.get('StatusMessage')}")
    return False
