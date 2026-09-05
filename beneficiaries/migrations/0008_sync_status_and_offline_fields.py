"""
Migration 0008 — Upgrade offline sync tracking.

Replaces the simple `is_synced` BooleanField with a `sync_status` CharField
that implements a four-state machine:

    pending_sync  — record created locally, not yet sent to central server
    synced        — central server accepted (HTTP 200/201)
    sync_conflict — central server returned 409 (conflicting data)
    sync_rejected — central server returned 400/422 (permanently rejected)

Data migration:
    is_synced=True  → sync_status='synced'
    is_synced=False → sync_status='pending_sync'  (default, no change needed)

New audit fields added alongside:
    offline_device     — hostname of the workstation that created the record offline
    sync_attempted_at  — timestamp of the most recent sync attempt (any outcome)

The existing `sync_error` and `last_synced_at` fields are preserved unchanged.
"""
from django.db import migrations, models


def forwards_migrate_sync_status(apps, schema_editor):
    """Copy is_synced=True records to sync_status='synced'."""
    Beneficiary = apps.get_model('beneficiaries', 'Beneficiary')
    # Records where is_synced=True get promoted to 'synced'.
    # Records where is_synced=False keep the default 'pending_sync' — no update needed.
    Beneficiary.objects.filter(is_synced=True).update(sync_status='synced')


def reverse_migrate_sync_status(apps, schema_editor):
    """Restore is_synced from sync_status for rollback."""
    Beneficiary = apps.get_model('beneficiaries', 'Beneficiary')
    Beneficiary.objects.filter(sync_status='synced').update(is_synced=True)


class Migration(migrations.Migration):

    dependencies = [
        ('beneficiaries', '0007_unique_nonempty_senior_citizen_id'),
    ]

    operations = [
        # Step 1: Add sync_status with default 'pending_sync' so existing rows
        # get a valid value immediately (before the data migration runs).
        migrations.AddField(
            model_name='beneficiary',
            name='sync_status',
            field=models.CharField(
                max_length=15,
                choices=[
                    ('pending_sync',   'Pending Sync'),
                    ('synced',         'Synced'),
                    ('sync_conflict',  'Sync Conflict'),
                    ('sync_rejected',  'Sync Rejected'),
                ],
                default='pending_sync',
                db_index=True,
                help_text='Current sync state (see SYNC_* constants). Set by sync.py.',
            ),
        ),

        # Step 2: Data migration — promote previously-synced records.
        migrations.RunPython(
            forwards_migrate_sync_status,
            reverse_migrate_sync_status,
        ),

        # Step 3: Drop the old boolean field.
        migrations.RemoveField(
            model_name='beneficiary',
            name='is_synced',
        ),

        # Step 4: Add new offline-device audit fields.
        migrations.AddField(
            model_name='beneficiary',
            name='offline_device',
            field=models.CharField(
                max_length=255,
                blank=True,
                default='',
                help_text='Hostname of the offline workstation that created this record, if any.',
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='beneficiary',
            name='sync_attempted_at',
            field=models.DateTimeField(
                null=True,
                blank=True,
                help_text='Timestamp of the most recent sync attempt (any outcome).',
            ),
        ),
    ]
