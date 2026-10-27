"""Windows：只读扫描 ai-yxt.exe 私有内存，提取 access token。"""

from __future__ import annotations

import csv
import ctypes
import ctypes.wintypes as wt
import subprocess
import time
from dataclasses import dataclass

from config import CLIENT_EXE, CREATE_NO_WINDOW, DEFAULT_SCAN_TIMEOUT
from token_model import (TokenCandidate, TokenScanError, extract_from_bytes)


@dataclass
class ScanStats:
    processes: int = 0
    regions: int = 0
    bytes_read: int = 0
    candidates: int = 0
    elapsed: float = 0.0
    denied_processes: int = 0
    partial: bool = False


class _MBI64(ctypes.Structure):
    _fields_ = [
        ('BaseAddress', ctypes.c_ulonglong),
        ('AllocationBase', ctypes.c_ulonglong),
        ('AllocationProtect', wt.DWORD),
        ('__alignment1', wt.DWORD),
        ('RegionSize', ctypes.c_ulonglong),
        ('State', wt.DWORD),
        ('Protect', wt.DWORD),
        ('Type', wt.DWORD),
        ('__alignment2', wt.DWORD),
    ]


class _MBI32(ctypes.Structure):
    _fields_ = [
        ('BaseAddress', wt.DWORD),
        ('AllocationBase', wt.DWORD),
        ('AllocationProtect', wt.DWORD),
        ('RegionSize', wt.DWORD),
        ('State', wt.DWORD),
        ('Protect', wt.DWORD),
        ('Type', wt.DWORD),
    ]


PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
READABLE_PROTECT = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}

_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
_kernel32.OpenProcess.restype = wt.HANDLE
_kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_kernel32.CloseHandle.argtypes = [wt.HANDLE]
_kernel32.ReadProcessMemory.restype = wt.BOOL
_kernel32.ReadProcessMemory.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
]
_kernel32.VirtualQueryEx.restype = ctypes.c_size_t
_kernel32.VirtualQueryEx.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
]

_MBI = _MBI64 if ctypes.sizeof(ctypes.c_void_p) == 8 else _MBI32


def list_client_pids(image: str = CLIENT_EXE) -> list[int]:
    try:
        output = subprocess.check_output(
            ['tasklist', '/FI', 'IMAGENAME eq %s' % image, '/FO', 'CSV', '/NH'],
            text=True, errors='replace', timeout=10,
            creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as error:
        raise TokenScanError('无法枚举客户端进程：%s' % error) from error

    pids = []
    for row in csv.reader(output.splitlines()):
        if len(row) >= 2 and row[0].strip().lower() == image.lower():
            try:
                pids.append(int(row[1]))
            except ValueError:
                continue
    return pids


def _iter_regions(handle):
    address = 0
    mbi = _MBI()
    while _kernel32.VirtualQueryEx(
            handle, ctypes.c_void_p(address), ctypes.byref(mbi),
            ctypes.sizeof(mbi)):
        base = mbi.BaseAddress
        size = mbi.RegionSize
        if not size:
            break
        yield base, size, mbi.State, mbi.Protect, mbi.Type
        address = base + size


def _is_private_readable(state: int, protect: int, region_type: int) -> bool:
    if state != MEM_COMMIT or region_type != MEM_PRIVATE:
        return False
    protect = protect & 0xFF
    if protect in (PAGE_NOACCESS, PAGE_GUARD):
        return False
    return protect in READABLE_PROTECT


def _scan_pid(pid: int, deadline: float, chunk_size: int = 2 * 1024 * 1024,
              overlap: int = 256, stats: ScanStats | None = None
              ) -> list[TokenCandidate]:
    handle = _kernel32.OpenProcess(
        PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        if stats is not None:
            stats.denied_processes += 1
        return []

    found: dict[str, TokenCandidate] = {}
    try:
        for base, size, state, protect, region_type in _iter_regions(handle):
            if time.monotonic() > deadline:
                break
            if size < 64 or not _is_private_readable(state, protect, region_type):
                continue
            if stats is not None:
                stats.regions += 1
            offset = 0
            while offset < size:
                if time.monotonic() > deadline:
                    break
                length = min(chunk_size, size - offset)
                read_size = min(length + overlap, size - offset)
                buffer = ctypes.create_string_buffer(read_size)
                bytes_read = ctypes.c_size_t(0)
                ok = _kernel32.ReadProcessMemory(
                    handle, ctypes.c_void_p(base + offset), buffer,
                    read_size, ctypes.byref(bytes_read))
                if ok and bytes_read.value:
                    data = buffer.raw[:bytes_read.value]
                    if stats is not None:
                        stats.bytes_read += len(data)
                    for candidate in extract_from_bytes(
                            data, pid, base + offset):
                        found.setdefault(candidate.value, candidate)
                offset += length
    finally:
        _kernel32.CloseHandle(handle)
    return list(found.values())


def scan_tokens(image: str = CLIENT_EXE,
                timeout: float = DEFAULT_SCAN_TIMEOUT,
                stats: ScanStats | None = None,
                stop_after_first: bool = True) -> list[TokenCandidate]:
    """扫描客户端内存。

    默认找到第一个进程里的候选 token 就停（主进程通常 0.1 秒内就有），
    认证失败时调用方可以再用 ``stop_after_first=False`` 做完整扫描。
    """

    started = time.monotonic()
    stats = stats or ScanStats()
    pids = list_client_pids(image)
    stats.processes = len(pids)
    if not pids:
        raise TokenScanError(
            '没有找到运行中的爱问云客户端（%s）。请先打开并登录爱问云。' % image)

    deadline = started + max(1.0, timeout)
    all_candidates: dict[str, TokenCandidate] = {}
    for pid in pids:
        found_here = _scan_pid(pid, deadline, stats=stats)
        for candidate in found_here:
            all_candidates.setdefault(candidate.value, candidate)
        if found_here and stop_after_first and all_candidates:
            stats.partial = True
            break
        if time.monotonic() > deadline and all_candidates:
            stats.partial = True
            break

    stats.elapsed = time.monotonic() - started
    stats.candidates = len(all_candidates)
    if not all_candidates:
        hint = ''
        if stats.denied_processes:
            hint = '（部分进程拒绝访问，请用同一用户运行，或关闭杀毒软件的内存防护）'
        raise TokenScanError(
            '没有在爱问云进程里找到登录态。请确认客户端已登录；'
            '如果刚登录，请等页面加载完成后再试。' + hint)

    return sorted(
        all_candidates.values(), key=lambda item: item.timestamp, reverse=True)[:20]
