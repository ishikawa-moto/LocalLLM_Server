"""Reentrant, cross-process guard for knowledge transactions (no stale-PID deletion)."""
import contextlib
import functools
import os
import threading
import time

_registry = {}
_registry_lock = threading.Lock()


@contextlib.contextmanager
def guard(brain):
    key = str(brain.runtime.resolve()).casefold()
    with _registry_lock:
        state = _registry.setdefault(key, (threading.RLock(), threading.local()))
    mutex, local = state
    with mutex:
        if getattr(local, 'depth', 0):
            local.depth += 1
            try:
                yield
            finally:
                local.depth -= 1
            return
        path = brain.runtime / 'knowledge-transaction.lock'
        with path.open('a+b') as handle:
            if path.stat().st_size == 0:
                handle.write(b'0'); handle.flush()
            deadline = time.monotonic() + 30
            while True:
                try:
                    handle.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Knowledge transaction busy')
                    time.sleep(.05)
            local.depth = 1
            try:
                # Before any guarded read/write, finish a prior interrupted transaction.
                if (brain.runtime / 'apply-transactions').exists():
                    from approved_apply import ApplyEngine
                    ApplyEngine(brain).recover()
                yield
            finally:
                local.depth = 0
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


def guarded(function):
    @functools.wraps(function)
    def call(self, *args, **kwargs):
        brain = getattr(self, 'brain', self)
        with guard(brain):
            return function(self, *args, **kwargs)
    return call
