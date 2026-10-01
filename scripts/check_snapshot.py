#!/usr/bin/env python3
"""verify 第 6 步：校验 Q6_SNAPSHOT 指定的快照（快照 ID 或快照目录路径）。

未设置时：Q6_REQUIRE_SNAPSHOT=1（验收模式）退出码 1；否则打印 SKIP。
独立成文件是因为 Windows PowerShell 5.1 传参会吞掉 `python -c` 里的双引号。
"""

import os
import sys
from pathlib import Path

from q6.data.snapshot import verify_snapshot

snapshot = os.environ.get("Q6_SNAPSHOT")
if not snapshot:
    if os.environ.get("Q6_REQUIRE_SNAPSHOT") == "1":
        print("FAIL snapshot: Q6_REQUIRE_SNAPSHOT=1 but Q6_SNAPSHOT unset", file=sys.stderr)
        sys.exit(1)
    print("SKIP snapshot (Q6_SNAPSHOT unset)")
else:
    verify_snapshot(Path(os.environ.get("Q6_SNAPSHOT_ROOT", "data/snapshots")), snapshot)
    print(f"snapshot {snapshot} OK")
