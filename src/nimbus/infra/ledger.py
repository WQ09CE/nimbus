"""Ledger — external bookkeeping for multi-pod turn execution (nimbus-lab, R1).

Phase 1a of the recovery design: RECORD ONLY. It answers "which pod is running
which turn, and which running turns have lost their pod" — nothing here resumes,
cancels or fences anything (epochs arrive in R2, resume in R3).

Keys (Valkey / Redis, all under one logical namespace):

  pod:{pod}                hash {last, port}  EX dead_after   a pod is alive iff the key exists
  pods                     set of pod ids ever seen
  turn:{session}           hash {epoch, pod, request_id, started}  EX owner_ttl   who owns the running turn;
                           epoch is monotonic and never reset — the fence token for every log write
  orphan:{session}         hash {pod, request_id, detected, detected_by}   first scanner to see it wins
  ledger:orphans_detected  counter

Liveness = heartbeat + key expiry, so a frozen (SIGSTOP) pod dies in the ledger
exactly like a killed one — the watcher is other pods, never the pod itself.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List

logger = logging.getLogger("nimbus.ledger")


class Ledger:
    def __init__(
        self,
        url: str,
        pod_id: str,
        port: int = 0,
        heartbeat_s: float = 5.0,
        dead_after_s: float = 15.0,
        scan_s: float = 10.0,
        owner_ttl_s: int = 1800,
        client: Any = None,
    ):
        self.url = url
        self.pod_id = pod_id
        self.port = port
        self.heartbeat_s = heartbeat_s
        self.dead_after_s = dead_after_s
        self.scan_s = scan_s
        self.owner_ttl_s = owner_ttl_s
        self._r = client
        self._tasks: List[asyncio.Task] = []
        # Called with each newly recorded orphan {session_id, pod, request_id, ...};
        # the owner (SessionManagerV2) decides: resume here, or fast-fail.
        self.on_orphan = None

    # -- lifecycle --------------------------------------------------------

    async def connect(self) -> None:
        if self._r is None:
            import redis.asyncio as redis  # optional extra: nimbus[ledger]

            self._r = redis.from_url(self.url, decode_responses=True)

    async def start(self) -> None:
        await self.connect()
        await self.heartbeat()
        self._tasks = [
            asyncio.create_task(self._loop(self.heartbeat, self.heartbeat_s), name="ledger-heartbeat"),
            asyncio.create_task(self._loop(self.scan_once, self.scan_s), name="ledger-scanner"),
        ]
        logger.info("ledger started pod=%s heartbeat=%ss dead_after=%ss scan=%ss",
                    self.pod_id, self.heartbeat_s, self.dead_after_s, self.scan_s)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []
        if self._r is not None:
            # graceful exit: no longer a candidate owner; turns it still owned
            # (there should be none) become visible as orphans immediately
            await self._r.delete(f"pod:{self.pod_id}")

    async def _loop(self, fn, every: float) -> None:
        while True:
            await asyncio.sleep(every)
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # the ledger must never take a pod down
                logger.warning("ledger %s failed: %s", getattr(fn, "__name__", fn), e)

    # -- heartbeat --------------------------------------------------------

    async def heartbeat(self) -> None:
        key = f"pod:{self.pod_id}"
        await self._r.hset(key, mapping={"last": f"{time.time():.3f}", "port": str(self.port)})
        await self._r.expire(key, int(self.dead_after_s))
        await self._r.sadd("pods", self.pod_id)

    # -- ownership (record only) -----------------------------------------

    # Atomic take-over: epoch is monotonic per session and NEVER reset (the hash
    # is never deleted), so a writer holding an older epoch can always be told apart.
    _CLAIM = """
local e = redis.call('HINCRBY', KEYS[1], 'epoch', 1)
redis.call('HSET', KEYS[1], 'pod', ARGV[1], 'request_id', ARGV[2], 'started', ARGV[3])
redis.call('EXPIRE', KEYS[1], ARGV[4])
return e
"""

    async def claim(self, session_id: str, request_id: str) -> int:
        """Take ownership of the session's turn; returns the new epoch (fence token)."""
        # A claim implies liveness: co-write the heartbeat so a scanner can never
        # see "turn owned by X" without "X alive" (ledger wipe / failover race).
        await self.heartbeat()
        epoch = await self._r.eval(self._CLAIM, 1, f"turn:{session_id}", self.pod_id, request_id,
                                   f"{time.time():.3f}", self.owner_ttl_s)
        return int(epoch)

    async def release(self, session_id: str, request_id: str) -> bool:
        """Drop the owner fields if the record is still ours (same request). The epoch stays."""
        key = f"turn:{session_id}"
        cur = await self._r.hgetall(key)
        if cur and cur.get("request_id") == request_id:
            await self._r.hdel(key, "pod", "request_id", "started")
            return True
        return False

    # -- orphan scanner (any live pod; first to record wins) -------------

    async def scan_once(self) -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = []
        async for key in self._r.scan_iter(match="turn:*"):
            session_id = key.split(":", 1)[1]
            owner = await self._r.hgetall(key)
            if not owner.get("pod") or await self._r.exists(f"pod:{owner['pod']}"):
                continue  # released (epoch-only record) or owner alive
            # Grace: a turn younger than dead_after_s had a live owner when it
            # started — a missing pod key that early is ledger loss, not death.
            try:
                if time.time() - float(owner.get("started", "0")) < self.dead_after_s:
                    continue
            except ValueError:
                pass
            okey = f"orphan:{session_id}"
            if await self._r.hsetnx(okey, "detected", f"{time.time():.3f}"):
                rec = {"pod": owner.get("pod", ""), "request_id": owner.get("request_id", ""),
                       "started": owner.get("started", ""), "detected_by": self.pod_id,
                       "resolution": "none"}
                await self._r.hset(okey, mapping=rec)
                await self._r.incr("ledger:orphans_detected")
                rec["session_id"] = session_id
                found.append(rec)
                logger.warning("ORPHAN turn: session=%s owner pod=%s request=%s (detected by %s)",
                               session_id, rec["pod"], rec["request_id"], self.pod_id)
        for rec in found:
            if self.on_orphan is not None:
                try:
                    await self.on_orphan(rec)
                except Exception as e:  # the handler's failure is recorded, never propagated
                    logger.warning("on_orphan(%s) failed: %s", rec["session_id"], e)
                    await self.resolve(rec["session_id"], f"handler_error:{type(e).__name__}")
        return found

    async def resolve(self, session_id: str, resolution: str) -> None:
        """Record how an orphan was handled: resume | fast_fail | skipped:* | handler_error:*."""
        await self._r.hset(f"orphan:{session_id}", mapping={"resolution": resolution, "resolved": f"{time.time():.3f}"})

    # -- observability ----------------------------------------------------

    async def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        pods: Dict[str, Any] = {}
        for pod in sorted(await self._r.smembers("pods")):
            h = await self._r.hgetall(f"pod:{pod}")
            pods[pod] = {"alive": bool(h), "age_s": round(now - float(h["last"]), 1) if h else None}
        turns = {k.split(":", 1)[1]: await self._r.hgetall(k) async for k in self._r.scan_iter(match="turn:*")}
        orphans = {k.split(":", 1)[1]: await self._r.hgetall(k) async for k in self._r.scan_iter(match="orphan:*")}
        return {"pods": pods, "turns": turns, "orphans": orphans,
                "orphans_detected": int(await self._r.get("ledger:orphans_detected") or 0),
                "rejected_writes": int(await self._r.get("ledger:rejected_writes") or 0)}


async def _main(argv: List[str]) -> None:
    url = os.environ.get("NIMBUS_LEDGER_URL", "redis://127.0.0.1:6379")
    led = Ledger(url, pod_id="cli")
    await led.connect()
    if argv[:1] == ["dump"] and len(argv) > 1:  # events of a session stream: seq type [detail]
        for _id, f in await led._r.xrange(f"sess:{argv[1]}:log"):
            e = json.loads(f["e"])
            d = e.get("data", {})
            m = d.get("message", {}) if isinstance(d, dict) else {}
            x = ""
            if e["type"] in ("turn/end", "step/end"):
                x = json.dumps({k: v for k, v in d.items() if k in ("reason", "turn", "step", "synthetic")})
            elif e["type"] in ("assistant/message", "user/message", "tool/result"):
                x = (m.get("content") or "")[:60].replace("\n", " ")
            print(f'{e["seq"]:>3} {e["type"]:<18} {x}')
        return
    if argv[:1] == ["reset"]:
        n = 0
        for pat in ("pod:*", "turn:*", "orphan:*", "pods", "ledger:*", "sess:*"):
            async for k in led._r.scan_iter(match=pat):
                await led._r.delete(k)
                n += 1
        print(f"reset: {n} keys")
        return
    print(json.dumps(await led.snapshot(), indent=1))


def main() -> None:
    asyncio.run(_main(sys.argv[1:]))


if __name__ == "__main__":
    main()
