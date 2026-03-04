import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from nimbus.adapters.direct_adapter import DirectAdapter
from nimbus.core.models.registry import ModelRegistry, ModelInfo

@pytest.mark.asyncio
async def test_direct_adapter_fallback_on_429():
    """
    Test that DirectAdapter catches a 429 error from litellm.acompletion
    and retries with the fallback model.
    """
    # 1. Setup mock models in registry
    # Original model
    ModelRegistry.register(ModelInfo(
        model_id="gemini-3.1-pro-preview",
        provider="google",
        tier="pro",
        aliases=["gemini-3.1-pro"],
        manifest=MagicMock(),
        context_window=10000
    ))
    # Fallback model (3 Pro)
    ModelRegistry.register(ModelInfo(
        model_id="gemini-3-pro-preview",
        provider="google",
        tier="pro",
        aliases=["gemini-3-pro"],
        manifest=MagicMock(),
        context_window=10000
    ))
    # Fallback model (3 Flash)
    ModelRegistry.register(ModelInfo(
        model_id="gemini-3-flash-preview",
        provider="google",
        tier="flash",
        aliases=["gemini-3-flash"],
        manifest=MagicMock(),
        context_window=10000
    ))

    # 2. Mock litellm.acompletion
    # First call raises Exception("429 Resource Exhausted")
    # Second call returns a valid async generator
    
    mock_response_chunk = MagicMock()
    mock_response_chunk.choices = [MagicMock()]
    mock_response_chunk.choices[0].delta.content = "Fallback success"
    mock_response_chunk.choices[0].delta.tool_calls = None

    async def async_gen():
        yield mock_response_chunk

    with patch("nimbus.adapters.direct_adapter.acompletion", new_callable=AsyncMock) as mock_acompletion:
        mock_acompletion.side_effect = [
            Exception("429 Resource Exhausted"), # 1st call fails
            async_gen()                          # 2nd call succeeds
        ]

        # 3. Initialize adapter with the primary model
        from nimbus.adapters.types import LLMConfig
        adapter = DirectAdapter(config=LLMConfig(model="gemini-3.1-pro-preview"))
        
        # 4. Call chat
        mock_mmu = MagicMock()
        mock_mmu.assemble_context.return_value = [{"role": "user", "content": "Hello"}]
        response = await adapter.chat(mmu=mock_mmu)

        # 5. Assertions
        assert response.content == "Fallback success"
        
        # Verify acompletion was called twice
        assert mock_acompletion.call_count == 2
        
        # Verify first call used original model
        call_args_1 = mock_acompletion.call_args_list[0]
        assert call_args_1.kwargs["model"] == "gemini/gemini-3.1-pro-preview"
        
        # Verify second call used fallback model
        call_args_2 = mock_acompletion.call_args_list[1]
        # Based on registry logic: 3.1-pro -> 3-pro
        assert "gemini-3-pro-preview" in call_args_2.kwargs["model"]

