"""q6：v6 量化研究与回测框架。"""

import os
import sys

# Arrow 的内存池只在 pyarrow 首次导入时按这个环境变量选定，之后 set_memory_pool 换池不完全生效（实测）。
# 默认的 mimalloc 不把读完的 parquet 缓冲区还给系统：P2-07 全区间回测逐年读快照，macOS 峰值 RSS 945MB，
# jemalloc 实测 360MB，回测结果逐位不变。Windows 的 pyarrow 不带 jemalloc，保持默认。已显式设置时尊重之。
if sys.platform != "win32" and "pyarrow" not in sys.modules:
    os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "jemalloc")
