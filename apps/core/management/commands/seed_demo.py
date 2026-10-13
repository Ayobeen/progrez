from django.core.management.base import BaseCommand
from apps.core.seed import seed_content, reset_persona, PERSONAS
from apps.core.models import User

class Command(BaseCommand):
    help = 'Seed curated content and missing demo learners; --reset resets demo learners only.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true')

    def handle(self, *args, **options):
        seed_content()
        for slug in PERSONAS:
            if options['reset'] or not User.objects.filter(username=slug).exists():
                reset_persona(slug)
        self.stdout.write(self.style.SUCCESS('2 courses, 24 missions, 12 competencies, 20 interventions, and 4 demo learners ready.'))
