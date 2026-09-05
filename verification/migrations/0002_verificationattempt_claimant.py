from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('verification', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='verificationattempt',
            name='claimant_type',
            field=models.CharField(
                choices=[('beneficiary', 'Beneficiary'), ('representative', 'Authorized Representative')],
                default='beneficiary',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='verificationattempt',
            name='decision_reason',
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
