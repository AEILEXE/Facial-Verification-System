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
            name='Beneficiary',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('beneficiary_id', models.CharField(max_length=20, unique=True)),
                ('first_name', models.CharField(max_length=100)),
                ('middle_name', models.CharField(blank=True, max_length=100)),
                ('last_name', models.CharField(max_length=100)),
                ('date_of_birth', models.DateField()),
                ('gender', models.CharField(choices=[('M', 'Male'), ('F', 'Female'), ('O', 'Other')], max_length=10)),
                ('address', models.TextField()),
                ('barangay', models.CharField(max_length=100)),
                ('municipality', models.CharField(max_length=100)),
                ('province', models.CharField(max_length=100)),
                ('contact_number', models.CharField(blank=True, max_length=20)),
                ('has_representative', models.BooleanField(default=False)),
                ('rep_first_name', models.CharField(blank=True, max_length=100)),
                ('rep_last_name', models.CharField(blank=True, max_length=100)),
                ('rep_relationship', models.CharField(blank=True, max_length=100)),
                ('rep_contact', models.CharField(blank=True, max_length=20)),
                ('rep_id_type', models.CharField(blank=True, max_length=50)),
                ('rep_id_number', models.CharField(blank=True, max_length=50)),
                ('consent_given', models.BooleanField(default=False)),
                ('consent_date', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('active', 'Active'), ('inactive', 'Inactive'), ('pending', 'Pending')], default='pending', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('registered_by', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='registered_beneficiaries',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'fans_beneficiaries',
                'ordering': ['last_name', 'first_name'],
            },
        ),
    ]
