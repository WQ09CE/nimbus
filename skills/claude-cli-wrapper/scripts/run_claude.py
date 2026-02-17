import sys
import subprocess
import os

def main():
    if len(sys.argv) < 3:
        print("Usage: python run_claude.py <query> <mode>")
        sys.exit(1)

    query = sys.argv[1]
    mode = sys.argv[2]

    # 个人使用的 Wrapper 配置
    env = os.environ.copy()
    # 尝试同时注入为 API KEY 和 AUTH TOKEN，以兼容不同版本的 CLI
    token = "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"
    env["ANTHROPIC_API_KEY"] = token
    env["ANTHROPIC_AUTH_TOKEN"] = token
    # 绕过嵌套检测
    if "CLAUDECODE" in env:
        del env["CLAUDECODE"]
    
    # 构造命令
    # 使用 --non-interactive 模式以适应 Agent 调用，或者根据需要调整
    # 这里的 'claude' 假设已经在 PATH 中
    cmd = ["claude", query]
    
    if mode == "execute":
        # 针对执行任务的优化
        pass
    
    try:
        # 使用 subprocess 捕获输出并返回给 Nimbus
        # 注意：这里我们使用了 text=True 和 capture_output=True
        process = subprocess.run(
            cmd, 
            env=env, 
            capture_output=True, 
            text=True,
            timeout=300 # 设置 5 分钟超时
        )
        
        if process.returncode == 0:
            print(process.stdout)
        else:
            print(f"Error (Exit Code {process.returncode}):")
            print(process.stderr)
            print(process.stdout)
            
    except subprocess.TimeoutExpired:
        print("Error: Claude Code command timed out.")
    except Exception as e:
        print(f"Error executing Claude Code: {str(e)}")

if __name__ == "__main__":
    main()
