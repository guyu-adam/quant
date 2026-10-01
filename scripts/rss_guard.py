#!/usr/bin/env python3
"""Run a command and enforce a per-process peak RSS limit."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit-mb", type=float, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("a command is required after --")
    if args.limit_mb <= 0:
        parser.error("--limit-mb must be positive")
    return args


def windows_peak(pid: int, process: subprocess.Popen[bytes]) -> int:
    import psutil

    observed: dict[int, psutil.Process] = {}
    peak = 0
    while process.poll() is None:
        try:
            root = psutil.Process(pid)
            observed[pid] = root
            for child in root.children(recursive=True):
                observed[child.pid] = child
        except psutil.Error:
            pass
        for proc in list(observed.values()):
            try:
                peak = max(peak, proc.memory_info().peak_wset)
            except (psutil.Error, AttributeError):
                observed.pop(proc.pid, None)
        time.sleep(0.05)
    # The root may exit between the last poll and wait completion.
    for proc in observed.values():
        try:
            peak = max(peak, proc.memory_info().peak_wset)
        except (psutil.Error, AttributeError):
            pass
    return peak


def main() -> int:
    args = parse_args()
    if os.name == "nt":
        child = subprocess.Popen(args.command)
        peak_bytes = windows_peak(child.pid, child)
        returncode = child.wait()
    else:
        import resource

        child = subprocess.Popen(args.command)
        returncode = child.wait()
        peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        # macOS reports bytes; other POSIX systems (including Linux) report KiB.
        peak_bytes = int(peak if sys.platform == "darwin" else peak * 1024)

    peak_mib = peak_bytes / (1024 * 1024)
    print(f"PEAK_RSS {peak_mib:.1f} MiB (limit {args.limit_mb:g})", flush=True)
    if peak_bytes <= 0:
        # 读不到读数时不能当作通过，否则守卫形同虚设
        print("RSS MEASUREMENT FAILED", file=sys.stderr, flush=True)
        return returncode or 1
    if returncode == 0 and peak_mib > args.limit_mb:
        print("RSS LIMIT EXCEEDED", file=sys.stderr, flush=True)
        return 1
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
