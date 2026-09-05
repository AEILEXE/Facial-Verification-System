"""
Management command: check_system

Verifies that the FANS-C environment is correctly configured and all critical
components are available before the server is started.

Usage:
    python manage.py check_system
    python manage.py check_system --quiet   # exit-code only, no output on success
"""
import sys
import os
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Run a health check on the FANS-C environment and dependencies'

    def add_arguments(self, parser):
        parser.add_argument(
            '--quiet',
            action='store_true',
            help='Suppress output when all checks pass; still prints failures.',
        )

    def handle(self, *args, **options):
        quiet = options['quiet']
        errors = []
        warnings = []

        def ok(msg):
            if not quiet:
                self.stdout.write(self.style.SUCCESS(f'  [OK]  {msg}'))

        def warn(msg):
            warnings.append(msg)
            self.stdout.write(self.style.WARNING(f'  [WARN] {msg}'))

        def fail(msg):
            errors.append(msg)
            self.stdout.write(self.style.ERROR(f'  [FAIL] {msg}'))

        if not quiet:
            self.stdout.write('')
            self.stdout.write('  FANS-C System Health Check')
            self.stdout.write('  ' + '-' * 40)

        # ── 1. Python version ──────────────────────────────────────────────
        ver = sys.version_info
        if ver.major == 3 and ver.minor == 11:
            ok(f'Python {ver.major}.{ver.minor}.{ver.micro}')
        elif ver.major == 3 and ver.minor < 11:
            fail(
                f'Python {ver.major}.{ver.minor}.{ver.micro} is too old. '
                'Python 3.11 is required.'
            )
        else:
            fail(
                f'Python {ver.major}.{ver.minor}.{ver.micro} is not supported. '
                'tensorflow-cpu 2.13.x requires Python 3.11 exactly. '
                'Python 3.12+ will fail with a DLL load error.'
            )

        # ── 2. python-dotenv ───────────────────────────────────────────────
        try:
            import dotenv  # noqa: F401
            ok('python-dotenv is installed')
        except ImportError:
            fail('python-dotenv is not installed. Run: pip install python-dotenv')

        # ── 3. cryptography (Fernet) ───────────────────────────────────────
        try:
            from cryptography.fernet import Fernet  # noqa: F401
            ok('cryptography (Fernet) is installed')
        except ImportError:
            fail('cryptography is not installed. Run: pip install cryptography')

        # ── 4. EMBEDDING_ENCRYPTION_KEY ────────────────────────────────────
        from django.conf import settings
        key = getattr(settings, 'EMBEDDING_ENCRYPTION_KEY', '')
        if not key:
            fail(
                'EMBEDDING_ENCRYPTION_KEY is not set in .env. '
                'Run: python manage.py generate_key  and paste the output into .env.'
            )
        else:
            try:
                from cryptography.fernet import Fernet
                Fernet(key.encode() if isinstance(key, str) else key)
                ok('EMBEDDING_ENCRYPTION_KEY is valid')
            except Exception as e:
                fail(f'EMBEDDING_ENCRYPTION_KEY is set but invalid: {e}')

        # ── 5. SECRET_KEY ──────────────────────────────────────────────────
        secret = getattr(settings, 'SECRET_KEY', '')
        if not secret or 'insecure' in secret or 'your-secret-key' in secret:
            warn(
                'SECRET_KEY looks like a placeholder. '
                'Set a strong random value in .env before deploying.'
            )
        else:
            ok('SECRET_KEY is set')

        # ── 6. TensorFlow import ───────────────────────────────────────────
        try:
            import tensorflow as tf  # noqa: F401
            ok(f'TensorFlow {tf.__version__} imported successfully')
        except ImportError:
            fail(
                'tensorflow-cpu is not installed. '
                'Run: pip install tensorflow-cpu==2.13.1'
            )
        except Exception as e:
            fail(f'TensorFlow import failed: {e}')

        # ── 7. keras-facenet / FaceNet model ──────────────────────────────
        try:
            from keras_facenet import FaceNet  # noqa: F401
            ok('keras-facenet is installed')
        except ImportError:
            fail(
                'keras-facenet is not installed. '
                'Run: pip install keras-facenet==0.3.2'
            )
        except Exception as e:
            fail(f'keras-facenet import failed: {e}')

        # ── 8. OpenCV ──────────────────────────────────────────────────────
        try:
            import cv2  # noqa: F401
            ok(f'OpenCV {cv2.__version__} is installed')
        except ImportError:
            fail('opencv-python is not installed. Run: pip install opencv-python')

        # ── 9. scipy ───────────────────────────────────────────────────────
        try:
            import scipy  # noqa: F401
            ok(f'scipy {scipy.__version__} is installed')
        except ImportError:
            fail('scipy is not installed. Run: pip install scipy==1.11.4')

        # ── 10. numpy version ──────────────────────────────────────────────
        try:
            import numpy as np
            ver_parts = [int(x) for x in np.__version__.split('.')[:2]]
            if ver_parts[0] == 1 and ver_parts[1] == 24:
                ok(f'numpy {np.__version__} (compatible)')
            elif ver_parts[0] >= 2:
                warn(
                    f'numpy {np.__version__} detected. TensorFlow 2.13 requires numpy < 2.0. '
                    'Downgrade with: pip install numpy==1.24.3'
                )
            else:
                ok(f'numpy {np.__version__}')
        except ImportError:
            fail('numpy is not installed.')

        # ── 11. Database connection ────────────────────────────────────────
        try:
            from django.db import connection
            connection.ensure_connection()
            ok('Database connection successful')
        except Exception as e:
            fail(f'Database connection failed: {e}')

        # ── 12. Pending migrations ─────────────────────────────────────────
        try:
            from django.db.migrations.executor import MigrationExecutor
            executor = MigrationExecutor(connection)
            plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
            if plan:
                unapplied = [f'{app}.{name}' for (app, name), _ in plan]
                warn(
                    f'Unapplied migrations detected: {", ".join(unapplied)}. '
                    'Run: python manage.py migrate'
                )
            else:
                ok('All migrations applied')
        except Exception as e:
            warn(f'Could not check migrations: {e}')

        # ── 13. .env file present ──────────────────────────────────────────
        from pathlib import Path
        base_dir = Path(settings.BASE_DIR)
        env_path = base_dir / '.env'
        if env_path.exists():
            ok('.env file present')
        else:
            warn(
                '.env file not found. Copy .env.example to .env and configure it. '
                'The system will use insecure defaults.'
            )

        # ── 14. Liveness mode ──────────────────────────────────────────────
        liveness_required = getattr(settings, 'LIVENESS_REQUIRED', True)
        demo_mode = getattr(settings, 'DEMO_MODE', True)
        if liveness_required:
            ok('Liveness: STRICT mode (LIVENESS_REQUIRED=True) — liveness failures block verification')
        else:
            ok(
                'Liveness: ASSISTED ROLLOUT mode (LIVENESS_REQUIRED=False) — '
                'liveness is recorded but does not block verification'
            )
        if demo_mode:
            ok('Verification: ASSISTED ROLLOUT mode (DEMO_MODE=True, threshold 0.60)')
        else:
            ok('Verification: STRICT mode (DEMO_MODE=False, threshold 0.75)')

        # ── Summary ────────────────────────────────────────────────────────
        if not quiet:
            self.stdout.write('')

        if errors:
            self.stdout.write(self.style.ERROR(
                f'  {len(errors)} error(s) found. Fix the issues above before starting the server.'
            ))
            self.stdout.write('')
            sys.exit(1)
        elif warnings:
            if not quiet:
                self.stdout.write(self.style.WARNING(
                    f'  All critical checks passed with {len(warnings)} warning(s).'
                ))
                self.stdout.write('')
        else:
            if not quiet:
                self.stdout.write(self.style.SUCCESS('  All checks passed.'))
                self.stdout.write('')
