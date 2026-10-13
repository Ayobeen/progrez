from types import SimpleNamespace
from django.test import TestCase
from apps.engines.momentum import state_for, score_components
from apps.engines.friction import rank_friction
from apps.engines.recovery import recovery_components

def metrics(**changes):
    m = dict(missions_completed_7d=1, missions_expected_7d=5, missions_missed_7d=4, assessment_average=80, assessment_trend=0, average_session_minutes=8, expected_session_minutes=20, days_since_last_activity=4, active_days=1, repeated_attempts=0, same_concept_failures=0, hint_count=0, abandonment_rate=0, connectivity_failures=0, confidence=60, baseline_knowledge=70, feedback=[])
    return {**m, **changes}

class MomentumTests(TestCase):
    def test_boundaries(self):
        for score, state in [(0,'DISENGAGED'),(19.9,'DISENGAGED'),(20,'AT_RISK'),(40,'SLOWING'),(60,'STABLE'),(80,'PROGRESSING'),(100,'PROGRESSING')]:
            self.assertEqual(state_for(score), state)

    def test_perfect_components_and_clamping(self):
        score, parts = score_components(metrics(missions_completed_7d=20, active_days=7, days_since_last_activity=0, assessment_average=100, average_session_minutes=20, confidence=100))
        self.assertEqual(score,100)
        self.assertTrue(all(0 <= n <= 100 for n in parts.values()))

    def test_good_scores_short_sessions_raise_time(self):
        ranked=rank_friction(metrics())
        self.assertEqual(ranked[0]['type'],'time')
        self.assertFalse(any(c['type']=='difficulty' for c in ranked))

    def test_repeated_failures_raise_difficulty(self):
        ranked=rank_friction(metrics(assessment_average=40,assessment_trend=-30,repeated_attempts=3,average_session_minutes=40,hint_count=2))
        self.assertEqual(ranked[0]['type'],'difficulty')
        self.assertGreaterEqual(ranked[0]['confidence'],.8)

    def test_same_concept_and_baseline_raise_prerequisite(self):
        ranked=rank_friction(metrics(assessment_average=41,repeated_attempts=3,same_concept_failures=3,baseline_knowledge=42))
        self.assertEqual(ranked[0]['type'],'prerequisite_gap')

    def test_disconnects_raise_connectivity(self):
        ranked=rank_friction(metrics(connectivity_failures=3,feedback=['connectivity']))
        self.assertEqual(ranked[0]['type'],'connectivity')
        self.assertLessEqual(ranked[0]['confidence'],1)

    def test_user_relevance_feedback_overrides_low_activity(self):
        self.assertEqual(rank_friction(metrics(feedback=['relevance']))[0]['type'],'relevance')

    def test_recovery_components_clamp_decline(self):
        b=SimpleNamespace(assessment_average=80,confidence_score=70,momentum_score=60)
        c=recovery_components(b,40,30,30,3,3)
        self.assertEqual(c['competency_improvement'],0)
        self.assertEqual(c['momentum_restoration'],0)
        self.assertEqual(c['recovery_completion'],100)
