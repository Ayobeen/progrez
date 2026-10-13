"""Explicit course context for requests, engines, and background jobs.

The context lives on a request-local User instance, never in a process-global
variable. Shared Circle links resolve their own stored course independently.
"""
from django.core.exceptions import ValidationError
from rest_framework.exceptions import NotFound
from .models import Course, LearnerProfile, LearnerMission


def course_for(user):
    return getattr(user, '_learning_course', None) or Course.objects.get(slug='data-analysis')


def bind_course(user, course):
    user._learning_course = course
    return user


def bind_request(request, user):
    # An explicit tab header wins over the session, so switching in another tab
    # cannot submit an old assessment or preferences to the newly selected course.
    slug = request.headers.get('X-Progrez-Course') or request.GET.get('course') or request.session.get('course_slug')
    if slug:
        course = Course.objects.filter(slug=slug, is_published=True).first()
        if not course:
            raise NotFound('This course is unavailable. Choose another course.')
        bind_course(user, course)
    return user


def course_payload(course):
    return {key: getattr(course, key) for key in (
        'id', 'slug', 'title', 'description', 'category', 'level',
        'default_goal', 'success_example', 'starting_example')}


def catalog(user):
    profiles = set(LearnerProfile.objects.filter(user=user).values_list('course_id', flat=True))
    rows = []
    for course in Course.objects.filter(is_published=True):
        from .models import Mission, DiagnosticSession
        total = Mission.objects.filter(milestone__course=course).count()
        completed = LearnerMission.objects.filter(user=user, course=course, status='COMPLETED').count()
        rows.append({**course_payload(course), 'enrolled': course.id in profiles,
                     'missions': total, 'milestones': course.milestone_set.count(),
                     'completed_missions': completed, 'progress': round(completed / max(total, 1) * 100),
                     'diagnosed': DiagnosticSession.objects.filter(user=user, course=course, completed_at__isnull=False).exists()})
    return rows


def validate_questions(questions):
    if not isinstance(questions, list) or not questions:
        raise ValidationError('Provide a non-empty list of checkpoint questions.')
    for q in questions:
        if not isinstance(q, dict) or not isinstance(q.get('prompt'), str) or not q['prompt'].strip():
            raise ValidationError('Each question needs a prompt.')
        options = q.get('options')
        answer = q.get('answer')
        if not isinstance(options, list) or len(options) < 2 or any(not isinstance(o, str) or not o.strip() for o in options):
            raise ValidationError('Each question needs at least two non-empty options.')
        if type(answer) is not int or not 0 <= answer < len(options):
            raise ValidationError('Each answer must be a valid zero-based option index.')
        if not isinstance(q.get('explanation'), str) or not q['explanation'].strip():
            raise ValidationError('Each question needs an explanation.')
