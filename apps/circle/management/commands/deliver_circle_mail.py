from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from apps.circle.models import CircleEmail
from apps.circle.services import deliver_email

class Command(BaseCommand):
    help = 'Deliver consented, privacy-checked Circle verification and milestone emails through the configured backend.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50)

    def handle(self, *args, **options):
        if not settings.CIRCLE_EMAIL_ENABLED:
            raise CommandError('Email delivery is disabled. Configure the backend, site origin, and CIRCLE_EMAIL_ENABLED first.')
        sent = 0
        limit = max(1, min(options['limit'], 500))
        for delivery in CircleEmail.objects.filter(state='PENDING').order_by('id')[:limit]:
            sent += bool(deliver_email(delivery))
        self.stdout.write(f'Delivered {sent} Circle emails.')
