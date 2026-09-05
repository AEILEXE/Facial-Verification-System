from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Adds offline-sync tracking fields to the Beneficiary model.

    is_synced      — False until the record is successfully sent to the central API.
    sync_error     — Stores the last error message from a failed sync attempt.
    last_synced_at — Timestamp of the most recent successful sync.

    These fields support the offline-first workflow: registrations made without
    internet access are marked is_synced=False and queued for the next time
    connectivity is restored.
    """

    dependencies = [
        ('beneficiaries', '0005_add_representative_model'),
    ]

    operations = [
        migrations.AddField(
            model_name='beneficiary',
            name='is_synced',
            field=models.BooleanField(
                default=False,
                db_index=True,
                help_text='True when this record has been synced to the central server.',
            ),
        ),
        migrations.AddField(
            model_name='beneficiary',
            name='sync_error',
            field=models.TextField(
                blank=True,
                help_text='Last sync error message, if any. Cleared on successful sync.',
            ),
        ),
        migrations.AddField(
            model_name='beneficiary',
            name='last_synced_at',
            field=models.DateTimeField(
                null=True,
                blank=True,
                help_text='Timestamp of the last successful sync to the central server.',
            ),
        ),
    ]
