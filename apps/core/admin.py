from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from . import models

@admin.register(models.User)
class ProgrezUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (('Learner', {'fields': ('name', 'preferred_language', 'timezone', 'is_demo')}),)
    list_display = ('email', 'name', 'is_demo', 'is_staff')

for name in ['LearnerProfile', 'GoalContract', 'DiagnosticSession', 'Competency', 'LearningPath', 'LearnerMission', 'AssessmentAttempt', 'LearnerCompetency', 'LearnerSignal', 'MomentumSnapshot', 'FrictionEvent', 'Intervention', 'RecoveryPlan', 'RecoveryBaseline', 'RecoveryResult']:
    admin.site.register(getattr(models, name))
admin.site.site_header = 'Progrez administration'


@admin.register(models.Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'is_published', 'sequence')
    list_filter = ('is_published', 'category')
    prepopulated_fields = {'slug': ('title',)}
    fieldsets = (
        ('Course identity', {'fields': ('title', 'slug', 'description', 'category', 'level', 'sequence')}),
        ('Learner starting point', {'fields': ('default_goal', 'success_example', 'starting_example', 'diagnostic'), 'description': 'Diagnostic JSON: a list of prompt, options, answer (zero-based index), and explanation. The final question checks understanding of a short explanation.'}),
        ('Publish when ready', {'fields': ('is_published',), 'description': 'Save a draft first. Add competencies and milestones for this course, then missions with assessment checkpoints. Return here to publish. Incomplete courses cannot be published.'}),
    )


@admin.register(models.Milestone)
class MilestoneAdmin(admin.ModelAdmin):
    list_display = ('title', 'course', 'sequence', 'competency')
    list_filter = ('course',)


class AssessmentInline(admin.StackedInline):
    model = models.Assessment
    extra = 1
    max_num = 1


@admin.register(models.Mission)
class MissionAdmin(admin.ModelAdmin):
    list_display = ('title', 'milestone', 'sequence', 'estimated_minutes')
    list_filter = ('milestone__course', 'milestone')
    inlines = [AssessmentInline]


@admin.register(models.Assessment)
class AssessmentAdmin(admin.ModelAdmin):
    list_display = ('title', 'mission', 'pass_score')
    list_filter = ('mission__milestone__course',)
