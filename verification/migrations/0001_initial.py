import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('beneficiaries', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='FaceEmbedding',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('embedding_data', models.BinaryField()),
                ('embedding_version', models.CharField(default='facenet-v1', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('beneficiary', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='face_embedding',
                    to='beneficiaries.beneficiary',
                )),
                ('created_by', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'db_table': 'fans_face_embeddings'},
        ),
        migrations.CreateModel(
            name='VerificationAttempt',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('liveness_passed', models.BooleanField(null=True)),
                ('liveness_score', models.FloatField(blank=True, null=True)),
                ('anti_spoof_score', models.FloatField(blank=True, null=True)),
                ('head_movement_completed', models.BooleanField(default=False)),
                ('similarity_score', models.FloatField(blank=True, null=True)),
                ('threshold_used', models.FloatField(default=0.75)),
                ('decision', models.CharField(
                    blank=True,
                    choices=[('verified', 'Verified'), ('not_verified', 'Not Verified'), ('manual_review', 'Manual Review'), ('denied', 'Denied')],
                    max_length=20,
                    null=True,
                )),
                ('attempt_number', models.PositiveSmallIntegerField(default=1)),
                ('session_id', models.UUIDField(default=uuid.uuid4)),
                ('fallback_triggered', models.BooleanField(default=False)),
                ('fallback_id_verified', models.BooleanField(null=True)),
                ('fallback_id_type', models.CharField(blank=True, max_length=50)),
                ('overridden', models.BooleanField(default=False)),
                ('override_reason', models.TextField(blank=True)),
                ('override_at', models.DateTimeField(blank=True, null=True)),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('notes', models.TextField(blank=True)),
                ('beneficiary', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='verification_attempts',
                    to='beneficiaries.beneficiary',
                )),
                ('override_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='overridden_verifications',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('performed_by', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='performed_verifications',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'db_table': 'fans_verification_attempts', 'ordering': ['-timestamp']},
        ),
        migrations.CreateModel(
            name='SystemConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(max_length=100, unique=True)),
                ('value', models.CharField(max_length=500)),
                ('description', models.TextField(blank=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('updated_by', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'db_table': 'fans_system_config'},
        ),
    ]
