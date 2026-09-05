from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('beneficiaries', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='beneficiary',
            name='senior_citizen_id',
            field=models.CharField(blank=True, max_length=50, verbose_name='Senior Citizen ID Number'),
        ),
        migrations.AddField(
            model_name='beneficiary',
            name='valid_id_type',
            field=models.CharField(blank=True, max_length=50, verbose_name='Valid ID Type'),
        ),
        migrations.AddField(
            model_name='beneficiary',
            name='valid_id_number',
            field=models.CharField(blank=True, max_length=50, verbose_name='Valid ID Number'),
        ),
    ]
