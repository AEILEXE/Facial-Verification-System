"""
Management command: generate_key
Generates a Fernet encryption key for EMBEDDING_ENCRYPTION_KEY.

Usage:
    python manage.py generate_key
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Generate a Fernet encryption key for embedding encryption'

    def handle(self, *args, **options):
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
        self.stdout.write(self.style.SUCCESS('Generated Fernet key:'))
        self.stdout.write(key)
        self.stdout.write(self.style.WARNING(
            '\nAdd this to your .env file as:\n'
            f'EMBEDDING_ENCRYPTION_KEY={key}\n'
            'Keep this key SECRET. Losing it will make stored embeddings unreadable.'
        ))
