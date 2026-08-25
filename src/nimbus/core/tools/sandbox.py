"""Bash sandbox -- confine command execution to a trust boundary.

Wraps subprocess execution in an OS-level sandbox so a compromised or
buggy agent command cannot exfiltrate data or write outside the workspace.
This is a *trust-domain boundary* enforced by the kernel, distinct from
KernelGate's in-process permission checks: the check happens on every
open()/connect() syscall, so no command construction can evade it.

Posture (general-purpose shell -> allow-default, deny the dangerous faces):
- **env whitelist**: child inherits only safe vars, never API keys / secrets
- **network denied**: egress off -- the exfiltration backstop (read may leak,
  but nothing leaves the box)
- **writes confined** to the agent's writable_roots + temp dir
- **reads left open**: pragmatic first cut; tightening reads is the next step

macOS uses Seatbelt (sandbox-exec); Linux uses bubblewrap when available.
Callers select ``off`` / ``best_effort`` / ``required`` and must surface the
returned plan so unavailable protection is never a silent downgrade.
"""

import os
import shutil
import subprocess
import sys
from functools import lru_cache
from typing import Dict, List, Optional

# Env vars safe to pass into the sandbox. Everything else (notably
# ANTHROPIC_API_KEY, OPENAI_API_KEY, and any *_TOKEN / *_SECRET) is dropped:
# a command that never sees a credential cannot leak it.
_ENV_WHITELIST = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM",
    "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ",
})

# Failure attribution is backend-specific, never a cross-backend union:
# Seatbelt reports EPERM while bubblewrap's read-only mount reports EROFS.
#
# Denial: Seatbelt surfaces blocked syscalls as EPERM; the sandbox *worked*
# and stopped an effect. Runner failure: sandbox-exec itself reports on
# stderr with this prefix (bad profile, missing binary); the command was
# never executed. Exit status alone proves neither.
_SEATBELT_DENIAL_SIGNATURES = ("Operation not permitted",)
_SEATBELT_RUNNER_FAILURE_PREFIX = "sandbox-exec: "
_BWRAP_DENIAL_SIGNATURES = (
    "Read-only file system",
    "Network is unreachable",
    "Temporary failure in name resolution",
)
_BWRAP_RUNNER_FAILURE_PREFIX = "bwrap: "
_VALID_MODES = frozenset({"off", "best_effort", "required"})


@lru_cache(maxsize=1)
def sandbox_backend() -> Optional[str]:
    """Return a *usable* kernel sandbox backend for this host.

    Executable presence is not enough inside containers: seccomp/user-namespace
    policy can make bubblewrap fail before the command starts. Probe once so
    best-effort reports ``unavailable`` instead of breaking every Bash call as
    a runner failure.
    """
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        return "seatbelt"
    if sys.platform.startswith("linux"):
        executable = shutil.which("bwrap")
        if executable:
            try:
                probe = subprocess.run(
                    [
                        executable,
                        "--die-with-parent",
                        "--new-session",
                        "--unshare-pid",
                        "--unshare-ipc",
                        "--unshare-uts",
                        "--unshare-net",
                        "--ro-bind", "/", "/",
                        "--dev", "/dev",
                        "--proc", "/proc",
                        "--", "/bin/true",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=2.0,
                    check=False,
                )
                if probe.returncode == 0:
                    return "bubblewrap"
            except (OSError, subprocess.SubprocessError):
                pass
    return None


def sandbox_available() -> bool:
    """True if an OS sandbox mechanism is wired for this platform."""
    return sandbox_backend() is not None


def configured_mode(explicit: Optional[str] = None) -> str:
    """Resolve ``off`` / ``best_effort`` / ``required``.

    ``NIMBUS_SANDBOX_MODE`` is authoritative. The older boolean
    ``NIMBUS_BASH_SANDBOX`` remains a compatibility alias for best-effort.
    Direct tool users default to off; the product server passes its configured
    mode explicitly through KernelGate.
    """
    raw = explicit
    if raw is None:
        raw = os.environ.get("NIMBUS_SANDBOX_MODE")
    if raw is None:
        legacy = os.environ.get("NIMBUS_BASH_SANDBOX", "").strip().lower()
        raw = "best_effort" if legacy in ("1", "true", "on", "yes") else "off"
    mode = str(raw).strip().lower().replace("-", "_")
    if mode == "besteffort":
        mode = "best_effort"
    return mode if mode in _VALID_MODES else "off"


def sandbox_plan(
    mode: str,
    writable_roots: List[str],
    allow_network: bool = False,
) -> Dict[str, object]:
    """Describe the effective capability before execution."""
    resolved_mode = configured_mode(mode)
    backend = sandbox_backend() if sandbox_available() else None
    requested = resolved_mode != "off"
    state = "off"
    if requested:
        state = "active" if backend else "unavailable"
    return {
        "mode": resolved_mode,
        "requested": requested,
        "required": resolved_mode == "required",
        "state": state,
        "backend": backend or "none",
        "network": "host" if allow_network or not requested else "denied",
        "allow_network": allow_network,
        "writable_roots": [os.path.realpath(p) for p in writable_roots if p],
        "env": "host" if not requested else "whitelist",
    }


def classify_output(output: str, backend: Optional[str] = None) -> Optional[str]:
    """Attribute a failed sandboxed command to runner, policy, or command."""
    # Omitted backend preserves the original Seatbelt classifier API; active
    # executions always pass their concrete backend explicitly.
    active_backend = backend or "seatbelt"
    if active_backend == "bubblewrap":
        if any(line.startswith(_BWRAP_RUNNER_FAILURE_PREFIX) for line in output.splitlines()):
            return "runner-failure"
        if any(sig in output for sig in _BWRAP_DENIAL_SIGNATURES):
            return "denial"
        return None

    # Seatbelt is the legacy/default dialect for callers that omit backend.
    for line in output.splitlines():
        if line.startswith(_SEATBELT_RUNNER_FAILURE_PREFIX):
            return "runner-failure"
    if any(sig in output for sig in _SEATBELT_DENIAL_SIGNATURES):
        return "denial"
    return None


def sandboxed_env(base_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Return an env dict filtered to the whitelist.

    This is the single most important line of the sandbox: it keeps
    credentials out of the child regardless of what the command does.
    """
    src = base_env if base_env is not None else os.environ
    return {k: v for k, v in src.items() if k in _ENV_WHITELIST}


def build_profile(writable_roots: List[str], allow_network: bool = False) -> str:
    """Build a Seatbelt (.sb) profile string.

    Allow-default is correct for a shell that runs arbitrary tools -- deny-default
    is unworkable because a single dynamically-linked binary pulls in an
    unenumerable set of implicit dependencies (dyld, shared cache, mach services).
    We instead start permissive and deny only the faces we care about.
    """
    lines = [
        "(version 1)",
        "(allow default)",
    ]
    if not allow_network:
        # Egress off. When the broker/credential-proxy lands (Phase 2), this
        # becomes a narrow allow for the broker's unix socket instead.
        lines.append("(deny network*)")
    lines.append("(deny file-write*)")
    # Devices every shell needs to function.
    lines.append(
        '(allow file-write-data '
        '(literal "/dev/null") (literal "/dev/stdout") (literal "/dev/stderr") '
        '(literal "/dev/tty") (literal "/dev/dtracehelper"))'
    )
    # Temp dirs: many tools stage work here.
    lines.append('(allow file-write* (subpath "/private/tmp"))')
    lines.append('(allow file-write* (subpath "/private/var/tmp"))')
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        lines.append(f'(allow file-write* (subpath {_sb_str(os.path.realpath(tmpdir))}))')
    # The agent's own writable roots.
    for root in writable_roots:
        if root:
            lines.append(f'(allow file-write* (subpath {_sb_str(os.path.realpath(root))}))')
    return "\n".join(lines) + "\n"


def _sb_str(path: str) -> str:
    """Quote a path as a Seatbelt string literal (escape backslash and quote)."""
    escaped = path.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_bwrap_argv(
    writable_roots: List[str], allow_network: bool = False,
) -> List[str]:
    """Build a bubblewrap prefix with read-only host view and narrow writes.

    Reads remain open by design, matching the Seatbelt profile. The child gets
    writable host temp directories, explicitly writable workspace roots, a
    minimal /dev, and no network namespace when egress is disabled.
    """
    argv = [
        "bwrap",
        "--die-with-parent",
        "--new-session",
        # A private PID/proc view prevents a child from reading the parent
        # server's /proc/<pid>/environ and recovering credentials stripped
        # from its own environment.
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
    ]
    if not allow_network:
        argv.append("--unshare-net")

    # General-purpose build tools need temp space. Reads are already open, so
    # binding host temp writable does not widen the documented read posture.
    for temp_root in ("/tmp", "/var/tmp"):
        if os.path.isdir(temp_root):
            argv.extend(("--bind", temp_root, temp_root))

    seen = {"/tmp", "/var/tmp"}
    for root in writable_roots:
        if not root:
            continue
        real = os.path.realpath(root)
        if real in seen or not os.path.exists(real):
            continue
        # A writable /tmp bind already covers pytest/tmp workspaces and avoids
        # mounting a nested destination twice.
        if real.startswith("/tmp/") or real.startswith("/var/tmp/"):
            continue
        seen.add(real)
        argv.extend(("--bind", real, real))
    return argv


def wrap_command(
    command_argv: List[str],
    writable_roots: List[str],
    tmp_dir: str,
    allow_network: bool = False,
) -> List[str]:
    """Wrap a command for the available backend."""
    backend = sandbox_backend()
    if backend == "seatbelt":
        profile = build_profile(writable_roots, allow_network=allow_network)
        return wrap_argv(profile, tmp_dir) + command_argv
    if backend == "bubblewrap":
        return build_bwrap_argv(writable_roots, allow_network=allow_network) + ["--"] + command_argv
    return command_argv


def wrap_argv(profile: str, tmp_dir: str) -> List[str]:
    """Return the argv prefix that runs a command under the given profile.

    Writes the profile to a file under tmp_dir and returns
    ['sandbox-exec', '-f', <profile_path>] to be prepended to the real argv.
    Caller owns tmp_dir cleanup.
    """
    profile_path = os.path.join(tmp_dir, "nimbus-bash.sb")
    with open(profile_path, "w", encoding="utf-8") as f:
        f.write(profile)
    return ["sandbox-exec", "-f", profile_path]
