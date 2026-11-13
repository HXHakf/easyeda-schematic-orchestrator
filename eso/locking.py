"""Cross-process locks for one schematic target, released by the operating system."""
import os
from contextlib import contextmanager
from pathlib import Path
from .core import Refused, digest


@contextmanager
def file_lock(path, *, blocking=False, busy_message='file is busy in another ESO operation'):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            try:
                mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
                msvcrt.locking(handle.fileno(), mode, 1)
            except OSError as error:
                raise Refused(busy_message) from error
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                mode = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
                fcntl.flock(handle, mode)
            except OSError as error:
                raise Refused(busy_message) from error
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def target_lock(target):
    if not target or not target.get('project_uuid') or not target.get('document_uuid'):
        raise Refused('target lock requires exact project and document')
    directory = Path(os.environ.get('ESO_LOCK_DIR', Path.home() / '.local/share/eso/locks'))
    key = digest([target['project_uuid'], target['document_uuid']])
    with file_lock(directory / (key + '.lock'), busy_message='target is busy in another ESO operation'):
        yield
