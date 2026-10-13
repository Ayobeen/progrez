from django.contrib import admin
from .models import JourneyPrivacy, SupportMember, Achievement, CircleNotification, Encouragement, CircleEmail

@admin.register(SupportMember)
class SupportMemberAdmin(admin.ModelAdmin):
    list_display = ('name', 'learner', 'status', 'circle_access', 'muted_by_learner')
    exclude = ('access_nonce', 'visitor_hash', 'network_hash')

@admin.register(Encouragement)
class EncouragementAdmin(admin.ModelAdmin):
    list_display = ('learner', 'member', 'kind', 'created_at', 'hidden')
    exclude = ('visitor_hash', 'network_hash')

admin.site.register([JourneyPrivacy, Achievement, CircleNotification, CircleEmail])
