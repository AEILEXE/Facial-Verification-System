from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('verification', '0015_add_user_face_embedding'),
    ]

    operations = [
        migrations.AddField(
            model_name='stipendevent',
            name='custom_event_type',
            field=models.CharField(
                blank=True,
                max_length=100,
                help_text='Required when event_type is "custom". Free-text distribution name.',
            ),
        ),
        migrations.AlterField(
            model_name='stipendevent',
            name='event_type',
            field=models.CharField(
                choices=[
                    ('regular', 'Regular Monthly Stipend'),
                    ('birthday_bonus', 'Birthday Bonus'),
                    ('custom', 'Other / Custom'),
                ],
                default='regular',
                max_length=30,
            ),
        ),
    ]
