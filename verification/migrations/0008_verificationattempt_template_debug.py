from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('verification', '0007_alter_stipendevent_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='verificationattempt',
            name='matched_template',
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name='verificationattempt',
            name='templates_checked',
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
