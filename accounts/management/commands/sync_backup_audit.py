"""
Management command: sync_backup_audit

Mirrors scripts/admin/daily-backup.ps1's backup manifests into AuditLog so
backup success/failure has a permanent record in the app's own audit trail,
not just the live-scanned System Health page (fans.views.system_health).

The PowerShell backup script deliberately never calls into Django -- it must
keep working even if the app/DB is broken, since that's the scenario a
backup exists to recover from. This command instead reads the manifests the
script already writes (see fans/backup_status.py) and is idempotent: each
backup directory is logged at most once, identified by AuditLog.target_id.

Usage:
    python manage.py sync_backup_audit
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from fans.backup_status import _scan_fans_backup_directories, _is_fans_backup_restore_ready
from logs.models import AuditLog, Notification
from logs.notifications import notify_admins


class Command(BaseCommand):
    help = 'Mirror backup manifest status into AuditLog (idempotent, safe to re-run).'

    def handle(self, *args, **options):
        backups_root = settings.BASE_DIR / 'backups'

        already_logged = set(
            AuditLog.objects
            .filter(action__in=[AuditLog.ACTION_BACKUP_COMPLETED, AuditLog.ACTION_BACKUP_INCOMPLETE])
            .values_list('target_id', flat=True)
        )

        logged = 0
        for entry, base, seq, manifest in _scan_fans_backup_directories(backups_root):
            if entry.name in already_logged:
                continue
            if manifest is not None and _is_fans_backup_restore_ready(entry, manifest):
                AuditLog.log(
                    action=AuditLog.ACTION_BACKUP_COMPLETED,
                    user=None,
                    target_type='BackupDirectory',
                    target_id=entry.name,
                    details={
                        'restore_ready': True,
                        'db_backup_bytes': manifest.get('db_backup_bytes'),
                        'env_included': manifest.get('env_included'),
                        'integrity_check': manifest.get('integrity_check'),
                    },
                )
            else:
                AuditLog.log(
                    action=AuditLog.ACTION_BACKUP_INCOMPLETE,
                    user=None,
                    target_type='BackupDirectory',
                    target_id=entry.name,
                    details={
                        'restore_ready': False,
                        'reason': 'manifest missing or failed restore-ready check',
                        'manifest_present': manifest is not None,
                    },
                )
                # A failed/incomplete backup is a meaningful, actionable event —
                # unlike a successful one (routine, not worth an alert).
                notify_admins(
                    category=Notification.CATEGORY_SYSTEM_ALERT,
                    title='Backup incomplete or failed',
                    message=f'Backup "{entry.name}" is not restore-ready — check System Health.',
                    url='/system/health/',
                    dedupe_key=f'system_alert_backup_incomplete:{entry.name}',
                    priority=Notification.PRIORITY_HIGH,
                )
            logged += 1

        if logged:
            self.stdout.write(self.style.SUCCESS(f'sync_backup_audit: logged {logged} new backup director{"y" if logged == 1 else "ies"}.'))
