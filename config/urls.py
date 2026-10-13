from django.contrib import admin
from django.urls import path, re_path
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from apps.core.api import API
from apps.circle.api import CircleAPI

@ensure_csrf_cookie
def shell(request):
    response = render(request, 'index.html')
    response['Cache-Control'] = 'no-store'
    if request.path.startswith(('/u/', '/support/', '/circle/verify/')):
        response['X-Robots-Tag'] = 'noindex, nofollow'
    return response

urlpatterns = [path('admin/', admin.site.urls), re_path(r'^api/v1/circle/(?P<route>.*?)/?$', CircleAPI.as_view()), re_path(r'^api/v1/(?P<route>.*?)/?$', API.as_view()), re_path(r'^(?!api/|static/).*$', shell)]
