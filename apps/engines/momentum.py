from apps.core.courses import course_for, bind_course
from datetime import timedelta
from statistics import mean
from django.utils import timezone
from apps.core.models import LearnerSignal, MomentumSnapshot, RecoveryPlan

def clamp(value, low=0, high=100):
    return max(low, min(high, value))

def state_for(score):
    return 'PROGRESSING' if score >= 80 else 'STABLE' if score >= 60 else 'SLOWING' if score >= 40 else 'AT_RISK' if score >= 20 else 'DISENGAGED'

class SignalAggregator:
    def __init__(self, user, now=None):
        self.user, self.now = user, now or timezone.now()

    def aggregate(self):
        history = LearnerSignal.objects.filter(user=self.user, course=course_for(self.user), recorded_at__lte=self.now)
        events = list(history.filter(recorded_at__gte=self.now - timedelta(days=14)).order_by('recorded_at', 'id'))
        recent = [s for s in events if s.recorded_at >= self.now - timedelta(days=7)]
        boundaries = [s for s in recent if s.signal_type == 'SIMULATION_BOUNDARY']
        if self.user.is_demo and boundaries:
            recent = [s for s in recent if s.id > boundaries[-1].id]
        kind = lambda name: [s for s in recent if s.signal_type == name]
        assessments = [s.numeric_value for s in kind('ASSESSMENT_COMPLETED') if s.numeric_value is not None]
        prior = [s.numeric_value for s in events if s.recorded_at < self.now - timedelta(days=7) and s.signal_type == 'ASSESSMENT_COMPLETED' and s.numeric_value is not None]
        sessions = [s.numeric_value for s in kind('SESSION_ENDED') if s.numeric_value is not None]
        activity = [s for s in recent if s.signal_type in ('SESSION_STARTED', 'MISSION_STARTED', 'MISSION_COMPLETED', 'ASSESSMENT_COMPLETED', 'RECOVERY_STEP_COMPLETED')]
        completed = len(kind('MISSION_COMPLETED')) + len(kind('RECOVERY_STEP_COMPLETED'))
        inactivity = max([s.numeric_value or 0 for s in kind('INACTIVITY')] or [0])
        # An explicit simulated absence ends when later genuine learning resumes.
        absences = kind('INACTIVITY')
        if activity and absences and activity[-1].recorded_at > absences[-1].recorded_at:
            inactivity = 0
        activity_types = ('SESSION_STARTED', 'MISSION_STARTED', 'MISSION_COMPLETED', 'ASSESSMENT_COMPLETED', 'RECOVERY_STEP_COMPLETED')
        last_activity = activity[-1] if activity else history.filter(signal_type__in=activity_types).order_by('recorded_at', 'id').last()
        first_activity = history.filter(signal_type__in=activity_types).order_by('recorded_at', 'id').first()
        days = max(inactivity, (self.now - last_activity.recorded_at).total_seconds()/86400 if last_activity else 0)
        return {
            'missions_completed_7d': completed, 'missions_expected_7d': 5,
            'missions_missed_7d': max(0, 5 - completed),
            'assessment_average': mean(assessments[-5:]) if assessments else self.user.profile.baseline_knowledge,
            'assessment_trend': (mean(assessments[-3:]) - (mean(prior[-3:]) if prior else assessments[0])) if assessments else 0,
            'latest_assessment': assessments[-1] if assessments else self.user.profile.baseline_knowledge,
            'average_session_minutes': mean(sessions[-5:]) if sessions else 0,
            'expected_session_minutes': self.user.profile.preferred_session_minutes,
            'days_since_last_activity': round(days, 2),
            'active_days': len({s.recorded_at.date() for s in activity}),
            'repeated_attempts': len(kind('ASSESSMENT_FAILED')),
            'same_concept_failures': max([sum(1 for t in kind('ASSESSMENT_FAILED') if t.metadata.get('concept') == s.metadata.get('concept')) for s in kind('ASSESSMENT_FAILED') if s.metadata.get('concept')] or [0]),
            'hint_count': len(kind('HINT_REQUESTED')),
            'abandonment_rate': len(kind('MISSION_ABANDONED')) / max(1, len(kind('MISSION_STARTED'))),
            'connectivity_failures': len(kind('CONNECTIVITY_FAILURE')),
            'confidence': self.user.profile.confidence_score,
            'baseline_knowledge': self.user.profile.baseline_knowledge,
            'feedback': [s.text_value for s in kind('LEARNER_FEEDBACK')],
            'signal_count': len(recent),
            'history_signal_count': history.count(),
            'observation_days': (self.now - first_activity.recorded_at).total_seconds()/86400 if first_activity else 0,
            'has_learning_history': first_activity is not None,
            'simulated': bool(self.user.is_demo and boundaries),
        }

def score_components(metrics):
    consistency = clamp(metrics['active_days'] / 5 * 100) * max(0, 1 - metrics['days_since_last_activity'] / 7)
    completion = clamp(metrics['missions_completed_7d'] / metrics['missions_expected_7d'] * 100)
    assessment = clamp(metrics['assessment_average'] + metrics['assessment_trend'] * .5)
    engagement = clamp(metrics['average_session_minutes'] / max(1, metrics['expected_session_minutes']) * 100) * (1 - clamp(metrics['abandonment_rate'], 0, 1))
    components = dict(consistency_score=consistency, completion_score=completion, assessment_score=assessment, engagement_score=engagement, confidence_score=metrics['confidence'])
    score = round(sum(components[k] * w for k, w in zip(components, [.25, .25, .2, .15, .15])), 1)
    return score, {k: round(v, 1) for k, v in components.items()}

def calculate_momentum(user, now=None):
    now = now or timezone.now()
    metrics = SignalAggregator(user, now).aggregate()
    score, components = score_components(metrics)
    # A new learner starts with a neutral baseline, never an inactivity diagnosis.
    if not metrics['has_learning_history']:
        score = 50
        components = {k: 50 for k in components}
    state = 'RECOVERING' if RecoveryPlan.objects.filter(user=user, course=course_for(user), status='ACTIVE').exists() else state_for(score)
    snapshot = MomentumSnapshot.objects.create(user=user, course=course_for(user), score=score, state=state, calculated_at=now, metrics=metrics, **components)
    profile = user.profile
    profile.current_momentum_score, profile.current_state = score, state
    profile.save()
    return snapshot
