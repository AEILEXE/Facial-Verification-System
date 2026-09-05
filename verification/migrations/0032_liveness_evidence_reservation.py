# v2.1.16 Final Hardening Patch (Codex NO-GO #4) — atomic reservation table
# for the perceptual-hash replay race. See LivenessEvidenceReservation's
# docstring in verification/models.py for the full rationale.
import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('verification', '0031_liveness_tx_context_binding'),
    ]

    operations = [
        migrations.CreateModel(
            name='LivenessEvidenceReservation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('evidence_hash', models.CharField(blank=True, db_index=True, default='', max_length=64)),
                ('evidence_pixel_hash', models.CharField(blank=True, db_index=True, default='', max_length=64)),
                ('evidence_phash', models.CharField(blank=True, db_index=True, default='', max_length=32)),
                ('reserved_at', models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                'db_table': 'fans_liveness_evidence_reservations',
                'ordering': ['-reserved_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='livenessevidencereservation',
            constraint=models.UniqueConstraint(condition=models.Q(('evidence_hash__gt', '')), fields=('evidence_hash',), name='unique_nonblank_reservation_evidence_hash'),
        ),
        migrations.AddConstraint(
            model_name='livenessevidencereservation',
            constraint=models.UniqueConstraint(condition=models.Q(('evidence_pixel_hash__gt', '')), fields=('evidence_pixel_hash',), name='unique_nonblank_reservation_evidence_pixel_hash'),
        ),
    ]
