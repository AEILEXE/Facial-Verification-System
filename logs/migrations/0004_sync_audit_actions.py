# Generated for FANS-C — adds three offline-sync audit action choices.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('logs', '0003_alter_auditlog_action'),
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
                    ('face_update_request', 'Face Update Requested'),
                    ('face_update_approved', 'Face Update Approved'),
                    ('face_update_rejected', 'Face Update Rejected'),
                    ('manual_verify_request', 'Manual Verification Requested'),
                    ('manual_verify_approved', 'Manual Verification Approved'),
                    ('manual_verify_rejected', 'Manual Verification Rejected'),
                    ('claim', 'Claim Recorded'),
                    ('special_claim_request', 'Special Claim Requested'),
                    ('special_claim_approved', 'Special Claim Approved'),
                    ('special_claim_rejected', 'Special Claim Rejected'),
                    ('register_approved', 'Registration Approved'),
                    ('register_rejected', 'Registration Rejected'),
                    ('duplicate_face', 'Duplicate Face Detected'),
                    ('sync_accepted', 'Sync Accepted'),
                    ('sync_conflict', 'Sync Conflict'),
                    ('sync_rejected', 'Sync Rejected'),
                ],
                max_length=30,
            ),
        ),
    ]
