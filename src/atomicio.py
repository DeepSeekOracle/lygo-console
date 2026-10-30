"""Atomic file writes that survive a stick.

Why this module exists: a fixed temp name (foo.json.tmp) collides when two request threads
write the same file at once, and on removable media antivirus / the search indexer can hold
the target open for a moment. os.replace then fails with PermissionError [WinError 32] and
the exception lands inside a request handler. So: one unique temp name per write, fsync, and
a short retry loop on the swap, and a last-resort in-place write that opens the target with
read+write+delete sharing, so a reader pinning it (Windows gives us no FILE_SHARE_DELETE from
plain open()) is never the one that gets denied. Reads go through read_text() for the same
reason: the OS denies an open() for a moment while a writer swaps the file, and a console
refresh must not answer 500.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

RETRY_DELAYS = (0.0, 0.05, 0.15, 0.4)

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(target: Path) -> threading.Lock:
    """One lock per target path: two writes to the same file must not race the swap."""
    key = str(target).lower() if os.name == "nt" else str(target)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _LOCKS[key] = threading.Lock()
        return lock


def _transient(exc: OSError) -> bool:
    """WinError 32/33 (in use / lock violation) are worth waiting out; nothing else is."""
    return isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in (32, 33)


def _write_in_place(target: Path, text: str, encoding: str) -> None:
    """Non-atomic rewrite that still lets concurrent readers open the target.

    CPython's open() requests FILE_SHARE_READ|FILE_SHARE_WRITE but never delete, which is why a
    reader pins the file against os.replace(). Truncating it under the same sharing flags - plus
    delete - keeps reads working for the duration of the write.
    """
    data = text.encode(encoding)
    if os.name != "nt":
        target.write_text(text, encoding=encoding, newline="\n")
        return
    import ctypes
    from ctypes import wintypes

    GENERIC_WRITE = 0x40000000
    CREATE_ALWAYS = 2
    FILE_ATTRIBUTE_NORMAL = 0x80
    SHARE_READ_WRITE_DELETE = 0x1 | 0x2 | 0x4
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_void_p]
    handle = k32.CreateFileW(str(target), GENERIC_WRITE, SHARE_READ_WRITE_DELETE, None,
                             CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, None)
    if not handle or handle == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        buf = ctypes.create_string_buffer(data, len(data) or 1)
        written = wintypes.DWORD(0)
        if not k32.WriteFile(ctypes.c_void_p(handle), buf, len(data), ctypes.byref(written), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if written.value != len(data):
            raise OSError(f"short write to {target}: {written.value}/{len(data)}")
    finally:
        k32.CloseHandle(ctypes.c_void_p(handle))


READ_RETRY_DELAYS = (0.0, 0.02, 0.05, 0.15, 0.3)


def read_text(path: Any, *, encoding: str = "utf-8", errors: str | None = None) -> str:
    """Read text, waiting out a transient lock another handle holds on the file.

    Windows denies an open() with ERROR_ACCESS_DENIED for the instant a concurrent writer
    swaps or truncates a file; in a request handler that lands on the user as a 500. Retry
    the transient case (~0.5 s worst case), never a missing file or a permanent denial.
    """
    target = Path(path)
    last: OSError | None = None
    for delay in READ_RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            return target.read_text(encoding=encoding, errors=errors)
        except OSError as exc:
            if isinstance(exc, FileNotFoundError) or not _transient(exc):
                raise
            last = exc
    if last is not None:
        raise last
    raise OSError(f"could not read {target}")  # pragma: no cover - loop always sets last


def atomic_write_text(path: Any, text: str, *, encoding: str = "utf-8") -> None:
    """Write text to path atomically, safe for concurrent writers and locked targets."""
    target = Path(path)
    with _lock_for(target):
        _write_locked(target, text, encoding)


def _write_locked(target: Path, text: str, encoding: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as fh:
            fh.write(text)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass  # fsync is a durability nicety, not a correctness requirement
        last: OSError | None = None
        for delay in RETRY_DELAYS:
            if delay:
                time.sleep(delay)
            try:
                os.replace(tmp, target)
                return
            except OSError as exc:
                if not _transient(exc):
                    raise
                last = exc
        # Readers on Windows pin the file without sharing delete, so the swap can never win.
        # A torn state file is recoverable (readers treat bad JSON as empty); a lost write is not.
        try:
            _write_in_place(target, text, encoding)
            return
        except OSError:
            pass
        if last is not None:
            raise last
        raise OSError(f"could not replace {target}")
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
