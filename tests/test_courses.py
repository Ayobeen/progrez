from django.test import TestCase, Client, override_settings
from django.core.exceptions import ValidationError
from django.core import mail
from apps.core.models import (Course, User, LearnerProfile, GoalContract, DiagnosticSession,
    Mission, Milestone, AssessmentAttempt, LearnerMission, LearnerSignal, MomentumSnapshot,
    RecoveryPlan, FrictionEvent, Competency, Assessment)
from apps.core.seed import seed_content, reset_persona
from apps.core.courses import bind_course
from apps.circle.models import JourneyPrivacy, SupportMember, Achievement, CircleNotification, Encouragement, CircleEmail
from apps.circle.services import privacy_for, supporter_token, deliver_email, email_fingerprint
from django.utils import timezone


class CourseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_content()
        cls.da = Course.objects.get(slug='data-analysis')
        cls.py = Course.objects.get(slug='introduction-to-python')
        cls.user = reset_persona('tunde')

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.client.force_login(self.user)
        self.guest = Client()

    def post(self, route, data=None, course=None, status=200, client=None):
        headers = {'HTTP_X_PROGREZ_COURSE': course.slug} if course else {}
        response = (client or self.client).post('/api/v1/'+route+'/', data or {}, content_type='application/json', **headers)
        self.assertEqual(response.status_code, status, response.content[:1200])
        return response.json()

    def get(self, route, course=None, status=200, client=None):
        headers = {'HTTP_X_PROGREZ_COURSE': course.slug} if course else {}
        response = (client or self.client).get('/api/v1/'+route+'/', **headers)
        self.assertEqual(response.status_code, status, response.content[:1200])
        return response.json()

    def select(self, course):
        return self.post('courses/select', {'slug': course.slug})

    def onboard(self, course):
        self.select(course)
        self.post('goals', {'goal': course.default_goal, 'done_when': course.success_example, 'available_minutes_daily': 10}, course)
        diagnostic = self.post('diagnostics/start', course=course)
        self.post(f"diagnostics/{diagnostic['id']}/complete", {'answers': [q['answer'] for q in course.diagnostic]}, course)
        return diagnostic

    def complete(self, mission, course):
        self.post(f'missions/{mission.id}/start', course=course)
        return self.post(f'assessments/{mission.assessment.id}/submit', {'answers': [q['answer'] for q in mission.assessment.rubric]}, course)

    def share(self, course, visibility='PUBLIC'):
        privacy = privacy_for(bind_course(self.user, course))
        data = {'display_name':'Tunde', 'slug':privacy.slug, 'visibility':visibility, 'allow_encouragement':True}
        data.update({k: True for k in ['show_goal','show_course','show_progress','show_competencies','show_milestones','show_achievements']})
        self.post('circle/privacy', data, course)
        privacy.refresh_from_db()
        return privacy

    def test_catalog_has_two_standalone_courses_and_no_answer_keys(self):
        data = self.get('courses')
        self.assertEqual(len(data['courses']), 2)
        self.assertEqual([c['missions'] for c in data['courses']], [12,12])
        self.assertEqual([c['completed_missions'] for c in data['courses']], [4,0])
        self.assertNotIn('diagnostic', str(data))
        self.assertFalse(data['can_add_course'])
        self.assertEqual(Mission.objects.filter(milestone__course=self.da).count(), 12)

    def test_login_and_registration_offer_course_selection(self):
        self.client.logout()
        self.post('auth/register', {'name':'New learner','email':'new@courses.test','password':'A-long-password-281!'}, status=201)
        self.assertIsNone(self.get('auth/me')['course'])
        self.assertEqual(LearnerProfile.objects.filter(user__email='new@courses.test').count(), 0)
        self.select(self.py)
        self.assertEqual(self.get('auth/me')['course']['slug'], self.py.slug)
        self.post('auth/logout')
        logged = self.post('auth/login', {'email':'new@courses.test','password':'A-long-password-281!'})
        self.assertIsNone(logged['course'])
        self.assertEqual(len(logged['courses']), 2)

    def test_python_diagnostic_and_progress_do_not_change_data_analysis(self):
        before = self.get('state', self.da)
        diag = self.onboard(self.py)
        self.assertIn('print', diag['questions'][0]['prompt'])
        self.assertNotIn('answer', diag['questions'][0])
        mission = Mission.objects.filter(milestone__course=self.py).first()
        self.complete(mission, self.py)
        py = self.get('state', self.py)
        after = self.get('state', self.da)
        self.assertEqual(py['completed_missions'], 1)
        self.assertEqual(py['progress'], 8)
        self.assertEqual(after['completed_missions'], before['completed_missions'])
        self.assertEqual(after['momentum'], before['momentum'])
        self.assertEqual(after['goal'], before['goal'])
        self.assertEqual(py['profile']['preferred_session_minutes'], 10)
        self.assertEqual(len(py['milestones']), 6)

    def test_cross_course_ids_are_rejected_for_mutations_and_reads(self):
        diagnostic = self.onboard(self.py)
        mission = Mission.objects.filter(milestone__course=self.py).first()
        self.get(f'missions/{mission.id}', self.da, 403)
        self.post(f'missions/{mission.id}/start', course=self.da, status=403)
        self.post(f'assessments/{mission.assessment.id}/submit', {'answers':[1,0]}, self.da, 403)
        self.post(f"diagnostics/{diagnostic['id']}/answer", {'question':0,'answer':0}, self.da, 404)
        goal = GoalContract.objects.get(user=self.user, course=self.py, status='ACTIVE')
        self.post(f'goals/{goal.id}', {'goal':'Wrong course','done_when':'No'}, self.da, 404)
        self.assertFalse(LearnerMission.objects.filter(user=self.user, mission=mission).exists())

    def test_course_header_keeps_an_old_tab_bound_after_session_switch(self):
        self.select(self.py)
        self.assertEqual(self.get('state')['course']['slug'], self.py.slug)
        self.assertEqual(self.get('state', self.da)['course']['slug'], self.da.slug)
        self.post('profile', {'minutes':7,'confidence':33,'connectivity':'LOW','timezone':'Africa/Lagos'}, self.py)
        self.assertEqual(self.get('state', self.py)['profile']['confidence_score'], 33)
        self.assertEqual(self.get('state', self.da)['profile']['confidence_score'], 78)

    def test_python_comeback_uses_python_content_and_isolated_baseline(self):
        self.onboard(self.py)
        self.post('demo/simulate', {'scenario':'failures'}, self.py)
        py = self.get('state', self.py)
        plan = self.post('comeback/confirm-friction', {'event_id':py['friction']['id'],'confirmation':'yes','friction':'prerequisite_gap'}, self.py)
        target = Mission.objects.get(id=plan['target_mission_id'])
        self.assertEqual(target.milestone.course_id, self.py.id)
        self.assertIn('print', plan['steps'][0]['content'])
        self.post('comeback/plan/start', {'plan_id':plan['id']}, self.da, 404)
        self.post('comeback/plan/start', {'plan_id':plan['id']}, self.py)
        for step in (1,2):
            self.post(f'comeback/steps/{step}/complete', {'plan_id':plan['id']}, self.py)
        self.post(f'missions/{target.id}/start', course=self.py)
        result = self.post(f'assessments/{target.assessment.id}/submit', {'answers':[q['answer'] for q in target.assessment.rubric], 'recovery_plan_id':plan['id'], 'confidence':85}, self.py)
        self.assertIsNotNone(result['recovery'])
        self.assertIsNone(self.get('state', self.da)['result'])
        self.assertIsNone(self.get('state', self.da)['friction'])
        self.assertEqual(RecoveryPlan.objects.get(id=plan['id']).course_id, self.py.id)

    def test_circle_links_privacy_and_inboxes_are_course_specific(self):
        self.onboard(self.py)
        da_privacy = self.share(self.da)
        py_privacy = self.share(self.py)
        self.assertNotEqual(da_privacy.slug, py_privacy.slug)
        py = self.get('circle/public/'+py_privacy.slug, client=self.guest)
        self.assertEqual(py['profile']['course'], self.py.title)
        self.assertEqual(py['profile']['progress'], 0)
        self.assertFalse(py['profile']['competencies'])
        self.assertNotIn('Data fundamentals', str(py))
        self.post(f'circle/public/{py_privacy.slug}/encourage', {'kind':'PROUD'}, client=self.guest)
        self.assertEqual(self.get('circle/inbox', self.py)['unread'], 1)
        self.assertEqual(self.get('circle/inbox', self.da)['unread'], 0)
        message = Encouragement.objects.get(course=self.py)
        self.post(f'circle/encouragement/{message.id}/seen', course=self.da, status=404)
        self.share(self.da, 'PRIVATE')
        self.get('circle/public/'+py_privacy.slug, client=self.guest)
        self.get('circle/public/'+da_privacy.slug, status=404, client=self.guest)

    def test_only_python_followers_receive_python_milestones(self):
        self.onboard(self.py)
        da_privacy = self.share(self.da)
        py_privacy = self.share(self.py)
        self.post(f'circle/public/{da_privacy.slug}/follow', {'name':'DA friend'}, client=self.guest)
        py_follow = self.post(f'circle/public/{py_privacy.slug}/follow', {'name':'Python friend'}, client=self.guest)
        for mission in Mission.objects.filter(milestone__course=self.py, milestone__sequence=1):
            self.complete(mission, self.py)
        self.assertEqual(CircleNotification.objects.filter(member__course=self.py).count(), 1)
        self.assertFalse(CircleNotification.objects.filter(member__course=self.da).exists())
        # Link context wins even when an unrelated course is selected in the browser.
        token = py_follow['support_path'].split('/')[-1]
        data = self.get('circle/support/'+token, self.da, client=self.guest)
        self.assertEqual(data['profile']['course'], self.py.title)
        self.assertEqual(data['notifications'][0]['achievement']['title'], 'Python basics completed')
        member = SupportMember.objects.get(course=self.py)
        self.post(f'circle/members/{member.id}/control', {'action':'REVOKE'}, self.da, 404)

    @override_settings(CIRCLE_EMAIL_ENABLED=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_email_uses_the_members_course_even_when_another_course_is_private(self):
        self.onboard(self.py)
        py_privacy = self.share(self.py)
        member = SupportMember.objects.create(learner=self.user, course=self.py, name='Python supporter', status='ACTIVE', email='supporter@example.test', email_updates=True, email_verified_at=timezone.now())
        for mission in Mission.objects.filter(milestone__course=self.py, milestone__sequence=1):
            self.complete(mission, self.py)
        delivery = CircleEmail.objects.get(member=member, kind='MILESTONE')
        self.assertTrue(deliver_email(delivery))
        self.assertIn('Python basics completed', mail.outbox[-1].body)
        self.assertNotIn('Data fundamentals', mail.outbox[-1].body)

    def test_course_demo_reset_preserves_other_course_and_links(self):
        self.onboard(self.py)
        self.share(self.py)
        py_goal = GoalContract.objects.get(user=self.user, course=self.py)
        py_slug = JourneyPrivacy.objects.get(learner=self.user, course=self.py).slug
        self.post('demo/reset', course=self.da)
        self.assertTrue(GoalContract.objects.filter(id=py_goal.id).exists())
        self.assertEqual(JourneyPrivacy.objects.get(learner=self.user, course=self.py).slug, py_slug)
        self.assertEqual(self.get('state', self.da)['completed_missions'], 4)

    def test_full_python_course_completes_without_data_analysis_leakage(self):
        self.onboard(self.py)
        for mission in Mission.objects.filter(milestone__course=self.py):
            self.complete(mission, self.py)
        py = self.get('state', self.py)
        self.assertEqual(py['progress'], 100)
        self.assertEqual(py['completed_missions'], 12)
        self.assertEqual(len([c for c in py['competencies'] if c['status']=='DEMONSTRATED']), 6)
        self.assertIsNone(py['next_mission'])
        self.assertEqual(Achievement.objects.get(learner=self.user, course=self.py, event_key='path-complete').title, 'Introduction to Python completed')
        self.assertEqual(self.get('state', self.da)['completed_missions'], 4)

    def test_staff_can_add_a_third_course_without_code_changes(self):
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()
        self.assertTrue(self.get('courses')['can_add_course'])
        course = Course.objects.create(slug='writing', title='Clear Writing', description='Write clearly.', default_goal='Write clearly', success_example='I can edit a paragraph.', diagnostic=self.py.diagnostic[:2])
        self.post('courses/select', {'slug':'writing'}, status=404)
        competency = Competency.objects.create(course=course, code='WR001', name='Edit a paragraph')
        milestone = Milestone.objects.create(course=course, sequence=1, title='Clear sentences', description='Edit.', competency=competency)
        lesson = Mission.objects.create(milestone=milestone, title='Use concrete words', description='Write simply.', content='Choose familiar words.', sequence=1)
        Assessment.objects.create(mission=lesson, title='Writing checkpoint', rubric=[{'prompt':'Which is simpler?', 'options':['Use','Utilize'], 'answer':0,'explanation':'Use is shorter and familiar.'}])
        course.is_published = True
        course.full_clean()
        course.save()
        self.assertEqual(len(self.get('courses')['courses']), 3)
        self.onboard(course)
        self.complete(lesson, course)
        state = self.get('state', course)
        self.assertEqual(state['progress'], 100)
        self.assertEqual(len(state['milestones']), 1)

    def test_incomplete_courses_cannot_be_published_and_invalid_context_fails(self):
        draft = Course(slug='draft',title='Draft',description='Draft',default_goal='Learn',success_example='Apply',diagnostic=self.py.diagnostic,is_published=True)
        with self.assertRaises(ValidationError):
            draft.full_clean()
        response = self.client.get('/api/v1/state/', HTTP_X_PROGREZ_COURSE='missing')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get('/api/v1/courses/').status_code, 200)

    def test_reseeding_is_idempotent_and_preserves_existing_progress(self):
        self.onboard(self.py)
        self.complete(Mission.objects.filter(milestone__course=self.py).first(), self.py)
        before = list(LearnerMission.objects.filter(user=self.user).values_list('id','course_id','status'))
        seed_content()
        self.assertEqual(Course.objects.count(), 2)
        self.assertEqual(Mission.objects.count(), 24)
        self.assertEqual(before, list(LearnerMission.objects.filter(user=self.user).values_list('id','course_id','status')))
