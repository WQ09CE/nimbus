"""Real owned-runtime isolation/cancellation checks; never run in an existing container."""

import asyncio
import base64
import io
import json
import subprocess
import zipfile
from pathlib import Path

from nimbus_chat_lab.agent_sandbox import Sandbox


async def okay():
    pass


async def main():
    cfg = json.loads((Path.home() / ".config/nimbus-chat-lab/agent-runtime.json").read_text())
    sandbox = Sandbox(None, okay, cfg)
    command = """python - <<'PY'
import os,socket,json
facts={'host_home_absent':not os.path.exists('/home/dennis'),'host_runtime_socket_absent':not os.path.exists('/run/user/1000'),'credentials_absent':not any('TOKEN' in k or 'DSN' in k or 'API_KEY' in k for k in os.environ),'workspace_tmpfs_bytes':os.statvfs('/workspace').f_blocks*os.statvfs('/workspace').f_frsize}
try:open('/etc/nimbus_probe','w');facts['rootfs_readonly']=False
except OSError:facts['rootfs_readonly']=True
for name,addr in [('external_network',('1.1.1.1',443)),('host_loopback',('127.0.0.1',55432))]:
 s=socket.socket();s.settimeout(1)
 try:s.connect(addr);facts[name+'_blocked']=False
 except OSError:facts[name+'_blocked']=True
 finally:s.close()
print(json.dumps(facts))
PY"""
    initial = await sandbox.operation("bash", {"command": command})
    report = json.loads(initial["output"])
    report["gvisor_kernel"] = initial["kernel"] == "4.19.0-gvisor"
    written = await sandbox.operation("write", {"path": "proof.txt", "content": "SANDBOX-PERSIST"})
    seed = base64.b64decode(written["archive"])
    read = await sandbox.operation("read", {"path": "proof.txt"}, seed)
    report["snapshot_roundtrip"] = read["output"] == "SANDBOX-PERSIST"
    report["read_does_not_mutate_snapshot"] = read["archive"] == written["archive"]
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../../escape", "bad")
    try:
        await sandbox.operation("list", {}, bad.getvalue())
        report["archive_traversal_rejected"] = False
    except RuntimeError:
        report["archive_traversal_rejected"] = True
    # Inspect the actual transient unit while the owned sandbox is running.
    original = asyncio.create_subprocess_exec
    owned = []

    async def spawn(*args, **kwargs):
        if "nimbus_chat_lab.child_exec" in args:
            owned.extend(
                a.split("=", 1)[1] + ".service" for a in args if a.startswith("--unit=nimbus-sbox-")
            )
        return await original(*args, **kwargs)

    asyncio.create_subprocess_exec = spawn
    task = asyncio.create_task(sandbox.operation("bash", {"command": "sleep 25"}))
    props = {}
    processes = []
    try:
        for _ in range(120):
            await asyncio.sleep(0.1)
            if not owned:
                continue
            raw = subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "show",
                    owned[0],
                    "-p",
                    "ControlGroup",
                    "-p",
                    "MemoryMax",
                    "-p",
                    "TasksMax",
                    "-p",
                    "CPUQuotaPerSecUSec",
                    "-p",
                    "RuntimeMaxUSec",
                ],
                capture_output=True,
                text=True,
            ).stdout
            props = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
            cg = Path("/sys/fs/cgroup" + props.get("ControlGroup", ""))
            if not props.get("ControlGroup") or not (cg / "cgroup.procs").exists():
                continue
            processes = []
            for pid in (cg / "cgroup.procs").read_text().split():
                try:
                    processes.append(Path("/proc", pid, "comm").read_text().strip())
                except FileNotFoundError:
                    pass
            if any("sentry" in n for n in processes):
                break
        report["limits"] = {k: v for k, v in props.items() if k != "ControlGroup"}
        report["gvisor_under_cgroup_limit"] = any("sentry" in n for n in processes)
        report["hard_memory_limit"] = props.get("MemoryMax") == str(768 * 1024 * 1024)
        report["hard_task_limit"] = props.get("TasksMax") == "256"
        report["hard_cpu_limit"] = props.get("CPUQuotaPerSecUSec") == "1s"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        asyncio.create_subprocess_exec = original
    await asyncio.sleep(0.5)
    report["cancelled_unit_inactive"] = (
        bool(owned)
        and subprocess.run(["systemctl", "--user", "is-active", "--quiet", owned[0]]).returncode
        != 0
    )
    containers = json.loads(
        subprocess.check_output(
            ["podman", "ps", "--filter", "label=nimbus-chat-owned=true", "--format", "json"]
        )
    )
    report["no_owned_running_containers"] = not containers
    bad_cfg = {**cfg, "runtime": "/var/tmp/nimbus-gvisor-missing/runsc"}
    try:
        await Sandbox(None, okay, bad_cfg).operation("bash", {"command": "echo MUST_NOT_RUN"})
        report["missing_runtime_fail_closed"] = False
    except RuntimeError:
        report["missing_runtime_fail_closed"] = True
    evidence = Path(__file__).resolve().parents[2] / ".artifacts/sandbox-drills.json"
    evidence.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    assert all(v for k, v in report.items() if k not in ("limits", "workspace_tmpfs_bytes"))
    assert report["workspace_tmpfs_bytes"] <= 64 * 1024 * 1024


if __name__ == "__main__":
    asyncio.run(main())
