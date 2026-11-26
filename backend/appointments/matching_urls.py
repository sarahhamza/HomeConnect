"""appointments/matching_urls.py : à ajouter à appointments/urls.py :

    from .matching_urls import urlpatterns as matching_urlpatterns
    urlpatterns += matching_urlpatterns
"""
from django.urls import path

from . import matching_views as v

urlpatterns = [
    path("auto/", v.auto_request, name="auto_request"),
    path("auto/<str:pk>/", v.match_detail, name="match_detail"),
    path("prestataire/match/<str:pk>/", v.provider_match_action, name="provider_match_action"),
    path("prestataire/demandes-ouvertes/", v.provider_open_requests, name="provider_open_requests"),
    path("prestataire/demandes-ouvertes/<str:pk>/prendre/", v.provider_take_request,
         name="provider_take_request"),
    path("ia/", v.ai_dashboard, name="ai_dashboard"),
]
