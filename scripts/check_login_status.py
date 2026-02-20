import subprocess
import os

def check_claude_status():
    """
    检查 Nimbus 进程是否能直接识别到用户本地的 Claude Code 登录状态。
    """
    # 彻底清除可能导致嵌套报错的环境变量
    env = os.environ.copy()
    if "CLAUDECODE" in env:
        del env["CLAUDECODE"]
    
    print(f"当前用户: {os.getlogin() if hasattr(os, 'getlogin') else 'unknown'}")
    print(f"HOME 目录: {os.path.expanduser('~')}")
    print("-" * 30)

    try:
        # 执行 claude config get，如果已登录，它会输出当前的配置信息
        # 加上 -v 或 --help 确保命令本身能运行
        result = subprocess.run(
            ["claude", "config", "get"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            print("✅ Claude Code 登录状态识别成功！")
            print("配置内容如下:")
            print(result.stdout)
        else:
            print("❌ 依然提示未登录。")
            print("错误输出:", result.stderr)
            print("标准输出:", result.stdout)
            
    except subprocess.TimeoutExpired:
        print("❌ 命令执行超时。")
    except FileNotFoundError:
        print("❌ 未在 PATH 中找到 'claude' 命令。")
    except Exception as e:
        print(f"❌ 发生未知错误: {str(e)}")

if __name__ == "__main__":
    check_claude_status()
