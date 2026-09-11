"""Runs INSIDE gVisor. Only /workspace tmpfs is writable; snapshots are bounded ZIPs."""

import base64
import io
import json
import os
import signal
import subprocess
import zipfile
from pathlib import Path

ROOT = Path("/workspace")
LIMIT = 32 * 1024 * 1024


def path(value):
    p = Path(value)
    if p.is_absolute() and p != ROOT and ROOT not in p.parents:
        raise ValueError("Absolute paths must stay in /workspace")
    p = (p if p.is_absolute() else ROOT / p).resolve()
    if p != ROOT and ROOT not in p.parents:
        raise ValueError("Path must stay in /workspace")
    return p


def main():
    request = json.loads(Path("/request.json").read_text())
    if Path("/seed.zip").stat().st_size:
        with zipfile.ZipFile("/seed.zip") as z:
            if len(z.infolist()) > 2000 or sum(i.file_size for i in z.infolist()) > LIMIT:
                raise ValueError("Workspace bound")
            for item in z.infolist():
                target = path(item.filename)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(z.read(item))
                    target.chmod((item.external_attr >> 16) & 0o777 or 0o644)
    op = request["action"]
    args = request["args"]
    out = ""
    code = 0
    if op == "bash":
        # Redirect to a bounded tmpfs file instead of unbounded host pipes.
        with open("/tmp/output", "wb") as f:
            p = subprocess.Popen(
                ["/bin/bash", "-lc", args["command"]],
                cwd=ROOT,
                stdout=f,
                stderr=subprocess.STDOUT,
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "HOME": "/workspace",
                    "LANG": "C.UTF-8",
                },
            )
            try:
                code = p.wait(timeout=45)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
                code = 124
            # This runner is PID 1 INSIDE gVisor. Reap detached/background writers
            # before taking a coherent snapshot; this never addresses host PIDs.
            if os.getpid() != 1:
                raise RuntimeError("Expected container init process")
            try:
                os.kill(-1, signal.SIGKILL)
            except ProcessLookupError:
                pass
            while True:
                try:
                    os.waitpid(-1, 0)
                except ChildProcessError:
                    break
        out = Path("/tmp/output").read_bytes()[:48000].decode(errors="replace")
    elif op == "read":
        out = path(args["path"]).read_bytes()[:48000].decode(errors="replace")
    elif op == "write":
        p = path(args["path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args["content"])
        out = "written"
    elif op == "edit":
        p = path(args["path"])
        text = p.read_text()
        old = args["old"]
        if not old or text.count(old) != 1:
            raise ValueError("Edit requires exactly one match")
        p.write_text(text.replace(old, args["new"], 1))
        out = "edited"
    elif op == "list":
        out = "\n".join(str(p.relative_to(ROOT)) for p in sorted(ROOT.rglob("*"))[:2000])
    else:
        raise ValueError("Unknown action")
    data = io.BytesIO()
    total = 0
    count = 0
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(ROOT.rglob("*")):
            if p.is_symlink():
                continue
            if not p.is_file():
                continue
            total += p.stat().st_size
            count += 1
            if total > LIMIT or count > 2000:
                raise ValueError("Persistent workspace bound")
            info = zipfile.ZipInfo(str(p.relative_to(ROOT)))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100000 | (p.stat().st_mode & 0o777)) << 16
            z.writestr(info, p.read_bytes())
    snapshot = data.getvalue()
    if len(snapshot) > 16 * 1024 * 1024:
        raise ValueError("Compressed workspace bound")
    print(
        json.dumps(
            {
                "output": out,
                "exit_code": code,
                "archive": base64.b64encode(snapshot).decode(),
                "kernel": os.uname().release,
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(json.dumps({"error": type(e).__name__}))
