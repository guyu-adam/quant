"""supervisor 测试用的假 worker：按 spec.params.mode 模拟各种退出方式。计数器文件记录第几次被拉起。"""

import json
import os
import sys
import time
from pathlib import Path

from q6.sim import layout


def main() -> int:
    saves = Path(sys.argv[sys.argv.index("--saves") + 1])
    spec = json.loads(Path(sys.argv[sys.argv.index("--spec-json") + 1][1:]).read_text(encoding="utf-8"))
    mode, rid = spec["params"]["mode"], spec["run_id"]
    n_file = saves / f"{rid}.starts"
    n = int(n_file.read_text()) + 1 if n_file.exists() else 1
    n_file.write_text(str(n))

    def beat():
        layout.write_json_atomic(layout.hb_dir(saves) / f"{rid}.json",
                                 dict(run_id=rid, pid=os.getpid(), ts=time.time(), n_days=n, total_days=3))

    if mode == "hang":  # 从不写心跳
        time.sleep(3600)
    beat()
    if mode == "stall":  # 写一次心跳后卡住
        time.sleep(3600)
    if mode == "crash" or (mode == "crash_once" and n == 1):
        return 3
    if mode == "soft_once" and n == 1:
        return 75
    if mode == "alloc":
        from q6.sim.worker import EXIT_HARD_MEMORY
        try:
            b = bytearray(int(spec["params"]["mb"]) * 2**20)
            b[::4096] = b"x" * len(b[::4096])  # 真的触碰每一页
        except MemoryError:
            return EXIT_HARD_MEMORY
    time.sleep(0.3)
    beat()
    return 0


if __name__ == "__main__":
    sys.exit(main())
