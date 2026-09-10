import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest
from conftest import rows, update
from nimbus.adapters.types import VcpuLLMResponse

from nimbus_chat_lab.engine import NimbusEngine, NoToolsAdapter
from nimbus_chat_lab.worker import Worker, WorkerPoisoned


async def no_op(*args):
    pass


async def test_tools_rejected_before_decoder():
    class Unsafe:
        async def chat(self, *args, **kwargs):
            return VcpuLLMResponse(tool_calls=[{"function": {"name": "Bash", "arguments": "{}"}}])

    with pytest.raises(RuntimeError, match="disabled tool"):
        await NoToolsAdapter(Unsafe(), no_op).chat([], tools=[])


@pytest.mark.parametrize(
    "text", ["<tool_call>", "[Called Bash]", "I'll run Bash", "```tool\nexample\n```"]
)
async def test_conversation_can_quote_tool_syntax(store, tmp_path, text):
    class Literal:
        async def chat(self, *args, **kwargs):
            return VcpuLLMResponse(content=text)

    await store.ingest(100, "nimbusbot", [update()])
    await Worker(store, NimbusEngine(tmp_path, adapter_factory=Literal)).run_once()
    result = (await rows(store, "SELECT state,result FROM turns"))[0]
    assert result == {"state": "succeeded", "result": text}


def fake_pi(root):
    path = root / "fake-pi"
    path.write_text(
        f'#!{sys.executable}\nimport os,time\nfrom pathlib import Path\nPath("model.pid").write_text(str(os.getpid()))\ntime.sleep(120)\n'
    )
    path.chmod(0o700)
    return path


async def wait_model(root):
    for _ in range(100):
        files = list(root.rglob("model.pid"))
        if files:
            return int(files[0].read_text())
        await asyncio.sleep(0.05)
    raise AssertionError("test model process did not start")


def running(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ")[1][0] != "Z"
    except FileNotFoundError:
        return False


async def test_cancellation_through_real_nimbus_joins_model_process(store, tmp_path):
    store.lease_seconds = 1
    exe = fake_pi(tmp_path)
    await store.ingest(100, "nimbusbot", [update()])
    job = asyncio.create_task(
        Worker(store, NimbusEngine(tmp_path / "attempts", pi_executable=str(exe))).run_once()
    )
    pid = None
    try:
        pid = await wait_model(tmp_path / "attempts")
        await store.ingest(100, "nimbusbot", [update(2, "/cancel")])
        await asyncio.wait_for(job, 12)
        assert (await rows(store, "SELECT state FROM turns"))[0]["state"] == "cancelled"
        assert not running(pid), "confirmed cancellation cannot leave the model subprocess running"
    finally:
        if not job.done():
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
        if pid and running(pid):
            os.kill(pid, signal.SIGKILL)


async def test_unstoppable_engine_does_not_release_admission(store):
    started, release = asyncio.Event(), asyncio.Event()

    class Resistant:
        async def run(self, *args):
            started.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass
            return "stopped by fixture"

    await store.ingest(100, "nimbusbot", [update()])
    job = asyncio.create_task(Worker(store, Resistant()).run_once())
    try:
        await started.wait()
        await store.ingest(100, "nimbusbot", [update(2, "/cancel")])
        with pytest.raises(WorkerPoisoned):
            await asyncio.wait_for(job, 12)
        assert (await rows(store, "SELECT state FROM turns"))[0]["state"] == "cancel_requested"
        assert await store.ingest(100, "nimbusbot", [update(3, "another")]) == ["busy"]
    finally:
        release.set()
        await asyncio.sleep(0.05)


async def test_cli_poison_path_exits_before_asyncio_shutdown_hangs():
    code = """import asyncio
import nimbus_chat_lab.cli as cli
from nimbus_chat_lab.worker import WorkerPoisoned
async def resistant():
    while True:
        try: await asyncio.sleep(100)
        except asyncio.CancelledError: pass
async def poison(args):
    asyncio.create_task(resistant())
    await asyncio.sleep(0)
    raise WorkerPoisoned()
cli.execute=poison
asyncio.run(cli.guarded_execute(None))
"""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", code, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        assert await asyncio.wait_for(proc.wait(), 5) == 2
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def test_worker_sigkill_kills_model_child(store, tmp_path):
    exe = fake_pi(tmp_path)
    await store.ingest(100, "nimbusbot", [update()])
    state = tmp_path / "state"
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "nimbus_chat_lab.cli",
        "--lease",
        "0.8",
        "worker",
        "--engine",
        "nimbus-pi",
        "--once",
        "--state",
        str(state),
        "--pi",
        str(exe),
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), "NIMBUS_LAB_DSN": store.dsn},
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    pid = None
    try:
        pid = await wait_model(state)
        assert running(pid)
        proc.send_signal(signal.SIGKILL)
        await proc.wait()
        for _ in range(40):
            if not running(pid):
                break
            await asyncio.sleep(0.05)
        assert not running(pid), (
            "parent-death fencing must reach the separate-session model process"
        )
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        if pid and running(pid):
            os.kill(pid, signal.SIGKILL)
