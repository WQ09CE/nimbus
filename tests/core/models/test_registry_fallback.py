
import pytest
from nimbus.core.models.registry import ModelRegistry, ModelInfo, ModelManifest

def test_registry_fallback():
    # Test Google Logic
    
    # 3.1 Pro -> 3 Pro
    fallback_31 = ModelRegistry.get_same_provider_fallback("google/gemini-3.1-pro-preview")
    assert fallback_31 == "google/gemini-3-pro-preview"
    
    # 3 Pro -> 3 Flash
    fallback_3 = ModelRegistry.get_same_provider_fallback("google/gemini-3-pro-preview")
    assert fallback_3 == "google/gemini-3-flash-preview"
    
    # 3 Flash -> 3.1 Pro
    fallback_flash = ModelRegistry.get_same_provider_fallback("google/gemini-3-flash-preview")
    assert fallback_flash == "google/gemini-3.1-pro-preview"

    # Test Generic Logic (Anthropic)
    
    # Sonnet (Pro) -> Haiku (Flash)
    fallback_sonnet = ModelRegistry.get_same_provider_fallback("anthropic/claude-sonnet-4-6")
    assert fallback_sonnet == "anthropic/claude-haiku-4"
    
    # Haiku (Flash) -> Sonnet (Pro)
    fallback_haiku = ModelRegistry.get_same_provider_fallback("anthropic/claude-haiku-4")
    assert fallback_haiku == "anthropic/claude-sonnet-4-6"

    # Test Generic Logic (OpenAI)
    # GPT-4o (Pro) -> GPT-4o-mini (Flash)
    fallback_gpt4o = ModelRegistry.get_same_provider_fallback("openai/gpt-4o")
    assert fallback_gpt4o == "openai/gpt-4o-mini"
    
    # GPT-4o-mini (Flash) -> GPT-4o (Pro)
    fallback_mini = ModelRegistry.get_same_provider_fallback("openai/gpt-4o-mini")
    assert fallback_mini == "openai/gpt-4o"

if __name__ == "__main__":
    test_registry_fallback()
    print("All tests passed!")
