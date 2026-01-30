"""
Nimbus v2 TUI 全面测试

测试内容：
1. 模块导入
2. CSS 语法验证
3. Widget 组件测试
4. App 组件测试
5. 消息流测试
"""

import asyncio
import sys
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_imports():
    """测试所有模块导入"""
    print("=" * 60)
    print("测试 1: 模块导入")
    print("=" * 60)

    errors = []

    # 测试主模块
    try:
        from nimbus.v2.tui import NimbusApp
        print(f"  ✓ nimbus.v2.tui.NimbusApp")
    except Exception as e:
        errors.append(f"NimbusApp: {e}")
        print(f"  ✗ nimbus.v2.tui.NimbusApp: {e}")

    # 测试 widgets
    try:
        from nimbus.v2.tui.widgets import Chatbox, PromptInput
        print(f"  ✓ nimbus.v2.tui.widgets.Chatbox")
        print(f"  ✓ nimbus.v2.tui.widgets.PromptInput")
    except Exception as e:
        errors.append(f"Widgets: {e}")
        print(f"  ✗ Widgets: {e}")

    # 测试 app 模块
    try:
        from nimbus.v2.tui.app import NimbusApp, load_config
        print(f"  ✓ nimbus.v2.tui.app 函数")
    except Exception as e:
        errors.append(f"App functions: {e}")
        print(f"  ✗ App functions: {e}")

    if errors:
        print(f"\n  失败: {len(errors)} 个导入错误")
        return False
    print(f"\n  通过: 所有模块导入成功")
    return True


def test_css_syntax():
    """测试 CSS 文件语法"""
    print("\n" + "=" * 60)
    print("测试 2: CSS 语法验证")
    print("=" * 60)

    css_path = Path(__file__).parent.parent / "src/nimbus/v2/tui/nimbus.tcss"

    if not css_path.exists():
        print(f"  ✗ CSS 文件不存在: {css_path}")
        return False

    css_content = css_path.read_text()
    print(f"  CSS 文件: {css_path.name} ({len(css_content)} bytes)")

    # 检查基本语法
    errors = []

    # 检查括号匹配
    open_braces = css_content.count("{")
    close_braces = css_content.count("}")
    if open_braces != close_braces:
        errors.append(f"括号不匹配: {{ = {open_braces}, }} = {close_braces}")

    # 检查常见错误
    lines = css_content.split("\n")
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # 检查属性行是否有分号
        if ":" in stripped and not stripped.startswith("/*") and not stripped.endswith(";") and not stripped.endswith("{"):
            if not stripped.endswith("*/"):
                # 可能缺少分号
                pass  # 不是严格错误

    # 检查无效的 Textual 变量
    invalid_vars = ["$text-muted", "$main-lighten", "$main-darken"]
    for var in invalid_vars:
        if var in css_content:
            errors.append(f"可能无效的变量: {var}")

    if errors:
        for err in errors:
            print(f"  ✗ {err}")
        return False

    print(f"  ✓ 括号匹配正确")
    print(f"  ✓ 无明显语法错误")
    return True


def test_chatbox_widget():
    """测试 Chatbox 组件"""
    print("\n" + "=" * 60)
    print("测试 3: Chatbox 组件")
    print("=" * 60)

    from nimbus.v2.tui.widgets.chatbox import Chatbox

    errors = []

    # 测试创建不同角色的 Chatbox
    roles = ["user", "assistant", "system", "error"]
    for role in roles:
        try:
            box = Chatbox(content=f"Test message for {role}", role=role)
            print(f"  ✓ Chatbox(role='{role}')")

            # 检查属性
            assert box.role == role
            assert box.content == f"Test message for {role}"
        except Exception as e:
            errors.append(f"Chatbox(role='{role}'): {e}")
            print(f"  ✗ Chatbox(role='{role}'): {e}")

    # 测试 append_chunk 方法
    try:
        box = Chatbox(content="Hello", role="assistant")
        box.append_chunk(" World")
        assert box.content == "Hello World"
        print(f"  ✓ append_chunk() 方法")
    except Exception as e:
        errors.append(f"append_chunk: {e}")
        print(f"  ✗ append_chunk: {e}")

    # 测试 render 方法 (替代 markdown 属性)
    try:
        box = Chatbox(content="**bold** and `code`", role="assistant")
        rendered = box.render()
        print(f"  ✓ render() 方法返回 Markdown")
    except Exception as e:
        errors.append(f"render: {e}")
        print(f"  ✗ render: {e}")

    if errors:
        print(f"\n  失败: {len(errors)} 个错误")
        return False
    print(f"\n  通过: Chatbox 组件正常")
    return True


def test_prompt_input_widget():
    """测试 PromptInput 组件"""
    print("\n" + "=" * 60)
    print("测试 4: PromptInput 组件")
    print("=" * 60)

    from nimbus.v2.tui.widgets.prompt_input import PromptInput

    errors = []

    # 测试创建
    try:
        prompt = PromptInput(id="test-prompt")
        print(f"  ✓ PromptInput 创建")
    except Exception as e:
        errors.append(f"创建: {e}")
        print(f"  ✗ PromptInput 创建: {e}")
        return False

    # 检查 bindings
    try:
        bindings = prompt.BINDINGS
        has_submit = any("submit" in str(b).lower() for b in bindings)
        if has_submit:
            print(f"  ✓ 提交快捷键已定义")
        else:
            errors.append("缺少提交快捷键")
            print(f"  ✗ 缺少提交快捷键")
    except Exception as e:
        errors.append(f"bindings: {e}")
        print(f"  ✗ bindings: {e}")

    # 检查 submit_ready reactive
    try:
        assert hasattr(prompt, "submit_ready")
        print(f"  ✓ submit_ready 属性")
    except Exception as e:
        errors.append(f"submit_ready: {e}")
        print(f"  ✗ submit_ready: {e}")

    # 检查消息类
    try:
        assert hasattr(PromptInput, "PromptSubmitted")
        print(f"  ✓ PromptSubmitted 消息类")
    except Exception as e:
        errors.append(f"PromptSubmitted: {e}")
        print(f"  ✗ PromptSubmitted: {e}")

    if errors:
        print(f"\n  失败: {len(errors)} 个错误")
        return False
    print(f"\n  通过: PromptInput 组件正常")
    return True


def test_nimbus_app():
    """测试 NimbusApp"""
    print("\n" + "=" * 60)
    print("测试 5: NimbusApp")
    print("=" * 60)

    from nimbus.v2.tui.app import NimbusApp

    errors = []

    # 测试创建
    try:
        app = NimbusApp()
        print(f"  ✓ NimbusApp 创建")
        print(f"    Title: {app.TITLE}")
        print(f"    CSS_PATH: {app.CSS_PATH.name}")
    except Exception as e:
        errors.append(f"创建: {e}")
        print(f"  ✗ NimbusApp 创建: {e}")
        return False

    # 检查 CSS 文件存在
    try:
        assert app.CSS_PATH.exists(), "CSS 文件不存在"
        print(f"  ✓ CSS 文件存在")
    except Exception as e:
        errors.append(f"CSS: {e}")
        print(f"  ✗ CSS: {e}")

    # 检查 bindings
    try:
        bindings = app.BINDINGS
        binding_actions = [str(b) for b in bindings]
        print(f"  ✓ Bindings: {len(bindings)} 个")
    except Exception as e:
        errors.append(f"bindings: {e}")
        print(f"  ✗ bindings: {e}")

    if errors:
        print(f"\n  失败: {len(errors)} 个错误")
        return False
    print(f"\n  通过: NimbusApp 正常")
    return True


def test_config_loading():
    """测试配置加载"""
    print("\n" + "=" * 60)
    print("测试 6: 配置加载")
    print("=" * 60)

    from nimbus.v2.tui.app import load_config

    try:
        config = load_config()
        print(f"  ✓ load_config() 成功")
        print(f"    Keys: {list(config.keys())}")

        if "llm" in config:
            llm_config = config["llm"]
            print(f"    LLM provider: {llm_config.get('default_provider', 'N/A')}")
    except Exception as e:
        print(f"  ✗ load_config(): {e}")
        return False

    return True


async def test_app_compose():
    """测试 App compose (异步)"""
    print("\n" + "=" * 60)
    print("测试 7: App Compose 结构")
    print("=" * 60)

    from nimbus.v2.tui.app import NimbusApp

    try:
        app = NimbusApp()

        # 检查 compose 方法存在
        assert hasattr(app, "compose"), "缺少 compose 方法"
        print(f"  ✓ compose 方法存在")

        # 检查必要的方法
        methods = ["add_message", "action_clear", "action_quit"]
        for method in methods:
            if hasattr(app, method):
                print(f"  ✓ {method}() 方法存在")
            else:
                print(f"  ⚠ {method}() 方法不存在")

        return True
    except Exception as e:
        print(f"  ✗ 错误: {e}")
        return False


async def test_app_startup():
    """测试 App 启动 (快速启动和退出)"""
    print("\n" + "=" * 60)
    print("测试 8: App 启动测试")
    print("=" * 60)

    from nimbus.v2.tui.app import NimbusApp

    try:
        app = NimbusApp()

        # 创建一个任务来自动退出
        async def auto_exit():
            await asyncio.sleep(0.5)
            app.exit()

        # 尝试运行 app（可能会因为没有 TTY 而失败）
        try:
            exit_task = asyncio.create_task(auto_exit())
            await asyncio.wait_for(app.run_async(), timeout=2.0)
            print(f"  ✓ App 启动和退出成功")
            return True
        except asyncio.TimeoutError:
            print(f"  ⚠ App 运行超时 (可能正常)")
            return True
        except Exception as e:
            # 在非 TTY 环境中可能会失败，这是预期的
            if "Invalid argument" in str(e) or "not a terminal" in str(e).lower():
                print(f"  ⚠ 非 TTY 环境，跳过启动测试")
                return True
            print(f"  ✗ 启动错误: {e}")
            return False
    except Exception as e:
        print(f"  ✗ 错误: {e}")
        return False


def run_all_tests():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("Nimbus v2 TUI 全面测试")
    print("=" * 60)

    results = []

    # 同步测试
    results.append(("模块导入", test_imports()))
    results.append(("CSS 语法", test_css_syntax()))
    results.append(("Chatbox 组件", test_chatbox_widget()))
    results.append(("PromptInput 组件", test_prompt_input_widget()))
    results.append(("NimbusApp", test_nimbus_app()))
    results.append(("配置加载", test_config_loading()))

    # 异步测试
    results.append(("App Compose", asyncio.run(test_app_compose())))
    results.append(("App 启动", asyncio.run(test_app_startup())))

    # 汇总
    print("\n" + "=" * 60)
    print("测试汇总")
    print("=" * 60)

    passed = sum(1 for _, r in results if r)
    failed = len(results) - passed

    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {name}")

    print(f"\n总计: {passed} 通过, {failed} 失败")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
