from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('verification', '0004_stipend_event_type'),
    ]

    operations = [
        # StipendEvent: payout window fields
        migrations.AddField(
            model_name='stipendevent',
            name='payout_start_date',
            field=models.DateField(
                blank=True,
                null=True,
                help_text='First day beneficiaries may claim. Defaults to date if not set.',
            ),
        ),
        migrations.AddField(
            model_name='stipendevent',
            name='payout_end_date',
            field=models.DateField(
                blank=True,
                null=True,
                help_text='Last day beneficiaries may claim. Defaults to date if not set.',
            ),
        ),

        # VerificationAttempt: demo_mode_active flag
        migrations.AddField(
            model_name='verificationattempt',
            name='demo_mode_active',
            field=models.BooleanField(
                default=False,
                help_text='True if DEMO_MODE was enabled at the time of this verification.',
            ),
        ),

        # VerificationAttempt: face_quality_ok flag
        migrations.AddField(
            model_name='verificationattempt',
            name='face_quality_ok',
            field=models.BooleanField(blank=True, null=True),
        ),
    ]
