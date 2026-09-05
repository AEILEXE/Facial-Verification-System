# v2.1.11 — President approval workflow for stipend schedules (Issue 5)
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_existing_as_approved(apps, schema_editor):
    """All pre-existing schedules become approved/published so we don't break
    historical events. Only newly-created Admin schedules will be pending."""
    StipendEvent = apps.get_model('verification', 'StipendEvent')
    StipendEvent.objects.filter(approval_status='pending_approval').update(
        approval_status='approved',
    )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('verification', '0018_liveness_transaction'),
    ]

    operations = [
        migrations.AddField(
            model_name='stipendevent',
            name='approval_status',
            field=models.CharField(
                choices=[
                    ('pending_approval', 'Pending Approval'),
                    ('approved', 'Approved / Published'),
                    ('rejected', 'Rejected'),
                ],
                default='approved',
                help_text='Approval state for this schedule. Approved/Published events are usable for claiming.',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='stipendevent',
            name='approved_by',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='approved_stipend_events',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='stipendevent',
            name='approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='stipendevent',
            name='rejection_reason',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='stipendevent',
            name='published_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_existing_as_approved, migrations.RunPython.noop),
    ]
