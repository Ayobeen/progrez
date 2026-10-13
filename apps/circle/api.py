from apps.core.courses import course_for, bind_request, bind_course
from datetime import timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction, IntegrityError
from django.db.models import Q
from django.forms.models import model_to_dict
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError, Throttled
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.views import APIView
from .models import JourneyPrivacy, SupportMember, Achievement, Encouragement, CircleNotification, ENCOURAGEMENTS, new_nonce
from .services import privacy_for, public_projection, can_view, ensure_view, session_member, remember_member, actor_hashes, resolve_member, supporter_path, supporter_notifications, inbox, achievement_payload, queue_verification, support_signal, verify_email_token, SHARE_FIELDS, ACTIVE_STATUSES

User = get_user_model()
RELATIONSHIPS = ['SUPPORTER', 'PARENT', 'SIBLING', 'SPOUSE', 'FRIEND', 'MENTOR', 'TEACHER', 'OTHER']


class CircleReadThrottle(AnonRateThrottle):
    rate = '600/hour'


class CircleUserThrottle(UserRateThrottle):
    rate = '1200/hour'


class StrictInput(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise ValidationError('Send a JSON object.')
        unknown = set(data) - set(self.fields)
        if unknown:
            raise ValidationError({'detail': 'Unsupported fields: ' + ', '.join(sorted(unknown))})
        return super().to_internal_value(data)


class PrivacyInput(StrictInput):
    display_name = serializers.CharField(max_length=100)
    slug = serializers.RegexField(r'^[a-z0-9]+(?:-[a-z0-9]+)*$', min_length=3, max_length=60)
    visibility = serializers.ChoiceField(choices=['PRIVATE', 'CIRCLE', 'PUBLIC'])
    show_goal = serializers.BooleanField()
    show_course = serializers.BooleanField()
    show_progress = serializers.BooleanField()
    show_competencies = serializers.BooleanField()
    show_milestones = serializers.BooleanField()
    show_achievements = serializers.BooleanField()
    allow_encouragement = serializers.BooleanField()


class InviteInput(StrictInput):
    name = serializers.CharField(max_length=80)
    relationship = serializers.ChoiceField(choices=RELATIONSHIPS, default='SUPPORTER')


class FollowInput(StrictInput):
    name = serializers.CharField(max_length=80)
    notify_milestones = serializers.BooleanField(default=True)
    notify_competencies = serializers.BooleanField(default=True)
    notifications_enabled = serializers.BooleanField(default=True)
    email = serializers.EmailField(allow_blank=True, default='')
    email_updates = serializers.BooleanField(default=False)
    website = serializers.CharField(allow_blank=True, default='', max_length=100)


class EncourageInput(StrictInput):
    kind = serializers.ChoiceField(choices=list(ENCOURAGEMENTS))
    achievement_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    website = serializers.CharField(allow_blank=True, default='', max_length=100)


def validate(cls, data):
    s = cls(data=data)
    s.is_valid(raise_exception=True)
    result = s.validated_data
    if result.pop('website', ''):
        raise ValidationError('This request could not be accepted.')
    return result


def owner(request):
    if not request.user.is_authenticated:
        raise PermissionDenied('Sign in as the learner to manage this Circle.')
    return bind_request(request, request.user)


def member_preferences(member):
    return {'name': member.name, 'status': member.status, 'notify_milestones': member.notify_milestones, 'notify_competencies': member.notify_competencies, 'notifications_enabled': member.notifications_enabled, 'email': member.email, 'email_updates': member.email_updates, 'email_verified': member.email_verified_at is not None, 'muted_by_learner': member.muted_by_learner}


def public_response(request, privacy, member=None):
    ensure_view(privacy, member)
    data = {'profile': public_projection(privacy), 'encouragement_types': ENCOURAGEMENTS, 'email_available': settings.CIRCLE_EMAIL_ENABLED, 'is_owner': bool(request.user.is_authenticated and request.user.id == privacy.learner_id)}
    if member and member.status in ACTIVE_STATUSES:
        data['supporter'] = member_preferences(member)
        data['support_path'] = supporter_path(member)
        data['notifications'] = supporter_notifications(member, privacy)
    else:
        data['supporter'], data['notifications'] = None, []
    return data


def apply_preferences(member, values):
    if values['email_updates'] and (not settings.CIRCLE_EMAIL_ENABLED or not values['email']):
        raise ValidationError('Email updates require an email address and a configured email service.')
    if values['email'] and not settings.CIRCLE_EMAIL_ENABLED:
        raise ValidationError('Email delivery is not configured. You can follow through your private Circle inbox.')
    normalized = values['email'].strip().lower()
    if normalized != member.email:
        member.email_verified_at = None
    member.name = values['name']
    member.notify_milestones = values['notify_milestones']
    member.notify_competencies = values['notify_competencies']
    member.notifications_enabled = values['notifications_enabled']
    member.email, member.email_updates = normalized, values['email_updates']
    member.save()
    if member.email_updates and not member.email_verified_at:
        queue_verification(member)


def send_encouragement(request, privacy, member, values):
    ensure_view(privacy, member)
    if not privacy.allow_encouragement:
        raise PermissionDenied('Encouragement is turned off for this journey.')
    if member and member.status not in ACTIVE_STATUSES:
        raise PermissionDenied('This support relationship is no longer active.')
    if request.user.is_authenticated and request.user.id == privacy.learner_id:
        raise ValidationError('You can preview this profile, but encouragement is for your supporters to send.')
    visitor, network = actor_hashes(request)
    if SupportMember.objects.filter(learner=privacy.learner, course=privacy.course, visitor_hash=visitor, status='BLOCKED').exists():
        raise PermissionDenied('Encouragement is unavailable.')
    since = timezone.now()-timedelta(hours=24)
    recent = Encouragement.objects.filter(learner=privacy.learner, course=privacy.course, created_at__gte=since)
    previous = recent.filter(Q(member=member) | Q(visitor_hash=visitor)).first() if member else recent.filter(visitor_hash=visitor).first()
    if previous:
        if previous.kind == values['kind'] and previous.achievement_id == values.get('achievement_id'):
            return {'sent': True, 'already_sent': True}
        raise Throttled(wait=max(1, int((previous.created_at+timedelta(hours=24)-timezone.now()).total_seconds())), detail='Your encouragement is already there. You can send another tomorrow.')
    if Encouragement.objects.filter(learner=privacy.learner, course=privacy.course, network_hash=network, created_at__gte=timezone.now()-timedelta(hours=1)).count() >= 10:
        raise Throttled(wait=3600, detail='This journey has received several messages from this connection. Please try again later.')
    achievement = None
    if values.get('achievement_id'):
        achievement = get_object_or_404(Achievement, id=values['achievement_id'], learner=privacy.learner, course=privacy.course)
        if not achievement_payload(achievement, privacy):
            raise Http404()
    message = Encouragement.objects.create(learner=privacy.learner, course=privacy.course, member=member, achievement=achievement, kind=values['kind'], visitor_hash=visitor, network_hash=network)
    support_signal(bind_course(privacy.learner, privacy.course), 'ENCOURAGEMENT_RECEIVED', encouragement_id=message.id, member_id=member.id if member else None)
    return {'sent': True, 'already_sent': False}


@method_decorator(csrf_protect, name='dispatch')
class CircleAPI(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [CircleReadThrottle, CircleUserThrottle]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'no-store, private'
        response['Referrer-Policy'] = 'no-referrer'
        response['X-Robots-Tag'] = 'noindex, nofollow'
        return response

    def get(self, request, route='manage'):
        parts = route.strip('/').split('/')
        if parts[0] == 'manage':
            user = owner(request)
            privacy = privacy_for(user)
            members = SupportMember.objects.filter(learner=user, course=course_for(user)).order_by('-id')[:100]
            return Response({'privacy': model_to_dict(privacy, exclude=['learner', 'id', 'course']), 'profile_path': '/u/'+privacy.slug, 'preview': public_projection(privacy), 'members': [{'id': m.id, 'name': m.name, 'relationship': m.relationship, 'status': m.status, 'circle_access': m.circle_access, 'muted': m.muted_by_learner, 'notify_milestones': m.notify_milestones, 'notify_competencies': m.notify_competencies, 'support_path': supporter_path(m) if m.status in ACTIVE_STATUSES else None} for m in members], 'inbox': inbox(user), 'email_available': settings.CIRCLE_EMAIL_ENABLED})
        if parts[0] == 'inbox':
            return Response(inbox(owner(request)))
        if len(parts) == 2 and parts[0] == 'public':
            privacy = get_object_or_404(JourneyPrivacy.objects.select_related('learner'), slug=parts[1])
            return Response(public_response(request, privacy, session_member(request, privacy)))
        if len(parts) == 2 and parts[0] == 'support':
            member = resolve_member(parts[1])
            privacy = privacy_for(member.learner, member.course)
            remember_member(request, privacy, member)
            return Response(public_response(request, privacy, member))
        if len(parts) == 2 and parts[0] == 'verify':
            member = verify_email_token(parts[1])
            return Response({'confirmed': member.email_verified_at is not None})
        return Response({'detail': 'Endpoint not found.'}, status=404)

    @transaction.atomic
    def post(self, request, route):
        parts = route.strip('/').split('/')
        d = request.data
        if parts[0] == 'privacy':
            user = owner(request)
            User.objects.select_for_update().get(id=user.id)
            privacy = privacy_for(user)
            values = validate(PrivacyInput, d)
            if JourneyPrivacy.objects.filter(slug=values['slug']).exclude(id=privacy.id).exists():
                raise ValidationError({'slug': 'This profile link is already in use. Choose another.'})
            for key, value in values.items():
                setattr(privacy, key, value)
            try:
                with transaction.atomic():
                    privacy.save()
            except IntegrityError:
                raise ValidationError({'slug': 'This profile link is already in use. Choose another.'})
            return Response({'saved': True, 'visibility': privacy.visibility, 'profile_path': '/u/'+privacy.slug})
        if parts[0] == 'invite':
            user = owner(request)
            User.objects.select_for_update().get(id=user.id)
            privacy = privacy_for(user)
            if privacy.visibility == 'PRIVATE':
                raise ValidationError('Choose My Circle or Public before creating an invitation.')
            values = validate(InviteInput, d)
            if SupportMember.objects.filter(learner=user, course=course_for(user), status__in=ACTIVE_STATUSES).count() >= 100:
                raise ValidationError('Your Circle has reached the 100-supporter MVP limit.')
            member = SupportMember.objects.create(learner=user, course=course_for(user), circle_access=True, **values)
            return Response({'id': member.id, 'support_path': supporter_path(member)}, status=201)
        if len(parts) == 3 and parts[0] == 'members' and parts[1].isdigit() and parts[2] == 'control':
            user = owner(request)
            User.objects.select_for_update().get(id=user.id)
            member = get_object_or_404(SupportMember, id=int(parts[1]), learner=user, course=course_for(user), status__in=ACTIVE_STATUSES)
            action = d.get('action')
            if action in ('MUTE', 'UNMUTE'):
                member.muted_by_learner = action == 'MUTE'
            elif action in ('REVOKE', 'BLOCK'):
                member.status = 'REVOKED' if action == 'REVOKE' else 'BLOCKED'
                member.notifications_enabled = False
                member.access_nonce = new_nonce()
            elif action in ('GRANT_CIRCLE', 'PUBLIC_ONLY'):
                member.circle_access = action == 'GRANT_CIRCLE'
            else:
                raise ValidationError('Choose a supported Circle action.')
            member.save()
            return Response({'saved': True})
        if len(parts) == 3 and parts[0] == 'encouragement' and parts[1].isdigit():
            user = owner(request)
            User.objects.select_for_update().get(id=user.id)
            message = get_object_or_404(Encouragement, id=int(parts[1]), learner=user, course=course_for(user))
            if parts[2] == 'seen':
                if message.seen_at is None:
                    message.seen_at = timezone.now()
                    message.save(update_fields=['seen_at'])
                    support_signal(user, 'ENCOURAGEMENT_OPENED', encouragement_id=message.id)
            elif parts[2] == 'hide':
                message.hidden = True
                message.save(update_fields=['hidden'])
            else:
                raise Http404()
            return Response({'saved': True})
        if len(parts) == 2 and parts[0] == 'verify':
            member = verify_email_token(parts[1], confirm=True)
            return Response({'confirmed': True, 'support_path': supporter_path(member)})
        if len(parts) == 3 and parts[0] in ('public', 'support'):
            member = resolve_member(parts[1]) if parts[0] == 'support' else None
            privacy = privacy_for(member.learner, member.course) if member else get_object_or_404(JourneyPrivacy.objects.select_related('learner'), slug=parts[1])
            User.objects.select_for_update().get(id=privacy.learner_id)
            # Refresh after the lock so a concurrent privacy change cannot authorize a stale write.
            privacy.refresh_from_db()
            if member:
                member.refresh_from_db()
                if member.status not in ACTIVE_STATUSES:
                    raise Http404()
            else:
                member = session_member(request, privacy)
            action = parts[2]
            if action == 'unsubscribe' and member:
                member.status, member.notifications_enabled = 'UNSUBSCRIBED', False
                member.access_nonce = new_nonce()
                member.save()
                return Response({'unsubscribed': True})
            ensure_view(privacy, member)
            if action == 'encourage':
                return Response(send_encouragement(request, privacy, member, validate(EncourageInput, d)))
            if action == 'follow' and parts[0] == 'public':
                if request.user.is_authenticated and request.user.id == privacy.learner_id:
                    raise ValidationError('This profile is yours. Open your Circle to manage supporters.')
                values = validate(FollowInput, d)
                visitor, network = actor_hashes(request)
                if member and member.status not in ACTIVE_STATUSES:
                    raise PermissionDenied('This support relationship is no longer active.')
                if not member:
                    if SupportMember.objects.filter(network_hash=network, created_at__gte=timezone.now()-timedelta(hours=1)).count() >= 5:
                        raise Throttled(wait=3600, detail='Several follow requests came from this connection. Please try again later.')
                    if SupportMember.objects.filter(learner=privacy.learner, course=privacy.course, status__in=ACTIVE_STATUSES).count() >= 100:
                        raise ValidationError('This Circle has reached its supporter limit.')
                    member = SupportMember.objects.create(learner=privacy.learner, course=privacy.course, name=values['name'], status='ACTIVE', activated_at=timezone.now(), visitor_hash=visitor, network_hash=network)
                elif member.status == 'INVITED':
                    member.status, member.activated_at = 'ACTIVE', timezone.now()
                    member.visitor_hash, member.network_hash = visitor, network
                apply_preferences(member, values)
                remember_member(request, privacy, member)
                return Response({'support_path': supporter_path(member), 'followed': True})
            if action in ('activate', 'preferences') and member and parts[0] == 'support':
                values = validate(FollowInput, d)
                visitor, network = actor_hashes(request)
                if member.status == 'INVITED':
                    member.status, member.activated_at = 'ACTIVE', timezone.now()
                    member.visitor_hash, member.network_hash = visitor, network
                apply_preferences(member, values)
                remember_member(request, privacy, member)
                return Response({'saved': True, 'support_path': supporter_path(member)})
            if action == 'seen' and member:
                nid = d.get('notification_id')
                if type(nid) is not int:
                    raise ValidationError('Choose a notification.')
                row = get_object_or_404(CircleNotification, id=nid, member=member)
                row.seen_at = row.seen_at or timezone.now()
                row.save(update_fields=['seen_at'])
                return Response({'saved': True})
        return Response({'detail': 'Endpoint not found.'}, status=404)
