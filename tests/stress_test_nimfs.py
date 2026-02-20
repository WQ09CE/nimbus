import os
import random
import string
import sys
import logging
import asyncio
from pathlib import Path

# 设置路径以允许导入 nimbus
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from nimbus.tools.nimfs_tools import nimfs_read_artifact

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("nimfs_stress_test.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def generate_random_text(length):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

async def test_nimfs_integrity(artifact_ref, original_text):
    """验证读取到的内容与原始文本是否一致"""
    logger.info(f"正在尝试通过 {artifact_ref} 读取内容...")
    
    # 模拟工具上下文
    ctx = {"workspace": os.getcwd()}
    
    # 调用读取工具
    result = await nimfs_read_artifact(ref=artifact_ref, **ctx)
    
    # 解析结果 (跳过 header)
    if "<!-- NimFS Artifact" in result:
        content = result.split("-->\n\n", 1)[1]
    else:
        content = result

    if content == original_text:
        logger.info("✅ 校验成功：读取到的内容与原始文本完全一致。")
        return True
    else:
        logger.error(f"❌ 校验失败：内容不一致！")
        logger.error(f"原始长度: {len(original_text)}, 读取长度: {len(content)}")
        return False

async def main():
    try:
        # 1. 生成大文本
        target_length = 50005
        logger.info(f"1. 正在生成 {target_length} 字符的随机文本...")
        original_text = generate_random_text(target_length)
        
        # 2. 指导用户触发 Auto-Offload
        print("\n" + "="*50)
        print("NIMFS STRESS TEST - STEP 1")
        print("="*50)
        print(f"生成的原始文本 (前100字): {original_text[:100]}...")
        print("\n请执行以下 Bash 命令来触发系统的 Auto-Offload 机制：")
        print(f"python3 -c \"print('{original_text}')\"")
        print("="*50 + "\n")

        # 3. 提示输入引用
        print("系统触发 Auto-Offload 后，请复制生成的 'nimfs://artifact/...' 引用并粘贴在下方：")
        artifact_ref = input("Artifact Reference: ").strip()
        
        if not artifact_ref.startswith("nimfs://"):
            logger.error("无效的引用格式。")
            return

        # 4. 验证完整性
        success = await test_nimfs_integrity(artifact_ref, original_text)
        
        if success:
            logger.info("测试圆满完成！")
        else:
            logger.info("测试未通过。")

    except Exception as e:
        logger.error(f"测试过程中出现错误: {e}")
        sys.exit(1)

if __name__ == "__main__":
    # 如果作为脚本直接运行，仅生成数据并告知如何验证
    # 这里我们为了自动化，可以直接在 main 中完成
    if len(sys.argv) > 1 and sys.argv[1] == "--verify":
        ref = sys.argv[2]
        orig_file = sys.argv[3]
        with open(orig_file, "r") as f:
            orig_text = f.read()
        asyncio.run(test_nimfs_integrity(ref, orig_text))
    else:
        # 简单模式：由 Implementer 代理完成闭环
        target_length = 55000
        text = generate_random_text(target_length)
        with open("temp_orig.txt", "w") as f:
            f.write(text)
        print(f"CREATED_TEMP_FILE: {os.path.abspath('temp_orig.txt')}")
        print(f"CONTENT_PREVIEW: {text[:50]}...")
        print(f"FULL_CONTENT: {text}")
