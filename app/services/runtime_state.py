"""Atomic, process-shared monitoring snapshots; no schema migration required."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import threading
from collections import defaultdict

from flask import current_app

_locks = defaultdict(threading.RLock)


def state_path(name):
    root = Path(current_app.config.get('DATA_ROOT') or os.getenv('RAMS_DATA_ROOT') or current_app.instance_path) / 'monitoring'
    root.mkdir(parents=True, exist_ok=True)
    return root / (name + '.json')


@contextmanager
def locked_state(name):
    """Hold an OS lock through a read/modify/write, including across WSGI workers."""
    path = state_path(name)
    with _locks[str(path)], open(path.with_suffix('.lock'), 'a+b') as lockfile:
        if os.name == 'nt':
            import msvcrt
            lockfile.seek(0)
            if not lockfile.read(1):
                lockfile.write(b'0'); lockfile.flush()
            lockfile.seek(0)
            msvcrt.locking(lockfile.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX)
        try:
            data = read_state(name)
            yield data
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=name, suffix='.tmp')
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as file:
                    json.dump(data, file, allow_nan=False)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        finally:
            if os.name == 'nt':
                lockfile.seek(0); msvcrt.locking(lockfile.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lockfile.fileno(), fcntl.LOCK_UN)


def read_state(name):
    try:
        data = json.loads(state_path(name).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}
