from apps.core.courses import course_for, bind_course
from apps.core.models import FrictionEvent, Intervention, RecoveryResult
from apps.core.content import FRICTIONS
from apps.ai.services import generate, FrictionInterpretation

def rank_friction(m):
    candidates = []
    def add(kind, rules):
        evidence = [label for condition, weight, label in rules if condition]
        score = min(1, sum(weight for condition, weight, label in rules if condition))
        if score:
            candidates.append({'type': kind, 'label': FRICTIONS[kind], 'confidence': round(score, 2), 'evidence': evidence})
    good = m['assessment_average'] >= 70
    short = m['average_session_minutes'] < m['expected_session_minutes'] * .6
    low = m['active_days'] < 3 or m['days_since_last_activity'] >= 3
    add('difficulty', [(m['assessment_trend'] <= -20, .3, 'Recent assessment scores dropped by at least 20 points.'), (m['repeated_attempts'] >= 3, .2, 'Three or more unsuccessful attempts this week.'), (m['average_session_minutes'] > m['expected_session_minutes'] * 1.5, .2, 'Sessions take more than 1.5 times the planned length.'), (m['hint_count'] >= 2, .15, 'You requested extra hints.'), ('difficulty' in m['feedback'], .4, 'You reported that the material feels difficult.')])
    add('prerequisite_gap', [(m['same_concept_failures'] >= 3, .3, 'Repeated errors cluster around the same concept.'), (m['baseline_knowledge'] < 50, .25, 'The diagnostic found a foundation worth revisiting.'), (m['same_concept_failures'] >= 3 and m['baseline_knowledge'] < 50, .3, 'The repeated errors match a weak foundation.')])
    add('time', [(short, .2, 'Sessions are shorter than your original plan.'), (m['missions_missed_7d'] >= 3, .2, 'Several planned missions remain unfinished.'), (good, .2, 'Your assessment performance remains strong.'), ('time' in m['feedback'], .5, 'You reported limited time.')])
    add('motivation', [(low, .25, 'Learning activity has become less frequent.'), (good, .15, 'Your understanding remains strong.'), (m['abandonment_rate'] > .4, .2, 'Several missions were set aside.'), ('motivation' in m['feedback'], .5, 'You reported difficulty getting started.')])
    add('relevance', [(good, .15, 'Assessment performance remains strong.'), (low, .2, 'Engagement is lower than expected.'), ('relevance' in m['feedback'], .6, 'You want a clearer connection to your goal.')])
    add('connectivity', [(m['connectivity_failures'] >= 2, .4, 'Several connection failures interrupted learning.'), (m['connectivity_failures'] > 0 and short, .2, 'Interrupted sessions are unusually short.'), (good, .1, 'Your understanding remains strong.'), ('connectivity' in m['feedback'], .5, 'You reported internet or data problems.')])
    for kind in ['content_overload', 'language_comprehension', 'confidence', 'external_interruption']:
        add(kind, [(kind in m['feedback'], .8, 'You told us what is getting in the way.'), (kind == 'confidence' and m['confidence'] < 35, .35, 'Your reported confidence is low.')])
    # On equal scores prefer a specific, evidenced foundation gap to generic difficulty.
    return sorted(candidates, key=lambda c: (c['confidence'], c['type'] == 'prerequisite_gap'), reverse=True)

def detect_friction(user, snapshot):
    bind_course(user, snapshot.course)
    existing = FrictionEvent.objects.filter(user=user, course=course_for(user), status__in=['AWAITING_CONFIRMATION', 'CONFIRMED', 'INTERVENTION_STARTED']).last()
    if existing:
        return existing
    ranked = rank_friction(snapshot.metrics)
    m = snapshot.metrics
    if m.get('history_signal_count', m['signal_count']) < 3 or not ranked:
        return None
    # Avoid labeling a new learner at risk merely because their first week is incomplete.
    if not (m.get('simulated') or m.get('observation_days', 0) >= 3 or m['repeated_attempts'] >= 3 or m['connectivity_failures'] >= 2 or m['feedback']):
        return None
    if snapshot.state not in ('AT_RISK', 'DISENGAGED') and ranked[0]['confidence'] < .7:
        return None
    top = ranked[0]
    copy = generate('friction_interpretation', {'candidate_frictions': ranked, 'recent_signals': m, 'selected_friction': top['type']}, FrictionInterpretation)
    question = copy['confirmation_question'] if copy and copy['primary_friction'] == top['type'] else ''
    return FrictionEvent.objects.create(user=user, course=course_for(user), momentum_snapshot=snapshot, primary_friction=top['type'], confidence=top['confidence'], candidate_friction=ranked, evidence=top['evidence'], confirmation_question=question)

def select_intervention(event, profile):
    options = list(Intervention.objects.filter(friction_type=event.primary_friction, is_active=True).order_by('id'))
    failed = set(RecoveryResult.objects.filter(recovery_plan__user=event.user, recovery_plan__course=course_for(event.user), recovery_score__lt=60).values_list('recovery_plan__intervention_id', flat=True))
    fresh = [i for i in options if i.id not in failed]
    if not fresh:
        return None
    if profile.connectivity_level == 'LOW':
        fresh.sort(key=lambda i: i.strategy_type not in ('TEXT_ONLY_RESOURCE', 'MICRO_MISSION'))
    return fresh[0]
