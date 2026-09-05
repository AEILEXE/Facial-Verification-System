"""
Management command: create_admin

Optional bootstrap helper for creating the initial FANS-C admin account.

RECOMMENDED METHOD FOR DEPLOYMENT:
    Use Django's built-in interactive command instead:
        python manage.py createsuperuser

    That command prompts for all values interactively and never requires
    credentials to be passed on the command line or stored in scripts.

This command exists as an alternative for headless / scripted environments
(e.g. a CI pipeline or a first-run setup script).  It reads credentials
from explicit arguments or from environment variables.  No credentials are
hard-coded as defaults -- you must supply them explicitly.

Usage examples:

    # Interactive (prompts for password if not supplied):
    python manage.py create_admin --username barangay_admin

    # Fully scripted (via environment variables):
    export BOOTSTRAP_ADMIN_USERNAME=barangay_admin
    export BOOTSTRAP_ADMIN_PASSWORD=<strong-password>
    python manage.py create_admin

    # All values on the command line (avoid in scripts committed to VCS):
    python manage.py create_admin --username barangay_admin --password <pwd>

Security note:
    Never commit credentials in shell scripts or Makefiles.  Use environment
    variables or the interactive prompt.  For production deployments, prefer
    `python manage.py createsuperuser` which enforces Django's full password
    validation suite interactively.
"""

import os
import getpass

from django.core.management.base import BaseCommand, CommandError

from accounts.models import CustomUser


class Command(BaseCommand):
    help = (
        'Create the initial FANS-C admin user. '
        'No credentials are hard-coded -- supply them via arguments, '
        'BOOTSTRAP_ADMIN_USERNAME / BOOTSTRAP_ADMIN_PASSWORD env vars, '
        'or an interactive prompt. '
        'For interactive setup, prefer: python manage.py createsuperuser'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            type=str,
            default=os.getenv('BOOTSTRAP_ADMIN_USERNAME', '').strip(),
            help=(
                'Admin username.  If not supplied, falls back to the '
                'BOOTSTRAP_ADMIN_USERNAME environment variable.'
            ),
        )
        parser.add_argument(
            '--password',
            type=str,
            default='',
            help=(
                'Admin password.  If not supplied, falls back to the '
                'BOOTSTRAP_ADMIN_PASSWORD environment variable, then '
                'prompts interactively.'
            ),
        )
        parser.add_argument(
            '--email',
            type=str,
            default=os.getenv('BOOTSTRAP_ADMIN_EMAIL', '').strip(),
            help='Admin email address.',
        )
        parser.add_argument('--first-name', type=str, default='')
        parser.add_argument('--last-name',  type=str, default='')
        parser.add_argument(
            '--role', type=str,
            default=CustomUser.ROLE_ADMIN,
            choices=[
                CustomUser.ROLE_PRESIDENT,
                CustomUser.ROLE_ADMIN,
                CustomUser.ROLE_IT,
                CustomUser.ROLE_STAFF,
            ],
            help='Role: admin (default), president, it, staff',
        )

    def handle(self, *args, **options):
        # ── Resolve username ─────────────────────────────────────────────────
        username = options['username'].strip()
        if not username:
            raise CommandError(
                'Username is required.\n'
                '  Supply --username <name>  or  set BOOTSTRAP_ADMIN_USERNAME.\n'
                '  Tip: use  python manage.py createsuperuser  for a fully '
                'interactive setup.'
            )

        # ── Resolve password ─────────────────────────────────────────────────
        # Priority: --password flag > BOOTSTRAP_ADMIN_PASSWORD env var > prompt
        password = (
            options['password'].strip()
            or os.getenv('BOOTSTRAP_ADMIN_PASSWORD', '').strip()
        )
        if not password:
            try:
                password = getpass.getpass(f'Password for "{username}": ')
                if not password:
                    raise CommandError('Password cannot be empty.')
                confirm = getpass.getpass('Confirm password: ')
                if password != confirm:
                    raise CommandError('Passwords do not match.')
            except (EOFError, KeyboardInterrupt):
                raise CommandError(
                    'No password supplied and interactive prompt is not '
                    'available.  Supply --password or set '
                    'BOOTSTRAP_ADMIN_PASSWORD.'
                )

        if not password:
            raise CommandError('Password cannot be empty.')

        # ── Guard: skip if user already exists ───────────────────────────────
        if CustomUser.objects.filter(username=username).exists():
            self.stdout.write(self.style.WARNING(
                f'User "{username}" already exists.  Skipping.'
            ))
            return

        # ── Create the admin account ─────────────────────────────────────────
        # Use create_user (not create_superuser) so that is_superuser and
        # is_staff are NOT set automatically.  App-level admin access is
        # granted exclusively via the role field (ROLE_PRESIDENT / ROLE_ADMIN / ROLE_IT).
        # Django's is_superuser flag is irrelevant for app permissions and
        # should not be given out by default to avoid accidental privilege leaks.
        email = options['email'] or f'{username}@fans.local'
        role = options['role']
        CustomUser.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=options['first_name'],
            last_name=options['last_name'],
            role=role,
            employee_id='ADM-001',
            is_superuser=False,
            is_staff=False,
        )
        self.stdout.write(self.style.SUCCESS(
            f'User "{username}" created with role "{role}".\n'
            'Keep the password secure.  Change it immediately if it was '
            'shared during setup or passed on the command line.'
        ))
