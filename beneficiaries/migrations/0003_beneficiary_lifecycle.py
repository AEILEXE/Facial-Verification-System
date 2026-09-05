from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('beneficiaries', '0002_beneficiary_id_fields'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='beneficiary',
            name='deactivated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='beneficiary',
            name='deactivated_reason',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='beneficiary',
            name='deactivated_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='deactivated_beneficiaries',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='beneficiary',
            name='status',
            field=models.CharField(
                choices=[
                    ('active', 'Active'),
                    ('inactive', 'Inactive'),
                    ('deceased', 'Deceased'),
                    ('pending', 'Pending'),
                ],
                default='pending',
                max_length=10,
            ),
        ),
    ]
