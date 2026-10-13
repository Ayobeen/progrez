import json
from datetime import timedelta
from unittest.mock import patch
from django.core import mail
from django.test import TestCase, Client, override_settings
from django.utils import timezone
from apps.core.models import User, Mission, LearnerMission, LearnerCompetency, LearnerSignal, GoalContract
from apps.core.seed import seed_content, reset_persona
from apps.core.api import complete_mission
from apps.circle.models import JourneyPrivacy, SupportMember, Achievement, CircleNotification, Encouragement, CircleEmail
from apps.circle.services import privacy_for, record_achievements, supporter_token, verification_token, deliver_email


class CircleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_content()
        cls.learner = reset_persona('tunde')
        cls.other = reset_persona('amina')

    def setUp(self):
        self.client.force_login(self.learner)
        self.guest = Client()
        self.privacy = JourneyPrivacy.objects.get(learner=self.learner)

    def post(self, path, data=None, status=200, client=None):
        response = (client or self.client).post('/api/v1/circle/'+path+'/', data=data or {}, content_type='application/json')
        self.assertEqual(response.status_code, status, response.content[:1500])
        return response.json()

    def get(self, path, status=200, client=None):
        response = (client or self.client).get('/api/v1/circle/'+path+'/')
        self.assertEqual(response.status_code, status, response.content[:1500])
        return response.json()

    def share(self, visibility='PUBLIC', **changes):
        payload = {'display_name':'Tunde', 'slug':self.privacy.slug, 'visibility':visibility, 'show_goal':True, 'show_course':True, 'show_progress':True, 'show_competencies':True, 'show_milestones':True, 'show_achievements':True, 'allow_encouragement':True}
        payload.update(changes)
        self.post('privacy',payload)
        self.privacy.refresh_from_db()

    def follow(self, guest=None, **changes):
        self.share()
        values = {'name':'Tomi', 'notify_milestones':True, 'notify_competencies':True}
        values.update(changes)
        result = self.post(f'public/{self.privacy.slug}/follow',values,client=guest or self.guest)
        return result['support_path'].split('/')[-1]

    def invite(self):
        self.share('CIRCLE')
        data = self.post('invite',{'name':'Tomi','relationship':'SIBLING'},201)
        return data['support_path'].split('/')[-1], data['id']

    def complete_cleaning(self):
        for mission in Mission.objects.filter(milestone__course__slug='data-analysis',milestone__sequence=3):
            lm, _ = LearnerMission.objects.get_or_create(user=self.learner,mission=mission)
            complete_mission(self.learner,lm)

    def test_private_is_default_for_existing_and_new_learners(self):
        self.assertEqual(self.privacy.visibility,'PRIVATE')
        self.get(f'public/{self.privacy.slug}',404,client=self.guest)
        self.post(f'public/{self.privacy.slug}/encourage',{'kind':'PROUD'},404,client=self.guest)
        u = User.objects.create_user(username='private-new',email='private@example.com',name='Private learner')
        self.assertEqual(privacy_for(u).visibility,'PRIVATE')

    def test_public_allowlist_excludes_all_sensitive_fields(self):
        goal = GoalContract.objects.get(user=self.learner,status='ACTIVE')
        goal.context='secret prior experience';goal.constraints=['secret constraint'];goal.save()
        self.share()
        data = self.get(f'public/{self.privacy.slug}',client=self.guest)
        encoded = json.dumps(data)
        for private in ['secret prior experience','secret constraint','assessment_average','confidence_score','primary_friction','current_momentum_score','learner_feedback','recovery_score','numeric_value','@demo.progrez.local']:
            self.assertNotIn(private,encoded)
        self.assertEqual(set(data['profile']),{'slug','name','visibility','allow_encouragement','goal','course','progress','competencies','competencies_total','milestones','achievements'})

    def test_each_field_can_be_hidden_and_unknown_fields_rejected(self):
        self.share(**{key:False for key in ['show_goal','show_course','show_progress','show_competencies','show_milestones','show_achievements']})
        data = self.get(f'public/{self.privacy.slug}',client=self.guest)
        self.assertEqual(set(data['profile']),{'slug','name','visibility','allow_encouragement'})
        self.post('privacy',{'show_scores':True},400)

    def test_circle_invitation_and_revocation(self):
        token,mid = self.invite()
        self.get(f'public/{self.privacy.slug}',404,client=self.guest)
        self.get('support/'+token,client=self.guest)
        self.post(f'support/{token}/activate',{'name':'Tomi'},client=self.guest)
        self.get(f'public/{self.privacy.slug}',client=self.guest)
        self.post(f'members/{mid}/control',{'action':'REVOKE'})
        self.get('support/'+token,404,client=self.guest)
        self.get(f'public/{self.privacy.slug}',404,client=self.guest)

    def test_public_follower_does_not_gain_invited_circle_access(self):
        token = self.follow()
        self.share('CIRCLE')
        self.get('support/'+token,404,client=self.guest)
        self.get(f'public/{self.privacy.slug}',404,client=self.guest)
        member=SupportMember.objects.get(name='Tomi',learner=self.learner)
        self.post(f'members/{member.id}/control',{'action':'GRANT_CIRCLE'})
        self.get('support/'+token,client=self.guest)

    def test_privacy_change_invalidates_existing_supporter_reads_and_writes(self):
        token = self.follow()
        self.share('PRIVATE')
        self.get('support/'+token,404,client=self.guest)
        self.post(f'support/{token}/encourage',{'kind':'PROUD'},404,client=self.guest)

    def test_unsubscribe_remains_available_after_profile_becomes_private(self):
        token = self.follow()
        self.share('PRIVATE')
        self.post(f'support/{token}/unsubscribe', client=self.guest)
        self.assertEqual(SupportMember.objects.get(learner=self.learner).status, 'UNSUBSCRIBED')

    def test_following_does_not_reset_anonymous_encouragement_limit(self):
        self.share()
        self.post(f'public/{self.privacy.slug}/encourage', {'kind':'PROUD'}, client=self.guest)
        token = self.follow()
        self.post(f'support/{token}/encourage', {'kind':'CONGRATS'}, 429, client=self.guest)

    def test_anonymous_encouragement_idempotence_rate_limit_and_open_signal(self):
        self.share()
        path = f'public/{self.privacy.slug}/encourage'
        first = self.post(path,{'kind':'PROUD'},client=self.guest)
        repeat = self.post(path,{'kind':'PROUD'},client=self.guest)
        self.assertFalse(first['already_sent']);self.assertTrue(repeat['already_sent'])
        self.post(path,{'kind':'CONGRATS'},429,client=self.guest)
        self.assertEqual(Encouragement.objects.filter(learner=self.learner).count(),1)
        self.assertEqual(LearnerSignal.objects.filter(user=self.learner,signal_type='ENCOURAGEMENT_RECEIVED').count(),1)
        received = self.get('inbox')
        self.assertEqual(received['unread'],1)
        eid = received['messages'][0]['id']
        self.post(f'encouragement/{eid}/seen')
        self.post(f'encouragement/{eid}/seen')
        self.assertEqual(self.get('inbox')['unread'],0)
        self.assertEqual(LearnerSignal.objects.filter(user=self.learner,signal_type='ENCOURAGEMENT_OPENED').count(),1)

    def test_encouragement_does_not_change_momentum_or_claim_causation(self):
        token = self.follow()
        before = self.learner.profile.current_momentum_score
        self.post(f'support/{token}/encourage',{'kind':'PROUD'},client=self.guest)
        self.learner.profile.refresh_from_db()
        self.assertEqual(self.learner.profile.current_momentum_score,before)
        mission=Mission.objects.filter(milestone__course__slug='data-analysis',milestone__sequence=3,sequence=1).get()
        r=self.client.post(f'/api/v1/missions/{mission.id}/start/',{},content_type='application/json')
        self.assertEqual(r.status_code,200)
        signal=LearnerSignal.objects.get(user=self.learner,signal_type='LEARNING_RESUMED_AFTER_ENCOURAGEMENT')
        self.assertIn('not evidence of causation',signal.metadata['interpretation'])

    def test_block_and_encouragement_off_take_effect(self):
        token = self.follow()
        member=SupportMember.objects.get(learner=self.learner,name='Tomi')
        self.post(f'support/{token}/encourage',{'kind':'PROUD'},client=self.guest)
        self.post(f'members/{member.id}/control',{'action':'BLOCK'})
        self.assertEqual(self.get('inbox')['total'],0)
        self.post(f'public/{self.privacy.slug}/encourage',{'kind':'PROUD'},403,client=self.guest)
        self.share(allow_encouragement=False)
        self.post(f'public/{self.privacy.slug}/encourage',{'kind':'PROUD'},403,client=Client())

    def test_ownership_controls_and_private_inbox(self):
        token,mid=self.invite()
        self.get('manage',403,client=self.guest)
        self.get('inbox',403,client=self.guest)
        self.client.force_login(self.other)
        self.post(f'members/{mid}/control',{'action':'REVOKE'},404)
        self.assertEqual(SupportMember.objects.get(id=mid).status,'INVITED')

    def test_milestone_notification_once_and_encouragement_attached(self):
        token=self.follow()
        self.assertEqual(CircleNotification.objects.count(),0)
        self.complete_cleaning()
        notifications=self.get('support/'+token,client=self.guest)['notifications']
        self.assertEqual(len(notifications),1)
        self.assertIn('Data cleaning',notifications[0]['achievement']['title'])
        self.complete_cleaning()
        self.assertEqual(CircleNotification.objects.count(),1)
        self.post(f'support/{token}/encourage',{'kind':'PROUD','achievement_id':notifications[0]['achievement']['id']},client=self.guest)
        self.assertEqual(self.get('inbox')['messages'][0]['name'],'Tomi')

    def test_muted_and_unsubscribed_supporters_do_not_receive_new_events(self):
        token=self.follow()
        member=SupportMember.objects.get(learner=self.learner,name='Tomi')
        self.post(f'members/{member.id}/control',{'action':'MUTE'})
        self.complete_cleaning()
        self.assertEqual(CircleNotification.objects.count(),0)
        self.post(f'support/{token}/unsubscribe',client=self.guest)
        self.get('support/'+token,404,client=self.guest)

    def test_hidden_achievement_removed_from_existing_notification(self):
        token=self.follow()
        self.complete_cleaning()
        self.assertEqual(len(self.get('support/'+token,client=self.guest)['notifications']),1)
        self.share(show_achievements=False)
        response=self.get('support/'+token,client=self.guest)
        self.assertEqual(response['notifications'],[])
        self.assertNotIn('achievements',response['profile'])

    def test_competency_only_preference_obeys_field_visibility(self):
        token=self.follow(notify_milestones=False,notify_competencies=True)
        self.share(show_milestones=False)
        self.complete_cleaning()
        data=self.get('support/'+token,client=self.guest)
        self.assertEqual(len(data['notifications']),1)
        self.assertTrue(data['notifications'][0]['achievement']['title'].startswith('Skill demonstrated:'))
        self.assertNotIn('milestones',data['profile'])

    def test_backfill_is_idempotent_and_silent(self):
        token=self.follow()
        Achievement.objects.filter(learner=self.learner).delete()
        record_achievements(self.learner,notify=False)
        before=Achievement.objects.filter(learner=self.learner).count()
        record_achievements(self.learner,notify=False)
        self.assertEqual(Achievement.objects.filter(learner=self.learner).count(),before)
        self.assertEqual(CircleNotification.objects.count(),0)

    def test_tampered_expired_tokens_and_cache_headers(self):
        token,mid=self.invite()
        self.get('support/'+token+'x',404,client=self.guest)
        with patch('django.core.signing.time.time',return_value=(timezone.now()-timedelta(days=91)).timestamp()):
            old=supporter_token(SupportMember.objects.get(id=mid))
        self.get('support/'+old,404,client=self.guest)
        response=self.guest.get('/api/v1/circle/support/'+token+'/')
        self.assertEqual(response['Cache-Control'],'no-store, private')
        self.assertEqual(response['Referrer-Policy'],'no-referrer')
        self.assertEqual(response['X-Robots-Tag'],'noindex, nofollow')

    def test_anonymous_csrf_and_honeypot(self):
        self.share()
        client=Client(enforce_csrf_checks=True)
        path=f'/api/v1/circle/public/{self.privacy.slug}/encourage/'
        self.assertEqual(client.post(path,{'kind':'PROUD'}).status_code,403)
        client.get('/u/'+self.privacy.slug)
        token=client.cookies['csrftoken'].value
        self.assertEqual(client.post(path,{'kind':'PROUD'},HTTP_X_CSRFTOKEN=token).status_code,200)
        self.post(f'public/{self.privacy.slug}/encourage',{'kind':'PROUD','website':'spam.example'},400,client=self.guest)

    def test_network_limit_cannot_be_bypassed_by_forwarded_header(self):
        self.share()
        path=f'public/{self.privacy.slug}/encourage'
        for _ in range(10):
            self.post(path,{'kind':'PROUD'},client=Client())
        response=Client().post('/api/v1/circle/'+path+'/',{'kind':'PROUD'},HTTP_X_FORWARDED_FOR='203.0.113.7')
        self.assertEqual(response.status_code,429)

    def test_email_is_unavailable_by_default(self):
        self.share()
        self.post(f'public/{self.privacy.slug}/follow',{'name':'Tomi','email':'tomi@example.com','email_updates':True},400,client=self.guest)
        self.assertEqual(SupportMember.objects.filter(learner=self.learner).count(),0)

    @override_settings(CIRCLE_EMAIL_ENABLED=True,EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_email_requires_confirmation_and_rechecks_privacy_before_delivery(self):
        token=self.follow(email='tomi@example.com',email_updates=True)
        member=SupportMember.objects.get(learner=self.learner,name='Tomi')
        self.assertIsNone(member.email_verified_at)
        verify=CircleEmail.objects.get(member=member,kind='VERIFY')
        self.assertTrue(deliver_email(verify))
        self.assertEqual(len(mail.outbox),1)
        self.assertNotIn('assessment',mail.outbox[0].body.lower())
        vt=verification_token(member)
        self.get('verify/'+vt,client=self.guest)
        member.refresh_from_db();self.assertIsNone(member.email_verified_at)
        self.post('verify/'+vt,client=self.guest)
        member.refresh_from_db();self.assertIsNotNone(member.email_verified_at)
        self.complete_cleaning()
        delivery=CircleEmail.objects.get(member=member,kind='MILESTONE')
        self.share('PRIVATE')
        self.assertFalse(deliver_email(delivery))
        delivery.refresh_from_db();self.assertEqual(delivery.state,'SKIPPED')
        self.assertEqual(len(mail.outbox),1)

    @override_settings(CIRCLE_EMAIL_ENABLED=True,EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_verified_milestone_email_contains_only_permitted_fields(self):
        token=self.follow(email='tomi@example.com',email_updates=True)
        member=SupportMember.objects.get(learner=self.learner,name='Tomi')
        self.post('verify/'+verification_token(member),client=self.guest)
        self.complete_cleaning()
        delivery=CircleEmail.objects.get(member=member,kind='MILESTONE')
        self.assertTrue(deliver_email(delivery))
        self.assertIn('Data cleaning completed',mail.outbox[0].body)
        self.assertNotIn('assessment_average',mail.outbox[0].body)
        self.assertFalse(deliver_email(delivery))
        self.assertEqual(len(mail.outbox),1)

    def test_reset_revokes_demo_circle_without_touching_real_learner(self):
        token=self.follow()
        oldslug=self.privacy.slug
        reset_persona('tunde')
        self.get('support/'+token,404,client=self.guest)
        self.get('public/'+oldslug,404,client=self.guest)
        self.assertEqual(JourneyPrivacy.objects.get(learner=self.learner).visibility,'PRIVATE')
