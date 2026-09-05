"""
Management command: init_config
Seeds the SystemConfig table with default values.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Initialize system configuration with default values'

    def handle(self, *args, **options):
        from verification.models import SystemConfig
        defaults = [
            ('verification_threshold', '0.75', 'Cosine similarity threshold for face matching'),
        ]
        for key, value, desc in defaults:
            obj, created = SystemConfig.objects.get_or_create(
                key=key,
                defaults={'value': value, 'description': desc}
            )
            status = 'Created' if created else 'Already exists'
            self.stdout.write(f'{status}: {key} = {obj.value}')
        self.stdout.write(self.style.SUCCESS('System config initialized.'))
