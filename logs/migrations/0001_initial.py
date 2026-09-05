import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AuditLog',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('action', models.CharField(
                    choices=[
                        ('login', 'Login'), ('logout', 'Logout'), ('login_failed', 'Login Failed'),
                        ('register', 'Registration'), ('verify', 'Verification'),
                        ('override', 'Override'), ('user_create', 'User Created'),
                        ('user_update', 'User Updated'), ('user_deactivate', 'User Deactivated'),
                        ('config_change', 'Config Changed'), ('fallback', 'Fallback Triggered'),
                    ],
                    max_length=30,
                )),
                ('target_type', models.CharField(blank=True, max_length=50)),
                ('target_id', models.CharField(blank=True, max_length=100)),
                ('details', models.JSONField(blank=True, default=dict)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('user_agent', models.CharField(blank=True, max_length=500)),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='audit_logs',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'db_table': 'fans_audit_logs', 'ordering': ['-timestamp']},
        ),
    ]
