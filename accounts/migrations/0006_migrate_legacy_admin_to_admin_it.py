# Generated manually on 2026-04-19
#
# Converts all existing users with role='admin' (legacy) to role='admin_it'.
# Removes 'Admin (legacy)' from ROLE_CHOICES so it can no longer appear in
# UI dropdowns or the navbar role badge.
#
# Permissions are fully preserved: admin_it has identical access to the
# legacy admin role (is_admin_it, is_admin both return True for admin_it).
#
# After this migration no DB row carries role='admin'.  The ROLE_ADMIN constant
# and is_admin_it property still reference 'admin' as a safety net, but the
# value will never appear in the system under normal operation.

from django.db import migrations, models


def migrate_legacy_admin_forward(apps, schema_editor):
    CustomUser = apps.get_model('accounts', 'CustomUser')
    CustomUser.objects.filter(role='admin').update(role='admin_it')


def migrate_legacy_admin_reverse(apps, schema_editor):
    # Intentionally a no-op: we don't know which admin_it users were originally
    # legacy admin, so we cannot safely reverse this migration.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_extend_role_choices'),
    ]

    operations = [
        # Data first: all 'admin' rows become 'admin_it' before the field
        # definition is updated (so no row carries a value not in the new choices).
        migrations.RunPython(
            migrate_legacy_admin_forward,
            migrate_legacy_admin_reverse,
        ),
        migrations.AlterField(
            model_name='customuser',
            name='role',
            field=models.CharField(
                choices=[
                    ('head_brgy', 'Head Barangay'),
                    ('admin_it',  'IT / Admin'),
                    ('staff',     'Staff'),
                ],
                default='staff',
                max_length=10,
            ),
        ),
    ]
