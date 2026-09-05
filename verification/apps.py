import os
import sys
import threading

from django.apps import AppConfig

# Management commands that don't serve live requests — warmup is pointless and slow.
_NO_WARMUP_CMDS = frozenset({
    'test', 'check', 'collectstatic', 'makemigrations', 'migrate',
    'showmigrations', 'sqlmigrate', 'dbshell', 'shell', 'createsuperuser',
    'generate_key', 'help', 'check_system', 'inspectdb', 'diffsettings',
})


def _warmup_is_suppressed() -> bool:
    """Return True when FaceNet warmup should be skipped."""
    # Explicit opt-out via environment flag (e.g. set by smoke-test script)
    _truthy = ('1', 'true', 'yes', 'on')
    if os.environ.get('FANS_SKIP_FACENET_WARMUP', '').strip().lower() in _truthy:
        return True
    if os.environ.get('FANS_SKIP_MODEL_WARMUP', '').strip().lower() in _truthy:
        return True
    # Running a management command that doesn't serve HTTP requests
    if any(arg in _NO_WARMUP_CMDS for arg in sys.argv[1:]):
        return True
    # Running under pytest
    if 'pytest' in sys.modules:
        return True
    return False


class VerificationConfig(AppConfig):
    name = 'verification'
    verbose_name = 'Verification'
    default = True

    def ready(self):
        """
        Warm up FaceNet + MTCNN in a background daemon thread on Django startup.

        Why: keras-facenet loads ~90 MB of weights on first use. Without warmup,
        the very first call to verify_submit (which computes an embedding) will
        block for 5-15 seconds while TensorFlow builds the graph. Subsequent calls
        are fast because the model is already in memory.

        The background thread is a daemon so it never prevents shutdown. If the
        warmup is still running when the first real verification arrives, get_facenet_model()
        will block briefly until the global _facenet_model is set, which is fine.

        Warmup is skipped entirely during test runs, management commands that do
        not serve HTTP traffic, and when FANS_SKIP_FACENET_WARMUP=1 is set.
        """
        if _warmup_is_suppressed():
            return

        def _warmup():
            import logging as _log
            _wlog = _log.getLogger('verification')
            try:
                from verification.face_utils import (
                    get_facenet_model, get_model_load_error,
                    FaceNetUnavailableError, _get_mtcnn,
                )

                _wlog.info('Warming up FaceNet model (background)...')
                try:
                    get_facenet_model()
                except FaceNetUnavailableError:
                    # Expected, handled condition — not a warmup bug. Biometric
                    # registration/verification fail closed until this is
                    # resolved (see get_facenet_model docstring); they do not
                    # silently fall back to mock/random embeddings.
                    _wlog.warning(
                        'FaceNet model unavailable — biometric verification will '
                        'fail closed until this is resolved. Detail: %s',
                        get_model_load_error(),
                    )
                    return

                _get_mtcnn()
                _wlog.info('FaceNet + MTCNN ready. First verification will be fast.')
            except Exception as exc:
                _wlog.exception('Model warmup failed: %s', exc)

        t = threading.Thread(target=_warmup, daemon=True, name='fans-facenet-warmup')
        t.start()
