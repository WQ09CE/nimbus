"""greeting 模块的单元测试"""
import pytest
from greeting import greet, farewell

class TestGreeting:
    def test_greet_with_name(self):
        result = greet("Alice")
        assert result == "你好，Alice！欢迎使用 Nimbus！"
    
    def test_farewell_with_name(self):
        result = farewell("Bob")
        assert result == "再见，Bob！期待下次见面！"
    
    def test_greet_with_chinese_name(self):
        result = greet("小明")
        assert "小明" in result

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
