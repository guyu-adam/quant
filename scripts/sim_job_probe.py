"""P3-04 证据：进 Job 后分配 300MB / 600MB，子进程 600MB。"""
import subprocess
import sys

from q6.sim import limits_win

h = limits_win.confine_self(512)


def alloc(mb):
    try:
        b = bytearray(mb * 2**20)
        b[::4096] = b"x" * len(b[::4096])
        return "ok"
    except MemoryError:
        return "MemoryError"


print("confined:", h is not None, "| 300MB:", alloc(300), "| 600MB:", alloc(600))
child = subprocess.run([sys.executable, "-c", "b = bytearray(600 * 2**20)"], capture_output=True, text=True)
last = child.stderr.strip().splitlines()[-1] if child.stderr else ""
print("child 600MB exit:", child.returncode, "|", last)
print("job:", limits_win.job_limits())
