import asyncio
import os
import shutil
import unittest
from pathlib import Path
from nimbus.tools.edit import edit_file

class TestEditRobustness(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("temp_test_edit")
        self.test_dir.mkdir(exist_ok=True)
        self.workspace = self.test_dir.absolute()

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def create_file(self, filename, content):
        path = self.test_dir / filename
        path.write_text(content, encoding="utf-8")
        return filename

    async def run_edit(self, filename, old_text, new_text):
        try:
            result = await edit_file(
                file_path=filename,
                old_text=old_text,
                new_text=new_text,
                workspace=self.workspace
            )
            return True, result
        except Exception as e:
            return False, str(e)

    def test_case_1_indentation_mismatch(self):
        """Case 1: 缩进不匹配。文件中是 4 空格，Edit 请求用了 2 空格。"""
        content = "def hello():\n    print('hello')\n    return True"
        filename = self.create_file("indent.py", content)
        
        # 模拟 2 空格缩进的请求
        old_text = "def hello():\n  print('hello')"
        new_text = "def hello():\n  print('hi')"
        
        success, msg = asyncio.run(self.run_edit(filename, old_text, new_text))
        print(f"\n[Case 1: Indentation Mismatch]\nSuccess: {success}\nMessage: {msg}")

    def test_case_2_newline_difference(self):
        """Case 2: 换行符/空行差异。"""
        content = "import os\n\ndef main():\n    pass"
        filename = self.create_file("newline.py", content)
        
        # 场景 A: 末尾多加换行符
        old_text_a = "import os\n\n"
        new_text_a = "import sys\n\n"
        
        # 场景 B: 中间少了一个空行
        old_text_b = "import os\ndef main():"
        new_text_b = "import sys\ndef main():"

        success_a, msg_a = asyncio.run(self.run_edit(filename, old_text_a, new_text_a))
        print(f"\n[Case 2A: Extra Newline At End]\nSuccess: {success_a}\nMessage: {msg_a}")

        success_b, msg_b = asyncio.run(self.run_edit(filename, old_text_b, new_text_b))
        print(f"\n[Case 2B: Missing Middle Empty Line]\nSuccess: {success_b}\nMessage: {msg_b}")

    def test_case_3_partial_match_risk(self):
        """Case 3: 局部匹配风险。有多处 return True。"""
        content = "def a():\n    return True\n\ndef b():\n    return True"
        filename = self.create_file("partial.py", content)
        
        old_text = "return True"
        new_text = "return False"
        
        success, msg = asyncio.run(self.run_edit(filename, old_text, new_text))
        print(f"\n[Case 3: Partial Match Risk]\nSuccess: {success}\nMessage: {msg}")

    def test_case_4_trailing_whitespace(self):
        """Case 4: 尾随空格差异。"""
        content = "line_with_space    \nnext_line"
        filename = self.create_file("whitespace.py", content)
        
        # 请求中没有尾随空格
        old_text = "line_with_space\nnext_line"
        new_text = "line_fixed\nnext_line"
        
        success, msg = asyncio.run(self.run_edit(filename, old_text, new_text))
        print(f"\n[Case 4: Trailing Whitespace]\nSuccess: {success}\nMessage: {msg}")

if __name__ == "__main__":
    unittest.main()
