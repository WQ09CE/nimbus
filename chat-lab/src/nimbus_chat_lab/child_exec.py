"""Linux-only exec launcher: a text-model process must not outlive its worker.

Runs in a fresh interpreter (NOT preexec_fn in a multithreaded process). The
systemd cgroup remains the backstop for arbitrary descendants. No tools may be
added to this launcher without reviewing the complete process-tree boundary.
"""

import ctypes
import os
import signal
import sys


def main():
    if sys.platform != "linux" or len(sys.argv) < 3:
        os._exit(2)
    expected_parent = int(sys.argv[1])
    libc = ctypes.CDLL(None, use_errno=True)
    # PR_SET_PDEATHSIG; retained across exec of the non-setuid Pi binary.
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        os._exit(2)
    # Close the race where the worker died before prctl() was installed.
    if os.getppid() != expected_parent:
        os._exit(2)
    os.execvpe(sys.argv[2], sys.argv[2:], os.environ)


if __name__ == "__main__":
    main()
