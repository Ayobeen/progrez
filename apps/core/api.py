from apps.core.courses import course_for, bind_course, bind_request, course_payload, catalog
import json
import logging
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.conf import settings
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction, IntegrityError
from django.db.models import Avg
from django.forms.models import model_to_dict
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework import serializers
from .models import *
from .content import FRICTIONS
from .seed import PERSONAS, reset_persona, signal, simulate
from apps.engines.momentum import calculate_momentum
from apps.engines.friction import detect_friction
from apps.engines.recovery import create_plan, start_plan, finish_plan
from apps.ai.services import generate, DiagnosticInterpretation, FrictionInterpretation, AdaptedExplanation
from apps.circle.services import inbox as circle_inbox, record_achievements, record_resumption

log = logging.getLogger('progrez')

class Registration(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    email = serializers.EmailField(max_length=200)
    password = serializers.CharField(max_length=128)

class GoalInput(serializers.Serializer):
    goal = serializers.CharField(max_length=1000)
    context = serializers.CharField(max_length=2000, allow_blank=True, default='')
    done_when = serializers.CharField(max_length=1000)
    constraints = serializers.ListField(child=serializers.ChoiceField(choices=list(FRICTIONS)), default=list, max_length=10)
    available_minutes_daily = serializers.IntegerField(min_value=5, max_value=180, default=20)
    connectivity_level = serializers.ChoiceField(choices=['LOW', 'MEDIUM', 'HIGH'], default='HIGH')
    confidence_score = serializers.IntegerField(min_value=0, max_value=100, default=60)
    target_date = serializers.DateField(required=False, allow_null=True)

def valid(cls, data):
    s = cls(data=data)
    s.is_valid(raise_exception=True)
    return s.validated_data

def integer(data, key, low, high, default=None):
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValidationError({key: f'Enter a whole number from {low} to {high}.'})
    return value

def public_questions(rubric):
    return [{'prompt': q['prompt'], 'options': q['options']} for q in rubric]

def mark(rubric, answers):
    if not isinstance(answers, list) or len(answers) != len(rubric):
        raise ValidationError('Answer every question before submitting.')
    for a, q in zip(answers, rubric):
        if isinstance(a, bool) or not isinstance(a, int) or a < 0 or a >= len(q['options']):
            raise ValidationError('Choose a valid answer for each question.')
    correct = [a == q['answer'] for a, q in zip(answers, rubric)]
    return round(sum(correct) / len(rubric) * 100, 1), [{'correct': c, 'explanation': q['explanation']} for c, q in zip(correct, rubric)]

def milestone_data(user):
    completed = set(LearnerMission.objects.filter(user=user, course=course_for(user), status='COMPLETED').values_list('mission_id', flat=True))
    opened = True
    result = []
    for m in Milestone.objects.filter(course=course_for(user)).prefetch_related('missions'):
        missions = list(m.missions.all())
        done = all(x.id in completed for x in missions)
        status = 'DEMONSTRATED' if done else ('IN_PROGRESS' if any(x.id in completed for x in missions) else 'AVAILABLE') if opened else 'LOCKED'
        previous = opened
        rows = []
        for item in missions:
            rows.append({'id': item.id, 'title': item.title, 'description': item.description, 'minutes': item.estimated_minutes, 'completed': item.id in completed, 'available': previous})
            previous = previous and item.id in completed
        result.append({'id': m.id, 'title': m.title, 'description': m.description, 'sequence': m.sequence, 'status': status, 'competency': m.competency.name, 'missions': rows})
        opened = opened and done
    return result

def accessible_mission(user, mid):
    for milestone in milestone_data(user):
        for item in milestone['missions']:
            if item['id'] == mid and (item['available'] or item['completed']):
                return get_object_or_404(Mission, id=mid)
    raise PermissionDenied('Complete the preceding mission to unlock this one.')

def serialize_plan(plan):
    if not plan:
        return None
    return {'id': plan.id, 'title': plan.title, 'reason': plan.reason, 'status': plan.status, 'strategy': plan.intervention.strategy_type, 'friction': plan.friction_event.primary_friction, 'started_at': plan.started_at, **plan.plan_json}

def serialize_friction(event):
    if not event:
        return None
    return {'id': event.id, 'type': event.primary_friction, 'label': FRICTIONS[event.primary_friction], 'confidence': event.confidence, 'candidates': event.candidate_friction, 'evidence': event.evidence, 'status': event.status, 'confirmation_question': event.confirmation_question}

def state(user):
    latest = MomentumSnapshot.objects.filter(user=user, course=course_for(user)).order_by('-id').first()
    if latest is None or latest.calculated_at < timezone.now() - timedelta(minutes=30):
        # Returning after an absence must update the score even before a new mission starts.
        with transaction.atomic():
            User.objects.select_for_update().get(id=user.id)
            latest = MomentumSnapshot.objects.filter(user=user, course=course_for(user)).order_by('-id').first()
            if latest is None or latest.calculated_at < timezone.now() - timedelta(minutes=30):
                detect_friction(user, calculate_momentum(user))
    p = user.profile
    goal = GoalContract.objects.filter(user=user, course=course_for(user), status='ACTIVE').last()
    diagnostic = DiagnosticSession.objects.filter(user=user, course=course_for(user), completed_at__isnull=False).last()
    milestones = milestone_data(user)
    missions = [q for m in milestones for q in m['missions']]
    next_mission = next((q for q in missions if q['available'] and not q['completed']), None)
    snapshots = list(MomentumSnapshot.objects.filter(user=user, course=course_for(user)).order_by('-id')[:12])[::-1]
    event = FrictionEvent.objects.filter(user=user, course=course_for(user), status__in=['AWAITING_CONFIRMATION', 'CONFIRMED', 'INTERVENTION_STARTED']).last()
    plan = RecoveryPlan.objects.filter(user=user, course=course_for(user)).exclude(status='SUPERSEDED').last()
    result = RecoveryResult.objects.filter(recovery_plan__user=user, recovery_plan__course=course_for(user)).last()
    return {
        'course': course_payload(course_for(user)), 'courses': catalog(user),
        'user': {'is_staff': user.is_staff, 'name': user.name, 'email': user.email, 'is_demo': user.is_demo, 'username': user.username, 'language': user.preferred_language, 'timezone': user.timezone},
        'profile': model_to_dict(p, exclude=['id', 'user']), 'goal': model_to_dict(goal, exclude=['user']) if goal else None,
        'diagnosed': bool(diagnostic), 'milestones': milestones, 'next_mission': next_mission,
        'progress': round(sum(q['completed'] for q in missions) / max(1, len(missions)) * 100),
        'completed_missions': sum(q['completed'] for q in missions),
        'competencies': list(LearnerCompetency.objects.filter(user=user, course=course_for(user)).values('competency__name', 'competency__code', 'status', 'score', 'demonstrated_at')),
        'momentum': {'score': p.current_momentum_score, 'state': p.current_state, 'history': [{'score': s.score, 'state': s.state, 'at': s.calculated_at} for s in snapshots], 'components': {k: getattr(snapshots[-1], k) for k in ['consistency_score', 'completion_score', 'assessment_score', 'engagement_score', 'confidence_score']} if snapshots else {}},
        'friction': serialize_friction(event), 'plan': serialize_plan(plan), 'result': model_to_dict(result) if result else None,
        'signals': list(LearnerSignal.objects.filter(user=user, course=course_for(user)).order_by('-id').values('signal_type', 'numeric_value', 'recorded_at')[:10]),
        'demo_mode': settings.DEMO_MODE, 'friction_types': FRICTIONS, 'circle': circle_inbox(user),
    }

def account_state(user):
    return {'user': {'name': user.name, 'email': user.email, 'is_demo': user.is_demo, 'is_staff': user.is_staff, 'username': user.username}, 'courses': catalog(user), 'course': None, 'demo_mode': settings.DEMO_MODE}

@method_decorator(csrf_protect, name='dispatch')
class API(APIView):
    permission_classes = [AllowAny]

    def get(self, request, route='state'):
        route = route.strip('/')
        if route == 'auth/me' and not request.user.is_authenticated:
            return Response({'authenticated': False, 'demo_mode': settings.DEMO_MODE})
        if not request.user.is_authenticated:
            raise PermissionDenied('Sign in to continue.')
        if route == 'auth/me' and not (request.headers.get('X-Progrez-Course') or request.session.get('course_slug')):
            return Response(account_state(request.user))
        u = request.user
        if route == 'courses':
            return Response({'courses': catalog(u), 'can_add_course': u.has_perm('core.add_course')})
        bind_request(request, u)
        DIAGNOSTIC = course_for(u).diagnostic
        if route in ('state', 'auth/me'):
            return Response(state(u))
        if route == 'goals/current':
            return Response(state(u)['goal'])
        if route in ('paths/current', 'competencies', 'competencies/progress') or route.startswith('paths/'):
            return Response(state(u)['milestones'])
        if route == 'missions/today':
            return Response(state(u)['next_mission'])
        parts = route.split('/')
        if parts[0] == 'missions' and len(parts) == 2 and parts[1].isdigit():
            mission = accessible_mission(u, int(parts[1]))
            data = model_to_dict(mission)
            data['questions'] = public_questions(mission.assessment.rubric)
            data['assessment_id'] = mission.assessment.id
            data['pass_score'] = mission.assessment.pass_score
            data['progress'] = model_to_dict(LearnerMission.objects.filter(user=u, course=course_for(u), mission=mission).first()) if LearnerMission.objects.filter(user=u, course=course_for(u), mission=mission).exists() else None
            return Response(data)
        if parts[0] == 'assessments' and len(parts) >= 2 and parts[1].isdigit():
            assessment = get_object_or_404(Assessment, id=int(parts[1]))
            accessible_mission(u, assessment.mission_id)
            if len(parts) == 3 and parts[2] == 'results':
                return Response(list(AssessmentAttempt.objects.filter(user=u, course=course_for(u), assessment=assessment).values('score', 'feedback', 'passed', 'created_at')))
            return Response({'id': assessment.id, 'title': assessment.title, 'questions': public_questions(assessment.rubric)})
        if route == 'diagnostics/latest':
            diagnostic = DiagnosticSession.objects.filter(user=u, course=course_for(u)).last()
            return Response({'session': model_to_dict(diagnostic) if diagnostic else None, 'questions': public_questions(DIAGNOSTIC)})
        if route.startswith('momentum/'):
            return Response(state(u)['momentum'])
        if route == 'comeback/status':
            return Response(state(u)['friction'])
        if route == 'comeback/plan':
            return Response(state(u)['plan'])
        if route == 'comeback/result':
            return Response(state(u)['result'])
        if route == 'comeback/download':
            plan = get_object_or_404(RecoveryPlan.objects.filter(user=u, course=course_for(u)).order_by('-id')[:1])
            content = plan.title + '\n\n' + plan.reason + '\n\n' + '\n\n'.join(f"{s['id']}. {s['title']} ({s['minutes']} min)\n{s['content']}" for s in plan.plan_json['steps'])
            return HttpResponse(content, content_type='text/plain; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="progrez-recovery-plan.txt"'})
        if route == 'analytics':
            if not (u.is_staff or settings.DEMO_MODE and u.is_demo):
                raise PermissionDenied()
            learners = User.objects.filter(is_demo=True) if u.is_demo else User.objects.filter(is_demo=False)
            results = RecoveryResult.objects.filter(recovery_plan__user__in=learners, recovery_plan__course=course_for(u))
            count = results.count()
            return Response({'scope': 'Demo learners' if u.is_demo else 'Learners', 'learners': learners.count(), 'at_risk': LearnerProfile.objects.filter(user__in=learners, course=course_for(u), current_state__in=['AT_RISK', 'DISENGAGED']).count(), 'events': FrictionEvent.objects.filter(user__in=learners, course=course_for(u)).count(), 'interventions': RecoveryPlan.objects.filter(user__in=learners, course=course_for(u), started_at__isnull=False).count(), 'recovery_success_rate': round(results.filter(recovery_score__gte=60).count()/count*100) if count else None, 'average_momentum_change': round(sum(r.post_momentum-r.pre_momentum for r in results)/count, 1) if count else None, 'outcomes': list(results.values('outcome', 'recovery_score', 'created_at'))})
        return Response({'detail': 'Endpoint not found.'}, status=404)

    def patch(self, request, route):
        return self.post(request, route)

    @transaction.atomic
    def post(self, request, route):
        route = route.strip('/')
        d = request.data
        if route == 'auth/register':
            data = valid(Registration, d)
            email = data['email'].lower()
            if User.objects.filter(email__iexact=email).exists():
                raise ValidationError('An account with this email already exists.')
            user = User(username=email, email=email, name=data['name'])
            try:
                validate_password(data['password'], user)
            except DjangoValidationError as exc:
                raise ValidationError({'password': exc.messages})
            user.set_password(data['password'])
            try:
                with transaction.atomic():
                    user.save()
            except IntegrityError:
                raise ValidationError('An account with this email already exists.')
            login(request, user)
            request.session.pop('course_slug', None)
            return Response(account_state(user), status=201)
        if route == 'auth/login':
            email = str(d.get('email', '')).lower()
            record = User.objects.filter(email__iexact=email, is_demo=False).first()
            user = authenticate(request, username=record.username if record else email, password=d.get('password', ''))
            if not user:
                raise ValidationError('Email or password is incorrect.')
            login(request, user)
            request.session.pop('course_slug', None)
            return Response(account_state(user))
        if route == 'demo/login':
            if not settings.DEMO_MODE or d.get('persona') not in PERSONAS:
                raise PermissionDenied('Demo mode is unavailable.')
            user = get_object_or_404(User, username=d['persona'], is_demo=True)
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            request.session.pop('course_slug', None)
            return Response(account_state(user))
        if not request.user.is_authenticated:
            raise PermissionDenied('Sign in to continue.')
        u = User.objects.select_for_update().get(id=request.user.id)
        if route not in ('courses/select', 'auth/logout'):
            bind_request(request, u)
        if route == 'courses/select':
            course = get_object_or_404(Course, slug=d.get('slug'), is_published=True)
            bind_course(u, course)
            LearnerProfile.objects.get_or_create(user=u, course=course)
            request.session['course_slug'] = course.slug
            return Response(state(u))
        if route == 'auth/logout':
            logout(request)
            return Response({'ok': True})
        DIAGNOSTIC = course_for(u).diagnostic
        if route == 'demo/reset':
            if not settings.DEMO_MODE or not u.is_demo:
                raise PermissionDenied()
            u = reset_persona(u.username, course_for(u))
            update_session_auth_hash(request, u)
            return Response(state(u))
        if route == 'demo/simulate':
            if not settings.DEMO_MODE or not u.is_demo:
                raise PermissionDenied()
            if d.get('scenario') not in ['persona', 'inactivity', 'decline', 'connectivity', 'failures']:
                raise ValidationError('Choose a supported scenario.')
            simulate(u, d['scenario'])
            return Response(state(u))
        if route == 'goals' or route.startswith('goals/'):
            values = valid(GoalInput, d)
            if route != 'goals':
                gid = route.split('/')[-1]
                goal = get_object_or_404(GoalContract, id=gid, user=u, course=course_for(u))
            else:
                goal = GoalContract.objects.filter(user=u, course=course_for(u), status='ACTIVE').last() or GoalContract(user=u, course=course_for(u))
            p = u.profile
            for key in ['available_minutes_daily', 'connectivity_level', 'confidence_score']:
                setattr(p, key, values.pop(key))
            p.preferred_session_minutes = min(30, p.available_minutes_daily)
            p.save()
            for k, v in values.items():
                setattr(goal, k, v)
            goal.save()
            return Response({'id': goal.id})
        if route == 'diagnostics/start':
            goal = get_object_or_404(GoalContract, user=u, course=course_for(u), status='ACTIVE')
            diagnostic = DiagnosticSession.objects.filter(user=u, course=course_for(u), completed_at__isnull=True).last()
            diagnostic = diagnostic or DiagnosticSession.objects.create(user=u, course=course_for(u), goal_contract=goal)
            return Response({'id': diagnostic.id, 'questions': public_questions(DIAGNOSTIC)})
        parts = route.split('/')
        if len(parts) == 3 and parts[0] == 'diagnostics' and parts[1].isdigit():
            diagnostic = get_object_or_404(DiagnosticSession, user=u, course=course_for(u), id=int(parts[1]))
            if diagnostic.completed_at:
                raise ValidationError('This diagnostic is already complete.')
            if parts[2] == 'answer':
                index = integer(d, 'question', 0, len(DIAGNOSTIC)-1)
                answer = integer(d, 'answer', 0, len(DIAGNOSTIC[index]['options'])-1)
                diagnostic.raw_results[str(index)] = answer
                diagnostic.save()
                return Response({'saved': True})
            if parts[2] == 'complete':
                answers = d.get('answers', [diagnostic.raw_results.get(str(i)) for i in range(len(DIAGNOSTIC))])
                total, feedback = mark(DIAGNOSTIC, answers)
                knowledge, _ = mark(DIAGNOSTIC[:-1], answers[:-1])
                assimilation, _ = mark(DIAGNOSTIC[-1:], answers[-1:])
                diagnostic.knowledge_score, diagnostic.assimilation_score = knowledge, assimilation
                diagnostic.confidence_score = u.profile.confidence_score
                diagnostic.reading_duration = integer(d, 'reading_duration', 0, 86400, 0)
                diagnostic.raw_results = {'answers': answers, 'feedback': feedback}
                recommended = min(u.profile.available_minutes_daily, 15 if knowledge < 60 else 25)
                ai = generate('diagnostic', {'knowledge_score': knowledge, 'assimilation_score': assimilation, 'available_minutes': u.profile.available_minutes_daily}, DiagnosticInterpretation)
                diagnostic.recommended_session_minutes = min(u.profile.available_minutes_daily, ai['recommended_session_minutes']) if ai else recommended
                diagnostic.completed_at = timezone.now()
                diagnostic.save()
                p = u.profile
                p.baseline_knowledge, p.preferred_session_minutes = knowledge, diagnostic.recommended_session_minutes
                p.save()
                LearningPath.objects.get_or_create(user=u, course=course_for(u), goal_contract=diagnostic.goal_contract, defaults={'title': course_for(u).title, 'description': course_for(u).description})
                calculate_momentum(u)
                return Response({'score': total, 'feedback': feedback, 'recommended_session_minutes': p.preferred_session_minutes})
        if len(parts) == 3 and parts[0] == 'missions' and parts[1].isdigit():
            mission = accessible_mission(u, int(parts[1]))
            lm, _ = LearnerMission.objects.get_or_create(user=u, course=course_for(u), mission=mission)
            action = parts[2]
            if action == 'start':
                if lm.status != 'IN_PROGRESS' and lm.status != 'COMPLETED':
                    lm.status, lm.started_at = 'IN_PROGRESS', timezone.now()
                    signal(u, 'MISSION_STARTED', metadata={'mission_id': mission.id})
                    signal(u, 'SESSION_STARTED')
                    record_resumption(u)
            elif action == 'hint':
                lm.hint_count += 1
                signal(u, 'HINT_REQUESTED', metadata={'mission_id': mission.id})
                lm.save()
                return Response({'hint': mission.assessment.rubric[0]['explanation']})
            elif action == 'abandon':
                if lm.status == 'IN_PROGRESS':
                    lm.status = 'PAUSED'
                    lm.abandon_count += 1
                    signal(u, 'MISSION_ABANDONED', metadata={'mission_id': mission.id})
            elif action == 'complete':
                if not AssessmentAttempt.objects.filter(user=u, course=course_for(u), assessment=mission.assessment, passed=True).exists():
                    raise ValidationError('Pass this mission’s checkpoint before completing it.')
                complete_mission(u, lm)
            elif action == 'adapt':
                adapted = generate('adapt_explanation', {'source_material': mission.content, 'source_reference': mission.source_title, 'language': u.preferred_language, 'level': u.profile.baseline_knowledge}, AdaptedExplanation)
                if adapted and adapted['source_reference'] != mission.source_title:
                    adapted = None
                return Response(adapted or {'adapted_content': mission.low_bandwidth_content, 'source_reference': mission.source_title, 'fallback': True})
            else:
                return Response({'detail': 'Unknown action.'}, status=404)
            lm.last_activity_at = timezone.now()
            lm.save()
            snapshot = calculate_momentum(u)
            detect_friction(u, snapshot)
            return Response({'ok': True})
        if len(parts) == 3 and parts[0] == 'assessments' and parts[1].isdigit() and parts[2] == 'submit':
            assessment = get_object_or_404(Assessment, id=int(parts[1]))
            mission = accessible_mission(u, assessment.mission_id)
            score, feedback = mark(assessment.rubric, d.get('answers'))
            duration = integer(d, 'duration_seconds', 0, 86400, 0)
            plan = None
            if d.get('recovery_plan_id'):
                plan = get_object_or_404(RecoveryPlan, id=d['recovery_plan_id'], user=u, course=course_for(u), status='ACTIVE')
                if plan.plan_json['target_mission_id'] != mission.id or not all(s['completed'] for s in plan.plan_json['steps'][:-1]):
                    raise ValidationError('Complete the recovery steps before the target assessment.')
                confidence = integer(d, 'confidence', 0, 100)
            lm, _ = LearnerMission.objects.get_or_create(user=u, course=course_for(u), mission=mission)
            if not lm.started_at:
                raise ValidationError('Start this mission before submitting its checkpoint.')
            passed = score >= assessment.pass_score
            attempt = AssessmentAttempt.objects.create(user=u, course=course_for(u), assessment=assessment, submission={'answers': d['answers']}, score=score, feedback=json.dumps(feedback), passed=passed, attempt_number=AssessmentAttempt.objects.filter(user=u, course=course_for(u), assessment=assessment).count()+1, duration_seconds=duration)
            lm.attempt_count += 1
            lm.actual_duration_seconds += duration
            lm.last_activity_at = timezone.now()
            lm.save()
            signal(u, 'ASSESSMENT_COMPLETED', score, metadata={'mission_id': mission.id})
            signal(u, 'ASSESSMENT_PASSED' if passed else 'ASSESSMENT_FAILED', score, metadata={'concept': mission.milestone.competency.code})
            signal(u, 'SESSION_ENDED', min(180, (timezone.now()-lm.started_at).total_seconds()/60))
            if passed:
                complete_mission(u, lm, attempt)
            if plan:
                result = finish_plan(plan, attempt, confidence)
            else:
                snapshot = calculate_momentum(u)
                detect_friction(u, snapshot)
            log.info('assessment_saved learner=%s passed=%s', u.id, passed)
            return Response({'score': score, 'passed': passed, 'feedback': feedback, 'recovery': model_to_dict(result) if plan else None})
        if route == 'comeback/check-in':
            snapshot = calculate_momentum(u)
            event = detect_friction(u, snapshot)
            return Response(serialize_friction(event))
        if route == 'comeback/confirm-friction':
            event = get_object_or_404(FrictionEvent, id=d.get('event_id'), user=u, course=course_for(u), status='AWAITING_CONFIRMATION')
            confirmation = d.get('confirmation')
            if confirmation not in ('yes', 'partly', 'no'):
                raise ValidationError('Choose yes, partly, or no.')
            chosen = d.get('friction', event.primary_friction)
            if chosen not in FRICTIONS:
                raise ValidationError('Choose one of the listed barriers.')
            if confirmation == 'no' and not d.get('friction'):
                raise ValidationError('Tell us what is getting in the way instead.')
            event.primary_friction, event.learner_confirmation = chosen, confirmation
            event.learner_feedback = str(d.get('feedback', ''))[:2000]
            event.status = 'CONFIRMED'
            event.save()
            signal(u, 'LEARNER_FEEDBACK', text=chosen)
            try:
                plan = create_plan(event)
            except ValueError as e:
                raise ValidationError(str(e))
            return Response(serialize_plan(plan))
        if route == 'comeback/plan/start':
            plan = get_object_or_404(RecoveryPlan, id=d.get('plan_id'), user=u, course=course_for(u))
            try:
                start_plan(plan)
            except ValueError as e:
                raise ValidationError(str(e))
            return Response(serialize_plan(plan))
        if len(parts) == 4 and parts[:2] == ['comeback', 'steps'] and parts[2].isdigit() and parts[3] == 'complete':
            plan = get_object_or_404(RecoveryPlan, id=d.get('plan_id'), user=u, course=course_for(u), status='ACTIVE')
            step_id = int(parts[2])
            if step_id not in (1, 2):
                raise ValidationError('The last step requires a real assessment.')
            steps = plan.plan_json['steps']
            if step_id == 2 and not steps[0]['completed']:
                raise ValidationError('Complete the review first.')
            if not steps[step_id-1]['completed']:
                steps[step_id-1]['completed'] = True
                signal(u, 'RECOVERY_STEP_COMPLETED', metadata={'plan_id': plan.id, 'step_id': step_id})
                plan.plan_json['steps'] = steps
                plan.save()
                calculate_momentum(u)
            return Response(serialize_plan(plan))
        if route == 'profile':
            minutes = integer(d, 'minutes', 5, 180)
            confidence = integer(d, 'confidence', 0, 100)
            connectivity = d.get('connectivity')
            if connectivity not in ('LOW', 'MEDIUM', 'HIGH'):
                raise ValidationError('Choose a valid connectivity level.')
            zone = str(d.get('timezone', 'Africa/Lagos'))
            try:
                ZoneInfo(zone)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValidationError('Enter a valid IANA timezone, such as Africa/Lagos.')
            u.timezone = zone
            u.save()
            p = u.profile
            p.available_minutes_daily, p.preferred_session_minutes, p.confidence_score, p.connectivity_level = minutes, min(minutes, 30), confidence, connectivity
            p.save()
            signal(u, 'CONFIDENCE_REPORTED', confidence)
            calculate_momentum(u)
            return Response(state(u))
        return Response({'detail': 'Endpoint not found.'}, status=404)

def complete_mission(user, lm, attempt=None):
    if lm.course_id != course_for(user).id or lm.mission.milestone.course_id != course_for(user).id:
        raise ValueError('Mission does not belong to the active course.')
    if lm.status != 'COMPLETED':
        lm.status, lm.completed_at = 'COMPLETED', timezone.now()
        lm.save()
        signal(user, 'MISSION_COMPLETED', metadata={'mission_id': lm.mission_id})
    milestone = lm.mission.milestone
    count = LearnerMission.objects.filter(user=user, course=course_for(user), mission__milestone=milestone, status='COMPLETED').count()
    demonstrated = count == milestone.missions.count()
    attempt = attempt or AssessmentAttempt.objects.filter(user=user, course=course_for(user), assessment=lm.mission.assessment, passed=True).last()
    LearnerCompetency.objects.update_or_create(user=user, course=course_for(user), competency=milestone.competency, defaults={'status': 'DEMONSTRATED' if demonstrated else 'DEVELOPING', 'score': attempt.score if attempt else 0, 'evidence': attempt, 'demonstrated_at': timezone.now() if demonstrated else None})
    record_achievements(user)
