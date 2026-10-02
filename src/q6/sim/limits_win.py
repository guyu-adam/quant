"""Windows 资源硬限（P3-04）：Job Object 的 `JOB_OBJECT_LIMIT_PROCESS_MEMORY`。

supervisor 启动后第一件事是 `confine_self(512)`：把**自己**放进一个新的 Job Object，限制 = 每个进程的
已提交内存（private commit）≤512MB，外加 KILL_ON_JOB_CLOSE。之后它拉起的 worker / 面板从出生起就在这个
Job 里（Windows 子进程默认继承父进程的 Job），没有"先跑起来再挂进去"的竞态窗口：
- 任何一个进程想提交超过 512MB，分配直接失败（Python 里是 MemoryError，worker 以退出码 76 退出；
  原生代码分配失败则进程崩溃）——由操作系统强制，不是代码里"尽量"。supervisor 记录并重启。
- supervisor 自己死掉时 Job 的最后一个句柄关闭，Job 内所有进程被系统一起结束，不会留下孤儿 worker
  继续写库（随后计划任务重新拉起 supervisor，worker 从检查点续跑）。

已提交内存 ≥ 工作集中的私有部分，所以这个限制比"RSS ≤512MB"更严。核查用 `job_peak_mb()`
（Job 记录的单进程峰值）和 psutil 的 peak_wset，两者都进 supervisor.json。

非 Windows（Mac 开发机）上这些函数什么都不做、返回 None——Mac 不是部署目标，内存靠 rss_guard 实测。
"""

from __future__ import annotations

import sys

JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9

_job = None  # 句柄必须一直持有：关掉就触发 KILL_ON_JOB_CLOSE


def _structs():
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):  # noqa: N801 - Win32 名称
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class EXTENDED(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BASIC),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    return ctypes, wintypes, EXTENDED


def _k32():
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    k.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    k.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                            ctypes.c_void_p)
    k.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    k.GetCurrentProcess.restype = wintypes.HANDLE
    return k


def confine_self(limit_mb: int = 512) -> int | None:
    """把当前进程放进新 Job（每进程提交内存 ≤ limit_mb，KILL_ON_JOB_CLOSE）。
    返回句柄值；非 Windows 返回 None。"""
    global _job
    if sys.platform != "win32":
        return None
    if _job is not None:
        return _job
    ctypes, _, EXTENDED = _structs()
    k = _k32()
    h = k.CreateJobObjectW(None, None)
    if not h:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW 失败")
    info = EXTENDED()
    flags = JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    info.BasicLimitInformation.LimitFlags = flags
    info.ProcessMemoryLimit = int(limit_mb) * 1024 * 1024
    if not k.SetInformationJobObject(h, JobObjectExtendedLimitInformation, ctypes.byref(info),
                                     ctypes.sizeof(info)):
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject 失败")
    if not k.AssignProcessToJobObject(h, k.GetCurrentProcess()):
        raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject 失败（父 Job 不允许嵌套？）")
    _job = h
    return h


def job_limits() -> dict | None:
    """当前 Job 的限制与峰值（MB）。没有 confine 过 / 非 Windows 返回 None。"""
    if sys.platform != "win32" or _job is None:
        return None
    ctypes, _, EXTENDED = _structs()
    k = _k32()
    info = EXTENDED()
    if not k.QueryInformationJobObject(_job, JobObjectExtendedLimitInformation, ctypes.byref(info),
                                       ctypes.sizeof(info), None):
        raise OSError(ctypes.get_last_error(), "QueryInformationJobObject 失败")
    mb = 1024 * 1024
    return dict(process_limit_mb=info.ProcessMemoryLimit / mb,
                peak_process_mb=round(info.PeakProcessMemoryUsed / mb, 1),
                peak_job_mb=round(info.PeakJobMemoryUsed / mb, 1),
                flags=hex(info.BasicLimitInformation.LimitFlags))
