"""
Stream safety for PyInstaller windowed (no-console) mode.

In --noconsole PyInstaller builds sys.stdout and sys.stderr are None.
Third-party libraries (TensorFlow, Keras, MTCNN) call print() or
sys.stderr.write() during import, raising:
    AttributeError: 'NoneType' object has no attribute 'write'

Import and call ensure_console_streams() at the very start of any entry
point that may run in windowed mode.  Calling it in normal dev mode is
a safe no-op (streams are already set).
"""
import os
import sys


def ensure_console_streams():
    """
    Redirect sys.stdout/sys.stderr to log files if they are None.

    Idempotent and safe to call in any environment.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return

    from pathlib import Path

    if getattr(sys, 'frozen', False):
        _base = Path(sys.executable).parent
    else:
        _base = Path(os.environ.get('FANS_RUNTIME_DIR', os.getcwd()))

    _logs = Path(os.environ.get('FANS_RUNTIME_DIR', str(_base))) / 'logs'

    def _open_fallback(name):
        try:
            _logs.mkdir(parents=True, exist_ok=True)
            return open(str(_logs / name), 'a', encoding='utf-8', buffering=1)
        except Exception:
            return open(os.devnull, 'w', encoding='utf-8')

    if sys.stdout is None:
        sys.stdout = _open_fallback('stdout.log')
    if sys.stderr is None:
        sys.stderr = _open_fallback('stderr.log')
