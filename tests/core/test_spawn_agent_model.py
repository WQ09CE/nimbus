from unittest.mock import MagicMock, patch

from nimbus.core.tools.spawn_agent import _resolve_model_for_role


def test_resolve_model_for_role_inherits_parent_without_override():
    cfg = MagicMock()
    cfg.agent_roles = {}
    cfg.default_model = "google/gemini-3-flash-preview"

    with patch("nimbus.config.get_config", return_value=cfg):
        model, inherited = _resolve_model_for_role("reader", "ollama/gemma4:26b")

    assert model == "ollama/gemma4:26b"
    assert inherited is True


def test_resolve_model_for_role_uses_explicit_override():
    cfg = MagicMock()
    cfg.agent_roles = {"reader": "ollama/gemma4:26b"}
    cfg.default_model = "google/gemini-3-flash-preview"

    with patch("nimbus.config.get_config", return_value=cfg):
        model, inherited = _resolve_model_for_role("reader", "openai/gpt-5")

    assert model == "ollama/gemma4:26b"
    assert inherited is False


def test_resolve_model_for_role_falls_back_to_default_without_parent():
    cfg = MagicMock()
    cfg.agent_roles = {}
    cfg.default_model = "ollama/gemma4:26b"

    with patch("nimbus.config.get_config", return_value=cfg):
        model, inherited = _resolve_model_for_role("worker")

    assert model == "ollama/gemma4:26b"
    assert inherited is False
