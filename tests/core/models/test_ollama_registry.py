from nimbus.core.models.manifest import OLLAMA_FEATURES, get_model_manifest
from nimbus.core.models.registry import ModelRegistry


NIMBUS_GEMMA4_MODEL = "ollama/gemma4:26b"


def test_unregistered_ollama_tags_pass_through():
    # Only the tags in daily use (qwen3.8:latest, gemma4:12b-it-qat) are
    # registered. Any OTHER "ollama/<tag>" still works: it passes through
    # normalize unchanged (llm_factory routes by provider prefix, not
    # registry) and falls back to the ollama manifest.
    assert ModelRegistry.get(NIMBUS_GEMMA4_MODEL) is None
    assert ModelRegistry.normalize(NIMBUS_GEMMA4_MODEL) == NIMBUS_GEMMA4_MODEL


def test_gemma_models_use_ollama_manifest_fallback():
    manifest = get_model_manifest(NIMBUS_GEMMA4_MODEL)

    assert manifest.features is OLLAMA_FEATURES
    assert manifest.features.json_tool_call_extraction is True
