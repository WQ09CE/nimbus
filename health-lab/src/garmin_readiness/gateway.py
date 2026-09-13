"""Local capability-bound Unix RPC. SDK always runs in a short-lived private child."""

import asyncio
import hmac
import json
import logging
import os
import signal
import socket
import stat
import struct
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

from .brief import projection, request_args
from .features import TZ, gmt
from .storage import Archive, LocalError, now, private_dir, private_file

MAX_RESPONSE = 24576
SAFE_ERRORS = {
    "auth",
    "access_denied",
    "rate_limit",
    "transport",
    "login_required",
    "interactive_reauth_required",
    "another_command_is_running",
    "archive_corrupt",
    "different_account_refused",
    "invalid_arguments",
    "invalid_date",
    "date_range_bound",
}


def atomic_json(path, value):
    data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    if path.exists() or path.is_symlink():
        private_file(path)
    fd, tmp = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        d = os.open(path.parent, os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(d)
        finally:
            os.close(d)
    finally:
        Path(tmp).unlink(missing_ok=True)


def load_private(path, bound=32768):
    private_file(path)
    if path.stat().st_size > bound:
        raise LocalError("file_bound")
    return json.loads(path.read_text())


def child(request):
    # No raw exceptions/SSO material may escape into Nimbus bridge diagnostics.
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    try:
        target, _ = request_args(request["action"], request["args"])
        archive = Archive(request["root"])
        refresh_error = None
        with archive.lock():
            with archive.connect() as c:
                row = c.execute("SELECT value FROM meta WHERE key='account'").fetchone()
            if not row or row[0] != request["account"]:
                raise LocalError("different_account_refused")
            if request["refresh"]:
                from .provider import authenticate, sync

                try:
                    api = authenticate(archive)
                    today = gmt(now()).astimezone(TZ).date()
                    sync(archive, api, today - timedelta(days=2), today)
                except LocalError as exc:
                    refresh_error = str(exc) if str(exc) in SAFE_ERRORS else "health_internal"
            result = projection(archive, request["action"], request["args"])
            result["refresh_error"] = refresh_error
            if refresh_error and result.get("source_status") == "complete":
                result["source_status"] = "stale"
                from .brief import render_daily

                result["observations"] = {"limited": "本次同步未完成，暂不判断今日恢复状态。"}
                result["advice"] = {"sync": "可稍后重新查看；若需登录，请仅在本机操作。"}
                result["text"] = render_daily(result)
        payload = {"ok": True, "result": result}
    except LocalError as exc:
        payload = {"ok": False, "error": str(exc) if str(exc) in SAFE_ERRORS else "health_internal"}
    except Exception:
        payload = {"ok": False, "error": "health_internal"}
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
    if len(data) > MAX_RESPONSE:
        data = b'{"ok":false,"error":"response_bound"}'
    sys.stdout.buffer.write(data)


class Gateway:
    def __init__(self, config):
        if (
            not isinstance(config.get("key"), str)
            or len(config["key"]) < 64
            or not isinstance(config.get("identity"), list)
            or len(config["identity"]) != 3
            or any(type(v) is not int or v <= 0 for v in config["identity"])
            or not isinstance(config.get("account"), str)
            or len(config["account"]) != 64
            or any(v not in "0123456789abcdef" for v in config["account"])
        ):
            raise LocalError("invalid_configuration")
        self.cfg = config
        self.root = Archive(config["root"]).root
        self.cache = private_dir(self.root / "gateway-cache")
        self.active = None
        self.active_key = None
        self.active_refresh = False
        self.clients = 0

    def key(self, action, args):
        target, days = request_args(action, args)
        return f"{action}-{target.isoformat()}-{days}"

    def cached(self, key):
        path = self.cache / (key + ".json")
        if not path.exists():
            return {"ok": False, "error": "refresh_in_progress"}
        payload = load_private(path)
        result = payload.get("result", {})
        if result.get("kind") == "daily_brief":
            from .brief import historical_view, render_daily

            result["source_status"] = "stale"
            result["observations"] = {
                "limited": "正在同步；此前快照仅供历史参考，暂不判断今日恢复。"
            }
            result["advice"] = {"sync": "可稍后再查看最新摘要。"}
            historical_view(result, now())
            result["text"] = render_daily(result)
        result["cache_only"] = True
        return payload

    def can_refresh(self):
        p = self.cache / "refresh-state.json"
        if not p.exists():
            return True
        state = load_private(p)
        return gmt(now()) >= gmt(state["next_allowed"])

    async def run_child(self, action, args, refresh, key):
        p = self.cache / "refresh-state.json"
        if refresh:
            atomic_json(p, {"next_allowed": (gmt(now()) + timedelta(minutes=5)).isoformat()})
        request = {
            "action": action,
            "args": args,
            "refresh": refresh,
            "root": str(self.root),
            "account": self.cfg["account"],
        }
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "garmin_readiness.gateway",
                "--worker",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            async with asyncio.timeout(150 if refresh else 15):
                proc.stdin.write(json.dumps(request).encode())
                await proc.stdin.drain()
                proc.stdin.close()
                chunks = bytearray()
                while True:
                    piece = await proc.stdout.read(min(4096, MAX_RESPONSE + 1 - len(chunks)))
                    if not piece:
                        break
                    chunks.extend(piece)
                    if len(chunks) > MAX_RESPONSE:
                        raise LocalError("response_bound")
                await proc.wait()
                if proc.returncode:
                    raise LocalError("child_failed")
                result = json.loads(chunks)
                if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                    raise LocalError("bad_response")
        except asyncio.CancelledError:
            raise
        except Exception:
            result = {"ok": False, "error": "health_unavailable"}
        finally:
            if proc and proc.returncode is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await proc.wait()
        if refresh:
            error = result.get("error") or result.get("result", {}).get("refresh_error")
            if error:
                cooldown = (
                    60
                    if error == "rate_limit"
                    else 30
                    if error
                    in {"auth", "login_required", "interactive_reauth_required", "access_denied"}
                    else 5
                )
                atomic_json(
                    p,
                    {
                        "next_allowed": (gmt(now()) + timedelta(minutes=cooldown)).isoformat(),
                        "error": error,
                    },
                )
        if not refresh and result.get("ok") and p.exists():
            failure = load_private(p).get("error")
            if failure:
                result["result"]["last_refresh_error"] = failure
                if result["result"].get("kind") == "daily_brief":
                    from .brief import render_daily

                    r = result["result"]
                    r["source_status"] = "stale"
                    r["observations"] = {
                        "limited": "最近同步未完成，当前仅有缓存或部分数据，暂不判断恢复。"
                    }
                    r["advice"] = {"sync": "可稍后再查看；如果需要认证，请在本机登录。"}
                    r["text"] = render_daily(r)
        if result.get("ok"):
            atomic_json(self.cache / (key + ".json"), result)
        return result

    async def dispatch(self, request):
        if not isinstance(request, dict) or set(request) != {
            "key",
            "identity",
            "action",
            "args",
            "refresh",
        }:
            return {"ok": False, "error": "unauthorized"}
        if not isinstance(request["key"], str) or not hmac.compare_digest(
            request["key"], self.cfg["key"]
        ):
            return {"ok": False, "error": "unauthorized"}
        if (
            not isinstance(request["identity"], list)
            or any(type(v) is not int for v in request["identity"])
            or request["identity"] != self.cfg["identity"]
            or type(request["refresh"]) is not bool
        ):
            return {"ok": False, "error": "unauthorized"}
        action, args = request["action"], request["args"]
        try:
            key = self.key(action, args)
        except (ValueError, TypeError, LocalError):
            return {"ok": False, "error": "invalid_arguments"}
        if self.active and not self.active.done():
            if request["refresh"]:
                if self.active_refresh and self.active_key == key:
                    return await asyncio.shield(self.active)
                # An offline projection is not a replacement for a requested network
                # refresh. Wait for the current account operation, then re-admit under
                # the durable cooldown. Other concurrent waiters still single-flight.
                await asyncio.shield(self.active)
                return await self.dispatch(request)
            return self.cached(key)
        target, _ = request_args(action, args)
        refresh = (
            request["refresh"]
            and action == "daily_brief"
            and target == gmt(now()).astimezone(TZ).date()
            and self.can_refresh()
        )
        self.active_key = key
        self.active_refresh = refresh
        self.active = asyncio.create_task(self.run_child(action, args, refresh, key))
        return await asyncio.shield(self.active)

    async def handle(self, reader, writer):
        self.clients += 1
        try:
            credentials = writer.get_extra_info("socket").getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, 12
            )
            if self.clients > 8 or struct.unpack("3i", credentials)[1] != os.getuid():
                return
            async with asyncio.timeout(160):
                request = await asyncio.wait_for(reader.readline(), 5)
                if len(request) > 4096:
                    return
                result = await self.dispatch(json.loads(request))
                data = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
                if len(data) > MAX_RESPONSE:
                    data = b'{"ok":false,"error":"response_bound"}'
                writer.write(data + b"\n")
                await writer.drain()
        except Exception:
            # Never send/log raw exceptions from credential-capable code.
            pass
        finally:
            self.clients -= 1
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


async def serve(config_path):
    import fcntl

    cfg = load_private(Path(config_path))
    private_dir(Path(cfg["socket"]).parent)
    lock = os.open(str(cfg["socket"]) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    private_file(Path(str(cfg["socket"]) + ".lock"))
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    path = Path(cfg["socket"])
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise LocalError("unsafe_socket")
        path.unlink()
    gateway = Gateway(cfg)
    server = await asyncio.start_unix_server(gateway.handle, path=str(path), limit=4096)
    path.chmod(0o600)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    os.umask(0o077)
    try:
        if sys.argv[1:] == ["--worker"]:
            child(json.loads(sys.stdin.buffer.read(4097)))
        elif len(sys.argv) == 3 and sys.argv[1] == "--config":
            asyncio.run(serve(sys.argv[2]))
        else:
            raise LocalError("invalid_invocation")
    except KeyboardInterrupt:
        pass
    except Exception:
        # Server errors must never trigger rich credential diagnostics.
        sys.stderr.write("health_gateway_unavailable\n")
        sys.exit(1)
