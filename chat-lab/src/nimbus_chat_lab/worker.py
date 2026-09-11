import asyncio
from contextlib import suppress
from uuid import uuid4


class AuthorityLost(Exception):
    pass


class CancelRequested(Exception):
    pass


class WorkerPoisoned(Exception):
    pass


class Worker:
    def __init__(self, store, engine, *, run_timeout=180):
        self.store, self.engine, self.run_timeout = store, engine, run_timeout
        self.incarnation = uuid4()

    async def run_once(self):
        claim = await self.store.claim(self.incarnation)
        if claim is None:
            return None

        async def before_request():
            state = await self.store.control(claim)
            if state == "cancel_requested":
                raise CancelRequested()
            if state != "running":
                raise AuthorityLost()

        async def emit(text):
            if not await self.store.progress(claim, text):
                await before_request()
                raise AuthorityLost()

        async def execute():
            history = await self.store.history(claim)
            return await asyncio.wait_for(
                self.engine.run(claim, history, emit, before_request), self.run_timeout
            )

        async def guard():
            while True:
                state = await self.store.control(claim, renew=True)
                if state == "cancel_requested":
                    raise CancelRequested()
                if state != "running":
                    raise AuthorityLost()
                await asyncio.sleep(min(1, self.store.lease_seconds / 5))

        job, heartbeat = asyncio.create_task(execute()), asyncio.create_task(guard())
        state, text = "failed", "执行失败。"
        try:
            done, _ = await asyncio.wait({job, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                await heartbeat  # authority/cancel/DB errors take precedence over a racing result
            text = await job
            state = "succeeded"
        except CancelRequested:
            state, text = (
                "cancelled",
                "已确认停止本次客户端执行；已完成的工具效果不会回滚，外部服务端计算是否停止未确认。",
            )
        except AuthorityLost:
            state, text = "interrupted", "执行权已失效，旧 worker 不再发布结果。"
        except asyncio.CancelledError:
            state, text = "interrupted", "worker 排空超时或关闭；任务未自动续跑。"
            raise
        except Exception as exc:
            # No prompts, DB DSNs, HTTP token URLs or raw model errors in user/log output.
            state, text = "failed", f"执行失败（{type(exc).__name__}）；未自动重跑。"
        finally:
            job.cancel()
            stopped, _ = await asyncio.wait({job}, timeout=8)
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await heartbeat
            if not stopped:
                # Do not release admission on an unconfirmed local stop. The CLI
                # must fail-stop this process; lease recovery owns interruption.
                raise WorkerPoisoned("Engine did not stop; worker must be terminated")
            with suppress(asyncio.CancelledError, Exception):
                await job
            committed = await self.store.finish(claim, state, text)
            if not committed and state == "succeeded":
                # A cancellation can commit after the last heartbeat and before finish.
                if await self.store.control(claim) == "cancel_requested":
                    await self.store.finish(
                        claim, "cancelled", "已确认停止；取消请求先于终态提交。"
                    )
        return claim

    async def run(self, stop):
        while not stop.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(stop.wait(), 0.25)
            except TimeoutError:
                pass
