"""
Management command: normalize_roles

Finds and optionally fixes accounts whose Django-level flags
(is_superuser, is_staff) are inconsistent with their app-level role.

Background
----------
Before the is_admin_it bug-fix (2026-04-16), the is_admin_it property
included ``or self.is_superuser``, which meant any account created via
``manage.py createsuperuser`` or ``create_admin`` (which used
create_superuser) would pass the IT check regardless of role.

This created the following bad combinations in the database:

    role='president' + is_superuser=True  -> President saw technical IT UI
    role='admin'     + is_superuser=True  -> Admin saw technical IT UI
    role='staff'     + is_superuser=True  -> Staff saw technical IT UI

This command identifies and corrects those combinations.

Rules applied
-------------
* Accounts with role in (president, admin, staff) should have:
    is_superuser=False
    is_staff=False
  These roles have no business having Django admin panel access.

* Accounts with role=it are left untouched:
  they may legitimately need is_superuser=True for Django /admin/.

Usage
-----
    # Dry run (default) — shows what would change, writes nothing:
    python manage.py normalize_roles

    # Apply changes:
    python manage.py normalize_roles --apply

    # Verbose output:
    python manage.py normalize_roles --apply --verbosity 2
"""

from django.core.management.base import BaseCommand
from accounts.models import CustomUser


# Roles that must NEVER carry is_superuser or is_staff privileges.
NON_PRIVILEGED_ROLES = (
    CustomUser.ROLE_PRESIDENT,
    CustomUser.ROLE_ADMIN,
    CustomUser.ROLE_STAFF,
    # Legacy values — caught here so any surviving pre-migration rows are cleaned up.
    CustomUser.ROLE_HEAD_BRGY,
    CustomUser.ROLE_ADMIN_IT,
)


class Command(BaseCommand):
    help = (
        'Find (and optionally fix) accounts whose is_superuser / is_staff '
        'flags are inconsistent with their app-level role. '
        'Runs as a dry-run by default; pass --apply to save changes.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            default=False,
            help='Actually save changes. Without this flag the command is read-only.',
        )

    def handle(self, *args, **options):
        apply = options['apply']
        verbosity = options['verbosity']

        # Find accounts that need fixing.
        bad_accounts = CustomUser.objects.filter(
            role__in=NON_PRIVILEGED_ROLES,
        ).filter(
            # At least one of these should not be True for these roles.
            is_superuser=True,
        ) | CustomUser.objects.filter(
            role__in=NON_PRIVILEGED_ROLES,
            is_staff=True,
        )

        # Deduplicate (union may include duplicates).
        bad_accounts = bad_accounts.distinct()

        if not bad_accounts.exists():
            self.stdout.write(self.style.SUCCESS(
                'No accounts with invalid privilege combinations found. '
                'Database is clean.'
            ))
            return

        # Materialise into a list now so the count stays correct after saves.
        bad_list = list(bad_accounts.order_by('role', 'username'))

        self.stdout.write(
            self.style.WARNING(
                f'Found {len(bad_list)} account(s) with invalid '
                f'is_superuser / is_staff flags for their role:'
            )
        )
        self.stdout.write('')

        for user in bad_list:
            self.stdout.write(
                f'  id={user.pk:<4} username={user.username!r:<20} '
                f'role={user.role!r:<12} '
                f'is_superuser={user.is_superuser} '
                f'is_staff={user.is_staff}'
            )
            if apply:
                user.is_superuser = False
                user.is_staff = False
                user.save(update_fields=['is_superuser', 'is_staff'])
                if verbosity >= 2:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'    -> Cleared is_superuser and is_staff for "{user.username}".'
                        )
                    )

        self.stdout.write('')

        if apply:
            self.stdout.write(self.style.SUCCESS(
                f'Done. Cleared is_superuser and is_staff on '
                f'{len(bad_list)} account(s).'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                'DRY RUN -- no changes were written.\n'
                'Run with --apply to fix these accounts:\n\n'
                '    python manage.py normalize_roles --apply'
            ))
