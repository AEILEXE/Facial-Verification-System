# v2.2.0 Post-UAT Phase 4 — backfill legacy rejected-registration records.
#
# Before this release, rejecting a PENDING registration (via the registration
# review queue, the duplicate-face review queue, or the name/DOB override
# review queue) set status='inactive' with a machine-generated
# deactivated_reason. That made a disapproved application indistinguishable
# from a beneficiary that really was once active and later deactivated.
#
# This migration reclassifies those specific legacy rows to the new
# 'disapproved' status by matching the exact machine-generated reason
# prefixes those code paths always wrote — no other data is touched, and
# nothing is deleted (history is fully preserved in the same row).
from django.db import migrations

_REJECTION_PREFIXES = (
    'Registration rejected by ',
    'Registration rejected as confirmed duplicate/fraud:',
    'Name/DOB override rejected',
)


def backfill_disapproved(apps, schema_editor):
    Beneficiary = apps.get_model('beneficiaries', 'Beneficiary')
    candidates = Beneficiary.objects.filter(status='inactive').exclude(deactivated_reason='')
    to_update = [
        b.pk for b in candidates.only('pk', 'deactivated_reason')
        if b.deactivated_reason.startswith(_REJECTION_PREFIXES)
    ]
    if to_update:
        Beneficiary.objects.filter(pk__in=to_update).update(status='disapproved')


def reverse_backfill(apps, schema_editor):
    # Not reversible in a lossless way (we no longer know which rows were
    # 'inactive' before this migration ran vs. genuinely disapproved records
    # created after it), and there is no correctness reason to reverse it —
    # left as a no-op.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('beneficiaries', '0014_add_disapproved_status'),
    ]

    operations = [
        migrations.RunPython(backfill_disapproved, reverse_backfill),
    ]
