from apps.core.courses import course_for, bind_course
from datetime import timedelta
from django.utils import timezone
from apps.core.models import RecoveryPlan, RecoveryBaseline, RecoveryResult, Mission, LearnerMission, LearnerSignal
from apps.core.content import FRICTIONS
from apps.ai.services import generate, PlanCopy
from .friction import select_intervention
from .momentum import calculate_momentum, clamp

def create_plan(event):
    existing = RecoveryPlan.objects.filter(friction_event=event).first()
    if existing:
        return existing
    bind_course(event.user, event.course)
    profile = event.user.profile
    intervention = select_intervention(event, profile)
    if intervention is None:
        raise ValueError('These approaches have already been tried. Choose a different barrier so we can build a fresh plan.')
    completed = LearnerMission.objects.filter(user=event.user, course=course_for(event.user), status='COMPLETED').values_list('mission_id', flat=True)
    target = Mission.objects.filter(milestone__course=event.course).exclude(id__in=completed).first() or Mission.objects.filter(milestone__course=event.course).last()
    budget = max(5, min(profile.available_minutes_daily, profile.preferred_session_minutes, intervention.maximum_minutes))
    goal = event.user.goalcontract_set.filter(course=event.course, status='ACTIVE').last()
    copy = generate('personalize_plan', {'intervention': intervention.strategy_type, 'friction': event.primary_friction, 'goal': goal.goal if goal else '', 'available_minutes': budget, 'language': event.user.preferred_language, 'source_material': target.content}, PlanCopy)
    reason = f'You identified {FRICTIONS[event.primary_friction].lower()} as the barrier. We have made each step fit within {budget} minutes, using text you can revisit at your own pace.'
    intro = {
        'time': 'Keep this session small. Read just the first concept, then stop at the checkpoint. A short completed session counts.',
        'difficulty': 'Read the worked example slowly. Explain each step before predicting the result.',
        'prerequisite_gap': 'Return to the first principle before retrying the challenge. Explain each step in your own words.',
        'motivation': 'Choose one small target: finish this example. You do not need to finish the whole course today.',
        'relevance': f'Think about how this example helps your goal: {goal.goal if goal else "apply this skill in everyday life"}. Write down one practical use.',
        'connectivity': 'This plan is entirely text. Download a copy if you need to read it without a connection; return here to save your progress.',
        'content_overload': 'Focus only on the example below. Leave the next topic for another session.',
        'language_comprehension': 'Start with the example. Write down unfamiliar terms, then explain each step in words you use every day.',
        'confidence': 'Begin with what you recognize. A mistake here is useful feedback, and you can retry.',
        'external_interruption': 'There is no late penalty. Pick a comfortable time and restart with this small step.'
    }[event.primary_friction]
    bridge = target.content
    # Seeded Tunde has a spreadsheet foundation gap before cleaning.
    if event.primary_friction == 'prerequisite_gap':
        foundation = Mission.objects.filter(milestone__course=event.course, milestone__sequence=max(1, target.milestone.sequence - 1)).first()
        if foundation:
            bridge = foundation.content
    steps = [
        {'id': 1, 'title': 'Revisit the essential idea', 'minutes': min(10, budget), 'content': intro + '\n\n' + bridge, 'completed': False, 'kind': 'read'},
        {'id': 2, 'title': 'Try a guided example', 'minutes': min(5, budget), 'content': 'Work through this example and compare each step with your own reasoning.\n\n' + target.content, 'completed': False, 'kind': 'practice'},
        {'id': 3, 'title': 'Show what you can do', 'minutes': min(target.estimated_minutes, budget), 'content': 'Retry the mission assessment. Your actual answers will determine the result. A lower score helps us choose a better next step.', 'completed': False, 'kind': 'assessment', 'mission_id': target.id}
    ]
    return RecoveryPlan.objects.create(user=event.user, course=course_for(event.user), friction_event=event, intervention=intervention, title=copy['title'] if copy else intervention.name, reason=copy['reason'] if copy else reason, plan_json={'steps': steps, 'delivery': 'TEXT', 'source': 'AI-assisted copy' if copy else 'Curated recovery template', 'encouragement': copy['encouragement'] if copy else 'Your pace can change. Your goal can stay.', 'target_mission_id': target.id, 'prompt_version': 'progrez-v1'})

def start_plan(plan):
    bind_course(plan.user, plan.course)
    if plan.status == 'ACTIVE':
        return plan
    if plan.status != 'READY':
        raise ValueError('This plan is already complete.')
    snapshot = calculate_momentum(plan.user)
    RecoveryBaseline.objects.get_or_create(recovery_plan=plan, defaults={'momentum_score': snapshot.score, 'assessment_average': snapshot.metrics['latest_assessment'], 'confidence_score': plan.user.profile.confidence_score, 'completion_rate': snapshot.metrics['missions_completed_7d']/5*100})
    plan.status, plan.started_at = 'ACTIVE', timezone.now()
    plan.expires_at = timezone.now() + timedelta(days=7)
    plan.save()
    plan.friction_event.status = 'INTERVENTION_STARTED'
    plan.friction_event.save()
    calculate_momentum(plan.user)
    return plan

def recovery_components(baseline, post_score, assessment, confidence, completed, total):
    return {
        'resumption': 100 if completed else 0,
        'competency_improvement': clamp((assessment - baseline.assessment_average) / max(20, 100 - baseline.assessment_average) * 100),
        'recovery_completion': completed / total * 100,
        'confidence_change': clamp((confidence - baseline.confidence_score) / max(20, 100 - baseline.confidence_score) * 100),
        'momentum_restoration': clamp((post_score - baseline.momentum_score) / max(20, 80 - baseline.momentum_score) * 100),
    }

def finish_plan(plan, attempt, confidence):
    bind_course(plan.user, plan.course)
    if attempt.course_id != plan.course_id or attempt.user_id != plan.user_id:
        raise ValueError("Assessment does not belong to this recovery journey.")
    if plan.status == 'COMPLETED':
        return plan.result
    if plan.status != 'ACTIVE':
        raise ValueError('Start your recovery plan first.')
    steps = plan.plan_json['steps']
    if not all(s['completed'] for s in steps[:-1]):
        raise ValueError('Complete the review and guided example before the final assessment.')
    steps[-1]['completed'] = True
    plan.plan_json['steps'] = steps
    plan.status, plan.completed_at = 'COMPLETED', timezone.now()
    plan.save()
    profile = plan.user.profile
    profile.confidence_score = confidence
    profile.save()
    LearnerSignal.objects.create(user=plan.user, course=course_for(plan.user), signal_type='CONFIDENCE_REPORTED', numeric_value=confidence, recorded_at=timezone.now())
    snapshot = calculate_momentum(plan.user)
    b = plan.baseline
    components = recovery_components(b, snapshot.score, attempt.score, confidence, 3, 3)
    score = round(sum(components[k] * w for k, w in zip(components, [.3, .3, .2, .1, .1])), 1)
    # Completion cannot establish recovery if the competency remains unproven.
    if not attempt.passed:
        score = min(score, 39)
    outcome = 'STRONG_RECOVERY' if score >= 80 else 'RECOVERING' if score >= 60 else 'PARTIAL_RECOVERY' if score >= 40 else 'INTERVENTION_INEFFECTIVE'
    result = RecoveryResult.objects.create(recovery_plan=plan, pre_momentum=b.momentum_score, post_momentum=snapshot.score, pre_assessment=b.assessment_average, post_assessment=attempt.score, pre_confidence=b.confidence_score, post_confidence=confidence, completion_percentage=100, recovery_score=score, outcome=outcome, components=components)
    plan.friction_event.status = 'RESOLVED' if score >= 60 else 'REJECTED'
    plan.friction_event.save()
    return result
