from django.core.management.base import BaseCommand
from django.db import transaction
from apps.core.models import User, LearnerProfile, LearnerSignal
from apps.core.courses import bind_course
from apps.engines.momentum import calculate_momentum
from apps.engines.friction import detect_friction

class Command(BaseCommand):
    help = 'Refresh momentum and detect inactivity for established course journeys. Suitable for an hourly scheduler.'

    def handle(self, *args, **options):
        count = 0
        for profile in LearnerProfile.objects.select_related('course').filter(user__is_active=True):
            if not LearnerSignal.objects.filter(user_id=profile.user_id, course=profile.course).exists():
                continue
            with transaction.atomic():
                user = bind_course(User.objects.select_for_update().get(id=profile.user_id), profile.course)
                detect_friction(user, calculate_momentum(user))
                count += 1
        self.stdout.write(f'Refreshed {count} course journeys.')
