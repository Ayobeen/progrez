import secrets
from django.conf import settings
from django.db import models
from django.utils import timezone


def new_slug():
    return secrets.token_hex(8)


def new_nonce():
    return secrets.token_urlsafe(24)


class JourneyPrivacy(models.Model):
    course = models.ForeignKey('core.Course', on_delete=models.PROTECT, default=1)
    learner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='journey_privacies')
    slug = models.SlugField(max_length=60, unique=True, default=new_slug)
    display_name = models.CharField(max_length=100, blank=True)
    visibility = models.CharField(max_length=10, choices=[('PRIVATE', 'Private'), ('CIRCLE', 'My Circle'), ('PUBLIC', 'Public')], default='PRIVATE')
    show_goal = models.BooleanField(default=False)
    show_course = models.BooleanField(default=True)
    show_progress = models.BooleanField(default=True)
    show_competencies = models.BooleanField(default=True)
    show_milestones = models.BooleanField(default=True)
    show_achievements = models.BooleanField(default=True)
    allow_encouragement = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)


    class Meta:
        constraints = [models.UniqueConstraint(fields=['learner', 'course'], name='unique_course_privacy')]


class SupportMember(models.Model):
    course = models.ForeignKey('core.Course', on_delete=models.PROTECT, default=1)
    learner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='circle_members')
    name = models.CharField(max_length=80)
    relationship = models.CharField(max_length=20, default='SUPPORTER')
    status = models.CharField(max_length=20, default='INVITED')
    circle_access = models.BooleanField(default=False)
    access_nonce = models.CharField(max_length=64, default=new_nonce)
    notify_milestones = models.BooleanField(default=True)
    notify_competencies = models.BooleanField(default=True)
    notifications_enabled = models.BooleanField(default=True)
    muted_by_learner = models.BooleanField(default=False)
    email = models.EmailField(blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    email_updates = models.BooleanField(default=False)
    visitor_hash = models.CharField(max_length=64, blank=True)
    network_hash = models.CharField(max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(null=True, blank=True)


class Achievement(models.Model):
    course = models.ForeignKey('core.Course', on_delete=models.PROTECT, default=1)
    learner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='circle_achievements')
    event_key = models.CharField(max_length=100)
    kind = models.CharField(max_length=30)
    title = models.CharField(max_length=180)
    milestone = models.ForeignKey('core.Milestone', null=True, blank=True, on_delete=models.CASCADE)
    occurred_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-occurred_at', '-id']
        constraints = [models.UniqueConstraint(fields=['learner', 'course', 'event_key'], name='circle_unique_achievement')]


class CircleNotification(models.Model):
    member = models.ForeignKey(SupportMember, on_delete=models.CASCADE, related_name='notifications')
    achievement = models.ForeignKey(Achievement, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [models.UniqueConstraint(fields=['member', 'achievement'], name='circle_unique_notification')]


ENCOURAGEMENTS = {
    'PROUD': 'Proud of you!',
    'KEEP_GOING': 'Keep going!',
    'GREAT': 'You’re doing great!',
    'YOU_GOT_THIS': 'You’ve got this!',
    'CONGRATS': 'Congratulations!',
}


class Encouragement(models.Model):
    course = models.ForeignKey('core.Course', on_delete=models.PROTECT, default=1)
    learner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='encouragements')
    member = models.ForeignKey(SupportMember, on_delete=models.SET_NULL, null=True, blank=True)
    achievement = models.ForeignKey(Achievement, on_delete=models.SET_NULL, null=True, blank=True)
    kind = models.CharField(max_length=20, choices=[(k, v) for k, v in ENCOURAGEMENTS.items()])
    visitor_hash = models.CharField(max_length=64, db_index=True)
    network_hash = models.CharField(max_length=64, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    seen_at = models.DateTimeField(null=True, blank=True)
    hidden = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at', '-id']


class CircleEmail(models.Model):
    """Outbox metadata only: message content is built under current privacy at delivery time."""
    member = models.ForeignKey(SupportMember, on_delete=models.CASCADE)
    notification = models.OneToOneField(CircleNotification, null=True, blank=True, on_delete=models.CASCADE)
    kind = models.CharField(max_length=20)
    state = models.CharField(max_length=15, default='PENDING')
    email_fingerprint = models.CharField(max_length=64)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
