import asyncio

from app.connectors import BiliAdapter, OneBotAdapter, WeChatAdapter


def test_onebot_mock_send_is_side_effect_free():
    result = asyncio.run(OneBotAdapter({"base_url": "http://onebot", "mock": True}).send("group:1", "hello"))
    assert result == {"ok": True, "mock": True, "adapter": "onebot", "target": "group:1", "content": "hello"}


def test_wechat_bridge_reports_unavailable():
    adapter = WeChatAdapter({"bridge_url": "http://bridge"})
    state = adapter.status()
    assert state.configured is True
    assert state.available is False


def test_bili_adapter_missing_binary_has_safe_error():
    adapter = BiliAdapter({"credential_ref": "local"})
    try:
        asyncio.run(adapter.discover("test"))
    except RuntimeError as exc:
        assert "bili-cli" in str(exc)

