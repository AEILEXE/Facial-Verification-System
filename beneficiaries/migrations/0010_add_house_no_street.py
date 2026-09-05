from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('beneficiaries', '0009_alter_beneficiary_sync_error'),
    ]

    operations = [
        migrations.AddField(
            model_name='beneficiary',
            name='house_no',
            field=models.CharField(blank=True, max_length=100, verbose_name='House No.'),
        ),
        migrations.AddField(
            model_name='beneficiary',
            name='street',
            field=models.CharField(blank=True, max_length=200, verbose_name='Street'),
        ),
        migrations.AlterField(
            model_name='beneficiary',
            name='address',
            field=models.TextField(blank=True),
        ),
    ]
