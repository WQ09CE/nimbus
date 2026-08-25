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
    ):
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=10.0)
        self._session_id = session_id
        self._lease: Optional[Lease] = None

    # -- catalog face --
    def advertise(self) -> List[ToolDefinition]:
        return []  # Bash's definition stays with the local registry

    def catalog_version(self) -> int:
        return 0

    # -- lease face --
    async def open_lease(self, session_id: str) -> Optional[Lease]:
        resp = await self._client.post("/v1/leases", json={"session_id": session_id})
        resp.raise_for_status()
        body = resp.json()
        return Lease(backend_id=self.backend_id, lease_id=body["lease_id"])

    async def close_lease(self, lease: Lease) -> None:
        try:
            await self._client.delete(f"/v1/leases/{lease.lease_id}")
        except httpx.HTTPError:
            logger.warning("close_lease best-effort failed", exc_info=True)

    # -- dispatch face --
    async def run(self, call: PreparedCall, lease: Optional[Lease] = None) -> BackendOutcome:
        if call.tool != "Bash":
            return BackendFault(
                code="BACKEND_UNAVAILABLE",
                message=f"vcompute executes Bash only, got {call.tool!r}",
                retryable=False,
            )
        try:
            active = lease or self._lease
            if active is None:
                active = await self.open_lease(self._session_id)
                self._lease = active
            timeout_s = call.deadline_s if call.deadline_s is not None else 60.0
            resp = await self._client.post(
                f"/v1/leases/{active.lease_id}/exec",
                json={
                    "call_id": call.call_id,
                    "command": str(call.args.get("command", "")),
                    # Daemon backstop slightly above the Gate deadline: the
                    # Gate owns the timeout; the backstop only reaps orphans.
                    "timeout_s": timeout_s + 5.0,
                },
                timeout=httpx.Timeout(timeout_s + 10.0),
            )
        except httpx.HTTPError as exc:
            return BackendFault(code="NETWORK", message=str(exc) or type(exc).__name__, retryable=True)

        if resp.status_code == 410:
            self._lease = None  # next call opens a fresh lease
            return BackendFault(
                code="LEASE_LOST", message=self._msg(resp), retryable=False,
            )
        if resp.status_code == 403:
            return BackendFault(code="AUTH_REQUIRED", message=self._msg(resp), retryable=False)
        if resp.status_code == 401:
            return BackendFault(code="AUTH_EXPIRED", message=self._msg(resp), retryable=False)
        if resp.status_code >= 500:
            return BackendFault(code="NETWORK", message=self._msg(resp), retryable=True)
        if resp.status_code != 200:
            return BackendFault(
                code="BACKEND_UNAVAILABLE",
                message=f"unexpected status {resp.status_code}: {self._msg(resp)}",
                retryable=False,
            )

        body = resp.json()
        return {
            "output": body.get("output", ""),
            "status": body.get("status", "OK"),
            "ui_detail": {
                "backend": self.backend_id,
                "lease_id": active.lease_id,
                "cwd": body.get("cwd"),
                "exit_code": body.get("exit_code"),
                "remote_duration_ms": body.get("duration_ms"),
            },
        }

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
