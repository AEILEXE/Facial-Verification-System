# Generated 2026-05-27
#
# Safety-net migration: re-converts any surviving legacy role values that
# may have been written AFTER migration 0008 ran (e.g. by the installer
# admin-creation form or the create_admin management command before v2.1.9
# fixed those tools to use the new role constants).
#
# Conversions applied:
#   admin_it  → admin
#   head_brgy → president

from django.db import migrations


def repair_forward(apps, schema_editor):
    CustomUser = apps.get_model('accounts', 'CustomUser')
    CustomUser.objects.filter(role='admin_it').update(role='admin')
    CustomUser.objects.filter(role='head_brgy').update(role='president')


def repair_reverse(apps, schema_editor):
    pass  # Intentional no-op: cannot distinguish which admin rows were admin_it.


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0008_roles_president_admin_it'),
    ]

    operations = [
        migrations.RunPython(repair_forward, repair_reverse),
    ]
