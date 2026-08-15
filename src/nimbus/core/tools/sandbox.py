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

macOS uses Seatbelt (sandbox-exec). Other platforms currently pass through
unchanged (bubblewrap wiring is a TODO -- see agent-infra-deep-dive-plan.md
Phase 1a); callers should check sandbox_available() to know which they got.
"""

import os
import shutil
import sys
from typing import Dict, List, Optional

# Env vars safe to pass into the sandbox. Everything else (notably
# ANTHROPIC_API_KEY, OPENAI_API_KEY, and any *_TOKEN / *_SECRET) is dropped:
# a command that never sees a credential cannot leak it.
_ENV_WHITELIST = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM",
    "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ",
})

# Failure-attribution dialect for the *Seatbelt* backend specifically -- not a
# cross-backend union. A future bubblewrap backend must ship its own dialect
# (EROFS wording) or denials will be misclassified.
#
# Denial: Seatbelt surfaces blocked syscalls as EPERM; the sandbox *worked*
# and stopped an effect. Runner failure: sandbox-exec itself reports on
# stderr with this prefix (bad profile, missing binary); the command was
# never executed. Exit status alone proves neither.
_DENIAL_SIGNATURES = ("Operation not permitted",)
_RUNNER_FAILURE_PREFIX = "sandbox-exec: "


def classify_output(output: str) -> Optional[str]:
    """Attribute a failed sandboxed command from its combined output.

    Returns 'runner-failure' when sandbox-exec itself failed (the command
    never ran), 'denial' when the sandbox likely blocked an operation, or
    None for an ordinary command failure. Runner failure is checked first:
    a broken sandbox must never be misreported as the command failing.
    Only meaningful for a nonzero exit under an active sandbox.
    """
    for line in output.splitlines():
        if line.startswith(_RUNNER_FAILURE_PREFIX):
            return "runner-failure"
    if any(sig in output for sig in _DENIAL_SIGNATURES):
        return "denial"
    return None


def sandbox_available() -> bool:
    """True if an OS sandbox mechanism is wired for this platform."""
    return sys.platform == "darwin" and shutil.which("sandbox-exec") is not None


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
