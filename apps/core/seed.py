from apps.core.courses import course_for, bind_course
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from .models import *
from .content import COURSE, STRATEGIES, DIAGNOSTIC
from .python_content import COURSE as PYTHON_COURSE, DIAGNOSTIC as PYTHON_DIAGNOSTIC
from apps.engines.momentum import calculate_momentum
from apps.engines.friction import detect_friction

PERSONAS = {'tunde': {'name': 'Tunde', 'barrier': 'prerequisite_gap', 'context': 'I know basic Excel and want to work with real datasets.', 'baseline': 42}, 'amina': {'name': 'Amina', 'barrier': 'time', 'context': 'I balance work and study, and need short sessions.', 'baseline': 82}, 'mariam': {'name': 'Mariam', 'barrier': 'connectivity', 'context': 'My internet is unreliable, but I enjoy learning.', 'baseline': 78}, 'david': {'name': 'David', 'barrier': 'relevance', 'context': 'I want practical skills for my small business.', 'baseline': 85}}

@transaction.atomic
def seed_content():
    definitions = [
        ('data-analysis', 'Data Analysis Foundations', 'From your first dataset to a decision that matters.', 'Data analysis', 'Become confident in data analysis', 'I can clean a real dataset and explain a useful insight.', 'I know a little Excel, but I am new to analysis.', 'DA', COURSE, DIAGNOSTIC),
        ('introduction-to-python', 'Introduction to Python', 'Write clear Python, solve small problems, and build your programming confidence.', 'Programming', 'Write useful programs with Python', 'I can explain and build a small Python program using conditions, loops, and functions.', 'I am new to programming and want to learn the basics.', 'PY', PYTHON_COURSE, PYTHON_DIAGNOSTIC),
    ]
    for order, (slug, course_title, description, category, goal, success, starting, prefix, curriculum, diagnostic) in enumerate(definitions, 1):
        course, _ = Course.objects.update_or_create(slug=slug, defaults={'title': course_title, 'description': description, 'category': category, 'default_goal': goal, 'success_example': success, 'starting_example': starting, 'diagnostic': diagnostic, 'is_published': True, 'sequence': order})
        for n, (title, competency, missions) in enumerate(curriculum, 1):
            c, _ = Competency.objects.update_or_create(code=f'{prefix}{n:03}', defaults={'course': course, 'name': competency, 'domain': category})
            milestone, _ = Milestone.objects.update_or_create(course=course, sequence=n, defaults={'title': title, 'description': f'Build confidence in {title.lower()}.', 'competency': c})
            for seq, (name, description, content, rubric) in enumerate(missions, 1):
                mission, _ = Mission.objects.update_or_create(milestone=milestone, sequence=seq, defaults={'title': name, 'description': description, 'content': content, 'low_bandwidth_content': content, 'estimated_minutes': 10 if seq == 1 else 15, 'source_title': f'Progrez curated {course_title} · v1'})
                Assessment.objects.update_or_create(mission=mission, defaults={'title': f'{name}: checkpoint', 'rubric': rubric})
    for friction, strategies in STRATEGIES.items():
        for strategy, title in strategies:
            Intervention.objects.update_or_create(strategy_type=strategy, defaults={'name': title, 'friction_type': friction, 'description': title, 'template': {'delivery': 'TEXT', 'steps': ['review', 'practice', 'reassess']}, 'measurement_strategy': {'assessment': True, 'confidence': True, 'momentum': True}})

def signal(user, kind, value=None, text='', metadata=None, at=None):
    return LearnerSignal.objects.create(user=user, course=course_for(user), signal_type=kind, numeric_value=value, text_value=text, metadata=metadata or {}, recorded_at=at or timezone.now())

@transaction.atomic
def reset_persona(slug, course=None):
    persona = PERSONAS[slug]
    user, _ = User.objects.get_or_create(username=slug, defaults={'email': f'{slug}@demo.progrez.local', 'name': persona['name'], 'is_demo': True})
    bind_course(user, course or Course.objects.get(slug='data-analysis'))
    if not user.is_demo:
        raise ValueError('Refusing to reset a real learner.')
    from apps.circle.models import JourneyPrivacy, SupportMember, Achievement, Encouragement
    for circle_model in (Encouragement, SupportMember, Achievement, JourneyPrivacy):
        circle_model.objects.filter(learner=user, course=course_for(user)).delete()
    # Explicit demo-only reset, including history; never used for real accounts.
    for model in [RecoveryPlan, FrictionEvent, MomentumSnapshot, LearnerSignal, LearnerCompetency, AssessmentAttempt, LearnerMission, DiagnosticSession, LearningPath, GoalContract]:
        model.objects.filter(user=user, course=course_for(user)).delete()
    user.set_unusable_password()
    user.save()
    profile, _ = LearnerProfile.objects.update_or_create(user=user, course=course_for(user), defaults={'baseline_knowledge': persona['baseline'], 'confidence_score': 78, 'preferred_session_minutes': 20, 'available_minutes_daily': 20, 'connectivity_level': 'LOW' if slug == 'mariam' else 'HIGH'})
    goal = GoalContract.objects.create(user=user, course=course_for(user), goal=course_for(user).default_goal, context=persona['context'] if course_for(user).slug == 'data-analysis' else course_for(user).starting_example, constraints=[persona['barrier']], done_when=course_for(user).success_example)
    LearningPath.objects.create(user=user, course=course_for(user), goal_contract=goal, title=course_for(user).title, description=course_for(user).description)
    DiagnosticSession.objects.create(user=user, course=course_for(user), goal_contract=goal, completed_at=timezone.now(), knowledge_score=persona['baseline'], assimilation_score=75, confidence_score=78, raw_results={'demo': True})
    now = timezone.now()
    for mission in Mission.objects.filter(milestone__course=course_for(user), milestone__sequence__lte=2):
        LearnerMission.objects.create(user=user, course=course_for(user), mission=mission, status='COMPLETED', started_at=now-timedelta(days=9), completed_at=now-timedelta(days=8), attempt_count=1)
        a = AssessmentAttempt.objects.create(user=user, course=course_for(user), assessment=mission.assessment, submission={'demo': True}, score=80, feedback='Seeded demo evidence.', attempt_number=1, passed=True)
        LearnerCompetency.objects.update_or_create(user=user, course=course_for(user), competency=mission.milestone.competency, defaults={'status': 'DEMONSTRATED', 'score': 80, 'evidence': a, 'demonstrated_at': now-timedelta(days=8)})
    for day in range(12, -1, -1):
        at = now - timedelta(days=day, minutes=1)
        signal(user, 'SESSION_STARTED', at=at)
        signal(user, 'SESSION_ENDED', 20, at=at)
        if day % 3 != 0:
            signal(user, 'MISSION_COMPLETED', at=at)
        signal(user, 'ASSESSMENT_COMPLETED', 80, at=at)
        if day <= 6:
            calculate_momentum(user, at + timedelta(seconds=1))
    calculate_momentum(user)
    from apps.circle.services import privacy_for, record_achievements
    privacy_for(user)
    record_achievements(user, notify=False)
    return user

@transaction.atomic
def simulate(user, scenario):
    if not user.is_demo:
        raise ValueError('Simulations are only available for demo learners.')
    signal(user, 'SIMULATION_BOUNDARY', metadata={'scenario': scenario})
    p = user.profile
    p.confidence_score = 35 if scenario in ('decline', 'failures', 'persona') and user.username == 'tunde' else 60
    p.save()
    barrier = PERSONAS.get(user.username, PERSONAS['tunde'])['barrier'] if scenario == 'persona' else {'inactivity': 'time', 'decline': 'difficulty', 'connectivity': 'connectivity', 'failures': 'prerequisite_gap'}[scenario]
    if barrier in ('difficulty', 'prerequisite_gap'):
        for _ in range(3):
            signal(user, 'MISSION_STARTED')
            signal(user, 'SESSION_ENDED', 40)
            signal(user, 'ASSESSMENT_COMPLETED', 41)
            signal(user, 'ASSESSMENT_FAILED', 41, metadata={'concept': 'formula_references' if course_for(user).slug == 'data-analysis' else 'python_basics'})
            signal(user, 'MISSION_ABANDONED')
        for _ in range(2):
            signal(user, 'HINT_REQUESTED')
    elif barrier == 'connectivity':
        signal(user, 'SESSION_STARTED')
        signal(user, 'SESSION_ENDED', 5)
        signal(user, 'ASSESSMENT_COMPLETED', 78)
        for _ in range(3):
            signal(user, 'CONNECTIVITY_FAILURE')
        signal(user, 'LEARNER_FEEDBACK', text='connectivity')
    else:
        signal(user, 'SESSION_STARTED')
        signal(user, 'SESSION_ENDED', 8)
        signal(user, 'ASSESSMENT_COMPLETED', 82)
        signal(user, 'INACTIVITY', 7 if scenario == 'inactivity' else 4)
        signal(user, 'LEARNER_FEEDBACK', text=barrier)
    # Simulating a new problem intentionally retires any previous demo plan.
    RecoveryPlan.objects.filter(user=user, course=course_for(user), status__in=['READY', 'ACTIVE']).update(status='SUPERSEDED')
    FrictionEvent.objects.filter(user=user, course=course_for(user), status__in=['AWAITING_CONFIRMATION', 'CONFIRMED', 'INTERVENTION_STARTED']).update(status='REJECTED')
    snapshot = calculate_momentum(user)
    detect_friction(user, snapshot)
    return snapshot
