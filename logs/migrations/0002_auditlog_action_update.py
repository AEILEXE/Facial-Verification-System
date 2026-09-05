from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Adds the 'update' action choice to AuditLog.action, used when a
    beneficiary record is edited or deactivated by staff/admin.
    """

    dependencies = [
        ('logs', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='auditlog',
            name='action',
            field=models.CharField(
                choices=[
                    ('login', 'Login'),
                    ('logout', 'Logout'),
                    ('login_failed', 'Login Failed'),
                    ('register', 'Registration'),
                    ('verify', 'Verification'),
                    ('override', 'Override'),
                    ('user_create', 'User Created'),
                    ('user_update', 'User Updated'),
                    ('user_deactivate', 'User Deactivated'),
                    ('config_change', 'Config Changed'),
                    ('fallback', 'Fallback Triggered'),
                    ('update', 'Record Updated'),
                ],
                max_length=30,
            ),
        ),
    ]
