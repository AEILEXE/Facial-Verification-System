# v2.1.11 — duplicate name+DOB override workflow (Issue 1)
import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('beneficiaries', '0011_duplicate_face_review'),
    ]

    operations = [
        migrations.CreateModel(
            name='DuplicateNameDobRequest',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('reason', models.TextField(help_text='Written reason from staff: why this is a different person.')),
                ('distinguishing_info', models.TextField(blank=True, help_text='Optional: address / contact / ID details that distinguish the two records.')),
                ('status', models.CharField(choices=[('pending', 'Pending Review'), ('approved', 'Approved'), ('rejected', 'Rejected')], default='pending', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('review_notes', models.TextField(blank=True)),
                ('existing_beneficiary', models.ForeignKey(blank=True, help_text='The existing beneficiary that shares the same name and DOB.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='namedob_collisions_as_existing', to='beneficiaries.beneficiary')),
                ('new_beneficiary', models.OneToOneField(help_text='The newly-created beneficiary record awaiting review.', on_delete=django.db.models.deletion.CASCADE, related_name='duplicate_namedob_request', to='beneficiaries.beneficiary')),
                ('requested_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='submitted_namedob_override_requests', to=settings.AUTH_USER_MODEL)),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reviewed_namedob_override_requests', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'fans_duplicate_namedob_requests',
                'ordering': ['-created_at'],
            },
        ),
    ]
