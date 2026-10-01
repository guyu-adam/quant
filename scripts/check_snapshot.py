#!/usr/bin/env python3
"""verify 第 6 步：校验 Q6_SNAPSHOT 指定的快照；未设置则跳过。

独立成文件是因为 Windows PowerShell 5.1 传参会吞掉 `python -c` 里的双引号。
"""

import os
from pathlib import Path

from q6.data.snapshot import verify_snapshot

snapshot = os.environ.get("Q6_SNAPSHOT")
if not snapshot:
    print("SKIP snapshot (Q6_SNAPSHOT unset)")
else:
    verify_snapshot(Path(os.environ.get("Q6_SNAPSHOT_ROOT", "data/snapshots")), snapshot)
    print(f"snapshot {snapshot} OK")
