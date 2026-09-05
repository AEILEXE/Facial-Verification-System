from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('beneficiaries', '0003_beneficiary_lifecycle'),
        ('verification', '0005_improvements'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── AdditionalFaceEmbedding ───────────────────────────────────────────
        migrations.CreateModel(
            name='AdditionalFaceEmbedding',
            fields=[
                ('id', models.UUIDField(
                    primary_key=True, default=uuid.uuid4,
                    editable=False, serialize=False,
                )),
                ('embedding_data', models.BinaryField()),
                ('embedding_version', models.CharField(default='facenet-v1', max_length=20)),
                ('label', models.CharField(
                    blank=True, max_length=100,
                    help_text='Free-text label e.g. "update-2025-03"',
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('beneficiary', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='additional_embeddings',
                    to='beneficiaries.beneficiary',
                )),
                ('created_by', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='created_additional_embeddings',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'fans_additional_face_embeddings',
                'ordering': ['-created_at'],
            },
        ),

        # ── FaceUpdateLog ─────────────────────────────────────────────────────
        migrations.CreateModel(
            name='FaceUpdateLog',
            fields=[
                ('id', models.UUIDField(
                    primary_key=True, default=uuid.uuid4,
                    editable=False, serialize=False,
                )),
                ('reason', models.CharField(
                    max_length=30,
                    choices=[
                        ('repeated_failure',  'Repeated Verification Failure'),
                        ('appearance_change', 'Major Appearance Change'),
                        ('poor_original',     'Poor Original Registration'),
                        ('staff_decision',    'Staff Decision / Other'),
                    ],
                )),
                ('action', models.CharField(
                    max_length=10,
                    choices=[
                        ('replace', 'Replace Primary Embedding'),
                        ('augment', 'Add as Additional Template'),
                    ],
                    default='replace',
                )),
                ('notes', models.TextField(blank=True)),
                ('success', models.BooleanField(default=False)),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('beneficiary', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='face_update_logs',
                    to='beneficiaries.beneficiary',
                )),
                ('performed_by', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='face_updates_performed',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'fans_face_update_logs',
                'ordering': ['-timestamp'],
            },
        ),
    ]
