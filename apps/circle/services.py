from apps.core.courses import course_for, bind_course
import hashlib
import secrets
from datetime import timedelta
from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from django.utils.crypto import salted_hmac
from apps.core.models import GoalContract, LearningPath, LearnerMission, LearnerCompetency, Milestone, Mission, LearnerSignal
from .models import JourneyPrivacy, SupportMember, Achievement, CircleNotification, Encouragement, CircleEmail, ENCOURAGEMENTS

SHARE_FIELDS = ('show_goal', 'show_course', 'show_progress', 'show_competencies', 'show_milestones', 'show_achievements')
ACTIVE_STATUSES = ('INVITED', 'ACTIVE')


def privacy_for(user, course=None):
    return JourneyPrivacy.objects.get_or_create(learner=user, course=course or course_for(user), defaults={'display_name': user.name or 'Learner'})[0]


def supporter_token(member):
    return signing.dumps({'id': member.id, 'nonce': member.access_nonce}, salt='progrez-circle-access', compress=True)


def supporter_path(member):
    return '/support/' + supporter_token(member)


def resolve_member(token):
    try:
        data = signing.loads(token, salt='progrez-circle-access', max_age=90*86400)
        return SupportMember.objects.select_related('learner').get(id=data['id'], access_nonce=data['nonce'], status__in=ACTIVE_STATUSES)
    except (signing.BadSignature, KeyError, TypeError, ValueError, SupportMember.DoesNotExist):
        raise Http404('This support link is no longer available.')


def session_member(request, privacy):
    mid = request.session.get('circle_members', {}).get(privacy.slug)
    if not isinstance(mid, int):
        return None
    return SupportMember.objects.filter(id=mid, learner_id=privacy.learner_id, course=privacy.course).first()


def remember_member(request, privacy, member):
    members = request.session.get('circle_members', {})
    members[privacy.slug] = member.id
    request.session['circle_members'] = members


def can_view(privacy, member=None):
    if privacy.visibility == 'PRIVATE':
        return False
    if privacy.visibility == 'PUBLIC':
        return member is None or member.course_id == privacy.course_id
    return bool(member and member.learner_id == privacy.learner_id and member.course_id == privacy.course_id and member.circle_access and member.status in ACTIVE_STATUSES)


def ensure_view(privacy, member=None):
    if not can_view(privacy, member):
        raise Http404('This journey is not available through this link.')


def actor_hashes(request):
    if 'circle_visitor' not in request.session:
        request.session['circle_visitor'] = secrets.token_urlsafe(24)
    visitor = salted_hmac('circle-visitor', request.session['circle_visitor']).hexdigest()
    # Do not trust client-supplied forwarding headers. No raw IP address is stored.
    network = salted_hmac('circle-network', request.META.get('REMOTE_ADDR', 'unknown')).hexdigest()
    return visitor, network


def achievement_payload(achievement, privacy, member=None, for_notification=False):
    if achievement.course_id != privacy.course_id or achievement.learner_id != privacy.learner_id:
        return None
    if not privacy.show_achievements:
        return None
    if achievement.kind == 'MILESTONE':
        show_milestone = privacy.show_milestones and (not for_notification or member.notify_milestones)
        show_competency = privacy.show_competencies and (not for_notification or member.notify_competencies)
        if not (show_milestone or show_competency) or not achievement.milestone:
            return None
        title = f'{achievement.milestone.title} completed' if show_milestone else f'Skill demonstrated: {achievement.milestone.competency.name}'
        skill = achievement.milestone.competency.name if show_competency else None
    else:
        if for_notification:
            return None  # MVP subscriptions notify only on milestone/competency completion.
        title, skill = achievement.title, None
    result = {'id': achievement.id, 'title': title, 'kind': achievement.kind, 'at': achievement.occurred_at}
    if skill:
        result['competency'] = skill
    return result


def public_projection(privacy):
    """Allowlist only. Never serialize a model or reuse the private dashboard payload."""
    user = bind_course(privacy.learner, privacy.course)
    data = {'slug': privacy.slug, 'name': privacy.display_name or user.name or 'Learner', 'visibility': privacy.visibility, 'allow_encouragement': privacy.allow_encouragement}
    if privacy.show_goal:
        goal = GoalContract.objects.filter(user=user, course=course_for(user), status='ACTIVE').last()
        data['goal'] = goal.goal if goal else None
    if privacy.show_course:
        path = LearningPath.objects.filter(user=user, course=course_for(user), status='ACTIVE').last()
        data['course'] = privacy.course.title
    if privacy.show_progress:
        total = Mission.objects.filter(milestone__course=course_for(user)).count()
        complete = LearnerMission.objects.filter(user=user, course=course_for(user), status='COMPLETED').count()
        data['progress'] = round(complete/max(total, 1)*100)
    if privacy.show_competencies:
        data['competencies'] = list(LearnerCompetency.objects.filter(user=user, course=course_for(user), status='DEMONSTRATED').values_list('competency__name', flat=True))
        data['competencies_total'] = Milestone.objects.filter(course=course_for(user)).count()
    if privacy.show_milestones:
        demonstrated = set(LearnerCompetency.objects.filter(user=user, course=course_for(user), status='DEMONSTRATED').values_list('competency_id', flat=True))
        data['milestones'] = [{'title': m.title, 'completed': m.competency_id in demonstrated} for m in Milestone.objects.filter(course=course_for(user))]
    if privacy.show_achievements:
        achievements = Achievement.objects.filter(learner=user, course=course_for(user)).select_related('milestone__competency')[:30]
        data['achievements'] = [payload for a in achievements if (payload := achievement_payload(a, privacy)) is not None]
    return data


def inbox(user):
    query = Encouragement.objects.filter(learner=user, course=course_for(user), hidden=False).exclude(member__status='BLOCKED').select_related('member')
    return {
        'unread': query.filter(seen_at__isnull=True).count(),
        'total': query.count(),
        'supporters': SupportMember.objects.filter(learner=user, course=course_for(user), status='ACTIVE').count(),
        'messages': [{'id': e.id, 'name': e.member.name if e.member else 'A supporter', 'relationship': e.member.relationship if e.member else '', 'kind': e.kind, 'message': ENCOURAGEMENTS[e.kind], 'at': e.created_at, 'seen': e.seen_at is not None} for e in query[:20]],
    }


def supporter_notifications(member, privacy):
    ensure_view(privacy, member)
    rows = CircleNotification.objects.filter(member=member).select_related('achievement__milestone__competency')[:30]
    return [{'id': n.id, 'achievement': payload, 'seen': n.seen_at is not None} for n in rows if (payload := achievement_payload(n.achievement, privacy, member, True)) is not None]


def email_fingerprint(email):
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def queue_verification(member):
    if not settings.CIRCLE_EMAIL_ENABLED or not member.email or not member.email_updates:
        return
    if CircleEmail.objects.filter(member=member, kind='VERIFY', created_at__gte=timezone.now()-timedelta(hours=24)).count() < 3:
        CircleEmail.objects.get_or_create(member=member, kind='VERIFY', state='PENDING', email_fingerprint=email_fingerprint(member.email))


def queue_notifications(achievement):
    privacy = privacy_for(achievement.learner, achievement.course)
    if privacy.visibility == 'PRIVATE':
        return
    for member in SupportMember.objects.filter(learner=achievement.learner, course=achievement.course, status='ACTIVE', notifications_enabled=True, muted_by_learner=False):
        if not can_view(privacy, member) or not achievement_payload(achievement, privacy, member, True):
            continue
        notification, created = CircleNotification.objects.get_or_create(member=member, achievement=achievement)
        if created and settings.CIRCLE_EMAIL_ENABLED and member.email_updates and member.email_verified_at:
            CircleEmail.objects.create(member=member, notification=notification, kind='MILESTONE', email_fingerprint=email_fingerprint(member.email))


def record_achievements(user, notify=True):
    completed = LearnerMission.objects.filter(user=user, course=course_for(user), status='COMPLETED')
    first = completed.order_by('completed_at', 'id').first()
    if first:
        Achievement.objects.get_or_create(learner=user, course=course_for(user), event_key='first-mission', defaults={'kind': 'FIRST_MISSION', 'title': 'First mission completed', 'occurred_at': first.completed_at or timezone.now()})
    for evidence in LearnerCompetency.objects.filter(user=user, course=course_for(user), status='DEMONSTRATED').select_related('competency'):
        milestone = Milestone.objects.filter(course=course_for(user), competency=evidence.competency).first()
        if milestone:
            achievement, created = Achievement.objects.get_or_create(learner=user, course=course_for(user), event_key=f'milestone:{milestone.id}', defaults={'kind': 'MILESTONE', 'title': f'{milestone.title} completed', 'milestone': milestone, 'occurred_at': evidence.demonstrated_at or timezone.now()})
            if created and notify:
                queue_notifications(achievement)
    total = Milestone.objects.filter(course=course_for(user)).count()
    demonstrated = LearnerCompetency.objects.filter(user=user, course=course_for(user), status='DEMONSTRATED').count()
    if total and demonstrated >= total:
        Achievement.objects.get_or_create(learner=user, course=course_for(user), event_key='path-complete', defaults={'kind': 'PATH_COMPLETED', 'title': f'{course_for(user).title} completed'})


def support_signal(user, kind, **metadata):
    LearnerSignal.objects.create(user=user, course=course_for(user), signal_type=kind, metadata=metadata, recorded_at=timezone.now())


def record_resumption(user):
    latest = Encouragement.objects.filter(learner=user, course=course_for(user), hidden=False, created_at__gte=timezone.now()-timedelta(days=7)).exclude(member__status='BLOCKED').first()
    if latest and not LearnerSignal.objects.filter(user=user, course=course_for(user), signal_type='LEARNING_RESUMED_AFTER_ENCOURAGEMENT', metadata__encouragement_id=latest.id).exists():
        support_signal(user, 'LEARNING_RESUMED_AFTER_ENCOURAGEMENT', encouragement_id=latest.id, interpretation='Temporal association only; not evidence of causation.')


def verification_token(member):
    return signing.dumps({'id': member.id, 'nonce': member.access_nonce, 'email': email_fingerprint(member.email)}, salt='progrez-circle-email', compress=True)


def verify_email_token(token, confirm=False):
    try:
        data = signing.loads(token, salt='progrez-circle-email', max_age=48*3600)
        member = SupportMember.objects.get(id=data['id'], access_nonce=data['nonce'], status='ACTIVE', email_updates=True)
        if not secrets.compare_digest(data['email'], email_fingerprint(member.email)):
            raise ValueError()
        ensure_view(privacy_for(member.learner, member.course), member)
    except (signing.BadSignature, SupportMember.DoesNotExist, KeyError, TypeError, ValueError):
        raise Http404('This email confirmation link is no longer available.')
    if confirm and member.email_verified_at is None:
        member.email_verified_at = timezone.now()
        member.save(update_fields=['email_verified_at'])
    return member


@transaction.atomic
def deliver_email(delivery):
    """Called only by the explicit worker command. Recheck privacy immediately before sending."""
    from django.contrib.auth import get_user_model
    get_user_model().objects.select_for_update().get(id=delivery.member.learner_id)
    delivery = CircleEmail.objects.select_for_update().select_related('member__learner', 'notification__achievement__milestone__competency').get(id=delivery.id)
    if delivery.state != 'PENDING' or not settings.CIRCLE_EMAIL_ENABLED:
        return False
    member = delivery.member
    privacy = privacy_for(member.learner, member.course)
    eligible = member.status == 'ACTIVE' and member.email_updates and member.email and email_fingerprint(member.email) == delivery.email_fingerprint and can_view(privacy, member)
    if delivery.kind == 'VERIFY':
        eligible = eligible and member.email_verified_at is None and delivery.created_at >= timezone.now()-timedelta(hours=48)
        subject = 'Confirm your Progrez Circle updates'
        body = 'You requested milestone updates from Progrez. Confirm your email to enable them:\n\n' + settings.CIRCLE_SITE_ORIGIN.rstrip('/') + '/circle/verify/' + verification_token(member)
    else:
        payload = achievement_payload(delivery.notification.achievement, privacy, member, True) if delivery.notification else None
        eligible = eligible and bool(member.email_verified_at) and member.notifications_enabled and not member.muted_by_learner and bool(payload)
        subject = 'A learning milestone to celebrate'
        body = f"{privacy.display_name or member.learner.name} reached a learning milestone.\n\n{payload['title'] if payload else ''}\n\nOpen your private Circle inbox to encourage them or turn off updates:\n" + settings.CIRCLE_SITE_ORIGIN.rstrip('/') + supporter_path(member)
    if not eligible:
        delivery.state = 'SKIPPED'
        delivery.save(update_fields=['state'])
        return False
    delivery.attempts += 1
    try:
        sent = send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [member.email], fail_silently=False)
        if sent != 1:
            raise RuntimeError('EmailBackendDidNotSend')
        delivery.state, delivery.sent_at, delivery.last_error = 'SENT', timezone.now(), ''
    except Exception as exc:
        delivery.last_error = type(exc).__name__[:100]
        if delivery.attempts >= 3:
            delivery.state = 'FAILED'
    delivery.save()
    return delivery.state == 'SENT'
