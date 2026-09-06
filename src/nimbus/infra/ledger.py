"""Ledger — external bookkeeping for multi-pod turn execution (nimbus-lab, R1).

Phase 1a of the recovery design: RECORD ONLY. It answers "which pod is running
which turn, and which running turns have lost their pod" — nothing here resumes,
cancels or fences anything (epochs arrive in R2, resume in R3).

Keys (Valkey / Redis, all under one logical namespace):

  pod:{pod}                hash {last, port, inc, rss_mb, mem_pct, lag_ms, gc2_ms, prev_oom_kills}  EX dead_after
                           a pod is alive iff the key exists AND its inc(arnation) is the one that claimed the turn
  podlast:{pod}            the same hash without expiry — the pod's last words, readable after it died
  pods                     set of pod ids ever seen
  turn:{session}           hash {epoch, pod, inc, request_id, started, bytes, owners}  EX owner_ttl   who owns the
                           running turn; epoch is monotonic and never reset — the fence token for every log write;
                           bytes = tool output the turn has ingested into the owner process (R4);
                           owners = "pod/epoch ..." history of every claim (R5: who ran this turn, in order)
  orphan:{session}         hash {pod, inc, epoch, request_id, detected, detected_by, bytes, owner_mem_pct,
                           resolution}  recorded once PER EPOCH (field e{epoch}): a turn whose rescuer dies
                           is an orphan again
  ledger:orphans_detected  counter

Liveness = heartbeat + key expiry, so a frozen (SIGSTOP) pod dies in the ledger
exactly like a killed one — the watcher is other pods, never the pod itself.
Incarnation (R4): a pod restarted under the same id beats with a new ``inc``, so
the turns its predecessor owned are orphans the moment the successor appears —
a same-name restart (k8s container restart) can no longer hide them.
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import os
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

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
        generation: int = 0,
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
        self.inc = uuid.uuid4().hex[:8]  # incarnation: this process, not this pod id
        self._lag_ms = 0.0    # how late the last heartbeat woke up (event-loop stall confession)
        self._gc2_ms = 0.0    # longest gen-2 GC pause since the last heartbeat
        self._gc_t0: Optional[float] = None
        self.prev_oom_kills = 0  # our cgroup's oom_kill count at start: the predecessor's cause of death
        # R5: deploy generation (build id). Once a live pod of a newer generation beats, this
        # pod is superseded: it finishes what it has but takes no new work — no handoff
        # consumption, no orphan scanning (Temporal worker versioning: old builds only
        # drain their pinned workflows).
        self.generation = generation
        self.superseded = False
        self.on_superseded = None
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
        self.prev_oom_kills = int(_cgroup_mem().get("oom_kill", 0))
        if self.prev_oom_kills:
            logger.warning("ledger pod=%s: this cgroup has oom_kill=%d — a previous incarnation was OOM-killed",
                           self.pod_id, self.prev_oom_kills)
        gc.callbacks.append(self._gc_cb)
        await self.heartbeat()
        self._tasks = [
            asyncio.create_task(self._beat_loop(), name="ledger-heartbeat"),
            asyncio.create_task(self._loop(self.scan_once, self.scan_s), name="ledger-scanner"),
        ]
        logger.info("ledger started pod=%s inc=%s heartbeat=%ss dead_after=%ss scan=%ss",
                    self.pod_id, self.inc, self.heartbeat_s, self.dead_after_s, self.scan_s)

    async def stop(self) -> None:
        if self._gc_cb in gc.callbacks:
            gc.callbacks.remove(self._gc_cb)
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

    async def _beat_loop(self) -> None:
        # A heartbeat that wakes up late confesses the stall it could not report
        # while it was happening (sync work / GC / swap on the event loop).
        while True:
            due = time.monotonic() + self.heartbeat_s
            await asyncio.sleep(self.heartbeat_s)
            self._lag_ms = max(0.0, (time.monotonic() - due) * 1000)
            try:
                await self.heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("ledger heartbeat failed: %s", e)

    def _gc_cb(self, phase: str, info: Dict[str, Any]) -> None:
        if info.get("generation") != 2:
            return
        if phase == "start":
            self._gc_t0 = time.perf_counter()
        elif self._gc_t0 is not None:
            self._gc2_ms = max(self._gc2_ms, (time.perf_counter() - self._gc_t0) * 1000)
            self._gc_t0 = None

    # -- heartbeat --------------------------------------------------------

    def facts(self) -> Dict[str, str]:
        """What the pod knows about itself right now — the precursors 0903 lacked:
        RSS, cgroup memory %, event-loop lag, longest gen-2 GC pause, prior OOM kills."""
        cg = _cgroup_mem()
        cur, mx = cg.get("current"), cg.get("max")
        f = {"inc": self.inc, "gen": str(self.generation), "rss_mb": f"{_rss_mb():.1f}",
             "lag_ms": f"{self._lag_ms:.0f}", "gc2_ms": f"{self._gc2_ms:.0f}", "prev_oom_kills": str(self.prev_oom_kills)}
        if cur is not None and mx:
            f["mem_pct"] = f"{100.0 * cur / mx:.0f}"
        self._gc2_ms = 0.0
        return f

    async def heartbeat(self) -> None:
        key = f"pod:{self.pod_id}"
        mapping = {"last": f"{time.time():.3f}", "port": str(self.port), **self.facts()}
        await self._r.hset(key, mapping=mapping)
        await self._r.expire(key, int(self.dead_after_s))
        await self._r.hset(f"podlast:{self.pod_id}", mapping=mapping)  # last words survive the expiry
        await self._r.sadd("pods", self.pod_id)
        if not self.superseded:
            await self._check_generation()

    async def _check_generation(self) -> None:
        for pod in await self._r.smembers("pods"):
            if pod == self.pod_id:
                continue
            h = await self._r.hgetall(f"pod:{pod}")
            if h and int(h.get("gen", "0") or 0) > self.generation:
                self.superseded = True
                logger.warning("ledger pod=%s gen=%s superseded by pod=%s gen=%s: no new work from here on",
                               self.pod_id, self.generation, pod, h.get("gen"))
                if self.on_superseded is not None:
                    try:
                        await self.on_superseded()
                    except Exception as e:
                        logger.warning("on_superseded failed: %s", e)
                return

    # -- ownership (record only) -----------------------------------------

    # Atomic take-over: epoch is monotonic per session and NEVER reset (the hash
    # is never deleted), so a writer holding an older epoch can always be told apart.
    _CLAIM = """
local e = redis.call('HINCRBY', KEYS[1], 'epoch', 1)
local o = redis.call('HGET', KEYS[1], 'owners')
redis.call('HSET', KEYS[1], 'pod', ARGV[1], 'request_id', ARGV[2], 'started', ARGV[3], 'inc', ARGV[5],
           'owners', (o or '') .. ARGV[1] .. '/' .. e .. ' ')
if ARGV[6] == '1' then redis.call('HSET', KEYS[1], 'bytes', '0') end
redis.call('EXPIRE', KEYS[1], ARGV[4])
return e
"""

    async def claim(self, session_id: str, request_id: str, fresh: bool = True) -> int:
        """Take ownership of the session's turn; returns the new epoch (fence token).
        fresh=True starts a new turn's ingest count; a takeover/resume keeps it."""
        # A claim implies liveness: co-write the heartbeat so a scanner can never
        # see "turn owned by X" without "X alive" (ledger wipe / failover race).
        await self.heartbeat()
        epoch = await self._r.eval(self._CLAIM, 1, f"turn:{session_id}", self.pod_id, request_id,
                                   f"{time.time():.3f}", self.owner_ttl_s, self.inc, "1" if fresh else "0")
        return int(epoch)

    async def account(self, session_id: str, nbytes: int) -> None:
        """Charge tool output the turn pulled into this process (R4: per-turn ingest,
        the attribution a process-level RSS cannot give)."""
        if nbytes > 0:
            await self._r.hincrby(f"turn:{session_id}", "bytes", int(nbytes))

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
        if self.superseded:
            return found  # a newer generation is alive: orphans are its work, not ours
        async for key in self._r.scan_iter(match="turn:*"):
            session_id = key.split(":", 1)[1]
            owner = await self._r.hgetall(key)
            if not owner.get("pod"):
                continue  # released (epoch-only record)
            alive = await self._r.hgetall(f"pod:{owner['pod']}")
            if alive and alive.get("inc", owner.get("inc")) == owner.get("inc"):
                continue  # owner alive, same incarnation
            if not alive:
                # Grace: a turn younger than dead_after_s had a live owner when it
                # started — a missing pod key that early is ledger loss, not death.
                # (A successor incarnation beating under the same id is proof, not loss.)
                try:
                    if time.time() - float(owner.get("started", "0")) < self.dead_after_s:
                        continue
                except ValueError:
                    pass
            okey = f"orphan:{session_id}"
            epoch = owner.get("epoch", "0")
            if await self._r.hsetnx(okey, f"e{epoch}", f"{time.time():.3f}"):  # once per epoch
                last = await self._r.hgetall(f"podlast:{owner['pod']}")
                rec = {"pod": owner.get("pod", ""), "inc": owner.get("inc", ""), "epoch": epoch,
                       "request_id": owner.get("request_id", ""), "started": owner.get("started", ""),
                       "bytes": owner.get("bytes", "0"), "owner_mem_pct": last.get("mem_pct", ""),
                       "detected": f"{time.time():.3f}", "detected_by": self.pod_id, "resolution": "none"}
                await self._r.hset(okey, mapping=rec)
                await self._r.incr("ledger:orphans_detected")
                rec["session_id"] = session_id
                found.append(rec)
                logger.warning("ORPHAN turn: session=%s owner pod=%s/%s epoch=%s ingest=%sB owner_mem=%s%% (detected by %s)",
                               session_id, rec["pod"], rec["inc"], epoch, rec["bytes"], rec["owner_mem_pct"], self.pod_id)
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
            last = h or await self._r.hgetall(f"podlast:{pod}")
            pods[pod] = {"alive": bool(h), "age_s": round(now - float(last["last"]), 1) if last else None,
                         **{k: last[k] for k in ("inc", "gen", "rss_mb", "mem_pct", "lag_ms", "gc2_ms", "prev_oom_kills") if k in last}}
        turns = {k.split(":", 1)[1]: await self._r.hgetall(k) async for k in self._r.scan_iter(match="turn:*")}
        orphans = {k.split(":", 1)[1]: await self._r.hgetall(k) async for k in self._r.scan_iter(match="orphan:*")}
        return {"pods": pods, "turns": turns, "orphans": orphans,
                "orphans_detected": int(await self._r.get("ledger:orphans_detected") or 0),
                "rejected_writes": int(await self._r.get("ledger:rejected_writes") or 0)}


def _cgroup_mem() -> Dict[str, Any]:
    """This process's cgroup v2 memory facts: current, max (None = unlimited), oom_kill count.
    Works inside a container (cgroup namespace) and under systemd alike."""
    try:
        rel = ""
        for line in open("/proc/self/cgroup"):
            if line.startswith("0::"):
                rel = line.strip()[3:]
        d = "/sys/fs/cgroup" + rel
        out: Dict[str, Any] = {"current": int(open(f"{d}/memory.current").read())}
        mx = open(f"{d}/memory.max").read().strip()
        out["max"] = None if mx == "max" else int(mx)
        for line in open(f"{d}/memory.events"):
            k, v = line.split()
            if k == "oom_kill":
                out["oom_kill"] = int(v)
        return out
    except (OSError, ValueError):
        return {}


def _rss_mb() -> float:
    try:
        return int(open("/proc/self/statm").read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2**20
    except (OSError, ValueError, IndexError):
        return 0.0


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
        for pat in ("pod:*", "podlast:*", "turn:*", "orphan:*", "pods", "ledger:*", "sess:*"):
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
