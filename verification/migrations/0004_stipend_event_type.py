from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('verification', '0003_stipend_event'),
    ]

    operations = [
        migrations.AddField(
            model_name='stipendevent',
            name='event_type',
            field=models.CharField(
                choices=[
                    ('regular', 'Regular Monthly Stipend'),
                    ('birthday_bonus', 'Birthday Bonus'),
                ],
                default='regular',
                max_length=30,
            ),
        ),
    ]
