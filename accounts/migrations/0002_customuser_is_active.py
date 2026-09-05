from django.db import migrations, models


class Migration(migrations.Migration):
    """
    CustomUser re-declares is_active = models.BooleanField(default=True)
    without verbose_name, which differs from the AbstractUser definition
    (verbose_name='active', help_text=...). This migration records that
    the verbose_name and help_text were removed from the custom model.
    """

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='customuser',
            name='is_active',
            field=models.BooleanField(default=True),
        ),
    ]
