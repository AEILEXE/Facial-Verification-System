# Generated 2026-05-27
#
# Renames system roles to match the new org-chart/permission structure:
#   head_brgy → president  (operational head; approves claims, overrides decisions)
#   admin_it  → admin      (administrative; manages users and beneficiaries)
#
# A new 'it' role value is added for the technical role. Existing admin_it users
# become 'admin' by default; an IT admin can reassign them to 'it' as needed.
#
# The field choices are updated to:
#   president, admin, it, staff

from django.db import migrations, models


def roles_forward(apps, schema_editor):
    CustomUser = apps.get_model('accounts', 'CustomUser')
    CustomUser.objects.filter(role='head_brgy').update(role='president')
    CustomUser.objects.filter(role='admin_it').update(role='admin')
    # Safety: catch any surviving legacy 'admin' row (should not exist post-0006)
    # and leave it as-is — 'admin' is a valid new role value.


def roles_reverse(apps, schema_editor):
    # Partial reverse: restore original values as best-effort.
    CustomUser = apps.get_model('accounts', 'CustomUser')
    CustomUser.objects.filter(role='president').update(role='head_brgy')
    # Cannot distinguish former admin_it from new admin; map all back.
    CustomUser.objects.filter(role='admin').update(role='admin_it')
    CustomUser.objects.filter(role='it').update(role='admin_it')


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0007_officer_models_user_fields'),
    ]

    operations = [
        # Data migration first: rename existing role values before updating choices.
        migrations.RunPython(roles_forward, roles_reverse),
        migrations.AlterField(
            model_name='customuser',
            name='role',
            field=models.CharField(
                choices=[
                    ('president', 'President'),
                    ('admin',     'Admin'),
                    ('it',        'IT'),
                    ('staff',     'Staff'),
                ],
                default='staff',
                max_length=10,
                help_text='Software permission level. Separate from Officer Position.',
            ),
        ),
    ]
