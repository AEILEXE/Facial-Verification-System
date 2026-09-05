import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('beneficiaries', '0001_initial'),
        ('verification', '0017_add_payout_time_window'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='LivenessTransaction',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('token', models.UUIDField(db_index=True, default=uuid.uuid4, unique=True)),
                ('claimant_type', models.CharField(default='beneficiary', max_length=20)),
                ('challenge_direction', models.CharField(blank=True, max_length=20)),
                ('attempt_number', models.PositiveSmallIntegerField(default=1)),
                ('anti_spoof_score', models.FloatField(default=0.0)),
                ('liveness_score', models.FloatField(default=0.0)),
                ('pa_score', models.FloatField(default=0.0, help_text='Presentation attack heuristic score (0=clean, 1=suspicious).')),
                ('pa_flags', models.JSONField(blank=True, default=dict)),
                ('embedding_data', models.BinaryField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField(db_index=True)),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                ('beneficiary', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='liveness_transactions', to='beneficiaries.beneficiary')),
                ('performed_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='liveness_transactions', to=settings.AUTH_USER_MODEL)),
                ('representative', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='liveness_transactions', to='beneficiaries.representative')),
                ('stipend_event', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='liveness_transactions', to='verification.stipendevent')),
                ('used_by_attempt', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='liveness_transaction', to='verification.verificationattempt')),
            ],
            options={
                'db_table': 'fans_liveness_transactions',
                'ordering': ['-created_at'],
            },
        ),
    ]
