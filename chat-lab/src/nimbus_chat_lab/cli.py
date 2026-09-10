import argparse
import asyncio
import json
import os
import shutil
import signal
import stat
import sys
from contextlib import suppress
from pathlib import Path

from loguru import logger

from .engine import EchoEngine, NimbusEngine
from .gateway import Gateway
from .store import Store
from .telegram import TelegramClient, identify
from .worker import Worker, WorkerPoisoned


def token_from_file(path: str) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_size > 4096
        ):
            raise ValueError("Token file must be an owned regular file, mode 0600, <=4096 bytes")
        return os.read(fd, 4096).decode().strip()
    finally:
        os.close(fd)


async def execute(args):
    if args.command == "doctor":
        print(
            json.dumps(
                {
                    "host": os.uname().nodename,
                    "cpu_count": os.cpu_count(),
                    "kvm_present": Path("/dev/kvm").exists(),
                    "binaries": {
                        n: shutil.which(n)
                        for n in ("pi", "chromium", "podman", "runsc", "psql", "initdb")
                    },
                    "dsn_configured": bool(os.getenv("NIMBUS_LAB_DSN")),
                    "token_file_configured": bool(os.getenv("NIMBUS_TG_TOKEN_FILE")),
                    "tool_execution": "DISABLED: no verified remote sandbox; no local fallback",
                },
                indent=2,
            )
        )
        return
    if args.command == "identify":
        client = TelegramClient(token_from_file(os.environ["NIMBUS_TG_TOKEN_FILE"]))
        try:
            print(json.dumps(await identify(client), indent=2))
        finally:
            await client.close()
        return
    dsn = os.environ.get("NIMBUS_LAB_DSN")
    if not dsn:
        raise ValueError("Set NIMBUS_LAB_DSN to a dedicated PostgreSQL database")
    store = Store(dsn, lease_seconds=args.lease)
    if args.command == "init":
        await store.initialize()
        return
    if args.command == "allow":
        await store.authorize(args.bot, args.user, args.chat)
        return
    if args.command == "scan":
        print(json.dumps({"recovered": await store.recover()}))
        return
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    if args.command == "gateway":
        token = token_from_file(os.environ["NIMBUS_TG_TOKEN_FILE"])
        client = TelegramClient(token)
        try:
            await Gateway(store, client, args.bot, drafts=args.drafts).run(stop)
        finally:
            await client.close()
        return
    if os.getenv("NIMBUS_VCOMPUTE_URL"):
        raise RuntimeError("Do not inherit another lab execution backend")
    root = Path(args.state).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.engine == "echo":
        engine = EchoEngine()
    elif args.engine == "nimbus-mock":
        from nimbus.testing.mock_llm import MockLLMAdapter

        engine = NimbusEngine(root / "attempts", adapter_factory=MockLLMAdapter)
    else:
        engine = NimbusEngine(root / "attempts", pi_executable=args.pi)
    worker = Worker(store, engine, run_timeout=args.run_timeout)
    if args.once:
        claim = await worker.run_once()
        print(json.dumps({"attempt": str(claim.attempt_id) if claim else None}))
        return
    job = asyncio.create_task(worker.run(stop))
    stopped = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait({job, stopped}, return_when=asyncio.FIRST_COMPLETED)
        if job in done:
            await job
        else:
            try:
                await asyncio.wait_for(asyncio.shield(job), args.drain_timeout)
            except TimeoutError:
                job.cancel()
                with suppress(asyncio.CancelledError):
                    await job
    finally:
        stopped.cancel()
        with suppress(asyncio.CancelledError):
            await stopped


async def guarded_execute(args):
    try:
        await execute(args)
    except WorkerPoisoned:
        # asyncio.run's normal shutdown would wait forever for a task that
        # suppresses cancellation. Fail-stop before it gets that opportunity.
        print(
            "FAILED: WorkerPoisoned; process stopping, lease scanner owns recovery.",
            file=sys.stderr,
            flush=True,
        )
        os._exit(2)


def main():
    os.umask(0o077)
    logger.disable("nimbus")  # Nimbus development logs can include model/tool bodies.
    parser = argparse.ArgumentParser(
        description="Private Telegram lab: dedicated PG, no local code execution"
    )
    parser.add_argument("--lease", type=float, default=30)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "identify", "init", "scan"):
        sub.add_parser(name)
    allow = sub.add_parser("allow")
    for field in ("bot", "user", "chat"):
        allow.add_argument(f"--{field}", type=int, required=True)
    gateway = sub.add_parser("gateway")
    gateway.add_argument("--bot", type=int, required=True)
    gateway.add_argument("--drafts", action="store_true")
    worker = sub.add_parser("worker")
    worker.add_argument("--engine", choices=["echo", "nimbus-mock", "nimbus-pi"], required=True)
    worker.add_argument("--state", default=".runtime/chat-lab")
    worker.add_argument("--pi", default="pi")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--run-timeout", type=float, default=180)
    worker.add_argument("--drain-timeout", type=float, default=120)
    try:
        asyncio.run(guarded_execute(parser.parse_args()))
    except (Exception, KeyboardInterrupt) as error:
        # Avoid dumping psycopg DSN, Telegram token URL, user text or model payload.
        print(
            f"BLOCKED/FAILED: {type(error).__name__}. Check operator setup and runbook; no credentials logged.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
