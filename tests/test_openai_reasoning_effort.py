from unittest.mock import patch


def _capture_request(monkeypatch, *, tools=True):
    from models.openai.open_ai_bot import OpenAIBot

    config = {
        "model": "gpt-5.6-sol",
        "open_ai_api_key": "test-key",
        "open_ai_api_base": "https://litellm.example/v1",
        "open_ai_allowed_openai_params": ["reasoning_effort"],
    }
    captured = {}

    with patch("models.openai.open_ai_bot.conf", return_value=config):
        bot = OpenAIBot()
    monkeypatch.setattr(bot, "get_api_config", lambda: {
        "api_key": config["open_ai_api_key"],
        "api_base": config["open_ai_api_base"],
        "model": config["model"],
        "allowed_openai_params": config["open_ai_allowed_openai_params"],
    })

    def fake_sync(params, _api_key, _api_base):
        captured.update(params)
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(bot, "_handle_sync_response", fake_sync)
    tool_defs = None
    if tools:
        tool_defs = [{
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "lookup",
                "parameters": {"type": "object", "properties": {}},
            },
        }]
    bot.call_with_tools(
        messages=[{"role": "user", "content": "test"}],
        tools=tool_defs,
        model="gpt-5.6-sol",
        reasoning_effort="low",
    )
    return captured


def test_gpt56_sol_keeps_low_reasoning_with_tools(monkeypatch):
    request = _capture_request(monkeypatch, tools=True)

    assert request["reasoning_effort"] == "low"
    assert request["allowed_openai_params"] == ["reasoning_effort"]
    assert "temperature" not in request
    assert request["tools"]


def test_official_openai_request_omits_litellm_passthrough(monkeypatch):
    from models.openai.open_ai_bot import OpenAIBot

    config = {
        "model": "gpt-5.6-sol",
        "open_ai_api_key": "test-key",
        "open_ai_api_base": "https://api.openai.com/v1",
        "open_ai_allowed_openai_params": [],
    }
    captured = {}
    with patch("models.openai.open_ai_bot.conf", return_value=config):
        bot = OpenAIBot()
    monkeypatch.setattr(bot, "get_api_config", lambda: {
        "api_key": config["open_ai_api_key"],
        "api_base": config["open_ai_api_base"],
        "model": config["model"],
        "allowed_openai_params": config["open_ai_allowed_openai_params"],
    })
    monkeypatch.setattr(
        bot,
        "_handle_sync_response",
        lambda params, _api_key, _api_base: captured.update(params) or {},
    )

    bot.call_with_tools(
        messages=[{"role": "user", "content": "test"}],
        model="gpt-5.6-sol",
        reasoning_effort="low",
    )

    assert captured["reasoning_effort"] == "low"
    assert "allowed_openai_params" not in captured


def test_openai_gpt56_sol_reasoning_capability():
    from models.reasoning_capabilities import get_reasoning_capability

    capability = get_reasoning_capability("openai", "gpt-5.6-sol")

    assert capability["supported"] is True
    assert capability["default"] == "medium"
    assert "low" in [item["value"] for item in capability["options"]]


def test_chatgpt_bot_exposes_litellm_passthrough_config():
    from models.chatgpt.chat_gpt_bot import ChatGPTBot

    config = {
        "bot_type": "openai",
        "model": "gpt-5.6-sol",
        "open_ai_api_key": "test-key",
        "open_ai_api_base": "https://litellm.example/v1",
        "open_ai_allowed_openai_params": ["reasoning_effort"],
    }
    with patch("models.chatgpt.chat_gpt_bot.conf", return_value=config):
        bot = ChatGPTBot()
        api_config = bot.get_api_config()

    assert api_config["allowed_openai_params"] == ["reasoning_effort"]
