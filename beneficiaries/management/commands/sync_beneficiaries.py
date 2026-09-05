"""
Management command: sync_beneficiaries

Sends locally-registered (pending_sync) beneficiary records to the central
server API when an internet connection is available.

Only records with sync_status='pending_sync' are processed.  Records in
'sync_conflict' or 'sync_rejected' require admin review via the sync conflict
dashboard (/sync/conflicts/) before they can be retried.

Usage:
    python manage.py sync_beneficiaries
    python manage.py sync_beneficiaries --force       # skip connectivity check
    python manage.py sync_beneficiaries --quiet       # suppress non-error output
    python manage.py sync_beneficiaries --batch 100   # override batch size

Configuration (in .env):
    SYNC_API_URL    — e.g. https://central.fans-c.gov.ph/api
    SYNC_API_KEY    — Bearer token / API key
    SYNC_TIMEOUT    — HTTP timeout in seconds (default 30)
    SYNC_BATCH_SIZE — Records per run (default 50)

Run automatically:
    - Called by run.ps1 on server startup (background process).
    - Can be scheduled with Windows Task Scheduler for periodic sync.

Key sharing note:
    EMBEDDING_ENCRYPTION_KEY must be identical on this device and the
    central server. Transfer it securely (not over plain email).
"""
import logging

from django.core.management.base import BaseCommand

logger = logging.getLogger('beneficiaries.sync')


class Command(BaseCommand):
    help = 'Sync pending_sync beneficiary records to the central server'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Run even if no internet connection is detected.',
        )
        parser.add_argument(
            '--quiet',
            action='store_true',
            help='Suppress all output except errors.',
        )
        parser.add_argument(
            '--batch',
            type=int,
            default=None,
            help='Maximum number of records to sync in this run (default: SYNC_BATCH_SIZE or 50).',
        )

    def handle(self, *args, **options):
        from django.conf import settings
        from beneficiaries.sync import is_online, sync_all, pending_count, conflict_count, rejected_count

        quiet = options['quiet']
        force = options['force']

        api_url = getattr(settings, 'SYNC_API_URL', '').strip()

        if not api_url:
            if not quiet:
                self.stdout.write(
                    self.style.WARNING(
                        'SYNC SKIP: SYNC_API_URL is not configured in .env. '
                        'Centralized/offline-only mode — data is stored locally only.'
                    )
                )
            return

        # Show current queue state before attempting sync
        n_pending   = pending_count()
        n_conflicts = conflict_count()
        n_rejected  = rejected_count()

        if not quiet:
            if n_conflicts or n_rejected:
                self.stdout.write(
                    self.style.WARNING(
                        f'SYNC QUEUE | pending={n_pending} '
                        f'conflict={n_conflicts} rejected={n_rejected}'
                    )
                )
                if n_conflicts:
                    self.stdout.write(
                        self.style.WARNING(
                            f'  {n_conflicts} record(s) in sync_conflict — '
                            'admin review required at /sync/conflicts/'
                        )
                    )
                if n_rejected:
                    self.stdout.write(
                        self.style.WARNING(
                            f'  {n_rejected} record(s) in sync_rejected — '
                            'admin review required at /sync/conflicts/'
                        )
                    )

        if n_pending == 0:
            if not quiet:
                self.stdout.write(self.style.SUCCESS('SYNC OK: No pending_sync records.'))
            return

        if not quiet:
            self.stdout.write(f'SYNC: {n_pending} record(s) pending sync to {api_url}')

        # Connectivity check
        if not force and not is_online():
            if not quiet:
                self.stdout.write(
                    self.style.WARNING(
                        f'SYNC SKIP: No internet connection detected. '
                        f'{n_pending} record(s) will sync when connectivity is restored.'
                    )
                )
            logger.info('SYNC SKIP | offline | pending=%d', n_pending)
            return

        batch_size = options['batch'] or int(getattr(settings, 'SYNC_BATCH_SIZE', 50))

        if not quiet:
            self.stdout.write(f'SYNC: Sending up to {batch_size} record(s) ...')

        result = sync_all(batch_size=batch_size)

        synced    = result['synced']
        failed    = result['failed']
        conflicts = result['conflicts']
        rejected  = result['rejected']

        if failed == 0 and conflicts == 0 and rejected == 0:
            if not quiet:
                self.stdout.write(
                    self.style.SUCCESS(f'SYNC DONE: {synced} synced, 0 errors.')
                )
        else:
            summary_parts = [f'{synced} synced']
            if failed:
                summary_parts.append(f'{failed} failed (will retry)')
            if conflicts:
                summary_parts.append(f'{conflicts} conflict (admin review needed)')
            if rejected:
                summary_parts.append(f'{rejected} rejected (admin review needed)')

            self.stdout.write(
                self.style.WARNING(
                    f'SYNC PARTIAL: {", ".join(summary_parts)}. '
                    'Check /sync/conflicts/ for conflict and rejected records.'
                )
            )
            # Exit with a non-zero code so callers (e.g. Task Scheduler) know
            # there were non-transient outcomes requiring attention.
            if conflicts or rejected:
                raise SystemExit(2)
            raise SystemExit(1)
