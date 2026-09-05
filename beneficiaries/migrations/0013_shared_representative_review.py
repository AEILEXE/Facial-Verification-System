# Migration for shared-representative review workflow (added 2026-05-28).
# Adds:
#   - Representative.shared_review_status field
#   - SharedRepresentativeReview model (admin review case for a flagged rep)

import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('beneficiaries', '0012_duplicate_namedob_request'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='representative',
            name='shared_review_status',
            field=models.CharField(
                max_length=20,
                default='none',
                db_index=True,
                choices=[
                    ('none',               'Not Shared'),
                    ('pending_review',     'Pending Representative Review'),
                    ('approved',           'Shared Representative Approved'),
                    ('rejected',           'Shared Representative Rejected'),
                    ('docs_required',      'Authorization Document Required'),
                    ('suspicious_blocked', 'Suspicious Representative Blocked'),
                ],
                help_text=(
                    'Set automatically when this representative\'s face matches '
                    'an existing representative for a different beneficiary. '
                    'Verification is blocked while status is pending/blocked/docs_required.'
                ),
            ),
        ),
        migrations.CreateModel(
            name='SharedRepresentativeReview',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('matched_beneficiary_id', models.CharField(
                    max_length=50,
                    help_text='Public beneficiary_id of the existing beneficiary already represented.',
                )),
                ('matched_beneficiary_name', models.CharField(blank=True, max_length=200)),
                ('matched_score', models.FloatField(default=0.0)),
                ('matched_threshold', models.FloatField(default=0.0)),
                ('status', models.CharField(
                    max_length=20,
                    default='pending_review',
                    db_index=True,
                    choices=[
                        ('pending_review',     'Pending Representative Review'),
                        ('approved',           'Shared Representative Approved'),
                        ('rejected',           'Shared Representative Rejected'),
                        ('docs_required',      'Authorization Document Required'),
                        ('suspicious_blocked', 'Suspicious Representative Blocked'),
                    ],
                )),
                ('flag_reason', models.TextField(
                    blank=True,
                    help_text='Why the system flagged this case (auto-generated).',
                )),
                ('flagged_at', models.DateTimeField(auto_now_add=True)),
                ('decision_notes', models.TextField(
                    blank=True,
                    help_text='Admin notes recorded with the decision.',
                )),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('authorization_document', models.FileField(blank=True, null=True, upload_to='representatives/authorization/')),
                ('flagged_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=models.deletion.SET_NULL,
                    related_name='shared_rep_flags',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('decided_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=models.deletion.SET_NULL,
                    related_name='shared_rep_decisions',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('representative', models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name='shared_reviews',
                    to='beneficiaries.representative',
                    help_text='The newly-registered representative whose face matched an existing one.',
                )),
            ],
            options={
                'db_table': 'fans_shared_representative_reviews',
                'ordering': ['-flagged_at'],
            },
        ),
    ]
