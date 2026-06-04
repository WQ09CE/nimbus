from nimbus.core.models.manifest import OLLAMA_FEATURES, get_model_manifest
from nimbus.core.models.registry import ModelRegistry


NIMBUS_GEMMA4_MODEL = "ollama/gemma4:26b"
OLLAMA_GEMMA4_MODEL = "gemma4:26b"


def test_gemma4_26b_ollama_model_is_registered():
    assert ModelRegistry.normalize("gemma4-26b") == NIMBUS_GEMMA4_MODEL
    assert ModelRegistry.normalize("gemma-4-26b") == NIMBUS_GEMMA4_MODEL
    assert ModelRegistry.normalize("gemma4") == NIMBUS_GEMMA4_MODEL
    assert ModelRegistry.normalize(NIMBUS_GEMMA4_MODEL) == NIMBUS_GEMMA4_MODEL

    info = ModelRegistry.get(NIMBUS_GEMMA4_MODEL)

    assert info is not None
    assert info.provider == "ollama"
    assert info.model_id == OLLAMA_GEMMA4_MODEL
    assert info.context_window == 128_000
    assert info.manifest.features is OLLAMA_FEATURES


def test_gemma_models_use_ollama_manifest_fallback():
    manifest = get_model_manifest(NIMBUS_GEMMA4_MODEL)

    assert manifest.features is OLLAMA_FEATURES
    assert manifest.features.json_tool_call_extraction is True
