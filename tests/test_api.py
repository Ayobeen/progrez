from unittest.mock import patch
from datetime import timedelta
from django.utils import timezone
from django.test import TestCase, Client, override_settings
from apps.core.models import *
from apps.core.seed import seed_content, reset_persona, simulate
from apps.core.content import DIAGNOSTIC
from apps.engines.momentum import calculate_momentum
from apps.ai.services import PlanCopy, generate

class FlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_content()
        cls.tunde=reset_persona('tunde')
        cls.amina=reset_persona('amina')

    def setUp(self):
        self.client.force_login(self.tunde)

    def post(self,route,data=None,status=200):
        response=self.client.post('/api/v1/'+route+'/',data=data or {},content_type='application/json')
        self.assertEqual(response.status_code,status,response.content[:1000])
        return response.json()

    def state(self):
        return self.client.get('/api/v1/state/').json()

    def test_authentication_required_and_csrf(self):
        client=Client(enforce_csrf_checks=True)
        self.assertEqual(client.get('/api/v1/state/').status_code,403)
        self.assertEqual(client.post('/api/v1/demo/login/',{'persona':'tunde'}).status_code,403)
        client.get('/')
        token=client.cookies['csrftoken'].value
        response=client.post('/api/v1/demo/login/',{'persona':'tunde'},HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code,200)

    @override_settings(DEMO_MODE=False)
    def test_demo_disabled(self):
        self.post('demo/login',{'persona':'tunde'},403)
        self.post('demo/simulate',{'scenario':'persona'},403)

    def test_fresh_registration_goal_diagnostic_and_ownership(self):
        self.client.logout()
        self.post('auth/register',{'name':'Ada','email':'ada@example.com','password':'Study-data-927!'},201)
        self.post('goals',{'goal':'Analyze shop sales','done_when':'I can explain revenue','available_minutes_daily':10,'confidence_score':50,'constraints':['time']})
        diagnostic=self.post('diagnostics/start')
        self.post(f"diagnostics/{diagnostic['id']}/complete",{'answers':[q['answer'] for q in DIAGNOSTIC],'reading_duration':45})
        s=self.state()
        self.assertTrue(s['diagnosed'])
        self.assertEqual(s['profile']['preferred_session_minutes'],10)
        self.assertEqual(s['completed_missions'],0)
        self.assertEqual(s['momentum']['score'],50)
        self.post('demo/reset',{},403)
        self.post('goals/'+str(GoalContract.objects.filter(user=self.tunde).first().id),{'goal':'Hijack','done_when':'No'},404)

    def test_mission_unlock_completion_idempotence_and_answers_hidden(self):
        m=Mission.objects.filter(milestone__course__slug='data-analysis',milestone__sequence=3,sequence=1).get()
        locked=Mission.objects.filter(milestone__course__slug='data-analysis',milestone__sequence=4,sequence=1).get()
        self.assertEqual(self.client.get(f'/api/v1/missions/{locked.id}/').status_code,403)
        data=self.client.get(f'/api/v1/missions/{m.id}/').json()
        self.assertNotIn('answer',data['questions'][0])
        self.post(f'missions/{m.id}/complete',{},400)
        self.post(f'missions/{m.id}/start')
        passed=self.post(f'assessments/{m.assessment.id}/submit',{'answers':[q['answer'] for q in m.assessment.rubric]})
        self.assertTrue(passed['passed'])
        count=LearnerSignal.objects.filter(user=self.tunde,signal_type='MISSION_COMPLETED',metadata__mission_id=m.id).count()
        self.post(f'missions/{m.id}/complete')
        self.assertEqual(LearnerSignal.objects.filter(user=self.tunde,signal_type='MISSION_COMPLETED',metadata__mission_id=m.id).count(),count)
        next_m=Mission.objects.get(milestone=m.milestone,sequence=2)
        self.assertEqual(self.client.get(f'/api/v1/missions/{next_m.id}/').status_code,200)
        self.post(f'missions/{next_m.id}/start')
        self.post(f'assessments/{next_m.assessment.id}/submit',{'answers':[q['answer'] for q in next_m.assessment.rubric]})
        self.assertEqual(self.client.get(f'/api/v1/missions/{locked.id}/').status_code,200)
        self.assertEqual(LearnerCompetency.objects.get(user=self.tunde,competency=m.milestone.competency).status,'DEMONSTRATED')

    def prepare_recovery(self, override=None):
        self.post('demo/simulate',{'scenario':'persona'})
        f=self.state()['friction']
        self.assertEqual(f['type'],'prerequisite_gap')
        p=self.post('comeback/confirm-friction',{'event_id':f['id'],'confirmation':'no' if override else 'yes','friction':override or f['type']})
        self.post('comeback/plan/start',{'plan_id':p['id']})
        return p

    def test_recovery_success_real_assessment_baseline_and_idempotence(self):
        p=self.prepare_recovery()
        baseline=RecoveryBaseline.objects.get(recovery_plan_id=p['id'])
        self.assertEqual(baseline.assessment_average,41)
        self.post('comeback/steps/2/complete',{'plan_id':p['id']},400)
        self.post('comeback/steps/1/complete',{'plan_id':p['id']})
        self.post('comeback/steps/1/complete',{'plan_id':p['id']})
        self.assertEqual(LearnerSignal.objects.filter(user=self.tunde,signal_type='RECOVERY_STEP_COMPLETED').count(),1)
        self.post('comeback/steps/2/complete',{'plan_id':p['id']})
        m=Mission.objects.get(id=p['target_mission_id'])
        self.post(f'missions/{m.id}/start')
        result=self.post(f'assessments/{m.assessment.id}/submit',{'answers':[q['answer'] for q in m.assessment.rubric],'recovery_plan_id':p['id'],'confidence':80})['recovery']
        self.assertEqual(result['post_assessment'],100)
        self.assertGreaterEqual(result['recovery_score'],60)
        self.assertGreater(result['post_momentum'],result['pre_momentum'])
        self.assertEqual(RecoveryBaseline.objects.get(pk=baseline.pk).momentum_score,baseline.momentum_score)
        self.assertEqual(RecoveryPlan.objects.get(id=p['id']).status,'COMPLETED')
        self.assertIsNone(self.state()['friction'])

    def test_failed_recovery_is_not_presented_as_success_and_rotates(self):
        p=self.prepare_recovery()
        for i in (1,2):self.post(f'comeback/steps/{i}/complete',{'plan_id':p['id']})
        m=Mission.objects.get(id=p['target_mission_id'])
        self.post(f'missions/{m.id}/start')
        result=self.post(f'assessments/{m.assessment.id}/submit',{'answers':[(q['answer']+1)%len(q['options']) for q in m.assessment.rubric],'recovery_plan_id':p['id'],'confidence':20})['recovery']
        self.assertEqual(result['outcome'],'INTERVENTION_INEFFECTIVE')
        self.post('comeback/check-in')
        f=self.state()['friction']
        self.assertIsNotNone(f)
        new=self.post('comeback/confirm-friction',{'event_id':f['id'],'confirmation':'yes','friction':'prerequisite_gap'})
        self.assertNotEqual(new['strategy'],p['strategy'])

    def test_learner_correction_wins(self):
        p=self.prepare_recovery('time')
        self.assertEqual(p['friction'],'time')
        self.assertEqual(p['strategy'],'MICRO_MISSION')

    def test_other_learner_cannot_start_plan(self):
        p=self.prepare_recovery()
        self.client.force_login(self.amina)
        self.post('comeback/plan/start',{'plan_id':p['id']},404)
        self.post('comeback/steps/1/complete',{'plan_id':p['id']},404)

    def test_personas_have_distinct_diagnoses(self):
        for slug,expected in [('amina','time'),('mariam','connectivity'),('david','relevance')]:
            u=reset_persona(slug)
            snapshot=simulate(u,'persona')
            self.assertEqual(FrictionEvent.objects.filter(user=u).last().primary_friction,expected)
            self.assertLess(snapshot.score,60)

    def test_plan_download(self):
        p=self.prepare_recovery()
        r=self.client.get('/api/v1/comeback/download/')
        self.assertEqual(r.status_code,200)
        self.assertIn(p['title'].encode(),r.content)

    def test_invalid_answers_and_confidence_rejected(self):
        m=Mission.objects.filter(milestone__course__slug='data-analysis',milestone__sequence=3).first()
        self.post(f'missions/{m.id}/start')
        self.post(f'assessments/{m.assessment.id}/submit',{'answers':[999,0]},400)
        self.post('profile',{'minutes':-5,'confidence':100,'connectivity':'HIGH'},400)

    @override_settings(AI_SERVICE_URL='https://gateway.example/generate')
    @patch('apps.ai.services.urlopen',side_effect=TimeoutError)
    def test_ai_timeout_keeps_deterministic_plan(self,mock):
        p=self.prepare_recovery()
        self.assertEqual(p['source'],'Curated recovery template')
        self.assertEqual(len(p['steps']),3)

    def test_schema_rejects_unbounded_ai_output(self):
        with self.assertRaises(ValueError):
            PlanCopy.model_validate({'title':'x','reason':'x','encouragement':'x','extra':'unknown'})

    def test_long_absence_is_not_reset_to_new_learner_baseline(self):
        LearnerSignal.objects.filter(user=self.tunde).update(recorded_at=timezone.now()-timedelta(days=20))
        MomentumSnapshot.objects.filter(user=self.tunde).update(calculated_at=timezone.now()-timedelta(days=20))
        s=self.state()
        self.assertLess(s['momentum']['score'],40)
        self.assertIsNotNone(s['friction'])
        latest=MomentumSnapshot.objects.filter(user=self.tunde).last()
        self.assertGreaterEqual(latest.metrics['days_since_last_activity'],20)

    def test_first_session_does_not_trigger_false_inactivity(self):
        u=User.objects.create_user(username='newbie',email='newbie@example.com',name='New learner')
        LearnerProfile.objects.create(user=u,baseline_knowledge=0,confidence_score=20)
        self.client.force_login(u)
        m=Mission.objects.first()
        self.post(f'missions/{m.id}/start')
        self.post(f'assessments/{m.assessment.id}/submit',{'answers':[(q['answer']+1)%len(q['options']) for q in m.assessment.rubric]})
        self.assertFalse(FrictionEvent.objects.filter(user=u).exists())
