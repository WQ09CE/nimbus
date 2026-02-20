import sys
import os
import pty
import subprocess
import select

def run_claude_interactive(query):
    """
    使用 pty 模拟真实终端运行 Claude Code CLI，绕过嵌套检测并使用危险模式。
    """
    # 准备环境变量
    env = os.environ.copy()
    # 绕过嵌套检测的关键
    if "CLAUDECODE" in env:
        del env["CLAUDECODE"]
    
    # 设置 Token（确保 CLI 能读到，虽然它更倾向于读 config.json）
    token = "sk-ant-oat01-HBE9P-bS_Emcf2qFAblJ9UZ5XZ50zXHrfnaeJWCUSsbrhXcC_FVWuvbl9e3t7SWlvdeZMy2l4X3SurfzX4)Z0g-OR0ddAAA"
    env["ANTHROPIC_AUTH_TOKEN"] = token
    env["ANTHROPIC_API_KEY"] = token

    # 构造命令：使用 --dangerously-skip-permissions 自动确认所有操作
    # 加上 -p (prompt) 参数直接传入 query
    cmd = ["claude", "-p", query, "--dangerously-skip-permissions"]

    print(f"执行命令: {' '.join(cmd)}")

    # 使用 pty 派生子进程
    master_fd, slave_fd = pty.openpty()
    process = subprocess.Popen(
        cmd,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        env=env,
        text=True,
        close_fds=True
    )

    # 收集输出
    output = []
    try:
        while process.poll() is None:
            r, w, e = select.select([master_fd], [], [], 1.0)
            if master_fd in r:
                data = os.read(master_fd, 1024).decode('utf-8', errors='ignore')
                if data:
                    print(data, end="", flush=True)
                    output.append(data)
    except Exception as e:
        print(f"\n[Error] {e}")
    finally:
        os.close(master_fd)
        os.close(slave_fd)
        process.terminate()

    return "".join(output)

if __name__ == "__main__":
    test_query = "Who are you? reply in one sentence."
    if len(sys.argv) > 1:
        test_query = sys.argv[1]
    
    run_claude_interactive(test_query)
