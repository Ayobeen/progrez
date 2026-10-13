from django.core.management.base import BaseCommand
from django.db import transaction
from apps.core.models import LearnerProfile
from apps.core.courses import bind_course
from apps.circle.services import privacy_for, record_achievements

class Command(BaseCommand):
    help = 'Create private Circle settings and historical achievements without notifying supporters.'

    def handle(self, *args, **options):
        count = 0
        for profile in LearnerProfile.objects.select_related('user', 'course'):
            user = bind_course(profile.user, profile.course)
            with transaction.atomic():
                privacy_for(user)
                record_achievements(user, notify=False)
            count += 1
        self.stdout.write(f'Prepared {count} course Circles; no notifications sent.')
