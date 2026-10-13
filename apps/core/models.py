from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings


class User(AbstractUser):
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=120)
    preferred_language = models.CharField(max_length=8, default='en')
    timezone = models.CharField(max_length=60, default='Africa/Lagos')
    is_demo = models.BooleanField(default=False)

    @property
    def profile(self):
        from .courses import course_for
        return LearnerProfile.objects.get_or_create(user=self, course=course_for(self))[0]

    def __str__(self):
        return self.name or self.username


class Course(models.Model):
    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=120)
    description = models.TextField()
    category = models.CharField(max_length=60, default='General learning')
    level = models.CharField(max_length=40, default='Beginner')
    default_goal = models.CharField(max_length=200)
    success_example = models.CharField(max_length=300)
    starting_example = models.CharField(max_length=300, blank=True)
    diagnostic = models.JSONField(default=list)
    is_published = models.BooleanField(default=False)
    sequence = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['sequence', 'id']

    def __str__(self):
        return self.title

    def clean(self):
        from django.core.exceptions import ValidationError
        from .courses import validate_questions
        if self.diagnostic:
            validate_questions(self.diagnostic)
        if self.is_published:
            if not self.diagnostic or len(self.diagnostic) < 2:
                raise ValidationError('Published courses need at least two diagnostic questions.')
            milestones = list(self.milestone_set.all()) if self.pk else []
            if not milestones:
                raise ValidationError('Save a draft, then add milestones, lessons, and checkpoints before publishing.')
            for milestone in milestones:
                if milestone.competency.course_id != self.pk:
                    raise ValidationError('Every competency must belong to this course.')
                missions = list(milestone.missions.all())
                if not missions:
                    raise ValidationError('Each milestone needs at least one lesson.')
                for mission in missions:
                    if not mission.content.strip() or not hasattr(mission, 'assessment'):
                        raise ValidationError('Each lesson needs content and a checkpoint.')
                    validate_questions(mission.assessment.rubric)


class Owned(models.Model):
    course = models.ForeignKey(Course, on_delete=models.PROTECT, default=1)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True


class LearnerProfile(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='course_profiles')
    course = models.ForeignKey(Course, on_delete=models.PROTECT, default=1)
    baseline_knowledge = models.FloatField(default=50)
    confidence_score = models.FloatField(default=60)
    available_minutes_daily = models.PositiveIntegerField(default=20)
    preferred_session_minutes = models.PositiveIntegerField(default=20)
    connectivity_level = models.CharField(max_length=10, default='HIGH')
    current_momentum_score = models.FloatField(default=50)
    current_state = models.CharField(max_length=20, default='SLOWING')
    updated_at = models.DateTimeField(auto_now=True)


    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'course'], name='unique_course_profile')]


class GoalContract(Owned):
    goal = models.TextField()
    context = models.TextField(blank=True)
    constraints = models.JSONField(default=list)
    done_when = models.TextField()
    target_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, default='ACTIVE')


class DiagnosticSession(Owned):
    goal_contract = models.ForeignKey(GoalContract, on_delete=models.CASCADE)
    completed_at = models.DateTimeField(null=True)
    knowledge_score = models.FloatField(default=0)
    assimilation_score = models.FloatField(default=0)
    confidence_score = models.FloatField(default=0)
    reading_duration = models.PositiveIntegerField(default=0)
    recommended_session_minutes = models.PositiveIntegerField(default=15)
    raw_results = models.JSONField(default=dict)


class Competency(models.Model):
    course = models.ForeignKey(Course, on_delete=models.PROTECT, default=1)
    code = models.CharField(max_length=8, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    domain = models.CharField(max_length=50, default='Data analysis')

    def __str__(self):
        return self.name


class LearningPath(Owned):
    goal_contract = models.ForeignKey(GoalContract, on_delete=models.CASCADE)
    title = models.CharField(max_length=120, default='Data Analysis Foundations')
    description = models.TextField(default='From your first dataset to a decision that matters.')
    status = models.CharField(max_length=20, default='ACTIVE')


class Milestone(models.Model):
    course = models.ForeignKey(Course, on_delete=models.PROTECT, default=1)
    title = models.CharField(max_length=120)
    description = models.TextField()
    competency = models.OneToOneField(Competency, on_delete=models.CASCADE)
    sequence = models.PositiveIntegerField()

    class Meta:
        ordering = ['course__sequence', 'sequence']
        constraints = [models.UniqueConstraint(fields=['course', 'sequence'], name='unique_course_milestone_sequence')]

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.competency_id and self.competency.course_id != self.course_id:
            raise ValidationError({'competency': 'Choose a competency from this course.'})

    def __str__(self):
        return self.title


class Mission(models.Model):
    milestone = models.ForeignKey(Milestone, on_delete=models.CASCADE, related_name='missions')
    title = models.CharField(max_length=150)
    description = models.TextField()
    content = models.TextField()
    low_bandwidth_content = models.TextField(blank=True)
    source_url = models.URLField(blank=True)
    source_title = models.CharField(max_length=200, blank=True)
    estimated_minutes = models.PositiveIntegerField(default=15)
    difficulty = models.CharField(max_length=20, default='BEGINNER')
    sequence = models.PositiveIntegerField()

    class Meta:
        ordering = ['milestone__sequence', 'sequence']
        constraints = [models.UniqueConstraint(fields=['milestone', 'sequence'], name='unique_mission_sequence')]

    def __str__(self):
        return self.title


class LearnerMission(Owned):
    mission = models.ForeignKey(Mission, on_delete=models.CASCADE)
    status = models.CharField(max_length=20, default='NOT_STARTED')
    started_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)
    actual_duration_seconds = models.PositiveIntegerField(default=0)
    attempt_count = models.PositiveIntegerField(default=0)
    hint_count = models.PositiveIntegerField(default=0)
    abandon_count = models.PositiveIntegerField(default=0)
    last_activity_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'mission'], name='unique_learner_mission')]


class Assessment(models.Model):
    mission = models.OneToOneField(Mission, on_delete=models.CASCADE, related_name='assessment')
    title = models.CharField(max_length=150)
    assessment_type = models.CharField(max_length=20, default='QUIZ')
    instructions = models.TextField(default='Apply what you learned. You can retry as often as you need.')
    rubric = models.JSONField(default=list)
    maximum_score = models.PositiveIntegerField(default=100)
    pass_score = models.PositiveIntegerField(default=70)


    def clean(self):
        from .courses import validate_questions
        from django.core.exceptions import ValidationError
        validate_questions(self.rubric)
        if not 1 <= self.pass_score <= self.maximum_score or self.maximum_score != 100:
            raise ValidationError('Checkpoints use a 100-point scale and a pass score from 1 to 100.')


class AssessmentAttempt(Owned):
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE)
    submission = models.JSONField(default=dict)
    score = models.FloatField()
    feedback = models.TextField()
    attempt_number = models.PositiveIntegerField()
    duration_seconds = models.PositiveIntegerField(default=0)
    passed = models.BooleanField(default=False)


class LearnerCompetency(Owned):
    competency = models.ForeignKey(Competency, on_delete=models.CASCADE)
    status = models.CharField(max_length=20, default='NOT_STARTED')
    score = models.FloatField(default=0)
    demonstrated_at = models.DateTimeField(null=True)
    evidence = models.ForeignKey(AssessmentAttempt, on_delete=models.SET_NULL, null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'competency'], name='unique_learner_competency')]


class LearnerSignal(Owned):
    signal_type = models.CharField(max_length=40)
    numeric_value = models.FloatField(null=True)
    text_value = models.CharField(max_length=200, blank=True)
    metadata = models.JSONField(default=dict)
    recorded_at = models.DateTimeField(db_index=True)

    class Meta:
        indexes = [models.Index(fields=['user', 'recorded_at'])]


class MomentumSnapshot(Owned):
    score = models.FloatField()
    state = models.CharField(max_length=20)
    consistency_score = models.FloatField()
    completion_score = models.FloatField()
    assessment_score = models.FloatField()
    engagement_score = models.FloatField()
    confidence_score = models.FloatField()
    metrics = models.JSONField(default=dict)
    calculated_at = models.DateTimeField(db_index=True)


class FrictionEvent(Owned):
    momentum_snapshot = models.ForeignKey(MomentumSnapshot, on_delete=models.CASCADE)
    primary_friction = models.CharField(max_length=40)
    confidence = models.FloatField()
    candidate_friction = models.JSONField(default=list)
    evidence = models.JSONField(default=list)
    learner_confirmation = models.CharField(max_length=20, blank=True)
    learner_feedback = models.TextField(blank=True)
    confirmation_question = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=30, default='AWAITING_CONFIRMATION')


class Intervention(models.Model):
    name = models.CharField(max_length=120)
    friction_type = models.CharField(max_length=40)
    strategy_type = models.CharField(max_length=40, unique=True)
    description = models.TextField()
    minimum_minutes = models.PositiveIntegerField(default=5)
    maximum_minutes = models.PositiveIntegerField(default=20)
    template = models.JSONField(default=dict)
    measurement_strategy = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)


class RecoveryPlan(Owned):
    friction_event = models.OneToOneField(FrictionEvent, on_delete=models.CASCADE)
    intervention = models.ForeignKey(Intervention, on_delete=models.PROTECT)
    title = models.CharField(max_length=160)
    reason = models.TextField()
    plan_json = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default='READY')
    started_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField(null=True)


class RecoveryBaseline(models.Model):
    recovery_plan = models.OneToOneField(RecoveryPlan, on_delete=models.CASCADE, related_name='baseline')
    momentum_score = models.FloatField()
    assessment_average = models.FloatField()
    confidence_score = models.FloatField()
    completion_rate = models.FloatField()
    captured_at = models.DateTimeField(auto_now_add=True)


class RecoveryResult(models.Model):
    recovery_plan = models.OneToOneField(RecoveryPlan, on_delete=models.CASCADE, related_name='result')
    pre_momentum = models.FloatField()
    post_momentum = models.FloatField()
    pre_assessment = models.FloatField()
    post_assessment = models.FloatField()
    pre_confidence = models.FloatField()
    post_confidence = models.FloatField()
    completion_percentage = models.FloatField()
    recovery_score = models.FloatField()
    outcome = models.CharField(max_length=40)
    components = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
