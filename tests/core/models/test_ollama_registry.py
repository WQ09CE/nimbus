from nimbus.core.models.manifest import OLLAMA_FEATURES, get_model_manifest
from nimbus.core.models.registry import ModelRegistry


NIMBUS_GEMMA4_MODEL = "ollama/gemma4:26b"


def test_ollama_models_are_not_registered():
    # Registry was trimmed to gemini + gpt series only. Ollama models are no
    # longer registered — full "ollama/<tag>" names pass through normalize
    # unchanged (llm_factory routes them by provider prefix, not registry).
    assert ModelRegistry.get(NIMBUS_GEMMA4_MODEL) is None
    assert ModelRegistry.normalize(NIMBUS_GEMMA4_MODEL) == NIMBUS_GEMMA4_MODEL


def test_gemma_models_use_ollama_manifest_fallback():
    manifest = get_model_manifest(NIMBUS_GEMMA4_MODEL)

    assert manifest.features is OLLAMA_FEATURES
    assert manifest.features.json_tool_call_extraction is True
