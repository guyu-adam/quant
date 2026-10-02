"""q6：v6 量化研究与回测框架。"""

import os
import sys

# Arrow 的内存池只在 pyarrow 首次导入时按这个环境变量选定，之后 set_memory_pool 换池不完全生效（实测）。
# 默认的 mimalloc 不把读完的 parquet 缓冲区还给系统：P2-07 全区间回测逐年读快照，macOS 峰值 RSS 945MB，
# jemalloc 实测 360MB，回测结果逐位不变。Windows 的 pyarrow 不带 jemalloc：默认 mimalloc 实测 764MB，
# system（HeapFree 会把大块还给系统）232MB，结果同样逐位不变。已显式设置时尊重之。
if "pyarrow" not in sys.modules:
    os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system" if sys.platform == "win32" else "jemalloc")

# macOS libmalloc 的 medium 区（约 32KB–8MB）释放后不还页：全区间逐年读快照，vmmap 显示 medium 区常驻 245MB、
# 实际在用 20MB（碎片 92%）。MallocMediumZone=0 让这些块走 large 路径（释放即归还）：csmf 向量回测峰值
# 808MB → 404MB，样板低波 461MB → 335MB，成交逐笔不变。libmalloc 只在进程启动时读这个变量，当前进程来不及，
# 这里设置是为了让 spawn 出来的子进程（敏感性分析的进程池等）继承；入口进程由 verify.sh / 启动命令导出。
if sys.platform == "darwin":
    os.environ.setdefault("MallocMediumZone", "0")
