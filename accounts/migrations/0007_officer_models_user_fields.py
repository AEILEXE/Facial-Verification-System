import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_migrate_legacy_admin_to_admin_it'),
    ]

    operations = [
        # New CustomUser fields
        migrations.AddField(
            model_name='customuser',
            name='account_status',
            field=models.CharField(
                choices=[('active', 'Active'), ('inactive', 'Inactive'), ('suspended', 'Suspended')],
                default='active',
                max_length=10,
                help_text='Active/Inactive/Suspended. Inactive and Suspended users cannot log in.',
            ),
        ),
        migrations.AddField(
            model_name='customuser',
            name='must_change_password',
            field=models.BooleanField(
                default=False,
                help_text='Force user to change their temporary password on next login.',
            ),
        ),
        migrations.AddField(
            model_name='customuser',
            name='assigned_office',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name='customuser',
            name='created_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='created_users',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='customuser',
            name='updated_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='updated_users',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # OfficerPosition
        migrations.CreateModel(
            name='OfficerPosition',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True)),
                ('description', models.TextField(blank=True)),
                ('order', models.PositiveSmallIntegerField(default=0, help_text='Lower number = higher in the org chart.')),
                ('is_unique', models.BooleanField(default=False, help_text='If True, only one active user may hold this position at a time.')),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='created_positions',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'db_table': 'fans_officer_positions', 'ordering': ['order', 'name']},
        ),
        # OfficerAssignment
        migrations.CreateModel(
            name='OfficerAssignment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('start_date', models.DateField()),
                ('end_date', models.DateField(blank=True, null=True)),
                ('is_current', models.BooleanField(default=True, help_text='True while the user actively holds this position.')),
                ('remarks', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('assigned_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='officer_assignments_made',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('position', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='assignments',
                    to='accounts.officerposition',
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='officer_assignments',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'db_table': 'fans_officer_assignments', 'ordering': ['-start_date']},
        ),
        # Seed default officer positions
        migrations.RunSQL(
            sql="""
            INSERT INTO fans_officer_positions (name, description, "order", is_unique, is_active, created_at, updated_at)
            VALUES
              ('President', '', 1, 1, 1, datetime('now'), datetime('now')),
              ('Vice President Internal', '', 2, 1, 1, datetime('now'), datetime('now')),
              ('Vice President External', '', 3, 1, 1, datetime('now'), datetime('now')),
              ('Secretary', '', 4, 1, 1, datetime('now'), datetime('now')),
              ('Treasurer', '', 5, 1, 1, datetime('now'), datetime('now')),
              ('Auditor', '', 6, 1, 1, datetime('now'), datetime('now')),
              ('PRO 1', '', 7, 0, 1, datetime('now'), datetime('now')),
              ('PRO 2', '', 8, 0, 1, datetime('now'), datetime('now')),
              ('Chairman of the Board', '', 9, 1, 1, datetime('now'), datetime('now')),
              ('Vice Chairman of the Board', '', 10, 1, 1, datetime('now'), datetime('now')),
              ('Board Secretary', '', 11, 1, 1, datetime('now'), datetime('now')),
              ('Board Undersecretary', '', 12, 1, 1, datetime('now'), datetime('now')),
              ('Board of Director', '', 13, 0, 1, datetime('now'), datetime('now')),
              ('Adviser / Punong Barangay', '', 14, 0, 1, datetime('now'), datetime('now'))
            ON CONFLICT (name) DO NOTHING;
            """,
            reverse_sql='DELETE FROM fans_officer_positions;',
        ),
    ]
