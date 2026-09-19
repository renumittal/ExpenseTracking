"""
URL configuration for expense_tracker project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect
from django.views.static import serve
from django.urls import re_path

def health(request):
    """Render's health check: no login, no database access -- only proves Django is running."""
    return JsonResponse({'status': 'ok'})


urlpatterns = [
    path('health/', health, name='health'),
    # The web app lives in web/ and uses only relative paths, so it also works when hosted
    # under /ExpenseTracking/ (GitHub Pages). Serve it at that same path locally.
    path('', lambda request: redirect('/ExpenseTracking/')),
    path('ExpenseTracking/', serve, {'path': 'index.html', 'document_root': settings.BASE_DIR / 'web'}),
    re_path(r'^ExpenseTracking/(?P<path>.+)$', serve, {'document_root': settings.BASE_DIR / 'web'}),
    path('admin/', admin.site.urls),
    path('api/', include('core.urls')),
]
