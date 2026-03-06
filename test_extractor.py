from nimbus.core.runtime.pipeline import JsonToolCallExtractor

fake_qwen_output = {"result": "✅ 系统测试完成", "id": None, "type": "text"}
parsed = JsonToolCallExtractor._extract_tool_calls(fake_qwen_output)
print(parsed)
