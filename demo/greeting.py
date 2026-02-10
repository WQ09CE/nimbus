"""一个简单的问候模块"""

def greet(name: str) -> str:
    """返回问候语"""
    return f"你好，{name}！欢迎使用 Nimbus！"

def farewell(name: str) -> str:
    """返回告别语"""
    return f"再见，{name}！期待下次见面！"

def celebrate(event: str) -> str:
    """返回庆祝语"""
    return f"🎉 恭喜！{event}！🎉"

if __name__ == "__main__":
    print(greet("世界"))
    print(celebrate("任务完成"))
