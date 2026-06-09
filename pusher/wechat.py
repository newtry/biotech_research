"""
Wechat Pusher
微信消息推送，支持 Server 酱和 PushPlus
"""
import requests
from typing import Optional


class WechatPusher:
    """微信推送"""

    def __init__(self, channel: str = "serverchan", sc_key: str = None, pushplus_token: str = None):
        """
        初始化推送渠道

        Args:
            channel: "serverchan" 或 "pushplus"
            sc_key: Server 酱 SCKEY
            pushplus_token: PushPlus token
        """
        self.channel = channel
        self.sc_key = sc_key
        self.pushplus_token = pushplus_token

    def push(self, title: str, content: str) -> bool:
        """
        发送微信消息

        Args:
            title: 消息标题
            content: 消息内容

        Returns:
            是否发送成功
        """
        if self.channel == "serverchan":
            return self._push_via_serverchan(title, content)
        elif self.channel == "pushplus":
            return self._push_via_pushplus(title, content)
        return False

    def _push_via_serverchan(self, title: str, content: str) -> bool:
        """通过 Server 酱发送（https://sct.ftqq.com）

        字段约定：text=标题、desp=正文（支持 markdown）
        成功响应：{"code": 0, "message": "..."}
        """
        if not self.sc_key:
            print("ServerChan SCKEY not configured")
            return False

        url = f"https://sct.ftqq.com/{self.sc_key}.send"
        payload = {
            "text": title,
            "desp": content,
        }

        try:
            resp = requests.post(url, data=payload, timeout=10)
            resp.raise_for_status()
            return resp.json().get("code") == 0
        except Exception as e:
            print(f"ServerChan push error: {e}")
            return False

    def _push_via_pushplus(self, title: str, content: str) -> bool:
        """通过 PushPlus 发送"""
        if not self.pushplus_token:
            print("PushPlus token not configured")
            return False

        url = "http://www.pushplus.plus/send"
        payload = {
            "token": self.pushplus_token,
            "title": title,
            "content": content,
            "template": "html"
        }

        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            return resp.json().get("code") == 200
        except Exception as e:
            print(f"PushPlus push error: {e}")
            return False

    def push_text(self, text: str) -> bool:
        """发送文本消息（简化版）"""
        return self.push("创新药动态追踪", text)


class ItChatPusher:
    """使用 itchat 推送（需扫码登录个人微信）"""

    def __init__(self, username: str = "filehelper"):
        """
        Args:
            username: 发送对象，filehelper 为文件传输助手
        """
        self.username = username

    def push(self, title: str, content: str) -> bool:
        """发送消息"""
        try:
            import itchat
            itchat.auto_login(hot=True)

            message = f"{title}\n\n{content}"
            itchat.send(message, toUserName=self.username)
            return True
        except Exception as e:
            print(f"ItChat push error: {e}")
            return False


def _smoke_missing_sc_key() -> None:
    """1. 缺 SCKEY 时直接返回 False，不发请求"""
    p = WechatPusher(channel="serverchan", sc_key="")
    assert p.push("t", "c") is False
    print("[1] 缺 SCKEY → False（未发请求）")


def _smoke_serverchan_success() -> None:
    """2. Server酱 成功路径：URL / payload / code==0"""
    from unittest.mock import patch, MagicMock

    captured = {}

    def fake_post(url, data=None, json=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": 0, "message": "success"}
        return mock_resp

    p = WechatPusher(channel="serverchan", sc_key="SCT123")
    with patch("pusher.wechat.requests.post", side_effect=fake_post):
        ok = p.push("Test Title", "Test Body")

    assert ok is True
    assert captured["url"] == "https://sct.ftqq.com/SCT123.send"
    assert captured["data"] == {"text": "Test Title", "desp": "Test Body"}
    print(f"[2] Server酱 成功 → URL={captured['url']} payload keys={list(captured['data'].keys())}")


def _smoke_serverchan_failure() -> None:
    """3. Server酱 失败响应：code != 0"""
    from unittest.mock import patch, MagicMock

    def fake_post(url, data=None, timeout=None):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": 400, "message": "bad sckey"}
        return mock_resp

    p = WechatPusher(channel="serverchan", sc_key="BAD")
    with patch("pusher.wechat.requests.post", side_effect=fake_post):
        ok = p.push("t", "c")
    assert ok is False
    print("[3] Server酱 失败响应（code=400）→ False")


def _smoke_pushplus_routing() -> None:
    """4. channel='pushplus' 时走 pushplus 端点（不应被发到 sct.ftqq.com）"""
    from unittest.mock import patch, MagicMock

    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": 200, "msg": "ok"}
        return mock_resp

    p = WechatPusher(channel="pushplus", pushplus_token="PP123")
    with patch("pusher.wechat.requests.post", side_effect=fake_post):
        ok = p.push("t", "c")
    assert ok is True
    assert "pushplus" in captured["url"]
    assert "sct.ftqq.com" not in captured["url"]
    print(f"[4] pushplus 路由 → URL={captured['url']}")


def _smoke_unknown_channel() -> None:
    """5. 未知 channel 返回 False"""
    p = WechatPusher(channel="webhook")
    assert p.push("t", "c") is False
    print("[5] 未知 channel → False")


if __name__ == "__main__":
    print("=" * 60)
    print("Wechat Pusher Smoke Test")
    print("=" * 60)
    _smoke_missing_sc_key()
    _smoke_serverchan_success()
    _smoke_serverchan_failure()
    _smoke_pushplus_routing()
    _smoke_unknown_channel()
    print("=" * 60)
    print("All smoke tests passed.")