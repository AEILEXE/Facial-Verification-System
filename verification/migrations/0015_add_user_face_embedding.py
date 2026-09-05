from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('verification', '0014_claimrecord_amount_claimrecord_override_at_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserFaceEmbedding',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('embedding_data', models.BinaryField()),
                ('embedding_version', models.CharField(default='facenet-v1', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='created_user_face_embeddings',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('user', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='user_face_embedding',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'fans_user_face_embeddings',
            },
        ),
    ]
