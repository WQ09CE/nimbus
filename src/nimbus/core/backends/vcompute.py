"""VComputeBackend — ExecutionBackend over the local vcompute service.

The first non-local backend; it exists to make the Gate contract earn its
failure table against a real channel (HTTP, leases, kills). Mapping:

  transport error / 5xx  → BackendFault NETWORK, retryable (Gate retries)
  410                    → BackendFault LEASE_LOST (model told; lease dropped,
                           the NEXT call lazily opens a fresh one — self-heal
                           at the call after the loss, never silently within)
  403                    → BackendFault AUTH_REQUIRED (user flow)
  401                    → BackendFault AUTH_EXPIRED (Gate does one silent
                           refresh attempt — the re-dispatch IS the refresh)
  200                    → raw split payload for Gate normalization

Lease lifecycle note (contract finding, see design doc §9/D1 follow-up): the
Gate threads lease=None in v1, so this backend is session-scoped and manages
its own lease lazily — the same way KernelGate itself is per-session. A
multi-session shared backend would need PreparedCall to carry session identity.
"""

import json
import logging
from typing import List, Optional

import httpx

from ..backend import BackendFault, BackendOutcome, Lease, PreparedCall
from ..tools.registry import ToolDefinition

logger = logging.getLogger("nimbus.backend.vcompute")


class VComputeBackend:
    backend_id = "vcompute"

    def __init__(
        self,
        base_url: str = "",
        session_id: str = "",
        client: Optional[httpx.AsyncClient] = None,
        mount_workspace: bool = True,
        restore_from: Optional[str] = None,
    ):
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=10.0)
        self._session_id = session_id
        self._lease: Optional[Lease] = None
        self._lease_mounted = False
        # False = true-cloud form: ignore the local workspace and use isolated
        # leases, so machine state lives only in the lease (and its snapshots).
        self._mount_workspace = mount_workspace
        # Deferred layer-3 restore: the first lease open replays this snapshot
        # instead of opening fresh (set when the binding is read before any
        # loop has built the backend — the resume-before-first-tool ordering).
        self._restore_from = restore_from
        # Crab-style side-effect tracking (memex Phase 2.5): dirty means a
        # side-effecting call dispatched since the last snapshot/restore, so
        # machine state may have diverged from it. Conservative: set on
        # dispatch attempt, not on confirmed success.
        self._dirty = False
        self._last_snapshot_id: Optional[str] = restore_from

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def last_snapshot_id(self) -> Optional[str]:
        return self._last_snapshot_id

    @property
    def lease_mounted(self) -> bool:
        """True when the active lease mounts the session workspace — a
        mounted workspace is durable by itself, so layer 3 skips binding."""
        return self._lease_mounted

    # -- catalog face --
    def advertise(self) -> List[ToolDefinition]:
        return []  # Bash's definition stays with the local registry

    def catalog_version(self) -> int:
        return 0

    # -- lease face --
    async def open_lease(
        self, session_id: str, workspace: str = "", cwd: str = "",
    ) -> Optional[Lease]:
        resp = await self._client.post(
            "/v1/leases",
            json={"session_id": session_id, "workspace": workspace, "cwd": cwd},
        )
        resp.raise_for_status()
        body = resp.json()
        self._lease_mounted = bool(body.get("mounted"))
        return Lease(backend_id=self.backend_id, lease_id=body["lease_id"])

    async def close_lease(self, lease: Lease) -> None:
        try:
            await self._client.delete(f"/v1/leases/{lease.lease_id}")
        except httpx.HTTPError:
            logger.warning("close_lease best-effort failed", exc_info=True)

    # -- snapshot face (layer-2 verbs; proposed ExecutionBackend contract
    # extension — kept concrete here until a second provider validates the
    # shape). snapshot requires a QUIESCED lease: the daemon answers 409
    # NOT_QUIESCED while calls are in flight, enforcing the two-phase cut
    # (runtime quiesce first, machine snapshot second) from its side too. --
    async def snapshot_lease(self, lease: Optional[Lease] = None) -> str:
        active = lease or self._lease
        if active is None:
            raise RuntimeError("no active lease to snapshot")
        resp = await self._client.post(f"/v1/leases/{active.lease_id}/snapshot")
        resp.raise_for_status()
        snapshot_id = resp.json()["snapshot_id"]
        self._dirty = False
        self._last_snapshot_id = snapshot_id
        return snapshot_id

    async def restore_lease(self, snapshot_id: str) -> Lease:
        """Open a fresh lease rebuilt from a snapshot and adopt it."""
        resp = await self._client.post(
            "/v1/leases",
            json={"session_id": self._session_id, "restore": snapshot_id},
        )
        resp.raise_for_status()
        lease = Lease(backend_id=self.backend_id, lease_id=resp.json()["lease_id"])
        self._lease = lease
        self._lease_mounted = False  # restored leases are always isolated
        self._dirty = False  # machine state == snapshot content, by definition
        self._last_snapshot_id = snapshot_id
        return lease

    # -- dispatch face --
    async def run(self, call: PreparedCall, lease: Optional[Lease] = None) -> BackendOutcome:
        if call.tool != "Bash":
            return BackendFault(
                code="BACKEND_UNAVAILABLE",
                message=f"vcompute executes Bash only, got {call.tool!r}",
                retryable=False,
            )
        if call.traits.side_effects in ("write", "execute"):
            self._dirty = True
        payload = {
            "call_id": call.call_id,
            "command": str(call.args.get("command", "")),
            # Daemon backstop slightly above the Gate deadline: the Gate owns
            # the timeout; the backstop only reaps orphans.
            "timeout_s": (call.deadline_s if call.deadline_s is not None else 60.0) + 5.0,
        }
        # Finding #4: the grant travels. Only a real OS-sandbox intent is
        # forwarded; host-scoped writable roots stay home (meaningless there).
        grant = call.sandbox_grant or {}
        if grant.get("mode") in ("best_effort", "required"):
            payload["sandbox"] = {
                "mode": grant["mode"],
                "allow_network": bool(grant.get("allow_network", False)),
                "required": bool(grant.get("required", False)),
            }
        timeout = httpx.Timeout(payload["timeout_s"] + 5.0)

        try:
            active = lease or self._lease
            if active is None:
                if self._restore_from:
                    snapshot_id, self._restore_from = self._restore_from, None
                    active = await self.restore_lease(snapshot_id)
                else:
                    # Finding #7: mount the session workspace so the file
                    # surface and the exec surface are the same directory
                    # (unless the true-cloud isolated form is requested).
                    pc = call.path_context if self._mount_workspace else None
                    workspace = str(getattr(pc, "target_root", "") or "") if pc else ""
                    cwd = str(getattr(pc, "execution_cwd", "") or "") if pc else ""
                    active = await self.open_lease(
                        call.session_id or self._session_id,
                        workspace=workspace, cwd=cwd,
                    )
                    self._lease = active
            async with self._client.stream(
                "POST", f"/v1/leases/{active.lease_id}/exec",
                json=payload, timeout=timeout,
            ) as resp:
                if resp.status_code != 200:
                    await resp.aread()
                    return self._fault_for(resp)
                body = None
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    obj = json.loads(line)
                    if obj.get("type") == "chunk":
                        # Finding #3: the streaming leg — remote output reaches
                        # the Gate's delta wrapper (and the UI) live.
                        if call.on_stream is not None:
                            try:
                                call.on_stream(obj.get("data", ""))
                            except Exception:
                                logger.exception("on_stream callback failed")
                    elif obj.get("type") == "result":
                        body = obj
                        break
        except httpx.HTTPError as exc:
            return BackendFault(code="NETWORK", message=str(exc) or type(exc).__name__, retryable=True)

        if body is None:
            return BackendFault(
                code="NETWORK", message="stream ended without a result line", retryable=True,
            )
        ui_detail = {
            "backend": self.backend_id,
            "lease_id": active.lease_id,
            "cwd": body.get("cwd"),
            "exit_code": body.get("exit_code"),
            "remote_duration_ms": body.get("duration_ms"),
        }
        sandbox_verdict = body.get("sandbox") or {}
        if sandbox_verdict.get("state") != "off":
            # Same surface the local runner uses: the Gate re-emits this as a
            # SANDBOX_STATUS event, so remote confinement is UI-attributable.
            ui_detail["sandbox_details"] = sandbox_verdict
        return {
            "output": body.get("output", ""),
            "status": body.get("status", "OK"),
            "ui_detail": ui_detail,
        }

    def _fault_for(self, resp: httpx.Response) -> BackendFault:
        if resp.status_code == 410:
            self._lease = None  # next call opens a fresh lease
            return BackendFault(code="LEASE_LOST", message=self._msg(resp), retryable=False)
        if resp.status_code == 403:
            return BackendFault(code="AUTH_REQUIRED", message=self._msg(resp), retryable=False)
        if resp.status_code == 401:
            return BackendFault(code="AUTH_EXPIRED", message=self._msg(resp), retryable=False)
        if resp.status_code >= 500:
            return BackendFault(code="NETWORK", message=self._msg(resp), retryable=True)
        return BackendFault(
            code="BACKEND_UNAVAILABLE",
            message=f"unexpected status {resp.status_code}: {self._msg(resp)}",
            retryable=False,
        )

    async def cancel(self, call_id: str, lease: Optional[Lease] = None) -> None:
        active = lease or self._lease
        if active is None:
            return
        try:
            await self._client.post(
                f"/v1/leases/{active.lease_id}/calls/{call_id}/cancel"
            )
        except httpx.HTTPError:
            # Best effort: the daemon backstop timeout is the last line.
            logger.warning("vcompute cancel best-effort failed", exc_info=True)

    @staticmethod
    def _msg(resp: httpx.Response) -> str:
        try:
            return str(resp.json().get("message", resp.text))
        except Exception:
            return resp.text
