# Generated manually on 2026-04-14
#
# Extends the 'role' field to include:
#   'head_brgy' — Head Barangay (operational admin, non-technical)
#   'admin_it'  — IT / Admin (technical administrator)
#
# The legacy 'admin' value is kept so no existing rows are broken.
# No data migration is required — existing 'admin' and 'staff' users
# remain valid and their is_admin / is_admin_it properties work as expected.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_add_profile_picture'),
    ]

    operations = [
        migrations.AlterField(
            model_name='customuser',
            name='role',
            field=models.CharField(
                choices=[
                    ('head_brgy', 'Head Barangay'),
                    ('admin_it',  'IT / Admin'),
                    ('staff',     'Staff'),
                    ('admin',     'Admin (legacy)'),
                ],
                default='staff',
                max_length=10,
            ),
        ),
    ]
