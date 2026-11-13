"""Cross-process locks for one schematic target, released by the operating system."""
import os
from contextlib import contextmanager
from pathlib import Path
from .core import Refused, digest


@contextmanager
def target_lock(target):
    if not target or not target.get('project_uuid') or not target.get('document_uuid'):
        raise Refused('target lock requires exact project and document')
    directory = Path(os.environ.get('ESO_LOCK_DIR', Path.home() / '.local/share/eso/locks'))
    directory.mkdir(parents=True, exist_ok=True)
    key = digest([target['project_uuid'], target['document_uuid']])
    with (directory / (key + '.lock')).open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise Refused('target is busy in another ESO operation') from error
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise Refused('target is busy in another ESO operation') from error
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
