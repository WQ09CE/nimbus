
import unittest
import os
import shutil
import asyncio
from pathlib import Path
from nimbus.core.nimfs import NimFSManager, NimFSError, MemoryCategory
from nimbus.tools.nimfs_tools import nimfs_write_artifact, nimfs_read_artifact

class TestNimFSSecurity(unittest.TestCase):
    def setUp(self):
        # 使用临时目录作为工作区，避免污染环境
        self.test_workspace = Path("/tmp/nimbus_test_workspace")
        self.test_workspace.mkdir(parents=True, exist_ok=True)
        # 强制设置工作区上下文
        self.ctx = {"workspace": str(self.test_workspace), "agent_role": "test-agent"}
        self.manager = NimFSManager(self.test_workspace)
        
    def tearDown(self):
        # 清理临时目录
        if self.test_workspace.exists():
            shutil.rmtree(self.test_workspace)
        # 清理生成的 NimFS 项目根目录
        project_root = self.manager.project_root
        if project_root.exists():
            shutil.rmtree(project_root)

    def test_normal_read_write(self):
        """1. 读写 NimFS 根路径下的合规文件"""
        print("\n[Test] Normal Read/Write")
        
        async def run():
            # 写入 Artifact
            result_write = await nimfs_write_artifact(
                content="hello nimbus",
                task_id="task-123",
                summary="test artifact",
                **self.ctx
            )
            print(f"Write result: {result_write}")
            
            # 从结果中提取 reference (简单提取)
            import re
            match = re.search(r"nimfs://artifact/[\w\-]+", result_write)
            self.assertIsNotNone(match)
            ref = match.group(0)
            
            # 读取 Artifact
            result_read = await nimfs_read_artifact(ref=ref, **self.ctx)
            self.assertIn("hello nimbus", result_read)
            print("Success: Normal read/write verified.")

        asyncio.run(run())

    def test_path_traversal_task_id(self):
        """2. 尝试通过不合规的 task_id 进行路径穿越写入"""
        print("\n[Test] Path Traversal via task_id")
        unsafe_task_id = "../../../etc"
        
        async def run():
            try:
                result = await nimfs_write_artifact(
                    content="malicious content",
                    task_id=unsafe_task_id,
                    **self.ctx
                )
                self.assertIn("NimFSError", result)
                print(f"Caught expected error message: {result.strip()}")
            except NimFSError as e:
                print(f"Caught expected exception: {e}")
                self.assertIn("Invalid task_id", str(e))

        asyncio.run(run())

    def test_read_outside_system_path(self):
        """3. 尝试读取 NimFS 权限范围外的系统路径"""
        print("\n[Test] Read Outside System Path (Validation Check)")
        # 验证底层安全检查函数
        from nimbus.core.nimfs.manager import _validate_within_root
        
        root = Path("/tmp/nimbus/safe")
        outside_path = Path("/etc/hosts")
        
        with self.assertRaises(NimFSError) as cm:
            _validate_within_root(outside_path, root)
        print(f"Verified security check: {cm.exception}")

    def test_memory_security(self):
        """4. 验证 Memory API 的路径安全性"""
        print("\n[Test] Memory API Security")
        # 尝试使用合规 API 写入
        mid = self.manager.write_memory(
            category=MemoryCategory.ENTITIES,
            title="Safe Entry",
            content="Some data"
        )
        self.assertIn("entities-", mid)
        
        # 验证读取
        content = self.manager.read_memory(mid, layer=2)
        self.assertEqual(content, "Some data")
        print("Success: Memory safe access verified.")

if __name__ == "__main__":
    unittest.main()
