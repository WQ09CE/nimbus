import asyncio
import base64

from nimbus_chat_lab.agent_sandbox import Sandbox


async def test_parallel_native_workspace_calls_do_not_lose_sibling_writes(monkeypatch):
    class State:
        value = b""

        async def archive(self, value=None):
            if value is not None:
                self.value = value
            return self.value

    async def allowed():
        pass

    async def operation(action, args, seed):
        await asyncio.sleep(0.03)
        return {"archive": base64.b64encode(seed + args["content"].encode()).decode()}

    state = State()
    sandbox = Sandbox(state, allowed, {})
    monkeypatch.setattr(sandbox, "operation", operation)
    await asyncio.gather(
        sandbox.execute("write", {"content": "a"}),
        sandbox.execute("write", {"content": "b"}),
    )
    assert state.value == b"ab"
