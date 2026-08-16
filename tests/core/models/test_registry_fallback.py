"""Registry trimmed to the models actually in use (2026-08):
openai-codex (subscription OAuth) + local ollama qwen3.8 / gemma4.
"""

from nimbus.core.models.registry import ModelRegistry


def test_codex_aliases_point_to_sol():
    # The generic "codex" alias tracks the newest subscription model.
    assert ModelRegistry.normalize("codex") == "openai-codex/gpt-5.6-sol"
    assert ModelRegistry.normalize("codex-latest") == "openai-codex/gpt-5.6-sol"
    assert ModelRegistry.normalize("sol") == "openai-codex/gpt-5.6-sol"
    assert ModelRegistry.normalize("gpt-5.4") == "openai-codex/gpt-5.4"


def test_ollama_models_registered_with_context_windows():
    qwen = ModelRegistry.get("qwen")
    assert qwen is not None
    assert qwen.full_name == "ollama/qwen3.8:latest"
    assert qwen.context_window == 262_144

    gemma = ModelRegistry.get("gemma4")
    assert gemma is not None
    assert gemma.full_name == "ollama/gemma4:12b-it-qat"


def test_removed_providers_pass_through_unregistered():
    # Gemini / plain-OpenAI / pi-codex registrations were removed; their
    # names normalize as-is (litellm can still be pointed at them manually).
    assert ModelRegistry.get("gemini") is None
    assert ModelRegistry.get("gpt-4o") is None
    assert (
        ModelRegistry.normalize("google/gemini-3-flash-preview")
        == "google/gemini-3-flash-preview"
    )


def test_same_provider_fallback_within_ollama():
    # qwen (pro) ↔ gemma4 (flash)
    assert (
        ModelRegistry.get_same_provider_fallback("ollama/qwen3.8:latest")
        == "ollama/gemma4:12b-it-qat"
    )
    assert (
        ModelRegistry.get_same_provider_fallback("ollama/gemma4:12b-it-qat")
        == "ollama/qwen3.8:latest"
    )
