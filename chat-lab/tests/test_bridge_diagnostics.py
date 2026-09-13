import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from conftest import rows, update

from nimbus_chat_lab.agent_bridge import PiBridge
from nimbus_chat_lab.diagnostics import MAX_RECORD, BridgeFailure, save_diagnostic
from nimbus_chat_lab.operations import Operations


def test_native_extension_error_contract():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the actual TypeScript handler test")
    env = {"PATH": os.environ["PATH"]}
    if os.getenv("NIMBUS_TEST_PI_BRIDGE"):
        env["NIMBUS_TEST_PI_BRIDGE"] = os.environ["NIMBUS_TEST_PI_BRIDGE"]
    result = subprocess.run(
        [node, str(Path(__file__).with_name("pi_bridge_diagnostics.mjs"))],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "PASS:" in result.stdout


async def okay():
    pass


def fake_pi(tmp_path, body):
    path = tmp_path / "fake-pi"
    path.write_text(f"#!{sys.executable}\nimport sys,json,os,time\nsys.stdin.read()\n" + body)
    path.chmod(0o700)
    return str(path)


def diagnostic(root):
    paths = list((root / "diagnostics").glob("*.json"))
    assert len(paths) == 1
    assert paths[0].stat().st_mode & 0o777 == 0o600
    assert paths[0].parent.stat().st_mode & 0o777 == 0o700
    return json.loads(paths[0].read_text())


async def test_plaintext_child_error_saved_but_exception_is_safe(tmp_path):
    executable = fake_pi(
        tmp_path,
        "sys.stderr.write('ORIGINAL stderr detail\\n')\nprint(json.dumps({'nimbus':True,'error':'provider_request_failed','diagnostic':{'error':{'message':'RAW upstream detail','stack':'RAW stack','cause':{'code':'ECONNRESET'}}}}))\n",
    )
    bridge = PiBridge(tmp_path, okay, executable)
    with pytest.raises(BridgeFailure) as caught:
        await bridge.chat([{"role": "user", "content": "not logged as a request"}])
    assert caught.value.stage == "provider_error"
    assert caught.value.diagnostic_saved
    assert "RAW" not in str(caught.value) and "ORIGINAL" not in str(caught.value)
    record = diagnostic(tmp_path)
    assert "RAW upstream detail" in record["stdout"] and "ECONNRESET" in record["stdout"]
    assert record["stderr"] == "ORIGINAL stderr detail\n"
    assert record["exit_code"] == 0
    assert "not logged as a request" not in json.dumps(record)


@pytest.mark.parametrize(
    "body,stage",
    [
        ("sys.stderr.write('original child error');sys.exit(7)", "child_exit"),
        ("print('not a protocol result')", "missing_result"),
        ('print(\'{\\"nimbus\\":true}\\n{\\"nimbus\\":true}\')', "duplicate_result"),
        (
            "print(json.dumps({'nimbus':True,'result':{'tool_calls':[{'function':{'name':'unregistered'}}]}}))",
            "response_validation",
        ),
    ],
)
async def test_child_failure_stages(tmp_path, body, stage):
    bridge = PiBridge(tmp_path, okay, fake_pi(tmp_path, body))
    with pytest.raises(BridgeFailure) as caught:
        await bridge.chat([])
    assert caught.value.stage == stage
    assert diagnostic(tmp_path)["stage"] == stage


async def test_stderr_flood_drains_without_deadlock_and_is_bounded(tmp_path):
    body = "sys.stderr.write('x'*1000000)\nprint(json.dumps({'nimbus':True,'result':{'content':'success'}}))\n"
    bridge = PiBridge(tmp_path, okay, fake_pi(tmp_path, body))
    result = await asyncio.wait_for(bridge.chat([]), 10)
    assert result.content == "success"
    record = diagnostic(tmp_path)
    assert len(record["stderr"]) == 262144
    assert record["stderr_truncated"] and record["stderr_bytes"] == 1000000
    assert bridge.last_failure is None


async def test_timeout_reaps_child_and_keeps_original_stderr(tmp_path, monkeypatch):
    real_timeout = asyncio.timeout
    monkeypatch.setattr("nimbus_chat_lab.agent_bridge.asyncio.timeout", lambda _: real_timeout(1))
    executable = fake_pi(
        tmp_path, "sys.stderr.write('timeout detail');sys.stderr.flush();time.sleep(30)"
    )
    with pytest.raises(BridgeFailure) as caught:
        await PiBridge(tmp_path, okay, executable).chat([])
    assert caught.value.stage == "timeout" and caught.value.diagnostic_saved
    assert isinstance(caught.value, TimeoutError)  # Preserve the pre-existing runtime contract.
    record = diagnostic(tmp_path)
    assert record["stderr"] == "timeout detail" and record["exit_code"] < 0


async def test_stdout_bound_retains_prefix_without_hanging(tmp_path):
    executable = fake_pi(
        tmp_path,
        "sys.stderr.write('bound detail');sys.stderr.flush();sys.stdout.write('x'*3100000)",
    )
    with pytest.raises(BridgeFailure) as caught:
        await asyncio.wait_for(PiBridge(tmp_path, okay, executable).chat([]), 10)
    assert caught.value.stage == "child_io"
    record = diagnostic(tmp_path)
    assert record["stdout_at_bound"] and len(record["stdout"]) == 3000000
    assert record["stderr"].startswith("bound detail")


async def test_cancel_reaps_child_and_retains_stderr(tmp_path):
    body = "open('child.pid','w').write(str(os.getpid()))\nsys.stderr.write('before cancellation\\n');sys.stderr.flush()\ntime.sleep(30)\n"
    bridge = PiBridge(tmp_path, okay, fake_pi(tmp_path, body))
    task = asyncio.create_task(bridge.chat([]))
    try:
        for _ in range(100):
            if (tmp_path / "child.pid").exists():
                break
            await asyncio.sleep(0.02)
        pid = int((tmp_path / "child.pid").read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        record = diagnostic(tmp_path)
        assert record["stage"] == "cancelled"
        assert "before cancellation" in record["stderr"]
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def test_diagnostic_storage_bounds_private_paths_and_no_overwrite(tmp_path):
    identifier = uuid4()
    assert save_diagnostic(tmp_path, identifier, {"raw": "\x00" * MAX_RECORD})
    record = diagnostic(tmp_path)
    assert record["record_truncated"]
    assert next((tmp_path / "diagnostics").glob("*.json")).stat().st_size <= MAX_RECORD
    assert not save_diagnostic(tmp_path, identifier, {"raw": "overwrite"})
    other = tmp_path / "other"
    other.mkdir()
    (other / "diagnostics").symlink_to(tmp_path / "diagnostics", target_is_directory=True)
    assert not save_diagnostic(other, uuid4(), {"raw": "redirect"})


def test_diagnostic_attempt_quota_and_unsafe_directory(tmp_path):
    from nimbus_chat_lab.diagnostics import MAX_RECORDS

    for _ in range(MAX_RECORDS):
        assert save_diagnostic(tmp_path, uuid4(), {"raw": "synthetic"})
    assert not save_diagnostic(tmp_path, uuid4(), {"raw": "beyond quota"})
    assert len(list((tmp_path / "diagnostics").iterdir())) == MAX_RECORDS
    other = tmp_path / "other"
    other.mkdir()
    (other / "diagnostics").mkdir(mode=0o755)
    (other / "diagnostics").chmod(0o755)
    assert not save_diagnostic(other, uuid4(), {"raw": "unsafe permission"})


async def test_agentos_preserves_failure_identity_without_leaking_to_pg_or_outbox(
    store, tmp_path, monkeypatch
):
    from nimbus_chat_lab import agent_engine
    from nimbus_chat_lab.worker import Worker

    executable = fake_pi(
        tmp_path,
        "print(json.dumps({'nimbus':True,'error':'provider_request_failed','diagnostic':{'message':'PRIVATE raw failure'}}))\n",
    )
    monkeypatch.setattr(
        agent_engine,
        "PiBridge",
        lambda root, authority, ignored: PiBridge(root, authority, executable),
    )
    store.agent_mode = True
    await store.ingest(100, "bot", [update()])
    engine = agent_engine.AgentEngine(store, tmp_path / "attempts", executable, {}, observe=True)
    claim = await Worker(store, engine, observe=True).run_once()
    task = (await Operations(store).report())["tasks"][0]
    failures = [r["failure"] for r in task["telemetry"] if r.get("failure")]
    assert len(failures) == 2  # model and runtime, same actual request identity
    assert failures[0] == failures[1] and failures[0]["stage"] == "provider_error"
    assert failures[0]["diagnostic_saved"]
    root = tmp_path / "attempts" / str(claim.attempt_id)
    assert "PRIVATE raw failure" in diagnostic(root)["stdout"]
    dump = json.loads((root / "session" / f"{claim.attempt_id}.json").read_text())
    assert dump["status"] == "error"
    assert (await rows(store, "SELECT state FROM turns")) == [{"state": "failed"}]
    public = json.dumps(await rows(store, "SELECT result FROM turns")) + json.dumps(
        await rows(store, "SELECT text FROM outbox")
    )
    assert "PRIVATE" not in public and "PRIVATE" not in json.dumps(task)


async def test_exhausted_native_timeout_is_not_mistaken_for_worker_deadline(
    store, tmp_path, monkeypatch
):
    from nimbus_chat_lab import agent_engine
    from nimbus_chat_lab.diagnostics import BridgeTimeout
    from nimbus_chat_lab.worker import Worker

    original_agent = agent_engine.AgentOS

    class OneAttemptAgent(original_agent):
        def stream_with_queue(self, *args, **kwargs):
            loop = super().stream_with_queue(*args, **kwargs)
            loop._max_retries = 0  # Test exhausts immediately; production policy is untouched.
            return loop

    class TimeoutBridge:
        def __init__(self, *args):
            self.last_failure = None

        async def chat(self, *args, **kwargs):
            self.last_failure = BridgeTimeout("timeout", uuid4())
            raise self.last_failure

    monkeypatch.setattr(agent_engine, "AgentOS", OneAttemptAgent)
    monkeypatch.setattr(agent_engine, "PiBridge", TimeoutBridge)
    store.agent_mode = True
    await store.ingest(100, "bot", [update()])
    engine = agent_engine.AgentEngine(store, tmp_path / "attempts", "unused", {}, observe=True)
    await Worker(store, engine, observe=True).run_once()
    assert (await rows(store, "SELECT state FROM turns")) == [{"state": "failed"}]
    failures = [
        r["failure"]
        for r in (await Operations(store).report())["tasks"][0]["telemetry"]
        if r.get("failure")
    ]
    assert len(failures) == 2 and failures[0] == failures[1]
    assert failures[0]["stage"] == "timeout"


async def test_logging_failure_does_not_replace_success_or_retry(tmp_path, monkeypatch):
    executable = fake_pi(
        tmp_path,
        "sys.stderr.write('warning');print(json.dumps({'nimbus':True,'result':{'content':'okay'}}))",
    )
    monkeypatch.setattr("nimbus_chat_lab.agent_bridge.save_diagnostic", lambda *a: False)
    assert (await PiBridge(tmp_path, okay, executable).chat([])).content == "okay"
