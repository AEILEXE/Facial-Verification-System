from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('verification', '0002_verificationattempt_claimant'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='StipendEvent',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('title', models.CharField(max_length=200)),
                ('date', models.DateField()),
                ('description', models.TextField(blank=True)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='created_stipend_events',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'fans_stipend_events',
                'ordering': ['date'],
            },
        ),
        migrations.AddField(
            model_name='verificationattempt',
            name='stipend_event',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='claims',
                to='verification.stipendevent',
            ),
        ),
        migrations.AddField(
            model_name='verificationattempt',
            name='face_quality_score',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='verificationattempt',
            name='decision_reason',
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AlterField(
            model_name='verificationattempt',
            name='threshold_used',
            field=models.FloatField(default=0.60),
        ),
    ]
